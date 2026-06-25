# SaaS 管理后台信息架构重构（侧边栏 + 顶层页面）

> 状态：已 brainstorm、已确认；实现中。
> 日期：2026-06-26。
> 范围：`graphrag-kb-platform/web`（仅前端）。不改后端、不改控制面/数据面、不回滚已有实现。

## 1. 背景

Phase 1–4 已落地（控制面 SQLite + asyncio worker + FastAPI + React SPA + 成本/图/导出/文档/查询）。功能基本覆盖 README，但侧边栏是扁平的（概览 / 知识库 / 任务管理 / 成本统计 / 系统状态），且文档/图谱/检索等重要能力被藏在 KB 详情页的 tab 里，整体不像完整的管理后台。

原始设计文档把「多租户 / SaaS / 鉴权计费」列为**非目标**——本次只做**表现层**的 SaaS 管理后台信息架构：同一套后端、无鉴权/计费，但导航与页面组织成完整的分组管理后台。每个重要功能既可从侧边栏顶层进入，也可从 KB 详情 tab 进入（KB 详情 tab 原样保留）。

## 2. 目标 / 非目标

**目标**
- 侧边栏按目标分组（工作台 / 知识库 / 检索与问答 / 分析与监控 / 系统管理）渲染。
- 新增 7 个顶层页面（文档管理、图谱管理、检索测试、问答对话、分析报表、系统设置、API Keys），复用现有 API 与组件，绝不伪造数据。
- 保留现有全部路由与 KB 详情 tab。

**非目标**
- 不改后端、不加新 API。
- 不引入鉴权 / 多租户 / 计费。
- 不删除/回滚任何已有页面或 tab。

## 3. 目标侧边栏 → 路由 → 页面

| 分组 | 菜单 | 路由 | 实现 |
|---|---|---|---|
| 工作台 | 概览 | `/` | `DashboardPage`（现状） |
| 知识库 | 知识库管理 | `/kbs` | `KbListPage`（现状） |
| 知识库 | 文档管理 | `/documents` | **新** `DocumentsCenterPage` |
| 知识库 | 图谱管理 | `/graph` | **新** `GraphCenterPage` |
| 检索与问答 | 检索测试 | `/query` | **新** `QueryTestPage` |
| 检索与问答 | 问答对话 | `/chat` | **新** `ChatPage` |
| 分析与监控 | 分析报表 | `/analytics` | **新** `AnalyticsPage` |
| 分析与监控 | 任务管理 | `/jobs` | `JobsPage`（现状） |
| 分析与监控 | 成本统计 | `/cost` | `CostPage`（现状） |
| 系统管理 | 系统状态 | `/system` | `SystemPage`（现状） |
| 系统管理 | 系统设置 | `/settings` | **新** `SettingsPage` |
| 系统管理 | API Keys | `/api-keys` | **新** `ApiKeysPage` |

底部保留 `/demo`（演示预览）+ 品牌/版本。`/kbs/:id/*` 全部 tab 不动。

**激活态**：`NavLink` 默认前缀匹配 + `end` 仅用于 `/`。`知识库管理`(`to=/kbs`) 在 KB 详情(`/kbs/:id/*`)保持激活。顶层新路由(`/documents` 等)与 `/kbs/:id/<同名>` 不冲突（路径前缀不同）。

## 4. 诚实数据策略（不改后端）

后端接口固定。spec 中若干项后端**无法提供**，一律「真实可获取 + 诚实空态」，绝不伪造：

| 能力 | 后端现状 | 前端处理 |
|---|---|---|
| 检索的引用/来源/实体片段 | `POST /kbs/:id/query` 仅返回 `{answer, method, error}` | 检索测试 & 问答对话只展示 **答案 + 方法 + 客户端实测耗时 + 错误**；引用区诚实说明「当前查询接口返回综合答案，未附带结构化引用」 |
| 查询耗时 | 接口不返回 | `performance.now()` 测客户端往返耗时（真实） |
| 任务趋势（时间轴） | job 无 `created_at` | 诚实空态「任务无时间戳，暂无法绘制趋势」 |
| 热门查询 | 无查询日志接口 | 诚实空态「当前版本未记录查询历史」 |
| 热门实体 | `getGraph(kbId,{limit})` 真实 degree | 取真实 Top-N（可选 KB） |
| 系统设置 | 无读写接口 | 只读说明（LLM/Embedding 透传 graphrag settings_yaml、上传 25MiB、索引 standard/fast + full/incremental、4 种查询法、密钥走环境变量不落库），**无保存按钮、不伪造成功** |
| API Keys | 无后端 | 明确「当前版本未启用 API Key 管理」，能力预留/配置说明页 |
| 文档管理顶层 | 无全局文档接口 | `listKbs` + 每 KB `listDocuments` 聚合（仿 `loadAllJobs`），失败降级 |
| 图谱管理顶层 | `getGraph(kbId)` | KB 选择器 + 复用 `GraphView` + 真实 degree Top-N 实体概览 |

## 5. 实现清单

### 5.1 改动现有
- `src/lib/nav.ts`：`PRIMARY_NAV`（扁平）→ `NAV_GROUPS`（分组：`{title, items[]}[]`）。保留 `NavItem`。
- `src/components/AppShell.tsx`：侧边栏按 `NAV_GROUPS` 渲染分组标题 + 图标 + 中文 + 激活态左侧条；底部保留演示/品牌/版本。`TopBar` 的 `TITLES` 补新路由。
- `src/App.tsx`：新增 7 条顶层 `Route`，保留现有全部路由。
- `src/components/icons.tsx`：新增 `IconChart`、`IconGear`、`IconKey`（沿用 stroke 风格）。
- `src/lib/aggregate.ts`：新增 `loadAllDocuments()`（listKbs + 每 KB listDocuments，失败降级）。

### 5.2 新页面
- `pages/DocumentsCenterPage.tsx`：跨 KB 文档概览（每 KB 文档数/分块/字节）+ 进入对应 KB 文档管理链接。
- `pages/GraphCenterPage.tsx`：KB 选择器 + 复用 `<GraphView>` + Top-N 实体 + 进入对应 KB 图谱。
- `pages/QueryTestPage.tsx`：KB 选择 + 4 方法 + 输入 + 答案/方法/耗时/错误 + 诚实引用说明。
- `pages/ChatPage.tsx`：左侧 KB 选择 + 中间会话 + 底部输入；每条回答附方法/耗时/错误 + 诚实引用说明。复用 `query()`。
- `pages/AnalyticsPage.tsx`：KB 数 / 文档数 / 任务数 / 成功率（真实 job 状态）/ 累计成本 / 每 KB 明细；趋势 & 热门查询诚实空态；热门实体真实 Top-N。
- `pages/SettingsPage.tsx`：只读配置说明（LLM/Embedding/索引/上传/查询/密钥），引导而非保存。
- `pages/ApiKeysPage.tsx`：能力预留页 + 明确未启用提示。

### 5.3 复用
- `query()`（client.ts）、`<GraphView>`、`<DocumentManager>`、`<JobList>`、`<CostPanel>`、`Stat/Card/EmptyState`、`useAsync`、`loadAllJobs/loadAllCost`。
- 新增 `src/lib/query-methods.ts`：抽出 4 方法元数据（local/global/drift/basic + needsReports），`QueryPage` 与新页共用（小重构，非回滚）。

## 6. 测试 / 验收

- 现有 19 个 vitest 全绿（App.test 渲染 `/`、KbListPage、DocumentManager、GraphView、CostPanel、JobDetailPage、client、useJobPolling）。
- 为新页加最小渲染测试（DocumentsCenterPage / AnalyticsPage / SettingsPage / ApiKeysPage 至少渲染 + 关键文案；QueryTestPage/ChatPage/GraphCenterPage 可选轻量测试）。
- `npm run build`（`tsc -b && vite build`）干净。
- `npm test` 全绿。
- Playwright/chrome-devtools 截图复核：侧边栏、概览、文档管理、图谱管理、检索测试、问答对话、分析报表、系统设置、API Keys。

## 7. 风险

1. **分组渲染破坏现有 nav-link 视觉** — 沿用同一 `nav-link`/`nav-link-active` 类与品牌渐变，仅加分组标题。
2. **聚合接口失败** — `loadAllDocuments` 仿 `loadAllJobs` 的 per-KB try/catch 降级。
3. **激活态误判** — `NavLink` 前缀匹配 + `/` 用 `end`；顶层新路由与 KB 子路由前缀不重叠。
4. **诚实 vs spec 差距** — 引用/趋势/热门查询后端拿不到，严格走空态，不伪造。
