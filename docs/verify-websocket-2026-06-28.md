# WebSocket 实时进度推送 — 验证记录

- 日期: 2026-06-28
- 分支: feat/websocket-realtime
- 规格: `docs/superpowers/specs/2026-06-28-websocket-realtime-progress-design.md`
- 计划: `docs/superpowers/plans/2026-06-28-websocket-realtime-progress.md`
- Base (main): `2ec0b5d` → HEAD

## 功能

Job 详情页从"浏览器 REST 轮询"升级为"服务端 WebSocket 推送",step 状态 + 各 step 的
unit 进度实时更新;WS 不可用时自动回退轮询,worker 零改动,无新依赖/迁移。

- 后端:`kb_platform/api/realtime.py`(`RealtimeHub` 全局单 poller + `JobBroadcaster`
  按 job diff 推送)、`kb_platform/api/routes_realtime.py`(`ws /jobs/{id}/events`)、
  `kb_platform/api/app.py`(新增 `lifespan` 启停 hub)。
- 前端:`web/src/hooks/useJobEvents.ts`(WS 订阅,断线重连 + terminal 关闭;无 WebSocket
  支持时静默降级)、`JobDetailPage`(实时优先 / 轮询兜底 + "实时"标记)、
  `web/vite.config.ts`(`/jobs` 代理开 `ws: true`)。
- 配置:`KB_POLL_INTERVAL_MS`(默认 500ms),lifespan 启动时读取。

## 自动化验证

### 后端(协议级端到端)

- `tests/test_realtime.py`(7):broadcaster snapshot/diff/broadcast、hub
  subscribe/unsubscribe、poll 循环推送 delta、广播器异常不致死。
- `tests/test_api_realtime.py`(2):**这是 WS 推送的端到端协议级证明** ——
  `with TestClient(app)` 触发 lifespan 启动真实 poller(20ms 周期),WS 连接后:
  (1) 立即收到 `snapshot`(job 状态 pending);(2) 在 DB 改一个 step 状态后,
  `receive_json()` 收到 `delta` 且含该 step;(3) 已 succeeded 的 job 连接收到
  `snapshot.status=succeeded`。
- 全量后端:`uv run pytest -q` → **235 passed**(1 条 httpx 弃用警告,预先存在,非本次引入)。
- Lint:`uv run ruff check .` → **All checks passed!**

### 前端

- `useJobEvents.test.tsx`(3):snapshot 置数、delta 合并 step、terminal 关 socket;
  断线 `connected=false`;null jobId 无副作用。
- `JobDetailPage.test.tsx`(2,既有,作回退回归守卫):在无 WebSocket 的 jsdom 环境下,
  页面仍从 REST 轮询渲染 —— 证明"WS 离线 → REST 兜底"成立。
- 全量前端:`npx vitest run` → **69 passed**;`npm run build`(`tsc -b && vite build`)→
  成功,1114 modules,`index.js` 476.70 kB(gzip 150 kB)。

## 手动 UX 冒烟(需人工执行)

自动化已证明协议正确;以下为"浏览器肉眼实时刷新"的人工验收(在真实 server+worker 上):

```bash
# 1. 构建 SPA(已构建亦可)
cd web && npm run build && cd ..
# 2. 两进程
uv run python -m kb_platform.server kb.db . 127.0.0.1 8000
uv run python -m kb_platform.worker kb.db
```

打开 `http://127.0.0.1:8000`,建 KB(选 LLM provider profile)→ 加文档 → 触发 full job →
进入**任务详情**页,确认:

- [ ] job 运行时标题旁出现"实时"标记。
- [ ] step 状态 / unit 进度无需手动刷新即随 worker 推进更新。
- [ ] 中途停掉 worker:"实时"标记消失(回退轮询),页面仍约每 2s 更新;重启 worker 后
      "实时"标记恢复。

> dev 模式(`cd web && npm run dev`,:5173)经 vite `/jobs` 代理(ws:true)转发到 :8000,
> 同样可用。生产同源直连,无需代理。

## 结论

后端 + 前端自动化全绿,WS 推送的协议级端到端行为由 `test_api_realtime.py` 覆盖。
剩余唯一人工项为浏览器肉眼实时刷新体验。
