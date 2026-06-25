# Phase 4 — 打磨（成本可视化 / 图可视化 / 文档管理 / 导出 / 加固）

> 状态：已 brainstorm、待写实现计划。
> 日期：2026-06-25。
> 范围：知识库平台（`graphrag-kb-platform`）设计文档 Phase 4「打磨」的完整落地设计。
> 前置：Phase 1 + 2a + 2b-1 + 2b-2 + 3a + 3b 均已在 `main`（控制面 SQLite + 独立 asyncio worker + FastAPI + React SPA）。

## 1. 背景与范围

设计文档第 10 节将 Phase 4 定义为「打磨 — cost 聚合可视化、graphml 可视化、文档管理 UX、导出」。项目 memory 另记一批延期技术债项。本设计把这两部分合并为**一个收尾阶段**，分三波推进，闭环 Phase 1–4 的全部设计目标。

**包含项（10 项）：**

| 编号 | 项 | 波次 | 类别 |
|---|---|---|---|
| E | `JobCreate.type` → `Literal["full","incremental"]` | 1 | 正确性 |
| F | `GET /health` + worker 优雅关闭 | 1 | 运维 |
| I | 策略按步注入（去全局 `STRATEGIES`） | 1 | 重构 |
| G | `community_reports.structured_output` 开关（DeepSeek 兼容） | 1 | 正确性 |
| H | delta-aware summarize / community_reports | 1 | 正确性 |
| A | 成本采集 + 聚合 API + 可视化 | 2 | 可观测 |
| C | 文档管理 UX（markitdown 全家桶上传 + 删除） | 3 | UX |
| D | 导出（zip / GraphML） | 3 | UX |
| B | GraphML + 图可视化（react-force-graph） | 3 | UX |
| J | Playwright E2E | 横切 | 测试 |

## 2. 目标 / 非目标

**目标**
- 解锁「真实可用」：DeepSeek 等无 structured-output 的 provider 能出社区报告（G）；增量索引后摘要/报告不陈旧（H）；可观测成本（A）；可运维（F）。
- 富交互：可上传多种文档、可导出、可看图（B/C/D）。
- 不改控制面/数据面核心架构，所有改动沿现有缝扩展。

**非目标（沿用设计文档第 11 节）**
- 多租户 / SaaS / 鉴权计费。
- 分布式 worker / 水平扩展。
- **文档删除导致的图收缩**（删除只清控制面，图不回缩；用增量刷新）。
- 自定义非 graphrag 图算法；重写 graphrag 存储/查询。

## 3. 架构总览

Phase 4 不引入新进程或新存储。所有改动沿现有缝扩展：

| 关注点 | 缝 | 做法 |
|---|---|---|
| 成本采集 | graphrag-llm completion | usage-capturing middleware（全策略自动覆盖） |
| delta 摘要/报告 | `UnitStepStrategy` | 新增 `SummarizeDeltaStrategy` / `CommunityReportsDeltaStrategy`，仿 `ExtractGraphDeltaStrategy` |
| 策略注入 | orchestrator 构造 | `strategies: dict[UnitKind, UnitStepStrategy]` 入参，废弃全局 `STRATEGIES` |
| 文件解析 | `GraphAdapter` | 新增 `read_document(data, filename) -> str`，底层 markitdown |
| 图数据 / 导出 | parquet 读层 | 复用 `graphrag_engine._norm_*` 规范化帧，派生 GraphML / 图 JSON |

### 3.1 数据模型变更（刻意保持极小）
- **无新表、无新列**。新增信息全部落进已有列：
  - 成本 → 已有 `Unit.cost_json`（middleware 写入，目前恒为 None）。
  - json_schema 开关 → 已有 `KnowledgeBase.settings_json`（键 `community_reports.structured_output`）。
  - delta 作用域 → 已有 `Unit.input_hash`（摘要/报告 unit 的输入指纹）。
- **Document 删除**：`Chunk.document_id` FK 改 `ON DELETE CASCADE`（一条 Alembic 迁移，SQLite 需 rebuild 表），删 Document 自动清其 chunk 行。
- `JobCreate.type`：API 层 `str` → `Literal["full","incremental"]`（仅校验，DB 列不动）。

### 3.2 新增 API 端点
```
GET    /health                                    # F
GET    /kbs/{id}/cost                             # A  按 step/model 汇总（跨 job）
GET    /kbs/{id}/jobs/{jid}/cost                  # A  单 job 成本
GET    /kbs/{id}/export?format=zip|graphml        # D  流式下载
GET    /kbs/{id}/graph?limit=N&q=&hop=1|2         # B  图 JSON
DELETE /kbs/{id}/documents/{doc_id}               # C  不回缩图
POST   /kbs/{id}/documents/upload (multipart)     # C  markitdown 全家桶
```
现有 `POST /kbs/{id}/documents`（文本粘贴）保留。`DocumentOut` 扩展 `bytes`、`chunk_count`。

### 3.3 前端新增
- **GraphView**（react-force-graph-2d，新依赖）放 KB 详情页：Top-N 全景 + 社区着色 + 搜索框聚焦邻域。
- **DocumentManager**（取代 `DocumentUpload`）：列表 + 文件上传/拖拽 + 文本粘贴 + 删除（带「图不回缩」提示）+ loading/disabled/error 态。
- **CostPanel**：Job 详情按 step 的 CSS 成本条 + 总额；KB 详情累计成本。**纯 CSS，不引图表库**。
- **Export 按钮**：KB 详情页一键下载 zip / GraphML。

### 3.4 依赖增减
- 后端：`+ markitdown`（文档解析，懒加载）。GraphML 自写 XML，不引 networkx。
- 前端：`+ react-force-graph-2d`（带 d3-force peer）。无图表库。

## 4. Wave 1 — 正确性 / 运维加固

### 4.1 E — `JobCreate.type` 收紧
- `JobCreate.type: Literal["full","incremental"] = "full"`；非法值 → 422（FastAPI 自动）。
- 测试：`type="bogus"` → 422；`"full"/"incremental"` 正常。

### 4.2 F — `/health` + worker 优雅关闭
- **`GET /health`**：
  ```json
  {"status":"ok"|"degraded","db":"ok"|"down",
   "worker":{"last_heartbeat_at":"<iso>|null","stale":bool}}
  ```
  - `db`：`SELECT 1` 探活；失败 → `db:"down"` 且 `status:"degraded"`（不抛 500）。
  - `worker.stale`：所有 RUNNING unit 中最新 `heartbeat_at` 超阈值（默认 60s，可配）判 stale；无 RUNNING unit 时 `last_heartbeat_at=null`、`stale=false`（空闲）。
- **优雅关闭**：worker 注册 SIGTERM/SIGINT → 置停止位 → 当前循环跑完在飞 unit、停止认领新批、落库、`exit 0`。硬杀仍由现有「stale RUNNING→PENDING」恢复兜底。
- 测试：health 正常 / DB down 降级；停止位起后不再认领（fake repo 注入）。

### 4.3 I — 策略按步注入
- orchestrator 构造签名增加 `strategies: dict[UnitKind, UnitStepStrategy]`；步骤从该 dict 解析策略。
- 现有 delta 路径的 `register_strategy("extract_graph", ExtractGraphDeltaStrategy(...))` **全局覆盖**改为：增量管线构造时**显式**传入 `{extract_graph: Delta, summarize: Delta, community_reports: Delta, ...}`；full 管线传默认集。
- 保留 `default_strategies()` 作为默认集构造器；`STRATEGIES` 全局表降级为「默认集来源」，不再运行时可变。测试各自注入，杜绝用例间全局污染。
- 测试：orchestrator 用注入的 fake 策略跑通；并发/顺序用例无全局状态泄漏。
- **必须在 H 之前完成**（H 依赖干净的注入）。

### 4.4 G — `community_reports.structured_output` 开关
- KB 设置项（`settings_json`）`community_reports.structured_output: bool`，默认 `true`。
- `true`：现行路径不变（graphrag `CommunityReportsExtractor` 的 structured output）。
- `false`（DeepSeek 等不支持 json_schema）：strategy 改走「纯文本 completion + 宽松解析」——同一份 reports prompt 但要求模型直接输出 JSON 对象，`json.loads` 解析（带正则兜底抽取 `{...}`），再映射到 report 数据类。失败的单社区 unit 走现有 proceed-on-failure（`min_success_ratio`），不整步崩。
- 测试：mock 一个「拒 response_format」的 completion → `false` 路径仍产出合法 report；`true` 路径行为不变。

### 4.5 H — delta-aware summarize / community_reports
- 增量管线顺序对齐 full 管线（`chunk→extract→summarize→finalize→cluster→community_reports`）。`plan_incremental` 现为 `load_update_documents → extract_graph(delta) → merge_delta → cluster → reconsolidate`，缺 summarize 与 community_reports。补成：
  `load_update_documents → extract_graph(delta) → merge_delta →` **`summarize(delta)`** `→ finalize →` **`cluster`** `→` **`community_reports(delta)`** `→ reconsolidate`
  （即：summarize 在 merge_delta 之后、cluster 之前；community_reports 在 cluster 之后——与 full 管线一致，因为 cluster 跑在已摘要的实体图上。）
  - **`SummarizeDeltaStrategy`**（subject_type=`entity`，继承 `SummarizeStrategy` 的 `run_unit/persist/finalize`，仅覆写 `next_units_batch`）：对每个实体算 `current_hash = hash(排序后的合并描述集合)`；查该 entity 最近一次 SUCCEEDED summarize unit 的 `input_hash`，**不同或缺失**才排程。`finalize` 照常把新摘要 merge 回持久化摘要。
  - **`CommunityReportsDeltaStrategy`**（subject_type=`community`，同理继承 `CommunityReportsStrategy`）：cluster 后对每个社区算 `current_hash = hash(排序后的成员实体 id)`；与最近 SUCCEEDED report unit 的 `input_hash` 比，变更/新增才排程。**消失的社区**（被合并）旧报告保留不删（YAGNI）。
- 首次增量（紧接 full build）：所有 entity/community 已有 full 期写入的 `input_hash`，故只有真正受新文档影响的才重跑 → unit 数 ≪ 全量。
- `reconsolidate`（增量后自动跑）逻辑不变，只是 `needs_reconsolidation` 标记来源包含 delta 摘要/报告。
- 测试：`incremental(加 1 文档)` 断言 summarize/report 的 **unit 数远小于全量**且只命中受影响实体/社区；`input_hash` 命中缓存的不重排。

## 5. Wave 2 — 成本采集 + 聚合 + 可视化（A）

### 5.1 采集（graphrag-llm middleware）
- usage-capturing middleware 挂在 `create_completion` 上，记录每次调用 `{model, prompt_tokens, completion_tokens}`，经 graphrag-llm 的 **model-cost 注册表**估算美元。
- **unit 作用域累积**：worker 每跑一个 unit，通过 **contextvar** 建 `CostRecorder`；middleware 把用量写进当前 recorder；`run_unit` 结束后 worker 读 `recorder.total()` 覆写 `result.cost_json`。**策略代码完全不改**。
- `FakeGraphAdapter` / 非 LLM unit：recorder 空 → `cost_json=None`，不影响。

**`cost_json` 结构（每 unit）：**
```json
{"items":[{"model":"deepseek-chat","prompt_tokens":1234,"completion_tokens":567,"estimated_cost_usd":0.0023}],
 "total_usd":0.0023}
```
（一个 unit 可能多次调用 → items 聚合；多模型则多 item。）

### 5.2 聚合（Repository，纯 Python）
- `Repository.job_cost(job_id)`：扫该 job 所有 SUCCEEDED unit 的 `cost_json`，按 **step** 与 **model** 双维度求和 → `{total_usd, by_step:{name:usd}, by_model:{model:{tokens,usd}}}`。
- `Repository.kb_cost(kb_id)`：跨该 KB 所有 job 汇总，附 per-job 明细。
- 解析 JSON 文本 + 求和；unit 量不大，不上 SQL json 函数。

### 5.3 API
```
GET /kbs/{id}/jobs/{jid}/cost   → JobCostOut
GET /kbs/{id}/cost              → KbCostOut（累计 + per-job）
```
新增 Pydantic `JobCostOut` / `KbCostOut` / `CostItem`。

### 5.4 前端 CostPanel（纯 CSS）
- **JobDetailPage** 新增「Cost」卡：总额 USD + 每个 step 一条横向 CSS 条（宽 ∝ 该 step 成本）。
- **KbDetailPage** 顶部展示累计成本。
- 走专用 `/cost` 端点（不塞进 `JobOut`，保持 job 详情轻量）；job 详情页轮询时顺带 fetch。

### 5.5 边界与降级
- `cost_json=None`（历史 unit / 非 LLM unit）→ 计 0，可选标记「未计价」。
- 未知 model 不在 cost 注册表 → `estimated_cost_usd=null`，不崩；UI 显示 `n/a`。
- 历史 KB 无成本数据 → 成本卡显示 `—`，引导「重跑可采集」。

## 6. Wave 3 — 富交互 UX + 横切 J

### 6.1 C — 文档管理 UX
**后端：**
- `POST /kbs/{id}/documents/upload`（multipart，支持多文件）：经 markitdown 抽取纯文本 → 落 `Document`（title=文件名、`source_uri`、`content_hash`、`bytes`、`text`）。文件大小上限默认 25MB（可配）。
- 新缝 `GraphAdapter.read_document(data: bytes, filename: str) -> str`，底层 markitdown（`.txt`/`.md` 直读，其余走 markitdown）。`FakeGraphAdapter` 实现最小版（按扩展名返回固定文本）。
- `DELETE /kbs/{id}/documents/{doc_id}`：删 Document（CASCADE 清 Chunk）→ 204；**图不回缩**，响应可带 `warning:"graph_not_shrunk"`。
- `DocumentOut` 扩展 `bytes`、`chunk_count`。

**前端 DocumentManager：** 文档列表（title / 人类可读 bytes / chunk 数 / status / 删除）+ 文件选择/拖拽上传 + 文本粘贴 + 删除二次确认（「图不回缩」）+ loading/disabled/error 态。

### 6.2 D — 导出
- `GET /kbs/{id}/export?format=zip|graphml`：
  - `zip`：`StreamingResponse` 打包 data_root 产物（entities/relationships/communities/community_reports/text_units parquet + graphml）。
  - `graphml`：仅返回 GraphML XML（`application/graphml+xml`），供 Gephi 等外部工具。
- **GraphML 自写**：`write_graphml(entities_df, relationships_df) -> str`，手写 XML（节点=id/title/type/degree/community，边=source/target/weight/description），不引 networkx。测试里回解析校验 XML 转义。
- 前端：KB 详情页「导出 zip」按钮 + 「下载 GraphML」链接。

### 6.3 B — 图可视化
**后端 `GET /kbs/{id}/graph?limit=N&q=&hop=1|2`：**
- 复用 `graphrag_engine._norm_*` 规范化帧（与查询引擎同源，零新耦合）。
- 无 `q`：按 degree 取 Top-N 实体 + 其间关系 + 每节点 community id（着色）。N 默认 200、硬上限 500。
- 有 `q`：标题子串匹配（大小写不敏感）命中实体 → BFS 取 hop-1/2 邻域。
- 响应：`{nodes:[{id,title,type,degree,community}], edges:[{source,target,weight,description}]}`。

**前端 GraphView（react-force-graph-2d）：** 拉取 `/graph` → 力导向布局；节点按 community 着色；悬停 tooltip（title/type/degree/description）；搜索框带 `q`+`hop` 重取并高亮聚焦节点；命中上限时提示「仅显示前 N，请搜索聚焦」。

### 6.4 J — Playwright E2E（最小集）
- 新 `tests/e2e/` + Playwright 配置；dev-server fixture 起 FastAPI（注入 `FakeGraphAdapter`）+ 由 FastAPI 服务的构建版 SPA。**全程不依赖真实 LLM**。
- happy path：建 KB → 传 `.txt` → 触发 full job → 轮询至 SUCCEEDED → 查询得到答案。（可选：看图、导出。）
- 独立 `poe test_e2e`（浏览器按需 `playwright install`），不进默认 `poe test`。
- `FakeGraphAdapter` 需补齐新缝（`read_document` 等）。

## 7. 测试策略

沿用现有分层，CI 默认不碰真实 LLM：
- **单测**：`CostRecorder`、`write_graphml`、delta 策略 `next_units_batch` 过滤、`read_document` 路由。
- **API 测**（TestClient）：`/health`、`/cost`、`/export`、`/graph`、上传/删除、`JobCreate.type` 422。
- **集成测**（真组件 + `FakeGraphAdapter`）：增量 delta 作用域、`structured_output=false` 解析（MockLLM）、成本聚合。
- **前端测**（vitest + RTL）：CostPanel / DocumentManager / GraphView 渲染 + 交互。
- **E2E**（Playwright，独立目标）：happy path 全栈。

## 8. 风险与对策

1. **策略注入重构（I）影响面大** — 放 Wave 1 最前、先于 H；全套现有测试须全绿才继续。
2. **SQLite FK CASCADE 迁移** — `Chunk.document_id` 改 `ON DELETE CASCADE` 需 rebuild 表；Alembic 迁移单测覆盖；`PRAGMA foreign_keys=ON` 已就位。
3. **cost contextvar 跨 unit 串扰** — `run_unit` 外层 try/finally 每单位重置 contextvar。
4. **markitdown 依赖/抽取失败** — 懒加载；单文件 try/except → `Document.status="failed"`，proceed-on-failure 不阻断。
5. **未知 model 不在 cost 注册表** — `estimated_cost_usd=null`，不崩；UI `n/a`。
6. **大图 /graph 性能** — N 硬上限 + 有界 BFS；命中上限给提示。
7. **GraphML XML 转义** — 测试里回解析校验特殊字符。
8. **Playwright 浏览器体积** — 独立可选目标，不进默认 `poe test`。

## 9. 验收标准（Done）

- 现有全部测试仍绿；每项新增对应测试。
- `uv run poe check`（ruff + pyright）干净；`web` lint + build 干净。
- FakeGraphAdapter 下 full + 增量管线产出图；MockLLM 下成本被采集并聚合。
- 真实 LLM 手验：DeepSeek + `structured_output=false` → community_reports 非空；增量加 1 文档 → summarize/reports unit 数远小于全量。
- 每个 PR 附 semversioner change 文件。

## 10. 发布形态

- 按 Wave 拆 3 个 PR（Wave 1 / 2 / 3），J 随 Wave 3 或独立；合并为一次 **minor** 发版（新增成本可视化、图可视化、导出、文件上传等用户可见能力）。
- Phase 4 是设计文档的**收尾阶段**——完成后 Phase 1–4 全部交付，项目设计目标闭环。

## 11. 推进顺序

1. **Wave 1**（E → F → I → G → H）：正确性/运维加固，小改、低风险、解锁真实可用。
2. **Wave 2**（A）：成本可观测，依赖策略层稳定。
3. **Wave 3**（C → D → B）：富交互 UX，新前端依赖隔离在最后。
4. **横切**（J）：Playwright E2E 覆盖前三波关键路径。
