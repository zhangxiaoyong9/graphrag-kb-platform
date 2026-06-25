# Phase 2b-2 — 仪表盘 + API 收口 设计文档

- 日期: 2026-06-25
- 状态: 已批准(待评审)
- 依赖: Phase 1 + 2a + 2b-1 已合并(`main`,64 tests green)
- 上游设计: `docs/superpowers/specs/2026-06-24-kb-platform-design.md`(总体 spec)

## 1. 背景与目标

2b-1 产出了一个可 curl/Postman 操作的后端服务,但**没有界面**。Phase 2b-2 加一个 **React 仪表盘**,让你能:建库、传文档、触发索引、看每一步/每个单元的实时进度、对失败单元手动重试 —— 即把你最初要的"可观测 + 可重试"做成一个可视化控制台。同时顺手**收口 2b-1 的 API 遗留**(Pydantic 校验、统一响应、任务列表、进度字段),给前端稳定契约。

不含查询(Phase 3)、不含 WebSocket(本期用轮询)、不含增量。

## 2. 范围

| 项 | 2b-2 是否含 |
|----|----------|
| React + TS + Vite SPA(在 `web/`) | ✅ |
| FastAPI 托管构建产物(prod)+ Vite 代理(dev) | ✅ |
| 实时进度:**前端轮询** `GET /jobs/{id}`(~2s) | ✅ |
| 仪表盘三视图(KB 列表 / KB 详情 / 任务详情) | ✅ |
| 任务详情:步骤时间线 + 单元表 + 手动重试 | ✅ |
| Tailwind CSS 样式 | ✅ |
| API 收口:Pydantic 写模型(422)、统一响应模型、`GET /kbs/{id}/jobs`、`GET /jobs/{id}` 增 `progress` | ✅ |
| WebSocket / SSE 实时推送 | ❌(轮询足够;契约不变,后续可平滑升级) |
| 查询(local/global/drift/basic) | ❌ Phase 3 |
| 增量索引 | ❌ Phase 3 |
| 鉴权 / 多租户 | ❌ |
| Playwright E2E | ❌ 默认延后(组件测试 + 构建冒烟即可) |

## 3. 架构:React SPA + FastAPI 托管 + 轮询

```
浏览器 ──(dev: Vite 5173 代理 /api → FastAPI 8000;prod: FastAPI StaticFiles 托管 web/dist)
          │  GET /jobs/{id} 每 ~2s 轮询(job 详情页)
          ▼
FastAPI(2b-1 既有 JSON API + 收口:Pydantic/统一响应/任务列表/进度)
          │
          ▼
SQLite 控制面 ← Worker 进程(2b-1,不变)
```

**布局:** 前端放 `graphrag-kb-platform/web/`(React + TS + Vite),独立 `package.json`。
- **dev**:Vite dev server(5173)+ `vite.config.ts` 把 `/api` 代理到 FastAPI(8000);两个进程。
- **prod**:`npm run build` → `web/dist/`;FastAPI 用 `StaticFiles` 托管 SPA(`/` → `index.html`,带 history fallback);一个进程。
- **实时进度**:job 详情页 `setInterval(getJob, 2000)`;job 终态(succeeded/failed)或离开页面时停止。零后端事件基础设施。

## 4. API 收口(后端任务,前端依赖)

- **Pydantic 写模型**:`POST /kbs`、`/kbs/{id}/documents`、`/kbs/{id}/jobs` 用 `BaseModel` 请求体 → 缺字段/类型错自动 **422**(替代现在的 `dict` + 500)。
- **统一响应 schema**:每个资源定义 Pydantic 响应模型(`KbOut`/`DocumentOut`/`JobOut`/`StepOut`/`UnitOut`),前端有稳定契约;`response_model=XxxOut` 强制。
- **`GET /kbs/{id}/jobs`**:列某 KB 的任务(前端避免 N+1)。
- **`GET /jobs/{id}` 增 `progress`**:各状态 unit 计数 `{pending, running, succeeded, failed, total}`,前端进度条直接用(按 step 聚合)。

## 5. 前端视图与组件结构

三个主视图(React Router):
```
/                         KB 列表:KB 卡片(名称/方法/最近任务状态)+ "新建 KB"
/kbs/:id                  KB 详情:文档区(列表 + 上传)+ 任务区(列表 + "触发索引")
/kbs/:id/jobs/:jobId      任务详情:步骤时间线 + 单元表(核心可观测页)
```

**任务详情页(核心):**
- **步骤时间线**:6 步竖排,每步 `名称 / 状态徽标 / 进度条(succeeded/total)/ 耗时`;点开看该步 unit 概览。
- **单元表**:当前步 unit 列表,按状态过滤;每行 `subject_id / 状态 / 耗时 / cost`;点行展开 `llm_raw_output` / `error`;失败行 **重试**(`POST /units/{id}/retry`)。
- **整步重试**:`partially_failed` 步显示 **"重试失败单元"**(`POST /steps/{id}/retry`)。
- **轮询**:`useJobPolling` 进页启动、终态/卸载停止。

**代码结构(`web/src/`):**
```
api/         client.ts(类型化 fetch 封装)、types.ts(对应后端响应模型)
hooks/       useJobPolling.ts、useKbs.ts
pages/       KbListPage.tsx、KbDetailPage.tsx、JobDetailPage.tsx
components/  KbForm、DocumentUpload、StepTimeline、UnitTable、RetryButton、StatusBadge、ProgressBar
App.tsx + router + main.tsx
```
组件只调 hooks,不直接 fetch;`api/client.ts` 集中端点 + 类型。

**配置透传:** 建 KB 表单含 `name / method / settings_yaml(JSON 文本框)/ min_success_ratio`;文档上传支持拖拽文件 + 文本粘贴。

## 6. 样式

**Tailwind CSS**(utility-first,零独立 CSS 文件、构建快)。不上组件库(避免重依赖);手写少量组件即可构成运维控制台。

## 7. 测试策略

- **后端(API 收口)**:扩展 FastAPI TestClient 测试 —— 写模型 422、响应模型校验、`GET /kbs/{id}/jobs`、`progress` 字段。2b-1 的 64 测试回归通过。
- **前端**:Vitest + React Testing Library —— `UnitTable`(过滤、失败行重试触发 API)、`StepTimeline`(徽标 + 进度)、`useJobPolling`(启停/卸载清理);**msw** 拦截 API;`npm run build` 构建冒烟。
- **E2E(默认延后)**:Playwright 跑 API+worker(FakeGraphAdapter)全链路;后续加固。

## 8. 非目标 / 延后项

- WebSocket / SSE(轮询足够;契约不变可升级)。
- 查询 + embeddings(Phase 3)。
- 增量索引(Phase 3)。
- 鉴权 / 多租户 / 组件库。
- Playwright E2E(默认延后)。
- `community_reports` 在不支持结构化输出的模型(如 DeepSeek)上为空 —— Phase 3 查询/global search 才需要,届时用 json_schema-capable 模型。
