# Phase 2b-1 — 后端服务 设计文档

- 日期: 2026-06-25
- 状态: 已批准(待评审)
- 依赖: Phase 1 + Phase 2a 已合并(`main`,41 tests green);`graphrag==3.1.*`
- 上游设计: `docs/superpowers/specs/2026-06-24-kb-platform-design.md`(总体 spec)

## 1. 背景与目标

Phase 1/2a 产出了一个可用集成测试闭环的**引擎**(单元级追踪 + 重试 + 完整图谱流水线),但全程用 `FakeGraphAdapter`、无 HTTP、无真实 LLM、无独立 worker。Phase 2b-1 把它升级为**一个可操作的后端服务**:

1. 接真实 graphrag 的 LLM/聚类/finalize(替换 Fake 版),让引擎能跑**真实**索引。
2. 引入**独立 worker 进程**(SQLite 当队列、轮询领取、`worker_id`/`heartbeat_at`、崩溃自动续跑)。
3. 提供 **REST API(FastAPI)** 做索引管理(建库/传文档/触发/状态/重试)。

不含查询、不含前端(分别 Phase 3 / Phase 2b-2)。

## 2. 范围

| 项 | 2b-1 是否含 |
|----|----------|
| 真实 `GraphRagAdapter`:`summarize_entity`/`report_community`/`cluster_relationships`(真 Leiden)/`finalize` | ✅ |
| 独立 worker 进程 + SQLite 队列 + 轮询领取 | ✅ |
| `worker_id`/`heartbeat_at` 列 + 崩溃**自动续跑** | ✅ |
| REST API(FastAPI):KB/文档/任务/状态/重试 | ✅ |
| LLM 配置(KB `settings_json` = graphrag settings.yaml)+ 文档接入(文件/文本) | ✅ |
| 查询(local/global/drift/basic)+ embeddings | ❌ Phase 3 |
| 增量索引 / 重新整合 | ❌ Phase 3 |
| React 仪表盘 / WebSocket 实时进度 | ❌ Phase 2b-2 |
| 鉴权 / 多租户 | ❌(个人/小团队,沿用 Phase 1 决策) |

## 3. 架构:API + Worker 双进程,SQLite 当队列

```
┌──────────── API 进程 (uvicorn + FastAPI) ────────────┐
│  KB CRUD / 文档上传 / POST jobs(PENDING) /           │
│  GET job·step·unit 状态 / POST retry                  │
│  不跑索引,只读写 SQLite                                │
└───────────────────────┬───────────────────────────────┘
                        │ 共享
              ┌─────────▼─────────┐
              │  SQLite (WAL) 控制面 │  ← job 表即队列
              └─────────┬─────────┘
                        │ 轮询领取
┌──────────── Worker 进程 (asyncio) ───────────────────┐
│  启动恢复 → 主循环:领一个 PENDING job → 原子置 RUNNING →│
│  用该 KB 的真实 GraphRagAdapter 跑 Orchestrator →      │
│  跑 unit 时盖 worker_id + 周期刷新 heartbeat_at         │
│  一次只跑一个 job(LLM 并发已在 unit 级 Semaphore)     │
└──────────────────────────────────────────────────────┘
```

**关键设计点:**
1. **job 表即队列**:API 插入 `status=PENDING` 的 job;worker 原子 `UPDATE ... SET status=RUNNING WHERE status=PENDING LIMIT 1` 领取(SQLite 单写者,天然无竞态)。无需 Redis。
2. **顺序执行**:worker 一次一个 job。LLM 并发已在 unit 级处理;job 级并发对个人/小团队是过度工程。
3. **崩溃恢复(自动续跑)**:worker 启动(及周期轻量)扫描 `status=RUNNING` 且 `heartbeat_at` 过期的 unit → 回退 `PENDING`;`status=RUNNING` 的 job → 回退 `PENDING` 让主循环重领。续跑幂等(见 §5)。
4. **查询不在 2b-1**:search 需 embeddings,留 Phase 3。2b-1 = 索引管理 API。

## 4. 真实 GraphRagAdapter LLM 方法(graphrag 接缝)

唯一耦合 graphrag 内部的模块(沿用 `kb_platform/graph/graphrag_adapter.py`)。四个方法接真实 graphrag,**签名/返回与 2a 的 `GraphAdapter` Protocol 完全一致**,因此 2a 引擎测试无需改动,只是底层从 Fake 换成真实。

| 方法 | 实现 | 类型 |
|------|------|------|
| `summarize_entity(name, descriptions)` | graphrag 描述摘要操作,按单实体调用(批量 op 退化到 1-item,或抽取其 per-entity LLM 调用) | async LLM |
| `report_community(context)` | graphrag `CommunityReportsExtractor`(按社区可调用:上下文 → 结构化报告) | async LLM |
| `cluster_relationships(rels_df)` | graphrag **真正 hierarchical Leiden**(`graphrag.graphs.hierarchical_leiden`),替换 2a fake 的连通分量,产出多层级 | 同步,确定性 |
| `finalize_entities_relationships(...)` | graphrag finalize 操作(算 degree / combined_degree) | 同步,确定性 |

- LLM 方法走 graphrag `create_completion`(经 KB 配置)+ 自带 **LLM 缓存**(续跑/重试零额外 token)。
- `build_default_adapter` 扩展:除 Phase 1 的 extract,再从 KB 配置构造 summarize/report 的 completion + 注入真 Leiden/finalize。
- **具体导入路径与单实体调用方式在 plan 阶段用 grep 核实**(同 Phase 1 Task 7 套路),本 spec 只定方向。

## 5. Worker 进程:轮询、领取、心跳、崩溃恢复

独立入口(`python -m kb_platform.worker`),与 uvicorn API 并行跑。

**启动恢复:**
```
扫描所有 status=RUNNING 且 heartbeat_at 过期的 unit → 回退 PENDING
扫描所有 status=RUNNING 的 job → 回退 PENDING(让主循环重新领取)
```
> 单 worker 场景:启动时所有 RUNNING 都是上次崩溃残留,直接重置即可。

**主循环(一次一个 job):**
```
while True:
    periodic_recovery()                 # 轻量;单 worker 下主要启动时生效
    job = claim_one_pending_job()       # 原子 UPDATE job SET status=RUNNING WHERE status=PENDING LIMIT 1
    if job is None: sleep(短); continue
    adapter = adapter_factory(kb)       # 默认:按 KB settings 建真实 adapter;测试注入 Fake
    await Orchestrator(repo, adapter, kb.data_root).run(job.id, kb.min_success_ratio)
```

**心跳:** `UnitWorker._process` 开始跑 unit 时 `set unit.worker_id=<pid>, heartbeat_at=now`;一个后台 asyncio task 每 N 秒刷新本 worker 正在跑的 unit 的 `heartbeat_at`(维护 in-process active-unit 集合)。

**续跑的幂等性(自动续跑为何安全):**
- Orchestrator 从首个非 `SUCCEEDED` 的 step 续跑;UnitWorker 只 claim `PENDING` unit、跳过 `SUCCEEDED`(2a 已实现);已成功 unit 产物在磁盘,合并结算照常计入。
- 重置的 unit 若输入未变 → 命中 graphrag LLM 缓存 → 零额外 token。

**进程管理:** supervisor / systemd / `uv run` 双进程拉起(API + Worker)。本计划只保证两条入口独立可跑;编排留部署文档。

## 6. REST API + LLM 配置 + 文档接入

**REST API(FastAPI,仅索引管理):**
```
KB
  POST   /kbs                         建 KB(name, method, settings_yaml, min_success_ratio)
  GET    /kbs | /kbs/{id}
文档
  POST   /kbs/{id}/documents          上传(multipart 文件 或 JSON 文本)→ 存 data_root/input + 建 document 行
  GET    /kbs/{id}/documents
任务
  POST   /kbs/{id}/jobs               触发索引(body: method)→ 建 PENDING job;立即返回 job id(202)
  GET    /jobs/{id}                   job 状态 + 各 step 摘要(状态/耗时/产出量/cost)
  GET    /jobs/{id}/steps
  GET    /steps/{id}/units?status=    unit 列表(可按状态过滤,含 llm_raw_output/error)
重试
  POST   /units/{id}/retry            重置该 unit → PENDING
  POST   /steps/{id}/retry            重置该步 failed units
```
> API 只读写 SQLite,不跑索引。`POST /jobs` 插入 PENDING job 即返回(202);worker 轮询领取。状态靠 GET 拉取(实时 WebSocket 推送留 Phase 2b-2)。

**LLM 配置:** KB 行 `settings_json` 存 graphrag `settings.yaml` 内容(model provider/model/encoding 等;API key 走环境变量,不入库)。worker 解析 → `ModelConfig` → `create_completion` → 构造真实 adapter。建 KB 时校验 settings 可解析。

**文档接入:** `POST /kbs/{id}/documents` 支持 (a) multipart 文件上传(txt/md/csv/json 等),(b) JSON 直接传文本。文件存 `data_root/input/`,用 graphrag input reader(graphrag-input)解析成 document 行。Phase 3 增量复用同一接入。

**依赖注入(可测):** worker 的 `adapter_factory`:生产默认"按 KB settings 建真实 adapter";**测试注入 `FakeGraphAdapter`**(不碰真 LLM)。API 层测试用 FastAPI TestClient + 内存 SQLite,完全不启 worker。

## 7. `Unit` 列迁移:`worker_id` + `heartbeat_at`

补齐 spec §5 在 2a 延后的两列(Alembic autogenerate):
| 列 | 用途 |
|----|------|
| `worker_id` (String, nullable) | 标识哪个 worker 进程在跑该 unit |
| `heartbeat_at` (DateTime, nullable) | 该 unit 最后心跳时间;恢复时据此判过期 |

`UnitWorker._process` 跑 unit 时盖这两列;后台 task 周期刷新 `heartbeat_at`。`test_migration.py` 断言新列存在。

## 8. 测试策略

- **API 层测试**(FastAPI TestClient + 内存 SQLite,不启 worker、不碰 LLM):KB/文档/job CRUD、`POST /jobs` 建 PENDING job、GET 状态/step/unit、retry 端点重置状态。
- **Worker 机制测试**(注入 `FakeGraphAdapter`,无真 LLM):轮询领取(PENDING→RUNNING→SUCCEEDED)、心跳盖戳、**崩溃恢复**(预置过期 RUNNING unit + RUNNING job → 启动恢复重置 → 续跑完成)。
- **真实 adapter 契约测试**(graphrag MockLLM,零成本):`summarize_entity` + `report_community` + `cluster_relationships`(小图真 Leiden)+ `finalize`。
- **后端服务 E2E**(API + 进程内 worker):建 KB → 传文档 → POST job → worker 跑(FakeGraphAdapter 或 MockLLM 真实 adapter)→ GET job SUCCEEDED + units 齐全。整条后端闭环,无真实 LLM 开销。
- **2a 回归**:现有 41 个测试全部仍通过。

## 9. 非目标 / 延后项

- 查询 + embeddings(Phase 3)。
- 增量索引 / 重新整合(Phase 3)。
- React 仪表盘 / WebSocket 实时进度(Phase 2b-2)。
- 鉴权 / 多租户 / 多 worker 并发(当前个人/小团队单 worker)。
- job 级并发(worker 一次一个 job)。
