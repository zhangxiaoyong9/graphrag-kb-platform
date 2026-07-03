# 2026-07-04 — LLM Provider 健康可视化

## 背景

`GET /llm/health`（`kb_platform/api/routes_llm_health.py`）已经暴露了进程级熔断器注册表 + 网关指标：

```json
{
  "profiles": [{"provider": "...", "model": "...", "api_base": "..." | null, "state": "closed|open|half_open"}],
  "metrics": {"ttft_ms_p50": float|null, "failover_detect_ms_p50": float|null,
              "failover_recover_ms_p50": float|null, "failovers": int, "successes": int}
}
```

前端目前只消费存活探针 `/health`（`AppShell` 顶部状态 + `SystemPage`），**完全没有**消费 `/llm/health` —— provider 的熔断状态、TTFT、故障转移时序在 UI 上无处可见。

**关键语义（决定 UI 标注）：** 熔断器注册表与指标是**进程内、内存中、重启清零**的；`/llm/health` 只反映 **API server 进程**（查询路径：local/global/drift/basic/cypher/hybrid 的 map-reduce/embed/rewriter），**不包括 worker 进程**（索引路径：extract/summarize/report/embed —— worker 是 `asyncio.run`-per-job、无持久 loop，其熔断是流量驱动且不由此端点暴露）。可视化必须明确标注这一点，否则用户会误以为是"全量 provider 健康"。

## 目标

- 在"分析与监控"分组下新增页面，可视化每个 endpoint 的熔断状态（色标）+ 网关指标卡。
- 进页面加载一次 + 手动"刷新"按钮重取。
- 醒目标注 server-only / 重启清零语义。

## 非目标

- **不做图表**：后端只暴露当前 p50 快照（滚动窗口的中位数），无时序历史，画图没数据。
- **不做 worker 进程健康**：端点不提供，且 worker 无持久 loop。
- **不做自动轮询**：已选手动刷新（熔断状态切换是秒~分钟级，数据本身非高频变化）。
- **无后端改动**：纯消费现有 `/llm/health`。

## 设计

### 数据源

- `web/src/api/client.ts` 加 `getLlmHealth()` → `GET /llm/health`（端点已存在，返回 `LlmHealth`）。
- `web/src/api/types.ts` 加：
  ```ts
  export type LlmHealthState = "closed" | "open" | "half_open";
  export interface LlmHealthProfile {
    provider: string;
    model: string;
    api_base: string | null;
    state: LlmHealthState;
  }
  export interface LlmHealthMetrics {
    ttft_ms_p50: number | null;
    failover_detect_ms_p50: number | null;
    failover_recover_ms_p50: number | null;
    failovers: number;
    successes: number;
  }
  export interface LlmHealth {
    profiles: LlmHealthProfile[];
    metrics: LlmHealthMetrics;
  }
  ```

### 路由 + 导航

- `web/src/App.tsx`：在 `AppShell` 路由组内加 `<Route path="/llm-health" element={<LlmHealthPage />} />`（顶层路由，与 `/analytics`、`/cost` 同级）。
- `web/src/lib/nav.ts`：在 "分析与监控" 组的 `items` 末尾追加 `{ to: "/llm-health", label: "LLM 健康", icon: IconPulse }`（`IconPulse` 已导入且语义贴切；图标可与"系统状态"复用）。
- `web/src/components/AppShell.tsx`：标题映射加 `"/llm-health": "LLM 健康"`。

### 页面 `web/src/pages/LlmHealthPage.tsx`

数据：`const { data, loading, error, reload } = useAsync(() => getLlmHealth(), []);`。`useAsync` 已返回 `reload()`，刷新/重试按钮直接 `onClick={reload}`（无需额外 state）。

布局（复用 `Card` / `CardHeader` / `Stat` / `EmptyState` / `Badge` / `IconRefresh` / `IconPulse` / `IconWarn`）：

1. **头部 Card**（`CardHeader` title="LLM 健康" subtitle="API server 进程的 provider 熔断状态与网关指标" icon=`IconPulse`，右上角放 `IconRefresh` 刷新按钮）。其下一行**醒目提示条**（`bg-warning-soft` + `text-[#b26b00]`，配 `IconWarn`）：
   > 仅反映 API server 进程（查询路径）的熔断器与网关指标；worker 索引路径不在此列；进程重启后数据清零。
2. **指标卡行**（grid of `Stat`，5 个）：
   - TTFT p50（`metrics.ttft_ms_p50`，单位 ms）
   - 故障转移检测 p50（`failover_detect_ms_p50`，ms）
   - 故障转移恢复 p50（`failover_recover_ms_p50`，ms）
   - 故障转移次数（`failovers`）
   - 成功次数（`successes`）
   
   值为 `null`（窗口空）→ 显示 "—"。
3. **熔断端点表**（`Card` + `<table>`）：列 `provider / model / api_base / 状态`。状态用色标 `Badge`：
   - `closed` → success 绿，文案"正常"
   - `open` → danger 红，文案"熔断"
   - `half_open` → warning 琥珀，文案"半开"
   
   `api_base` 为 `null` → "—"。
4. **空态**：`profiles.length === 0` → 用 `<EmptyState icon={<IconPulse />} title="暂无数据" hint="尚未发起任何 LLM 调用，或服务刚重启。触发一次查询后再刷新。" />` 替代表格。
5. **错误态**：`useAsync` 出错 → 错误条（`bg-danger-soft` + `IconWarn`）+"重试"按钮（等价于刷新，`setRefreshKey`）。

### 刷新机制

`useAsync` 已返回 `reload()`，刷新/重试按钮直接调 `reload`（无需额外 state）。`useAsync(() => getLlmHealth(), [])`。错误态的"重试"复用同一 `reload`。不做 `visibilitychange` 暂停、不做定时器。

## 契约小结

- **数据**：`GET /llm/health` → `{profiles, metrics}`（shape 见上）。
- **前端类型**：`LlmHealth` / `LlmHealthProfile` / `LlmHealthMetrics` / `LlmHealthState`。
- **路由** `/llm-health`；**导航**在"分析与监控"组、图标 `IconPulse`；**标题**"LLM 健康"。
- **状态徽章色标**：closed=success/绿、open=danger/红、half_open=warning/琥珀。
- **server-only + 重启清零标注为硬性验收项**（缺则误导用户）。
- 指标 `null` → "—"。

## 测试策略

`web/src/pages/LlmHealthPage.test.tsx`（msw mock `/llm/health`）：

- **happy**：返回 2 个 profile（一 closed、一 open）+ 非 null 指标 → 断言状态徽章文案+色标、"正常"/"熔断" 出现、指标卡数值、表格行数、server-only 标注出现。
- **null 指标**：`ttft_ms_p50 = null` → 该卡显示 "—"。
- **half_open**：再加一条断言琥珀徽章"半开"。
- **空态**：`profiles = []` → 空态出现，表格不渲染。
- **错误态**：handler 返回 500 → 错误条出现，点"重试"触发二次请求（handler 调用计数 → 2）。
- **刷新**：点页头"刷新" → handler 调用计数 → 2。

`web/src/api/client.test.ts`：若该文件已覆盖其它 client 函数，加一条 `getLlmHealth()` 命中 `/llm/health` 的断言；否则并入页面测试。

`web/src/App.test.tsx`：现有 "renders the dashboard at /" 冒烟测试会渲染 `AppShell`（含 `NAV_GROUPS`）——新增导航项不应破坏它；如需，加一条 `/llm-health` 路由可访问的断言。

## 风险与回滚

- 纯前端新增（1 页 + 1 client fn + 类型 + 路由 + nav 项 + 标题映射）；**无后端/迁移/数据面改动**。删除这些即完整回滚。
- 不影响现有 `/health`（存活探针）消费。
- 最大风险是 **server-only 标注缺失/不醒目** → 用户误以为全量健康；标注是硬性验收项。
- 图标 `IconPulse` 与"系统状态"复用——本平台已允许图标复用（IconSearch、IconKey 均复用），可接受。
