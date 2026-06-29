# 流式回答(SSE) — 设计文档

- 日期: 2026-06-29
- 状态: 已批准(按推荐方案:扩展 `QueryEngine` Protocol 的 `stream_search` + 既有端点改吐 SSE)
- 上游: 总体 spec `2026-06-24-kb-platform-design.md` §查询;查询 seam `kb_platform/query/engine.py`、`graphrag_engine.py`;A1 多轮 spec `2026-06-29-multiturn-conversational-chat-design.md`;API `POST /kbs/{id}/query`、`POST /conversations/{id}/messages`;仪表盘 `ChatPage.tsx`、`QueryTestPage.tsx`。
- 路线图: Q&A 体验 A1(多轮对话,已完成)→ **A2(流式回答,本 spec)** → A3(查询调优 UI)。

## 1. 背景与目标

A1 已把 Chat 页升级为多轮对话,但答案仍是一次性阻塞返回 —— 用户提问后要等整段答案生成完才看到第一个字,长答案的等待体感差。检索测试页(QueryTestPage)同理。

A2 把回答改为**token 级流式**:答案一边生成一边经 **SSE(Server-Sent Events)** 推给前端,首字延迟 ≈ LLM 首 token 延迟,而非全答案生成耗时。聊天路径与单次查询路径**都**流式。

**关键发现(降低风险):** graphrag 四种搜索引擎(local / global / drift / basic)本就暴露 `async stream_search(query, ...) -> AsyncGenerator[str]`:内部做完检索 + map-reduce 后,只把**最终答案**的 token 增量吐出。`graphrag_engine.py` 既有的 `_StreamFixWrapper` 正好绕过 graphrag-llm `stream=True` 的 await bug。因此流式不必从零造、不必在 completion 层做脆弱的"按调用次数判断最终答案"——graphrag 直接交答案增量。

**成功标准:**
- Chat 页与 QueryTestPage 的答案**逐字流式渲染**,首字延迟显著低于阻塞全量返回。
- `QueryEngine` Protocol 新增 `stream_search`,与 `search` 同输入;`FakeQueryEngine` / `GraphRagQueryEngine` 均实现,可**全程无真 LLM 测试**(沿用项目约定)。
- 既有端点 `POST /kbs/{id}/query`、`POST /conversations/{id}/messages` **改为返回 `text/event-stream`**(SSE-only);前端逐字消费。
- 流式答案的 **sources 不丢失**:经 `QueryCallbacks.on_context` 钩子取回,随终止事件下发。
- MCP 查询(`query_knowledge_base`)**工具契约不变**:MCP 代理内部聚合 SSE 流,对外仍返回单个结果;agent 侧零改动。
- 改写/检索/流式中任一环节失败 → SSE `error` 事件 + 已落库部分,绝不 500、绝不静默吞错。
- `ruff check .` + `pytest` + `npm test` / `npm run build` 全绿;无新增 Python/npm 依赖。

## 2. 范围(YAGNI)

**做:**
- `QueryEngine` Protocol + 两个实现新增 `stream_search`;新增 `StreamDelta` / `StreamDone` 数据类。
- `GraphRagQueryEngine`:抽出 `_build_engine(method, root)` 公用辅助;`SourceCapturingCallback`(`QueryCallbacks`)取回 sources;流式路径四种方法都套 `_StreamFixWrapper`。
- `routes_query.py`、`routes_conversations.py`:两个端点改为 SSE(`StreamingResponse`,`text/event-stream`)。
- `ConversationService.send_streaming(...)`:多轮流式编排(改写 → meta → 落 user → 流 answer → 落 assistant → done)。
- MCP `KbApiClient.query()` 消费 SSE 流并聚合成单结果。
- 前端:`fetch` + `ReadableStream` 解析 SSE 的工具函数;ChatPage 乐观气泡 + 流式填充;QueryTestPage 增量渲染。
- 测试:引擎 stream_search、SSE 端点事件序列、send_streaming、MCP 聚合、前端 fetch-stream(vitest)。

**不做(v1):**
- WebSocket 双向通道 —— 单向 token 流用 SSE 足够,且路线图已定 SSE。
- 独立的 JSON 端点 / `?stream=false` 开关 —— 端点 SSE-only(决策见 §3.2);MCP 内部聚合而非走 JSON 后门。
- 流式 token 用量精确统计 —— graphrag `stream_search` 不保证带 `usage`,token 走 best-effort(详见 §3.4)。
- 查询调优 UI(A3)—— 路线图后项,本 spec 不碰。
- worker / 索引路径**零改动**。
- 鉴权(平台整体目前无鉴权)。

## 3. 架构设计

### 3.1 扩展 QueryEngine Protocol(关键决策)

沿用 A1 的接缝纪律:**只有 `graphrag_engine.py` 碰 graphrag**。流式是同一引擎的另一个方法,不新建独立 Protocol。

```python
# kb_platform/query/engine.py
from collections.abc import AsyncIterator
from dataclasses import dataclass

@dataclass
class StreamDelta:
    text: str                     # 答案增量

@dataclass
class StreamDone:                 # 终止事件,携带与 QueryResult 同样的元数据
    method: str
    elapsed_ms: float | None = None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    sources: list[SourceRef] | None = None
    error: str | None = None

class QueryEngine(Protocol):
    async def search(self, method, query, kb_data_root) -> QueryResult: ...
    async def stream_search(self, method, query, kb_data_root) -> AsyncIterator[StreamDelta | StreamDone]: ...
```

**契约:** `stream_search` 先吐 **0 个或多个** `StreamDelta`,随后**恰好一个** `StreamDone` 收尾(无论成功或失败)。`StreamDone.error` 非空即代表失败(此时 `StreamDelta` 可能已吐了部分文本)。输入与 `search` 完全一致。

`FakeQueryEngine.stream_search` 吐几个确定性 delta(如把答案按字拆)再吐 `StreamDone`,供测试。

### 3.2 端点改 SSE(而非新增端点)

**决策:** 既有 `POST /kbs/{id}/query` 与 `POST /conversations/{id}/messages` 直接改为返回 `text/event-stream`,**不**新增 `/stream` 变体、**不**保留独立 JSON 端点。

理由:
- 单一端点/界面,无端点膨胀;前端与文档只面对一处。
- MCP 代理改为内部聚合 SSE(§3.5),工具契约与 agent 侧零变化 —— 这是 SSE-only 唯一的"破坏面",且封装在 `KbApiClient` 一处。
- 测试随端点一起改为解析 SSE(借一个小工具函数,前端/后端/MCP 共享解析语义)。

**SSE 事件协议**(每个事件一行 `event:` + 一行 `data: JSON` + 空行):

| event | data | 时机 |
|---|---|---|
| `meta` | `{method, rewritten_query?, rewrite_fell_back?}` | 首个事件。单次查询路径无改写,只带 `{method}`;聊天路径在改写完成后带改写结果 |
| `delta` | `{text}` | 每个答案增量,0..n 次 |
| `done` | chat:`{message: MessageOut}`;单次:`{result: QueryResultOut}` | 终止,恰好一次,带完整文本+sources+元数据 |
| `error` | `{message}` | 失败终止(与 `done` 互斥) |

`sources` 统一放 `done`(不单独发 `sources` 事件):global 的 `on_context` 在 map 之后才触发,时序晚于首批 delta;放进终止事件可规避时序耦合,前端在 `done` 时一并落 sources。

### 3.3 GraphRagQueryEngine.stream_search 实现

1. **抽公用构造器:** 把 `_run_graphrag_search` 内"读 parquet → norm → `read_indexer_*` → 按 method 选 factory + 参数"那段抽成 `_build_engine(method, root) -> engine`(含 reports 缺失守卫、`_resolve_config`、embedding store)。`search` 与 `stream_search` 共用,避免双份维护。
2. **套 `_StreamFixWrapper`:** 流式路径下四种方法都会 `await self.model.completion_async(..., stream=True)`,均需把 `engine.model` 包成 `_StreamFixWrapper`(当前只有 basic 在阻塞路径包)。统一在 `_build_engine` 后包一次。
3. **注册 `SourceCapturingCallback`:** 实现 `graphrag.callbacks.query_callbacks.QueryCallbacks`,`on_context(context)` 把 context_records 存进实例属性;其余方法空实现。在 `_build_engine` 时 `callbacks=[capturer]` 传入(local/global/basic 在 `stream_search` 内触发 `on_context`;drift 触发 `on_reduce_response_start`)。
4. **驱动流式:** 计时开始 → `async for chunk in engine.stream_search(query): yield StreamDelta(chunk)` → 计时结束 → 用 capturer 的 context 走既有 `_extract_sources(context_data, method)` → `yield StreamDone(method, elapsed_ms, sources=…, error=None)`。
5. **错误兜底:** 全程 try/except → `yield StreamDone(method, error=str(e), elapsed_ms=…)`(不抛,让端点转成 `error` 事件)。reports 缺失等前置守卫同样以 `StreamDone(error=…)` 收尾。

**drift best-effort:** drift 的 `stream_search`(primer + follow-up)最复杂;若其行为异常,以 `StreamDone(error=…)` 如实上报,不静默、不偷偷回退阻塞。

### 3.4 元数据:能恢复的恢复,不能的 best-effort

| 字段 | 流式下来源 |
|---|---|
| `sources` | `SourceCapturingCallback.on_context` → `_extract_sources`(**可恢复**) |
| `elapsed_ms` | 流式驱动计时(**可恢复**) |
| `prompt_tokens` / `output_tokens` | graphrag `stream_search` 不保证 chunk 带 `usage`;若末块带则取,否则 `None`(**best-effort / 可为 None**) |
| `llm_calls` | 流式无聚合,**`None`** |

落库(assistant 消息 / `QueryResultOut`)时:`None` 字段照存,前端容忍缺失(与现有"未知成本置 None,绝不为 0"的约定一致)。

### 3.5 多轮流式编排:ConversationService.send_streaming

```python
async def send_streaming(self, conversation_id, content, method) -> AsyncIterator[StreamEvent]:
    # 1. 载对话 + 历史,选 method(同 send)
    # 2. 阻塞改写(同 send;失败回退 raw,置 rewrite_fell_back)
    # 3. yield meta(method, rewritten_query, rewrite_fell_back)
    # 4. 落 user 消息(同 send,先落)
    # 5. accumulated = ""
    #    async for ev in self._engine.stream_search(method, standalone, root):
    #        if isinstance(ev, StreamDelta): accumulated += ev.text; yield delta(ev.text)
    #        else: done = ev
    # 6. 落 assistant 消息(content=accumulated or done.error 占位, sources, tokens, elapsed, error)
    # 7. 若 done.error: yield error(done.error)
    #    else: yield done(persisted MessageOut)
```

复用 `send` 的历史载入 / 改写 / 落库 / 标题逻辑(抽小函数共享,避免分叉)。`StreamEvent` 是对 `{type: "meta"|"delta"|"done"|"error", ...}` 的小封装,由 route 序列化成 SSE。

### 3.6 路由层

- `routes_query.py` `query_kb`:构造引擎(同现有解析)→ `StreamingResponse(engine.stream_search(...), media_type="text/event-stream")`,把 `StreamDelta|StreamDone` 序列化成 `meta/delta/done/error` SSE。KB 不存在 / settings 解析失败 → 直接吐一条 `error` 事件的 SSE(仍 200,流式契约)。
- `routes_conversations.py` `send_message`:构造 engine + rewriter(同现有)→ `StreamingResponse(service.send_streaming(...))`,事件同上。
- SSE 序列化用一个共享 helper(`kb_platform/api/sse.py`):`sse(event, data) -> str`,前后端/MCP 共享事件语义。

### 3.7 MCP:聚合 SSE,工具契约不变

`kb_platform/mcp/` 的 `KbApiClient.query()` 改为:对 `POST /kbs/{id}/query` 用 httpx 流式读 `text/event-stream`,按 `meta/delta/done/error` 解析 → 把 `delta.text` 拼成完整 answer,`done.result` 取 sources/元数据 → 返回单个 `QueryResult`。`list_knowledge_bases` / `query_knowledge_base` 工具签名与返回**不变**,agent 侧零改动。MCP 层仍不 import graphrag。

### 3.8 前端

- **传输:** `EventSource` 仅支持 GET,而端点是 POST 带 JSON body → 用 `fetch(POST) + response.body.getReader()` 手解 SSE(一个 `web/src/lib/sse.ts`:异步迭代 `parseSse(response)` → `{event, data}`)。`vite.config.ts` dev 代理已覆盖 `/kbs`、`/conversations`,无需改。
- **ChatPage:** 发送时乐观插入 user 气泡 + 空 assistant 气泡 → 迭代 SSE:`delta` 追加进 assistant 气泡,`done` 用持久化 `MessageOut` 替换 optimistic 气泡(落真实 id + sources),`error` 在气泡显示错误。改写提示复用 A1 的 "理解为 {rewritten_query}"(从 `meta`)。
- **QueryTestPage:** 同样用 `sse.ts` 增量渲染答案区,`done` 时填 sources/耗时/元数据。
- **API client:** `sendMessage` / `query` 不再 `req<T>`(JSON),改为返回 `fetch` 响应或其 reader,交由调用方迭代;保留类型定义。

## 4. 数据 / 控制面

**无新增表、无 Alembic 迁移。** 流式复用 A1 的 `conversation`/`message` 表与既有 `MessageOut`/`QueryResultOut` 模型;`done` 事件直接装这两者。assistant 消息在流结束**一次性**落库(全文 + sources + 元数据),与 A1 落库时机一致,不引入"流式增量落库"。

## 5. 错误处理与降级

| 场景 | 行为 |
|---|---|
| KB 不存在 / settings 解析失败 | SSE `error` 事件(流式契约,HTTP 200) |
| 改写失败 | 回退 raw 消息,`meta.rewrite_fell_back=true`,继续流式答案(同 A1) |
| 引擎 `stream_search` 抛异常 | 已吐的 delta 保留;落"部分文本 + error" assistant 消息;`error` 事件 |
| drift 流式异常 | 如实 `error`,不静默回退 |
| 网络中断(前端) | assistant 气泡保留已收文本,标记中断;不假装完成 |

任何失败都不抛 500、不静默吞错。

## 6. 测试策略

- **引擎层(`tests/test_query_engine.py` 扩展):** `FakeQueryEngine.stream_search` 吐确定性 delta + `StreamDone`;断言 delta/done 序列与契约(恰好一个 done)。
- **GraphRag 引擎:** 因需真 graphrag index,以 `FakeGraphAdapter` 产出的轻量 index + mock completion 验证 `_build_engine` 共用、`SourceCapturingCallback` 捕 context、`_StreamFixWrapper` 套用(不跑真 LLM)。
- **SSE 端点(`tests/test_api_query_stream.py` 新建):** httpx `AsyncClient` 流式读,断言 `meta → delta* → done` 事件序列与 `MessageOut`/`QueryResultOut` 装载;错误路径吐 `error`。
- **ConversationService(`tests/test_conversation.py` 扩展):** `send_streaming` 用 fake 引擎断言:改写 → meta → 落 user → 流式累加 → 落 assistant(全文+sources)→ done。
- **MCP(`tests/test_mcp.py` 扩展):** `KbApiClient.query` 消费一段固定 SSE 文本 → 聚合成正确 `QueryResult`;`query_knowledge_base` 工具返回单个结果。
- **SSE 解析单测:** 后端 `sse.py` 与前端 `sse.ts` 各自的序列化/解析往返。
- **前端(vitest):** mock `fetch` 返回 ReadableStream,断言 ChatPage/QueryTestPage 增量渲染 + `done` 落库;`sse.ts` 解析单测。

## 7. 约束与约定

- `loop="asyncio"`、localhost 代理等既有 gotcha 不变。
- 新增 UI 文案中文,与 ChatPage 现有风格一致。
- `ruff check .`(line-length 100);`pytest`、`npm test`、`npm run build` 全绿。
- 无新增 Python/npm 依赖(SSE 用 Starlette `StreamingResponse`;前端用原生 `fetch` + `ReadableStream`)。
- `chunk_id`/`content_hash` 等既有约定不动。
- worker / 索引 / 控制面状态机**零改动**。

## 8. 风险与回滚

- **风险 1 — drift `stream_search` 不稳:** best-effort,如实 `error`;若实测普遍失败,可后续把 drift 单独回退为"端点内阻塞 `search` 后一次性吐 delta + done"(对前端透明,仍走同一 SSE 契约)。本 spec 不预先实现该回退(YAGNI),留作实测后的 contingency。
- **风险 2 — token 统计缺失:** 接受 best-effort `None`;不为此引入额外 LLM 调用或解析黑魔法。
- **风险 3 — 端点 SSE-only 破坏面:** 封装在 MCP `KbApiClient` 一处 + 测试;前端两处调用点。无外部第三方消费者(平台未公开 API 契约)。
- **回滚:** 端点改动集中在两个 route + 一个 SSE helper;`stream_search` 是 Protocol 上的新增方法,删之即回退到 A1 阻塞态。frontend `sse.ts` 独立可删。

## 9. 未尽事项(留给 A3 或后续)

- 查询调优 UI(community levels / result count / temperature / prompt library)—— A3。
- 流式 token 精确计费 —— 待 graphrag-llm 流式 usage 稳定后再议。
- 可中止/取消流式(用户点"停止")—— 未在 v1 范围,后续可加(前端 `AbortController` + 后端 `Request.is_disconnected`)。
