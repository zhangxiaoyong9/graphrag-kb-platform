# A1 — WebSocket 实时进度推送 — 设计文档

- 日期: 2026-06-28
- 状态: 已批准(待评审)
- 归属: A1（补齐设计承诺 · 可观测性）
- 后续: A2(`update_clean_state` 增量收尾) 另起 spec
- 依赖: 现有 `useJobPolling`(REST 轮询)、`Repository.unit_counts_by_status`、`Repository.get_steps`

## 1. 背景与目标

主设计文档(`2026-06-24-kb-platform-design.md` §8 可观测性 / Phase 2)承诺了 **WebSocket
实时状态推送**,但目前代码里完全没有:前端 `JobDetailPage` 靠 `useJobPolling` 定时拉
REST。本 spec 补齐这一缺口——把 step 时间线 + unit 状态计数的刷新从"浏览器轮询"升级为
"服务端事件推送",让 job 详情页体感"实时"。

**核心约束**:worker 是独立进程,只写 SQLite;server 只读 SQLite,二者无直接通道。因此
"实时性"由 **server 端轮询 DB + diff + WS 推送** 实现,worker 零改动,不引入 Redis /
额外 IPC,契合"SQLite 既是状态库又是队列、单机 asyncio 够用"的现有架构哲学。

**成功标准**:
- 打开 job 详情页,step/unit 状态变化在 ~1s 内反映到 UI,无需手动刷新。
- WS 全程不可用时,页面自动回退到 REST 轮询,功能与数据不丢(WS 是增强,不是依赖)。
- worker 代码零改动;poller 单周期异常不会让推送永久停摆。

## 2. 范围(YAGNI)

**做**:job 详情页订阅 `ws://host/jobs/{job_id}/events`,推送 step 状态 + 各 step 的 unit
状态计数(snapshot + delta)。

**不做**:
- 全局事件流 / 多 job 聚合推送(只按单个 job)。
- unit 逐行内容推送(只推 step 级 + `unit_counts`;unit 表的明细行仍由 REST 分页拉取)。
- 跨 server 实例 / 分布式(单机单 server)。
- 替换 overview / KB 列表等其它页面的 REST 调用(它们继续走 REST)。

## 3. 后端设计

### 3.1 新模块 `kb_platform/api/realtime.py`

集中所有实时逻辑(WS 端点 + 广播 + 轮询),不污染现有 routes。

**`RealtimeHub`** —— 顶层协调者,持有:
- `repo: Repository`(查 DB)
- `interval: float`(轮询周期,默认来自 `KB_POLL_INTERVAL_MS`,见 §5)
- `_broadcasters: dict[int, JobBroadcaster]`(job_id → 广播器)
- `_task: asyncio.Task | None`(轮询协程)

方法:
- `start()` / `stop()`:启动 / 取消轮询协程(由 FastAPI `lifespan` 调用)。
- `subscribe(job_id, ws) -> dict`:把 `ws` 注册到该 job 的 broadcaster 并**立即返回当前
  snapshot**(不等下一周期);无 broadcaster 则惰性创建。
- `unsubscribe(job_id, ws)`:注销;订阅者清零后从 `_broadcasters` 移除。
- `_poll_loop()`:全局单循环,每周期遍历 `_broadcasters` 的 job_id,对每个查 step 列表 +
  各 step 的 `unit_counts_by_status`,与 broadcaster 内存里的上一帧 diff,有变化才
  `_broadcast`。

**`JobBroadcaster`** —— 单个 job 的订阅者管理:
- `subscribers: set[WebSocket]`
- `last_frame: dict | None`(上一帧 step 状态快照,diff 基准)
- `snapshot() -> dict`:读当前真值,产出 `{"type":"snapshot", "job":..., "steps":[...]}`
  (更新 `last_frame`)。
- `diff_and_emit() -> dict | None`:读当前真值,与 `last_frame` 比对;无变化返回 None,有
  变化则更新 `last_frame` 并返回 `{"type":"delta", "job":..., "steps":[变化项]}`(只含
  变化字段)。**每次读 DB 真值,diff 累计——不会漏中间状态**(最坏:首周期把累积变化一次
  推齐)。
- `broadcast(event)`:并发 `send_json` 给所有订阅者;单个抛错 → 静默从 `subscribers`
  注销(不让一个坏连接影响他人)。

**为何全局单 poller 而非每 job 一 task**:活跃 job 很少(个人 / 小团队),单循环更省、更
可控,O(有订阅者的 job 数) 查询/周期。

### 3.2 WS 端点

新增 `routes_realtime.py`,注册到 `app.py`:

```
@app.websocket("/jobs/{job_id}/events")
async def job_events(websocket, job_id):
    await websocket.accept()
    hub = websocket.app.state.realtime
    await websocket.send_json(hub.subscribe(job_id, websocket))   # 立即 snapshot
    try:
        while True:
            await websocket.receive_text()   # 保活;忽略客户端文本
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(job_id, websocket)
```

- 客户端只需保持连接、接收服务端推送;`receive_text` 仅用于感知断开。
- 已终态 job:subscribe 仍发当前 snapshot(snapshot 里 `job.status` 已是
  succeeded/failed),客户端据终态自行关闭;服务端不主动踢。

### 3.3 事件 schema

事件形状对齐前端 `types.ts` 的 `Step` + 计数字段,减少 UI 改造:

```jsonc
// 连接即发(全量)
{ "type": "snapshot",
  "job":  { "status": "running" },
  "steps": [ { "id", "name", "ordinal", "kind", "status",
               "unit_counts": {"pending","running","succeeded","failed","total"} } ] }
// 之后增量(仅变化字段)
{ "type": "delta", "job": {"status":"succeeded"},
  "steps": [ {"id","status","unit_counts"} ] }
```

`unit_counts` 直接取 `Repository.unit_counts_by_status(step_id)`。

### 3.4 可靠性 / 错误处理

- **客户端断开**:从 broadcaster 注销;前端自动回退 REST 轮询,重连后重发 snapshot。
- **poller 单周期异常**:**逐 job** 捕获 + 记日志,单个 job 查询失败不影响其他 job;
  整轮级别异常也被吞,轮询任务不得死。
- **broadcast 单连接失败**:捕获 → 注销该 ws,不影响其他订阅者。
- **job 终态**:poller 把终态作为一次 diff 推出 delta(含 `job.status`);客户端收到
  terminal 后关连接;broadcaster 在订阅者清零后由 registry 清理。
- **server 重启**:`lifespan` startup 重建 hub + 重启 poller;客户端重连。

### 3.5 lifespan 接线(`api/app.py`)

`create_app` 现无 lifespan。新增:`app = FastAPI(lifespan=...)`,其中:

```python
@asynccontextmanager
async def lifespan(app):
    hub = RealtimeHub(repo=app.state.repo, interval=poll_interval)
    app.state.realtime = hub
    await hub.start()
    try:
        yield
    finally:
        await hub.stop()
```

WS 端点经 `websocket.app.state.realtime` 取 hub。poll_interval 从 `KB_POLL_INTERVAL_MS`
读取一次。e2e fake server(`scripts/e2e_server.py`)同样获得 hub(无需特殊配置)。

## 4. 前端设计

### 4.1 新 hook `web/src/hooks/useJobEvents.ts`

- 参数:`jobId`。WS URL 由当前 `location` 推导(`http→ws` / `https→wss`,同源)。
- 收 `snapshot` → 置为初始完整数据;收 `delta` → `job.status` 覆盖,`steps` 按 `id`
  merge。
- 返回 `{ connected: boolean; data: SnapshotData | null }`。
- `job.status` 进入 terminal(succeeded/failed/cancelled) → 主动 `close()`。
- 组件卸载 → `close()`。
- 重连:指数退避或固定间隔重试(避免风暴);断连期间 `connected=false`。

### 4.2 `JobDetailPage` 改造(实时优先 / 轮询兜底,双保险)

- **始终保留**现有 `useJobPolling`(REST,低频如 3s)作兜底数据源 —— 保证 WS 全挂时页面不
  卡死、不丢数据。
- **叠加** `useJobEvents`:`connected && data` 为真时,用 WS 的实时帧覆盖展示;WS 断开
  自然回落到 REST 帧。
- `StepTimeline` / `UnitTable` 感知不到数据源差异(形状一致)。
- 可在标题栏用一个小指示(在线/离线)反映 WS 连接状态(可选,非必须)。

### 4.3 vite dev 代理(`web/vite.config.ts`)

现有代理是字符串形式(`"/jobs": "http://localhost:8000"`),不支持 WS。改为对象形式并开
`ws`:

```ts
"/jobs": { target: "http://localhost:8000", ws: true },
```

生产环境 SPA 由 server 同源托管,WS 直连,无需改动。

## 5. 配置项

- `KB_POLL_INTERVAL_MS`(默认 500):poller 轮询周期(毫秒)。server 启动时读取一次。
- 无新增 DB 字段、无新增 alembic 迁移。

## 6. 测试策略

后端(`tests/test_realtime.py` 等):
1. **`JobBroadcaster`**:`snapshot()` 产出全量且更新 `last_frame`;`diff_and_emit()` 两帧无
   变化返回 None、有变化只返变化项;`broadcast()` 并发推送、单连接失败被注销。
2. **`RealtimeHub` / poller**:`subscribe` 立即返回 snapshot;`_poll_loop` 周期异常被吞且
   继续;`start/stop` 生命周期;订阅者清零后 broadcaster 被清理。
3. **WS 端点集成**:`TestClient.websocket_connect("/jobs/{id}/events")` 连一个有活跃
   FakeGraphAdapter job 的 server(`create_app(repo, query_engine=FakeQueryEngine())` +
   后台 worker),改 DB 状态(如推进一个 unit),断言收到 snapshot + 后续 delta。
4. **终态**:连一个已 succeeded 的 job,断言收到 snapshot 且 `job.status=succeeded`。

前端(`web/src/hooks/useJobEvents.test.ts` 等):
5. mock `WebSocket`(vitest):snapshot/delta 正确合并;断线 `connected=false`;terminal 主动
   close;卸载 close。
6. `JobDetailPage.test.tsx`:WS 在线时用 WS 数据;WS 断开回退轮询(MSW 模拟 REST)。

E2E:**跳过**。fake server 的 FakeGraphAdapter job 跑太快,难以捕捉中间态;实时性由前端单
测 + 后端集成测覆盖,性价比不足。

## 7. 实现顺序(供 writing-plans 参考)

1. 后端 `realtime.py`(`JobBroadcaster` / `RealtimeHub`)+ 单测(1–2)。
2. `routes_realtime.py` WS 端点 + `app.py` lifespan 接线 + 注册 + 集成测(3–4)。
3. 前端 `useJobEvents.ts` + 单测(5)。
4. `JobDetailPage` 接线(实时优先/轮询兜底)+ 单测(6);`vite.config.ts` ws 代理。
5. 真实 LLM 冒烟(可选):用现有 verify 流程跑一个 job,确认 job 详情页实时刷新。
