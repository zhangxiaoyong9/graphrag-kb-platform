# 多轮对话式问答 — 设计文档

- 日期: 2026-06-29
- 状态: 已批准(按推荐方案:QueryEngine 之上的"重写层")
- 上游: 总体 spec `2026-06-24-kb-platform-design.md` §查询;查询 seam `kb_platform/query/engine.py`、API `POST /kbs/{id}/query`、仪表盘 `ChatPage.tsx`。
- 路线图: Q&A 体验 A1(本 spec)→ A2(流式回答)→ A3(查询调优 UI)。A1 只做多轮对话 + 持久化,A2/A3 后续单独立项。

## 1. 背景与目标

平台已具备完整的单轮检索(local / global / drift / basic),并已通过 HTTP API、仪表盘 Chat 页、MCP server 暴露。但 **Chat 页每次提问都是一次孤立检索**:LLM/检索看不到上下文,于是 "他负责什么?"、"再说详细点"、"那它和 X 呢?" 这类**指代/省略/追问**都无法解析,用户不得不每轮重新铺满背景。

A1 把 Chat 页升级为**真正的多轮对话**:后续提问相对历史做**改写**(standalone question),再走既有检索。对话**持久化进 SQLite**(可跨刷新/重启恢复、可回看)。

**成功标准:**
- Chat 页成为会话式:用户可新建/选择/删除对话,每个对话绑定单个 KB;后续提问相对历史解析,返回有来源依据的回答。
- 新增 `conversation` / `message` 两张控制面表(Alembic `0006`),对话跨刷新/重启可恢复。
- `QueryEngine` Protocol **保持单发**(`search(method, query, root)` 签名不变),graphrag 四种引擎与现有 `POST /kbs/{id}/query` 单发路径**零改动**(MCP、检索测试页继续走单发)。
- 多轮逻辑可用 `FakeQueryEngine` + 假 completion **全程无真 LLM 测试**(沿用项目约定)。
- 改写失败/引擎失败 → 优雅降级为消息级 error 或回退,绝不 500。

## 2. 范围(YAGNI)

**做:**
- `kb_platform/conversation/` 新包:`service.py`(ConversationService)、`rewriter.py`(Rewriter Protocol + FakeRewriter)。
- 两张控制面表 + Alembic `0006` 迁移 + Repository 方法。
- 新路由 `routes_conversations.py`:对话 CRUD + `POST /conversations/{id}/messages`(核心)。
- 一个 graph-seam helper `build_chat_complete(model_config)`(放 `graph/` 层),给重写器造一次性 chat completion。
- 前端 `ChatPage` 演进:对话侧栏 + 持久化 + 每条助手消息复用 `QueryResultView` + "理解为 {rewritten_query}" 提示。

**不做(v1):**
- 对话式**综合**(Option 2 的第二跳)——v1 用 graphrag 自带的 grounded 答案;答案偏"独立陈述"而非"聊天腔"。后续作为质量旋钮按需加。
- MCP 暴露多轮——MCP 维持单发(`query_knowledge_base`);agent 自己管理上下文。
- 对话**搜索 / 导出**、跨 KB 对话、对话内切换 KB。
- 流式回答(A2)、查询调优 UI(A3)——路线图后项。
- 鉴权(平台整体目前无鉴权,见原 spec §11 非目标)。

## 3. 架构设计

### 3.1 QueryEngine 之上的新层(关键决策)

graphrag 的 `engine.search(query: str)` 是**严格单发**:四种方法都以扁平 query 串做实体嵌入匹配 / 社区选择,没有地方塞对话历史。因此多轮**必须在 graphrag 之上的一层解决,而不是改 graphrag**。

`QueryEngine` Protocol 保持单发不变;新增 `ConversationService` 在其**之上**:

```
POST /conversations/{id}/messages   {content, method?}
   │
   ▼
ConversationService.send(conv_id, content, method)
   1. 取 conversation + 最近 ~6 条消息(历史窗口,见 §3.3)
   2. standalone = 首轮 ? content : Rewriter.rewrite(content, history)
   3. QueryResult = engine.search(method, standalone, data_root)   ← 引擎不变
   4. 落库:user 消息 + assistant 消息(answer, sources, tokens, rewritten_query, error)
   5. 返回 assistant 消息
```

首轮无历史 → 直接透传(零额外 LLM);后续每轮多一次廉价改写 LLM 调用。

### 3.2 保持 graphrag 缝干净(本仓库铁律)

`CLAUDE.md`:`graphrag_adapter.py` 是唯一 import graphrag 内部的模块;`graphrag_engine.py` 是查询侧唯一 reach into graphrag 的缝;`graph/cost_capture.py` 包 graphrag-llm。**`conversation/` 包绝不 import graphrag**。

重写器需要 LLM,因此:
- `Rewriter` 是 Protocol,带 `FakeRewriter`(测试);真实实现接受**注入的 completion callable**:`async (system, user) -> (text, usage)`。
- 该 callable 由**路由层**用一个新 graph-seam helper `build_chat_complete(model_config)` 构造(放 `graph/`,与引擎同一套凭据解析:`assemble_kb_settings` → model_config → completion)。
- 于是整个多轮链路可用 `FakeQueryEngine` + 一个假 completion 测,无真 LLM;graphrag-llm 的 import 仍只出现在 `graph/`。

### 3.3 Rewriter

```python
class Rewriter(Protocol):
    async def rewrite(self, message: str, history: list[HistoryTurn]) -> str: ...

class FakeRewriter:  # 测试用,确定性
    async def rewrite(self, message, history) -> str: ...
```

- **历史窗口**:取该 conversation 最近 **~6 条消息**(3 个 Q&A 对),截断更早的,控制改写 LLM 的 token 成本。
- `LlmRewriter(complete)`:`complete` 即 §3.2 注入的 callable;用一段固定的 standalone-question 改写 system prompt + 历史 + 新消息,返回**自包含 query**(如 "他负责什么?" + 历史 → "Acme 公司的 CEO 负责什么?")。
- **失败回退**:改写抛错/超时 → 回退到原始 message 作为 standalone,记日志,消息置 `rewrite_fell_back=True`,UI 可提示。绝不阻塞回答。
- 改写 LLM 走 KB 的 **LLM provider profile**(与检索同一 profile);其 token 计入该 assistant 消息的 token 合计(见 §4)。

## 4. 控制面数据模型(Alembic `0006`)

遵循 `db/models_profile.py` 的分文件惯例,新增 `db/models_conversation.py`:

```
conversation
  id, kb_id (FK→knowledge_base, ondelete cascade),
  title, created_at, updated_at
  -- title 首条 user 消息后自动取(~40 字截断),可 PATCH 改名

message
  id, conversation_id (FK→conversation, ondelete cascade),
  ordinal,                 -- 会话内自增,保证渲染顺序
  role (user | assistant),
  content,                 -- user 文本 / assistant 答案
  method,                  -- local/global/drift/basic,逐消息(user 行为 null)
  rewritten_query,         -- 改写出的 standalone query;首轮/回退时为 null
  rewrite_fell_back,       -- bool,assistant only;改写抛错回退到原始 message 时置 true
  sources_json,            -- list[SourceRef](assistant only)
  prompt_tokens, output_tokens, elapsed_ms, error,   -- assistant only(含改写 token)
  created_at
```

**逐消息携带检索结果**:每条 assistant 消息自带 sources / token / elapsed / error / rewritten_query,使 transcript 渲染与今日单轮同等丰富(答案 + 来源/证据 + token + 耗时 + 改写提示),且改写器读历史时直接读持久化的 user+assistant 行。`method` 逐消息存(默认取该会话上次用的方法),符合已确认默认。

Repository 方法:conversation CRUD、`list_by_kb(kb_id)`(带 last assistant snippet)、`messages_ordered(conv_id)`、级联删除靠 FK。

## 5. API 设计

新路由 `kb_platform/api/routes_conversations.py`,注册于 `app.py` 的 catch-all 之前:

| Method | Path | 说明 |
|--------|------|------|
| `POST` | `/kbs/{kb_id}/conversations` | 建对话 `{title?}` → `{id, kb_id, title}` |
| `GET` | `/kbs/{kb_id}/conversations` | 列表 `{id, title, updated_at, snippet}`(末条 assistant 预览) |
| `GET` | `/conversations/{id}` | 对话 + 有序消息(逐条含 content/method/sources/tokens/rewritten_query) |
| `PATCH` | `/conversations/{id}` | 改名 `{title}` |
| `DELETE` | `/conversations/{id}` | 删除 + 级联消息 |
| `POST` | `/conversations/{id}/messages` | **核心**:`{content, method?}` → ConversationService → 返回 assistant 消息 |

- `POST /kbs/{id}/query`(单发、MCP 用)**不变**。多轮走 `/conversations/{id}/messages`;二者共享底层引擎。
- `POST /conversations/{id}/messages` 内部:取 KB → `assemble_kb_settings`(与检索路由同一解析)→ `build_chat_complete` 造改写 callable → `GraphRagQueryEngine(data_root, model_config)` → `ConversationService.send(...)`,**沿用 `app.state.query_engine` 注入约定**(测试注入 Fake)。
- 跨 KB 访问 / 不存在 → `404`(不泄露存在性),与现有 profile 删除的 409 风格一致。

## 6. 前端演进(`ChatPage.tsx`)

现状:左 KB 选择、中 transcript、方法选择,每次 `query()` 独立检索,state 刷新即丢。演进:

- **左侧对话侧栏**:选中 KB 的对话列表 + "新建对话";点击切对话,`GET /conversations/{id}` 恢复 transcript(跨刷新持久)。
- **中间 transcript**:复用现有气泡;每条 assistant 气泡复用 `QueryResultView`(答案 + 来源/证据抽屉),追加 **"理解为:{rewritten_query}"** 小提示(仅后续轮)与既有 token/耗时 chip。
- **方法选择**:保留,逐次发送(默认上次方法)。
- 发送 → `POST /conversations/{id}/messages`;新建对话在首次发送时按需创建(或显式 "新建")。
- UI 文案沿用中文(与仪表盘一致)。

## 7. 错误处理 / 幂等

- **改写失败**:回退原始 message 为 standalone,日志 + `rewrite_fell_back` 标记;回答仍返回(只是上下文感知弱)。
- **引擎 / 设置失败**:沿用今日单发优雅路径 —— `QueryResult.error` 落到 assistant 消息 `error` 字段;user 消息照常入库;用户可重发。
- **KB / 对话不存在、跨 KB**:404。
- **幂等**:消息为 append-only;重发 = 新消息(不修改历史)。`ordinal` 保证顺序。

## 8. 测试策略(无真 LLM,沿用项目约定)

- **`ConversationService`**(`FakeRewriter` + `FakeQueryEngine`):首轮透传(不改写);后续轮以正确窗口调重写器;结果以 user+assistant 消息落库(含 sources/token);改写抛错时回退且 `rewrite_fell_back=True`。
- **Rewriter 逻辑**:历史窗口截取(取最近 N 条);`FakeRewriter` 确定性。
- **graph-seam helper**:`build_chat_complete` 从解析后 config 返回 callable(小冒烟,验证接线)。
- **API 集成**(`ASGITransport` + `FakeQueryEngine` + 假 completion):建对话 → 发消息 → 回 assistant 消息(含 sources/answer);后续轮改写;list/get/rename/delete;404;跨 KB 隔离。
- **Repository / model**:级联删除;按 `ordinal` 排序。
- **Alembic 0006** up/down 冒烟。
- **前端**(vitest):`ChatPage` 渲染对话列表、经新端点发送、显示改写提示 + 来源。

**回归:** `uv run pytest` 全绿(含新测试)、`uv run ruff check .` 通过;`cd web && npm test && npm run build` 通过;`npm run e2e` 不回归(Fake server 侧若需要可加对话端点)。

**手验(可选,真 LLM):** 启 API + worker,在 Chat 页对已索引 KB 连续追问(含指代/省略),核对后续轮 `rewritten_query` 合理且回答 grounded;刷新页面对话仍在。

## 9. 非目标 / 延后项

- 对话式综合(Option 2 第二跳)、MCP 多轮、对话搜索/导出、跨 KB 对话、鉴权 —— 见 §2。
- A2 流式回答、A3 查询调优 UI —— 路线图后项,A1 完成后单独立项。
