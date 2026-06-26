# 前后端 API 真实逻辑匹配 — 查询闭环 + 响应增强 + 配置可见

> 状态：已 brainstorm、已确认；待写实现计划。
> 日期：2026-06-26。
> 范围：`graphrag-kb-platform` 前端 + 后端（**本次含后端改动**，与上一阶段「仅前端 IA」不同）。
> 验证：用户提供 `DEEPSEEK_API_KEY`，可做真实 LLM 端到端验证。

## 1. 背景

上一阶段（SaaS 导航 IA）完成后，对前端↔后端 API 契约做了全量审计。结论：**字段形状全部对齐**（`web/src/api/types.ts` ↔ `kb_platform/api/models.py` ↔ 各 route），绝大多数端点跑的是真实逻辑，**只有一个端点没有真实逻辑——`POST /kbs/{id}/query`**，且该端点的响应**未暴露后端真实能力**。

### 1.1 审计结果（每个契约）

| 契约 | 形状 | 真实逻辑 | 发现 |
|---|---|---|---|
| `/kbs` CRUD、`/kbs/{id}` | ✓ | ✓ | `GET /kbs/{id}` 隐藏 `settings_json` → KB 配的模型/provider 无处可见 |
| documents / `/jobs` / `/steps` / retry | ✓ | ✓ | 字段已用满 |
| `/cost`（step/model/job） | ✓ | ✓ | 字段已用满 |
| `/graph`（nodes/edges/community） | ✓ | ✓ | 字段已用满 |
| `/health` | ✓ | ✓ | 字段已用满 |
| **`/kbs/{id}/query`** | ✓（极简） | **✗ 崩溃** | 见 1.2 |

### 1.2 查询端点的两个问题

**问题 A — 运行时崩溃。** 生产 `POST /kbs/{id}/query` 已接真实 `GraphRagQueryEngine`（`routes_query.py`：未注入 engine 时按 KB 构建）。但实测每次查询都抛：

```
Can't patch loop of type <class 'uvloop.Loop'>
```

根因（已定位）：`graphrag_llm/__init__.py:8` 在 import 时执行 `nest_asyncio2.apply()`。由于装了 `uvloop`，uvicorn 自动选用 uvloop，而 `nest_asyncio` **无法 patch uvloop**（`nest_asyncio2.py:336`）。查询时 `graphrag.query.factory` 首次 import `graphrag_llm` → `apply()` 在 uvloop 上炸 → 整个查询失败。

**问题 B — 响应未暴露真实能力。** graphrag 的 `SearchResult`（`graphrag/query/structured_search/base.py:28`）携带丰富数据，当前一处都没用：

| 字段 | 真实含义 | 当前 |
|---|---|---|
| `response` | 答案文本 | ✓ 用了 |
| `completion_time` | 服务端真实耗时 | ✗（前端只能测客户端往返） |
| `prompt_tokens` / `output_tokens` / `llm_calls` | 真实 token 用量 / 调用数 | ✗ |
| `context_data`（dict[str, DataFrame]） | local/drift 的实体、文本单元来源、关系；global 的社区报告 | ✗（前端用「无引用」占位） |

## 2. 目标 / 非目标

**目标**
- 让查询端到端可用：修复 uvloop 崩溃；4 种方法（local/global/drift/basic）对真实索引的 KB 返回真实答案。
- 暴露真实能力：`QueryResult` 带上服务端耗时、token 用量、**真实来源**（实体 + 文本单元片段）；前端检索/对话页展示它们，替换「无引用」占位。
- 配置可见：`GET /kbs/{id}` 返回脱敏后的 `settings`，KB 概要页显示模型 provider/model。

**非目标**
- 多租户 / 鉴权 / 计费；系统设置 / API Keys 页维持能力预留占位。
- 分析报表的「任务趋势 / 热门查询」（后端无时间戳 / 无查询日志 → 维持诚实空态）。
- 重写 graphrag 查询引擎或向量存储。

## 3. 决策（brainstorm 已定）

1. **修崩溃**：server 强制原生 asyncio loop（`uvicorn.run(app, host, port, loop="asyncio")`）。nest_asyncio 随之可 patch。worker 自带独立 asyncio loop，不受影响。
2. **丰富响应**：扩展现有 `QueryResultOut` 加可选字段（向后兼容），不新增端点。
3. **配置可见**：扩展 `GET /kbs/{id}` 返回脱敏 `settings`，在 KB 概要展示（全局「系统设置」页仍是平台级说明）。

## 4. 设计

### 4.1 Part 1 — 查询可运行（修崩溃）

- `kb_platform/server.py`：`uvicorn.run(app, host=host, port=port, loop="asyncio")`。
- 回归测试：用一个注入的 engine 跑 `routes_query`，断言 `import graphrag.query.factory`（→ 触发 graphrag_llm import）在 asyncio loop 下不抛 patch 异常；并验证正常返回 `QueryResultOut`。
- 不卸载 uvloop（保留给其他可能的用途；只是 uvicorn 不用它）。

### 4.2 Part 2 — 后端响应增强

- `kb_platform/query/engine.py`：`QueryResult` 增加可选字段
  ```python
  elapsed_ms: float | None = None
  prompt_tokens: int | None = None
  output_tokens: int | None = None
  llm_calls: int | None = None
  sources: list[SourceRef] | None = None
  ```
  新增 `@dataclass SourceRef: kind: str  # entity|text_unit|relationship ; name: str; text: str`。
- `GraphRagQueryEngine._run_graphrag_search`：从 `SearchResult` 读 `completion_time`/`prompt_tokens`/`output_tokens`/`llm_calls` 填入；新增 `_extract_sources(context_data, method, limit=8) -> list[SourceRef]`：
  - `context_data` 为 `dict[str, DataFrame]` 时：取 `entities`（按 `degree`/相关度 Top-N → entity）、`sources`/`text_units`（Top-N → text_unit，截断片段）。
  - `context_data` 为 list/str（basic/global 边界情形）时降级，能取则取，不能则 `sources=None`。
  - 全程 try/except：来源解析失败绝不影响答案返回（`sources=None`）。
- `FakeQueryEngine`：新字段恒 None（测试默认）。
- `kb_platform/api/models.py`：新增 `SourceOut(BaseModel)`；`QueryResultOut` 增加同名可选字段。
- `routes_query.py`：把 `QueryResult` 映射到 `QueryResultOut`（含 sources/tokens/elapsed）。

### 4.3 Part 3 — 前端消费真实数据

- `web/src/api/types.ts`：`QueryResult` 增 `elapsedMs?/promptTokens?/outputTokens?/llmCalls?/sources?: SourceRef[]`；`SourceRef { kind; name; text }`。
- `web/src/pages/QueryTestPage.tsx`：
  - 耗时改用服务端 `elapsedMs`（有则显示，回退客户端往返）。
  - 新增 token 用量小字（prompt/output/调用数）。
  - 「引用」区：有 `sources` 时渲染实体 chips + 文本单元片段（可折叠）；无则保留诚实说明。
- `web/src/pages/ChatPage.tsx`：每条助手回答下方同样展示 sources + tokens（紧凑版）。
- `KbLayout` 的「检索问答」tab（`QueryPage`）同步升级（复用同一渲染逻辑）。
- 诚实空态保留：global/drift 无社区报告 → 仍返回明确 `error`。

### 4.4 Part 4 — KB 配置可见

- `kb_platform/api/models.py`：新增 `KbDetailOut(KbOut)` 带 `settings: dict`。
- `routes_kbs.py` `GET /kbs/{id}` 改返回 `KbDetailOut`，`settings` 由 `_redact(kb.settings_json)` 产生（递归抹去 key 名含 `key`/`token`/`secret` 的值 → `"***"`；密钥本就不入库，这是双保险）。
- `GET /kbs`（列表）保持 `KbOut` 不变（不带回 settings，避免放大列表）。
- `web/src/api/types.ts`：`KbOut` 增 `settings?: dict`；`getKb` 返回带 settings。
- `web/src/pages/KbOverviewPage.tsx`：新增「模型配置」卡，展示 provider/model（从 settings.llm / settings.embedding 解析）+ `community_reports.structured_output` 标记。

### 4.5 Part 5 — DeepSeek 端到端验证

手动 live 验证（用户提供 key）：
1. `DEEPSEEK_API_KEY=... uv run python -m kb_platform.worker /tmp/verify.db`（后台）+ `uv run python -m kb_platform.server /tmp/verify.db /tmp/verify_data 127.0.0.1 8000`。
2. 建 KB：`settings_yaml` = deepseek provider + `community_reports.structured_output:false`（绕过 DeepSeek 拒 json_schema）。
3. 传 1 个 .txt → 触发 full → 轮询至 SUCCEEDED。
4. 依次查 local/basic（无需报告）、global/drift（需报告，已靠 structured_output:false 生成）→ 断言有真实答案 + 非空 sources + token > 0。

CI 可运行回归（不依赖真实 key）：
- 查询路由在 asyncio loop 下不抛 patch 异常（Part 1）。
- `GraphRagQueryEngine._extract_sources` 对构造的 `context_data` dict/list/str 各形态产出正确（Part 2）。
- `QueryResultOut` 序列化含新字段（Part 2）。
- `_redact` 抹掉 key/token（Part 4）。
- MockLLM 跑通 local 查询路径（复用现有 MockLLM 契约测试模式）。

## 5. DeepSeek 已知约束（沿用既有结论）

- DeepSeek 拒 `response_format` json_schema → community_reports 必须设 `structured_output:false`（Wave 1 G 已实现）。global/drift 依赖报告。
- extract/summarize/local/basic 在 DeepSeek 下正常。
- 验证用 KB 的 settings 必须显式带 provider/model + key 走 `DEEPSEEK_API_KEY` 环境变量（密钥不入库）。

## 6. 风险与对策

1. **`loop="asyncio"` 仍崩** — 极不可能（nest_asyncio 能 patch 原生 asyncio loop）；若崩，回退方案：worker 之外不在主进程跑查询，改在独立线程的 asyncio loop 跑 `engine.search`。
2. **`_extract_sources` 跨方法形态不一** — 全程 try/except + 降级 None；单测覆盖 dict/list/str 三态。
3. **脱敏漏字段** — key/token/secret 三类名一律 `***`；单测覆盖嵌套 dict + list。
4. **DeepSeek 报告为空致 global/drift 无来源** — 验证步骤 2 显式开 `structured_output:false`；若仍空，作为已知限制文档化（不阻塞 local/basic）。
5. **uvloop 仍被别的代码路径 import** — 仅 uvicorn 不选它即可；worker 独立进程无 uvicorn。

## 7. 验收（Done）

- 查询不再抛 patch-loop 异常；DeepSeek 下 4 方法返回真实答案 + 真实来源 + token。
- 现有 22 前端测试 + 后端测试全绿；新增 Part 1/2/4 单测。
- `web` build 干净；`npm test` 绿。
- KB 概要显示模型配置；检索/对话显示真实来源与 token。
- 每个 PR 附 semversioner change（若沿用；kb-platform 实际无 semversioner，按 ruff + pytest）。

## 8. 改动清单（实现时细化）

- 后端：`server.py`、`query/engine.py`、`api/models.py`、`api/routes_query.py`、`api/routes_kbs.py`（+ `_redact`）。
- 前端：`api/types.ts`、`pages/QueryTestPage.tsx`、`pages/ChatPage.tsx`、`pages/QueryPage.tsx`、`pages/KbOverviewPage.tsx`、`lib/query-methods.ts`（可能抽 sources 渲染组件）。
- 测试：后端 `tests/test_query_*`、`tests/test_redact`；前端 `pages/new-pages.test.tsx` 扩展或新增。
