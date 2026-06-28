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

## 浏览器冒烟(自动化 · Playwright)

设计 spec 原本以"fake job 跑太快、难以捕捉中间态"为由跳过 E2E;此处补一个**浏览器级**
冒烟 `web/e2e/realtime.smoke.spec.ts`,用 headless Chromium 驱动 e2e fake server
(`scripts/e2e_server.py`,FakeGraphAdapter,无 LLM/key),把单测证不了的"真实浏览器"三件事
证掉:开 WS 收 snapshot、收服务端推送 delta 且页面不刷新就更新、"实时"标记随 WS 连接状态
切换。运行:

```bash
cd web && npm run build
node_modules/.bin/playwright test e2e/realtime.smoke.spec.ts     # 单跑此冒烟
node_modules/.bin/playwright test                                # 全量 e2e
```

断言与证据(一次全量运行的 WS 帧日志):

- **A · snapshot** — 打开基线 job #1(已 succeeded):WS 连上,首帧 `type=snapshot`,
  标题旁"实时"标记可见。(终态 snapshot 不会关 socket —— 只有终态 *delta* 才会。)
- **B · 实时 delta(无刷新)** — `POST /kbs/1/jobs` 触发新 full job,进入其详情页,**不做任何
  reload**,DOM 状态经 WS 推送流转:`待处理 → 运行中 → 成功`;且至少收到 1 条 `delta`,
  其中含 `job.status=succeeded` 的终态帧(7 个 step 全 succeeded)。帧统计 `perJob={1:1, 2:4}`,
  `seenDomStatuses=[待处理,运行中,成功]`。
- **C · 断开 → 回退 → 重连** — 测试侧 `window.WebSocket` 埋点后强制 `close()`:connected 翻
  false →"实时"消失,页面回退到 `useJobPolling`(terminal 数据保留,页面不空);hook 1s 后
  重连 → snapshot →"实时"恢复。

截图:`docs/screenshots/realtime-2026-06-28/{a-snapshot,b-live-delta,c-fallback,d-reconnect}.png`
(a/b/d 可见"实时"标记,c 不可见)。

> 关于"停掉 worker →'实时'消失":此说法**不精确**。"实时"取决于浏览器↔server 的 WS 连接
> 状态(`live.connected`),而 `RealtimeHub` 跑在 server 进程内、与 worker 解耦;停掉 worker
> 只让 DB 不再变化,WS 并不断开,"实时"仍亮。真正能复现的回退路径是 **WS 断开 → 轮询兜底 →
> 重连恢复**,由 C 段强制断开 socket 验证。

全量 e2e:**11 passed**(含本冒烟),无回归。

## 结论

后端 + 前端 + 浏览器自动化全绿:WS 推送的协议级行为由 `test_api_realtime.py` 覆盖,真实浏览器
的 snapshot / 实时 delta 无刷新更新 / 断开回退重连由 `realtime.smoke.spec.ts` 覆盖。无遗留人工项。
