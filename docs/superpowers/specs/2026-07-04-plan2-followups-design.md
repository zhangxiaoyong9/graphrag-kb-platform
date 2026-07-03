# 2026-07-04 — plan2 follow-ups: 多轮对话 Cypher/截断透明度 + 预设 hops/timeout + 截断 UI

## 背景

`2026-07-03-neo4j-graph-query`（plan2）引入了三个 seam：

- `QueryParams.hops` / `QueryParams.cypher_timeout_ms`（`query/engine.py`、`query/params.py`）
- `StreamDone.truncated`（L2 行数上限标志）
- `StreamMeta{cypher}`（L3 透明度事件）

并在**单发查询**路径（`routes_query.py` → `QueryPage`）上接通：`routes_query.py:60-65` 对 `StreamMeta` 发 `meta{cypher}`，`routes_query.py:80` 把 `truncated` 写进 `done` 负载。

但三个相邻表面没有跟进：

1. **多轮对话**（`ConversationService` → `routes_conversations` → `ChatPage`）对 Cypher 与截断**完全无感**。`service.py:131-138` 的事件循环只对 `StreamDelta` 做特判，其它事件（含 `StreamMeta`）全部落到 `else: # StreamDone` 分支被当 done 吞掉；`done.truncated` 也从不进入任何事件数据。
2. **查询预设**（`QueryPreset` 列存表 + `QueryPresetsPage`）不带 `hops` / `cypher_timeout_ms`。`applyPreset` 已读 `p.hops`（`QueryPage.tsx:53`），但 `savePreset` 不发 hops；`cypher_timeout_ms` 全链路缺失（QueryPage 无输入、CRUD 模型无字段）。
3. **截断标志** `truncated` 在 `QueryResult` 上声明（`types.ts:88`）并经 SSE 透传，但**全仓库零渲染点** —— 单发查询两面（`QueryPage` / `QueryTestPage`）也不显示。

本设计把这三处补完，让 plan2 的三个 seam 在所有表面上行为一致。

## 目标

- **M1**：多轮对话实时显示生成的 Cypher，并在历史里持久化 Cypher + 截断标志（重开会话仍可审计）。
- **M2**：`hops` + `cypher_timeout_ms` 成为预设可持久化的方法旋钮（存、取、应用、编辑全链路）。
- **M3**：行数上限截断时，所有结果表面（单发查询两面 + 多轮对话）给出一致、可见的提示。

## 非目标

- 不改截断阈值 `ROW_CAP=1000`（`neo4j_engine.py:141`），也不把它动态暴露给 UI（文案保持解耦）。
- 不改单发查询的 `meta{cypher}` 行为（已正确）；只让多轮对话对齐。
- 不重构预设为 JSON blob（保持列存，只加两列）。
- 不引入 Cypher 语法高亮（`<pre>` 等宽即可）。
- 不动 graphrag / graphrag-llm 任何 seam。

## 设计

### 数据层 —— 一个 Alembic 迁移（`0011`）

两张表加列，全可空/带默认，旧行无感：

| 表 | 新列 | 类型 | 备注 |
|---|---|---|---|
| `message` | `cypher` | `TEXT NULL` | cypher/hybrid 方法生成的 Cypher；其余方法为 NULL |
| `message` | `truncated` | `BOOLEAN NOT NULL DEFAULT 0` | L2 标志，旧行视为未截断 |
| `query_preset` | `hops` | `INTEGER NULL` | hybrid 专属 |
| `query_preset` | `cypher_timeout_ms` | `INTEGER NULL` | cypher 专属 |

### 共享前端集成点：`QueryResultView`（计划阶段发现，据此简化）

`web/src/components/QueryResultView.tsx` 是三个结果页面（`QueryPage` / `QueryTestPage` / `ChatPage`）共用的"结果元数据"组件（渲染 method/elapsed/tokens/sources/error，**不**渲染答案正文）。因此：

- `<TruncatedNotice />` 与 cypher 折叠区**都落进 `QueryResultView` 内**（条件渲染），三页自动继承，无需各页单独插入。
- `QueryResult` 类型加一个可选 `cypher?: string | null`（`truncated` 已存在）。
- `ChatPage` 把持久化 Message 的 `cypher` / `truncated` 喂进传给 `QueryResultView` 的合成 `result` 对象即可。
- 单发查询（`QueryPage` / `QueryTestPage`）的 `result` 已自带 `truncated`（后端 done 负载携带），无需改动即显示截断；cypher 不在单发查询的显示范围（M1 范围外）。

### M1 — 多轮对话 Cypher + 截断透明度

**事件契约（对齐单发查询的双 meta 模式）**

- 会话流仍以一个 leading `meta{method, rewrite_fell_back, [rewritten_query]}` 开头（不变）。
- 引擎 yield `StreamMeta` 时，service 再 yield 一个 `meta{method, cypher}`（与 `routes_query.py:60-65` 同形）。
- 终态 `done` 的 `message` 对象自带 `cypher` + `truncated`（见 MessageOut），**单一事实源是持久化的 Message 行** —— 不再单独往 `done.data` 里塞 `truncated`。

**`conversation/service.py send_streaming` 改动（核心）**

事件循环由两分支改为三分支：

- `StreamDelta` → 累积 + yield delta（不变）
- **`StreamMeta`** → 捕获 `cypher = ev.cypher`；`yield StreamEvent("meta", {"method": chosen_method, "cypher": ev.cypher})`
- `StreamDone` → 捕获 `done`（读 `done.truncated`）

`add_message(...)` 调用补 `cypher=cypher` 与 `truncated=bool(done.truncated)`。终态 `done` 的 `StreamEvent` 数据保持 `{}`（message 自带两字段）。

**持久化层**

- `models_conversation.py Message`：加 `cypher: Mapped[str | None]`（Text, nullable）、`truncated: Mapped[bool]`（Boolean, default False, server_default "0"）。
- `repository.add_message(...)`：加 kw `cypher: str | None = None`、`truncated: bool = False`，传入构造。
- `api/models.py MessageOut`：加 `cypher: str | None = None`、`truncated: bool = False`。

**路由** —— `routes_conversations.py` **无需改动**（已透传 `ev.type / ev.data`；`_message_out` 会带上新字段）。

**前端**

- `types.ts ChatMessage`：加 `cypher?: string | null`、`truncated?: boolean`。
- `ChatPage.tsx`：
  - `meta` 分支：除现有 `rewritten_query` 外，读 `ev.data.cypher` 写到 pending 消息。
  - `done` 分支：`ev.data.message` 已自带两字段，现有 `{...persisted}` 合并自动带上。
  - 渲染：**保留**现有 inline 的 `rewritten_query` / `rewrite_fell_back`（行 334 / 341-343，**不动**）。cypher 与截断的呈现交给 `QueryResultView`（见上"共享前端集成点"）：`ChatPage` 传给 `QueryResultView` 的合成 `result` 补上 `cypher: m.cypher` 与 `truncated: m.truncated` 即可，**ChatPage 自身不新增 `<details>`**。

**M1 不改单发查询路径**（`routes_query.py` / `QueryPage` 已正确）。

### M2 — 预设持久化 `hops` + `cypher_timeout_ms`

**后端（纯加字段，resolver 不动 —— 前端回灌）**

- 迁移见上（`query_preset` 加两列）。
- `models.py QueryPreset`：加 `hops`、`cypher_timeout_ms` 两列映射。
- `api/models.py QueryPresetIn / QueryPresetUpdate / QueryPresetOut`：各加 `hops: int | None = None`、`cypher_timeout_ms: int | None = None`。
- `repository` 预设 CRUD（create / update / read）：把两字段纳入逐字段赋值。

> **范围扩展（审阅时请确认）**：plan2 只在 `QueryPage` 给 hybrid 加了 `hops` 输入，**没有**给 cypher 加 `cypher_timeout_ms` 输入（该字段目前只能经 KB `query_defaults` 或 per-query API 设置，是 resolver-only）。要让 `cypher_timeout_ms` 成为预设可存可取的旋钮，需同时在 `QueryPage` 调参面板补一个 `cypher_timeout_ms` 输入（`method === "cypher"` 时显示），并接进 buildParams / applyPreset / savePreset。这是把 plan2 留在 resolver-only 的字段补成 first-class 可调旋钮，属于 M2=B（hops + cypher_timeout_ms 一起加）的自然结果。

**前端**

- `types.ts QueryPreset`：加 `hops?: number | null`、`cypher_timeout_ms?: number | null`。
- `QueryPresetsPage.tsx`：
  - 草稿态加两字段。
  - 表单按 method 条件渲染：`hybrid` 显示 hops、`cypher` 显示 cypher_timeout_ms（与 `QueryPage` 现有 hops 输入同款式，placeholder 标注默认值 hops=2 / timeout=10000ms）。
  - 表格：不新增两列，把方法相关旋钮并入一个"方法旋钮"汇总列（`hybrid → hops=N`、`cypher → timeout=Nms`、否则 `—`），避免稀疏空列。
- `QueryPage.tsx`：
  - 新增 `cypher_timeout_ms` state + 输入（`method === "cypher"` 时显示）。
  - `buildParams`：补 `if (cypherTimeoutMs.trim()) p.cypher_timeout_ms = Number(cypherTimeoutMs);`
  - `applyPreset`：补 `setCypherTimeoutMs(p.cypher_timeout_ms != null ? String(p.cypher_timeout_ms) : "")`（hops 已有，不动）。
  - `savePreset`：发送体补 `hops: hops ? Number(hops) : null` 与 `cypher_timeout_ms: cypherTimeoutMs ? Number(cypherTimeoutMs) : null`。

### M3 — `truncated` 的 UI 渲染

- 新建共享组件 `web/src/components/TruncatedNotice.tsx`：琥珀色（`warning-soft`，与"需社区报告"标记同色系）小条，文案固定 **"结果已达行数上限，已截断。可缩小范围或调整上限。"**（不带数字，与 `ROW_CAP` 解耦）。
- **在 `QueryResultView` 内顶部（method 徽标行之前）渲染 `<TruncatedNotice />`，条件 `result.truncated`**。三页（`QueryPage` / `QueryTestPage` / `ChatPage`）共用 `QueryResultView`，一处接入三处生效：
  - `QueryPage` / `QueryTestPage`：`result` 已自带 `truncated`（后端 done 负载携带），无需改动。
  - `ChatPage`：合成 `result` 补 `truncated`（见 M1）。
- cypher 折叠区也落进 `QueryResultView`（条件 `result.cypher`，仅 ChatPage 喂入），与 `<TruncatedNotice />` 同组件内分区呈现。

## 契约小结

- **SSE（多轮对话）**：`meta{method, [rewritten_query], [rewrite_fell_back]}` →（可选）`meta{method, cypher}` → `delta{text}`* → `done{message: {…, cypher, truncated}}`。
- **Message 行**：`cypher NULL` + `truncated NOT NULL DEFAULT 0`。
- **QueryPreset 行**：`hops NULL` + `cypher_timeout_ms NULL`。
- **前端类型**：`ChatMessage` / `QueryPreset` 各加对应可选字段；`QueryResult` 加 `cypher?: string | null`（`truncated` 已存在，直接消费）。

## 测试策略

**后端**

- **M1**：`ConversationService.send_streaming` 用一个会吐 `StreamMeta(cypher=...)` 的 FakeQueryEngine，断言 (a) 按序 yield `meta{cypher}`、(b) `done` 的 message 带 `cypher`+`truncated`、(c) 落库 Message 行带两列。
- **M1**：迁移升级冒烟（`alembic upgrade head` 在带数据的库上不破坏旧行；`truncated` 默认 0）。
- **M2**：预设 CRUD 用 `hops=3, cypher_timeout_ms=8000` 往返，断言 read 回原值。

**前端**

- **M1**：`ChatPage.test.tsx` 喂 `meta{cypher}` + `done{message:{truncated:true, cypher:"MATCH ..."}}`，断言详情区出现 cypher 与截断标记。
- **M2**：`QueryPresetsPage.test.tsx` 存 hybrid 预设含 hops=3、断言 `createQueryPreset` 收到 hops；`QueryPage.test.tsx` 应用该预设后断言 hops 输入被填。
- **M3**：`QueryPage.test.tsx` / `QueryTestPage.test.tsx` 各喂 `truncated:true` 的 done，断言 `<TruncatedNotice />` 出现。

## 风险与回滚

- 迁移纯加列（可空/带默认），`alembic downgrade` 干净回滚。
- service 事件循环两分支 → 三分支：仅新增 `StreamMeta` 显式处理，`StreamDelta` / `StreamDone` 行为不变，风险低。
- `truncated` 字段已在 SSE 透传，前端只是开始消费，后端零改动。
- 不改任何 graphrag / graphrag-llm seam；不改单发查询路径。
