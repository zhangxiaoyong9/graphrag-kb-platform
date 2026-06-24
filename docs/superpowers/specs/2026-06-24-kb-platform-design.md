# GraphRAG 知识库管理平台 — 设计文档

- 日期: 2026-06-24
- 状态: 已批准(待评审)
- 依赖: Microsoft GraphRAG `graphrag==3.1.*`

## 1. 背景与目标

基于 Microsoft GraphRAG 构建一个**知识库管理平台**,在 graphrag 的图谱索引与查询能力之上,补足它缺失的一层:

1. **任务/步骤级追踪** — 索引或更新任务的每一步可观测(状态、耗时、产出量、成本)。
2. **单元级追踪 + 手动重试** — 昂贵的 LLM 步骤拆到 chunk/社区单元粒度,每个单元独立追踪,失败可单独勾选重试。
3. **可靠增量索引** — 新增文档时在已有图上建立关系,**不重新解析旧文档**的文本。
4. **带失败前进** — 容忍一定比例单元失败,任务可继续推进。

GraphRAG 自身只提供 workflow 级回调 + LLM 缓存(无单元级追踪/重试),增量能力存在但为整段 CLI run、不细粒度。本平台正是"可观测 + 可重试 + 可增量调度"这一层。

## 2. 已确认需求

| 维度 | 决策 |
|------|------|
| 形态 | 独立新项目;`graphrag` 作库依赖,锁 `graphrag==3.1.*` |
| 规模 | 个人/小团队;SQLite(WAL)+ 轻量异步队列 + FastAPI |
| 前端 | React + TypeScript + Vite SPA |
| 追踪粒度 | LLM 步骤按 chunk/单元追踪且可单独重试;非 LLM 步骤(分块/聚类)整步原子 |
| 增量 | 复用 graphrag `update_*` 合并;旧 chunk 文本永不重跑 LLM |
| LLM 提供方 | 透传 graphrag(litellm)的 settings 配置,按知识库设定;默认 OpenAI/Azure |

## 3. 关键决策:平台自驱动单元循环(方案②)

不让 graphrag 跑高层 `build_index`,而由平台按步驱动:

- **廉价强耦合步骤** → 直接调 graphrag 操作/工作流,记录步级状态。
- **昂贵可独立 LLM 步骤** → 由任务队列**逐单元循环**,调 graphrag 已有的**单单元原语**(如 `GraphExtractor.__call__(text, types, source_id)`),每单元落 SQLite 记录(状态/耗时/原始输出/错误/成本)。重试 = 重新入队该单元。
- **增量** → 调 graphrag 的 `_group_and_resolve_entities` / `_update_and_merge_relationships` 把 delta 合并进老图。

**理由**:精确重试单个 chunk 的诉求,只有把单元作为第一公民才能干净实现;图算法(抽取/聚类/合并/摘要)全部复用 graphrag 现成实现,平台只拥有"编排 + 追踪 + 重试 + 增量调度"这一层。代价(单元适配层)不可避免,且收益对等。

## 4. 整体架构

**控制面 / 数据面分离**:

- **控制面(SQLite,平台自有)** — job / step / unit / document / chunk / kb 配置,即追踪与调度元数据。
- **数据面(graphrag 原生)** — 实体 / 关系 / 社区 / 报告 / 向量,由 graphrag 的 `TableProvider`(parquet)+ vector store 读写;查询直接读这里。**平台不重写图存储。**

两个职责互不污染:平台管"做了什么、成败如何",graphrag 管"图本身"。

```
┌─────────────────────────── API 进程 (uvicorn + FastAPI) ─────────────────────────┐
│  KB/文档/配置 CRUD    触发索引/重试 unit/重试 step    查询转发(local/global/…)    │
│  静态托管 React SPA + WebSocket 进度推送                                          │
└────────────────────────────────────┬─────────────────────────────────────────────┘
                                      │ 读写
                          ┌───────────▼────────────┐
                          │   SQLite (WAL)  控制面  │  job/step/unit/document/chunk/kb
                          └───────────┬────────────┘
                                      │
┌──────────────────────── Worker 进程 (asyncio) ───────────────────────────────────┐
│  Orchestrator  按步驱动 job                                                        │
│   ├─ StepRunner:atomic 步 → 调 graphrag 操作(chunk/cluster/finalize)              │
│   ├─ UnitFanout:LLM 步 → 为每个 chunk/社区生成 unit 记录                          │
│   └─ UnitWorker:并发池拉取 pending unit → 调 graphrag 单单元原语 → 回写            │
│  IncrementalPlanner:新文档 → 规划 delta 步骤/受影响 unit                          │
│  graphrag Adapter:封装 extract_graph / summarize / report / cluster / update_*    │
└────────────────────────────────────┬─────────────────────────────────────────────┘
                                      │ 读写
                          ┌───────────▼────────────┐
                          │  数据面  parquet + 向量库 │  graphrag 原生产物
                          └────────────────────────┘
```

- **SQLite 既是状态库又是任务队列**(job/step/unit 自带状态机,worker 轮询 `pending` 与超时 `running`),无需 Redis。
- API 与 Worker 共享同一 SQLite(WAL 支持并发读 + 单写,小规模够用)。
- **graphrag Adapter 是唯一耦合 graphrag 内部的模块**;graphrag 升级只动这一层,其余模块面向平台自身抽象。

## 5. 控制面数据模型(SQLite)

只追踪"做了什么/成败如何/怎么重试";图数据留在 graphrag 的 parquet + 向量库,不进 SQLite。

```
knowledge_base (kb)
  id, name, method(standard/fast), settings_json(graphrag settings.yaml 内容),
  data_root(parquet/向量库路径), created_at

document
  id, kb_id, title, source_uri, content_hash, status(uploaded/parsed),
  bytes, created_at

chunk                        # 分块后的 text_unit,抽图的最小单元
  id, kb_id, document_id, ordinal, text, content_hash, token_count, created_at

job                          # 一次索引/增量运行
  id, kb_id, type(full | incremental), method, status, started_at,
  ended_at, stats_json, parent_job_id(增量指向被更新的 job)

step                         # job 内的有序步骤
  id, job_id, name, ordinal,
  kind(atomic | unit_fanout), status, started_at, ended_at,
  attempt_no, error_json

unit                         # unit_fanout 步骤里的可重试单元
  id, step_id, kind(extract_graph | summarize_descriptions |
                     community_report | embed),
  subject_type(chunk | entity | community), subject_id,
  status, attempt_no, started_at, ended_at, worker_id, heartbeat_at,
  input_hash,                # 判定是否命中 graphrag LLM 缓存
  cost_json(tokens/$), error_json, llm_raw_output, needs_reconsolidation(bool), created_at
```

**状态机:**

- `job`: `pending → running → succeeded | failed | cancelled`
- `step` (atomic): `pending → running → succeeded | failed`
- `step` (unit_fanout): `pending → running → succeeded | partially_failed | failed`
- `unit`: `pending → running → succeeded | failed`;**重试** = 新 attempt(`attempt_no+1`)回到 `pending`,保留历史错误

**带失败前进**:每个 unit_fanout 步配 `min_unit_success_ratio`(默认 `1.0`=严格;设为如 `0.95` 即容忍最多 5% 单元失败)。结算:全部成功 → `succeeded`;失败比例在容忍内 → `partially_failed` **且 job 继续推进**;超过容忍 → `failed` 阻塞。晚到成功的单元(在后续步骤跑完后才重试成功)标 `needs_reconsolidation=true`,其产物 append 进 parquet 但不在已算好的社区/报告/向量中,由"重新整合"动作吸收(见 §7)。

## 6. 执行引擎

**步骤序列(full 索引,镜像 graphrag Standard):**

```
1 load_input_documents        atomic      → 登记 document 行
2 create_base_text_units      atomic      → 切块,登记 chunk 行
3 create_final_documents      atomic
4 extract_graph               unit_fanout → 每个 chunk 一个 unit(GraphExtractor)
5 summarize_descriptions      unit_fanout → 每个同名实体一个 unit(合并多 chunk 描述)
6 finalize_graph              atomic      → 算度数,可出 graphml
7 create_communities          atomic      → Leiden 聚类
8 create_community_reports    unit_fanout → 每个社区一个 unit(自底向上按 level)
9 generate_text_embeddings    unit_fanout → 每个 item 一个 unit(执行时合并成批)
```

> graphrag 原本合一的 `extract_graph` 在此拆成 4、5 两步,因 subject 不同(chunk vs 实体),分开追踪/重试更干净。

**Atomic 步骤(1/2/3/6/7):** Orchestrator 直接调 graphrag Adapter 对应操作(如 `cluster_relationships`),单事务跑完。成功→`succeeded`,失败→`failed`(重试=整步重跑)。

**unit_fanout 步骤的执行循环(4/5/8/9):**

```
StepRunner 启动该步 → 为每个 subject 生成 unit 记录(status=pending) → step=running
UnitWorker 并发池(Semaphore = concurrent_requests)循环:
  1. 原子申领一批 pending unit(UPDATE … SET status=running, worker_id, heartbeat_at)
  2. 调 graphrag Adapter 单单元原语(命中 LLM 缓存则零 token)
  3. 成功 → 回写 parquet 产物 + unit(result/cost/raw_output, succeeded)
     失败 → unit(error, failed), attempt_no+1;attempt < max 自动重入队,否则留待手动
  4. 该步无 pending unit 时结算:
        全成功 → step succeeded;失败比例在容忍内 → partially_failed(job 继续);超容忍 → failed
```

**以一个 chunk 的 `extract_graph` unit 为例:**

```
unit(pending, subject=chunk42)
  → worker 申领 → 读 chunk42.text
  → Adapter.extract_chunk(text, types, source_id=chunk42)
       = graphrag GraphExtractor.__call__(...)   # 单元原语,带 LLM 缓存
  → 解析出 entities/relationships
  → 写入 parquet(entities/relationships,以 source_id 关联)
  → unit(succeeded, llm_raw_output, cost_json)
```

**三级重试:**

- **重试 unit**:`POST /units/{id}/retry` → 重置为 `pending`(新 attempt),step 回 `running`/`partially_failed`,worker 自动拾取。只重发该 chunk 的 LLM 请求。
- **重试 step**:unit 步重置该步所有 failed unit;atomic 步重跑整步。
- **重试 job**:从第一个非 `succeeded` 的 step 续跑。

**崩溃恢复:** `running` 的 unit/step 带 `worker_id` + `heartbeat_at`;worker 重启时把心跳过期的 `running` unit 回退为 `pending`、过期的 `running` step 标 `failed` 待人工。SQLite 单写者模型让"申领"天然原子。

**graphrag Adapter(唯一耦合点,稳定接口):**

```
extract_chunk(chunk) -> (entities_df, relationships_df)
summarize_entity(entity_group) -> description
report_community(community_context) -> report
embed_items(items) -> vectors          # 内部批量,外部按 unit 记账
cluster_relationships(relationships_df) -> communities     # atomic
finalize_entities/relationships(...)                       # atomic,算度数
merge_delta(previous_index, delta) -> merged               # 增量
```

## 7. 增量索引(新增文档,不重解析旧文档)

**触发:** `POST /kbs/{kb}/documents`(上传新文档,选"增量更新")→ 生成 `type=incremental` 的 job,`parent_job_id` 指向最近一次成功的 full/incremental job。

**IncrementalPlanner 规划的 delta 步骤:**

```
1  load_update_documents      atomic      只解析"新增"文档
2  create_base_text_units     atomic      只切块新文档 → 登记新 chunk 行
3  extract_graph              unit_fanout 仅对新 chunk 生成 unit  ★旧 chunk 文本永不进 LLM
4  summarize_descriptions     unit_fanout 对"被新 chunk 触碰到的同名实体"生成 unit
5  finalize_graph             atomic      重算度数
6  merge_delta                atomic      graphrag 合并逻辑,把 delta 并入老图
7  create_communities         atomic      Leiden 重聚类(图已含新老边)
8  create_community_reports   unit_fanout 仅对"成员发生变化"的社区重生成报告
9  generate_text_embeddings   unit_fanout 仅对新实体 + 变更社区报告 + 新 chunk 向量化
10 update_clean_state         atomic      合并 context.json/stats.json
```

**步骤 3 只对新 chunk 跑 LLM** — 老文档抽取结果直接来自老索引 parquet,零重算,满足"不重新解析旧文档"。

**受影响范围最小化(省钱):**

- 步骤 4 `summarize_descriptions` 的 unit subject = 新 chunk 抽出的实体 ∩ 已存在的同名实体(只有这些描述需重合并);全新实体跳过。
- 步骤 8 `community_report` 的 unit subject = 聚类前后 membership 发生增减的社区;未变社区报告保留。
- 步骤 9 `embed` 仅对新增/变更 item。

**与"重新整合"统一:** "重新整合"动作 = 一次特殊 incremental job,把所有 `needs_reconsolidation` 的晚到单元当作"新增 delta"走同一条 4–9 流水线。增量机制同时服务"加文档"和"吸收迟到的重试单元",一套代码两个入口。

**delta 命名空间合并:** 沿用 graphrag 带时间戳的 delta 存储,合并后原子替换主索引 output,`previous` 保留为回滚点。

## 8. 错误处理 / 幂等 / 可观测性

**失败分级:**

- **unit 级**:LLM 超时/限流/拒答/JSON 解析失败 → `unit.failed`,记 `error_json`(类型/消息/原始响应)。先按 `max_attempts` + 退避自动重试;耗尽后留待手动重试。JSON 解析走 graphrag 的 `json-repair`,修不好的才判失败。
- **atomic step 级**:整步抛错 → `step.failed`,job 阻塞(带失败前进配置下不影响已成功步)。
- **job 级**:Orchestrator 捕获每步;不可恢复的 step 失败 → `job.failed` 并记录出错步。支持取消(`job.cancelled`),running unit 标 `cancelled`。

**幂等与一致性:**

- unit 以 `input_hash` 命中 graphrag LLM 缓存 → 重试/断点续传零额外 token、结果确定。
- 单元产物按 `source_id` 写 parquet;重跑同一单元 = 覆盖该 source 产物(去重),不产生脏副本。
- SQLite schema 用 Alembic 管迁移;WAL 模式跑并发读 + 单写。

**可观测性(正是"可追踪"诉求的落点):**

- Dashboard:KB 列表 → job 列表 → **step 时间线**(状态/耗时/产出量/cost)→ **unit 表**(按状态过滤、点开看 `llm_raw_output`/错误、行内重试按钮)。WebSocket 推送实时状态。
- cost/token 聚合 unit → step → job → KB(graphrag-llm 的 `model_cost_registry` 提供单价)。
- `partially_failed` 与 `needs_reconsolidation` 在 UI 醒目标记 + "重试失败单元"/"重新整合"入口。

## 9. 测试策略

- **控制面单测**(不依赖真 LLM,内存 fake Adapter):Orchestrator 步骤排序与结算、`IncrementalPlanner` 受影响范围计算、UnitWorker 申领/结算、重试/状态迁移、`带失败前进` 阈值判定。平台核心层,高覆盖。
- **Adapter 契约测试**:锁 `graphrag==3.1.*`,小 fixture 调真实 graphrag 原语(带缓存、便宜);graphrag 升级时第一时间发现 breaking change。
- **端到端集成**:小语料 full 索引 → 断言 parquet 产物齐全、所有 unit 成功;**增量加文档 → 断言 `extract_graph` 的 unit.subject 只含新 chunk id(直接验证"不重解析旧文档"核心承诺)**;注入强制失败 → 断言 `partially_failed` + 重试后恢复。
- **DB 层**:Alembic 迁移、WAL 并发冒烟。

## 10. 分阶段实现

(供 writing-plans 逐步落地;每个阶段可独立验证)

- **Phase 1(MVP,验证核心缝)**:控制面 schema(Alembic)+ Orchestrator/StepRunner/UnitWorker + atomic 步骤(load/chunk/finalize/cluster)+ `extract_graph` unit 步 + 手动重试(unit/step)+ 最小 Dashboard(job/step/unit 列表与重试)+ full 索引端到端跑通。验证"单元级追踪 + 单 chunk 重试"。
- **Phase 2**:补全 unit 步(`summarize_descriptions` / `community_reports` / `generate_text_embeddings`)+ 带失败前进 + WebSocket 实时进度。
- **Phase 3**:增量索引 + 重新整合 + 查询接入(local / global / drift / basic)。
- **Phase 4**:打磨 — cost 聚合可视化、graphml 可视化、文档管理 UX、导出。

## 11. 非目标(YAGNI)

- 多租户 / SaaS / 鉴权计费(当前个人/小团队)。
- 分布式 worker / 水平扩展(单机 asyncio 够用)。
- 文档删除导致的图收缩(暂不支持;后续若需可加,复用增量框架的反向操作)。
- 自定义非 graphrag 图算法。
- 重写 graphrag 存储 / 查询(直接复用)。
