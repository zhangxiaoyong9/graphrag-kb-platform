# LLM Provider 健康可视化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dashboard page under "分析与监控" that visualizes `GET /llm/health` — per-endpoint circuit-breaker state (color-coded) + gateway metrics cards — with on-load fetch + manual refresh.

**Architecture:** Pure frontend, no backend change. A new `getLlmHealth()` client fn wraps the existing endpoint; `LlmHealthPage` renders metrics `Stat` cards + a breaker-state table via the shared `useAsync` hook (whose `reload()` drives the refresh button). Route + nav + title wiring make it reachable.

**Tech Stack:** React + TypeScript + Vite + Tailwind + Vitest + msw. Existing UI primitives (`Card`/`CardHeader`/`Stat`/`Badge`/`EmptyState`/`Spinner`) + `useAsync` hook.

**Spec:** `docs/superpowers/specs/2026-07-04-llm-health-visualization-design.md`

## Global Constraints

- Dashboard copy is Chinese; warning styling stays `bg-warning-soft` + `text-[#b26b00]` (matches the existing "需社区报告" / 检索详情 convention).
- The **server-process-only / reset-on-restart caveat is a hard acceptance item** — it must be prominent text in the page (the endpoint reflects only the API server query path, not the worker indexing path; metrics clear on restart).
- State color mapping is fixed: `closed → success/绿 ("正常")`, `open → danger/红 ("熔断")`, `half_open → warning/琥珀 ("半开")`.
- Metric values that are `null` (rolling window empty) render as "—".
- No charts (backend exposes only current p50 snapshot, no time series). No auto-polling. No backend changes.
- Commit messages end with `Co-Authored-By: Claude <noreply@anthropic.com>`.
- Each task runs its own test command and commits only after green.

---

## File Structure

- Modify `web/src/api/types.ts` — add `LlmHealthState` / `LlmHealthProfile` / `LlmHealthMetrics` / `LlmHealth`.
- Modify `web/src/api/client.ts` — add `getLlmHealth()`; extend the type-only import.
- Modify `web/src/api/client.test.ts` — add an `/llm/health` handler + a round-trip test.
- Create `web/src/pages/LlmHealthPage.tsx` — the page (header + caveat + metrics cards + breaker table + empty/error states).
- Create `web/src/pages/LlmHealthPage.test.tsx` — msw tests (happy / null metric / half_open / empty / error+retry / refresh).
- Modify `web/src/App.tsx` — add the `/llm-health` route.
- Modify `web/src/lib/nav.ts` — add the nav item under "分析与监控".
- Modify `web/src/components/AppShell.tsx` — add the title-map entry.
- Modify `web/src/App.test.tsx` — assert the nav label renders.

---

## Task 1: Data contract — types + `getLlmHealth()` client fn

**Files:**
- Modify: `web/src/api/types.ts` (append the four types)
- Modify: `web/src/api/client.ts:1` (extend import) + append one fn
- Test: `web/src/api/client.test.ts` (extend import + server handler + one test)

**Interfaces:**
- Consumes: the existing `req<T>(path, init?)` helper in `client.ts`.
- Produces: `getLlmHealth(): Promise<LlmHealth>` and the `LlmHealth` family of types, consumed by Task 2.

- [ ] **Step 1: Write the failing test**

In `web/src/api/client.test.ts`:

1. Add `getLlmHealth` to the import from `./client` (the existing line `import { ... } from "./client";`).
2. Add a handler inside the existing `setupServer(...)` call:

```ts
  http.get("/llm/health", () =>
    HttpResponse.json({
      profiles: [
        { provider: "openai", model: "gpt-4o-mini", api_base: null, state: "closed" },
        { provider: "deepseek", model: "deepseek-chat", api_base: "https://api.deepseek.com", state: "open" },
      ],
      metrics: { ttft_ms_p50: 123.4, failover_detect_ms_p50: null, failover_recover_ms_p50: null, failovers: 2, successes: 40 },
    }),
  ),
```

3. Append the test:

```ts
test("getLlmHealth returns profiles + metrics", async () => {
  const out = await getLlmHealth();
  expect(out.profiles).toHaveLength(2);
  expect(out.profiles[0]).toEqual({ provider: "openai", model: "gpt-4o-mini", api_base: null, state: "closed" });
  expect(out.metrics.failovers).toBe(2);
  expect(out.metrics.ttft_ms_p50).toBe(123.4);
  expect(out.metrics.failover_detect_ms_p50).toBeNull();
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd web && npx vitest run src/api/client.test.ts`
Expected: FAIL — `getLlmHealth` is not exported (and `LlmHealth` type does not exist).

- [ ] **Step 3: Add the types**

Append to `web/src/api/types.ts`:

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

- [ ] **Step 4: Add the client fn**

In `web/src/api/client.ts`:

Extend the type-only import on line 1 to include `LlmHealth` (add `LlmHealth` to the existing `import type { ... } from "./types";` list).

Append the fn (next to the other `get*` exports):

```ts
export const getLlmHealth = () => req<LlmHealth>("/llm/health");
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd web && npx vitest run src/api/client.test.ts`
Expected: PASS (the new test + all existing client tests).

- [ ] **Step 6: Type-check**

Run: `cd web && npx tsc -b --noEmit`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add web/src/api/types.ts web/src/api/client.ts web/src/api/client.test.ts
git commit -m "$(cat <<'EOF'
feat(api): getLlmHealth() client fn + LlmHealth types

Wraps GET /llm/health (per-endpoint breaker state + gateway metrics).
Data contract for the upcoming LLM health page.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `LlmHealthPage` component

**Files:**
- Create: `web/src/pages/LlmHealthPage.tsx`
- Test: `web/src/pages/LlmHealthPage.test.tsx`

**Interfaces:**
- Consumes: `getLlmHealth()` + `LlmHealth`/`LlmHealthState`/`LlmHealthMetrics` (Task 1); the `useAsync` hook (`{data, loading, error, reload}`); UI primitives `Card`/`CardHeader`/`Stat`/`Badge`/`EmptyState`/`Spinner` from `../components/ui`; icons `IconPulse`/`IconRefresh`/`IconWarn` from `../components/icons`.
- Produces: a default-exported `LlmHealthPage` rendered by the route wired in Task 3.

- [ ] **Step 1: Write the failing tests**

Create `web/src/pages/LlmHealthPage.test.tsx`:

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import LlmHealthPage from "./LlmHealthPage";

const OK = {
  profiles: [
    { provider: "openai", model: "gpt-4o-mini", api_base: null, state: "closed" },
    { provider: "deepseek", model: "deepseek-chat", api_base: "https://api.deepseek.com", state: "open" },
    { provider: "ollama", model: "qwen2", api_base: "http://localhost:11434", state: "half_open" },
  ],
  metrics: { ttft_ms_p50: 150, failover_detect_ms_p50: 80, failover_recover_ms_p50: 1200, failovers: 3, successes: 50 },
};

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("renders breaker states with color badges, metrics, and the server-only caveat", async () => {
  server.use(http.get("/llm/health", () => HttpResponse.json(OK)));
  render(<LlmHealthPage />);
  // three states rendered
  expect(await screen.findByText("正常")).toBeInTheDocument();
  expect(screen.getByText("熔断")).toBeInTheDocument();
  expect(screen.getByText("半开")).toBeInTheDocument();
  // a metric value (TTFT p50 rounded)
  expect(screen.getByText("150 ms")).toBeInTheDocument();
  // the hard-requirement caveat
  expect(screen.getByText(/仅反映 API server 进程/)).toBeInTheDocument();
});

test("shows — for null metrics", async () => {
  server.use(
    http.get("/llm/health", () =>
      HttpResponse.json({
        profiles: [{ provider: "openai", model: "gpt-4o-mini", api_base: null, state: "closed" }],
        metrics: { ttft_ms_p50: null, failover_detect_ms_p50: null, failover_recover_ms_p50: null, failovers: 0, successes: 0 },
      }),
    ),
  );
  render(<LlmHealthPage />);
  await screen.findByText("正常");
  expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(3); // the three null p50 cards
});

test("empty state when no profiles", async () => {
  server.use(
    http.get("/llm/health", () =>
      HttpResponse.json({ profiles: [], metrics: { ttft_ms_p50: null, failover_detect_ms_p50: null, failover_recover_ms_p50: null, failovers: 0, successes: 0 } }),
    ),
  );
  render(<LlmHealthPage />);
  expect(await screen.findByText("暂无数据")).toBeInTheDocument();
});

test("error state with retry re-fetches", async () => {
  let calls = 0;
  server.use(
    http.get("/llm/health", () => {
      calls += 1;
      return new HttpResponse(null, { status: 500 });
    }),
  );
  render(<LlmHealthPage />);
  const retry = await screen.findByRole("button", { name: /重试/ });
  expect(screen.getByText(/加载失败/)).toBeInTheDocument();
  fireEvent.click(retry);
  await waitFor(() => expect(calls).toBe(2));
});

test("refresh button re-fetches", async () => {
  let calls = 0;
  server.use(
    http.get("/llm/health", () => {
      calls += 1;
      return HttpResponse.json(OK);
    }),
  );
  render(<LlmHealthPage />);
  await screen.findByText("正常");
  const refresh = screen.getByRole("button", { name: /刷新/ });
  fireEvent.click(refresh);
  await waitFor(() => expect(calls).toBe(2));
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd web && npx vitest run src/pages/LlmHealthPage.test.tsx`
Expected: FAIL — `LlmHealthPage` does not exist.

- [ ] **Step 3: Implement the page**

Create `web/src/pages/LlmHealthPage.tsx`:

```tsx
import { useAsync } from "../hooks/useAsync";
import { getLlmHealth } from "../api/client";
import type { LlmHealthState } from "../api/types";
import { Card, CardHeader, Stat, EmptyState, Badge, Spinner } from "../components/ui";
import { IconPulse, IconRefresh, IconWarn } from "../components/icons";

const STATE_LABEL: Record<LlmHealthState, string> = {
  closed: "正常",
  open: "熔断",
  half_open: "半开",
};

const STATE_TONE: Record<LlmHealthState, "success" | "danger" | "warning"> = {
  closed: "success",
  open: "danger",
  half_open: "warning",
};

function ms(v: number | null | undefined): string {
  return v == null ? "—" : `${Math.round(v)} ms`;
}

/** Per-provider circuit-breaker state + gateway metrics, from GET /llm/health.
 * Reflects the API server process only (not the worker); resets on restart. */
export default function LlmHealthPage() {
  const { data, loading, error, reload } = useAsync(() => getLlmHealth(), []);
  const profiles = data?.profiles ?? [];
  const m = data?.metrics;

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader
          title="LLM 健康"
          subtitle="API server 进程的 provider 熔断状态与网关指标"
          icon={<IconPulse width={18} height={18} />}
          actions={
            <button type="button" className="btn btn-ghost btn-sm" onClick={reload} aria-label="刷新">
              <IconRefresh width={16} height={16} /> 刷新
            </button>
          }
        />
        <div className="mt-3 flex items-start gap-2 rounded-lg bg-warning-soft px-3 py-2 text-[12px] text-[#b26b00]">
          <IconWarn width={15} height={15} className="mt-0.5 shrink-0" />
          <span>仅反映 API server 进程（查询路径）的熔断器与网关指标；worker 索引路径不在此列；进程重启后数据清零。</span>
        </div>
      </Card>

      {error ? (
        <Card>
          <div className="flex items-center gap-2 rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">
            <IconWarn width={16} height={16} className="shrink-0" />
            <span>加载失败：{error}</span>
            <button type="button" className="btn btn-ghost btn-sm ml-auto" onClick={reload} aria-label="重试">
              重试
            </button>
          </div>
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
            <Stat label="TTFT p50" value={ms(m?.ttft_ms_p50)} icon={<IconPulse width={16} height={16} />} />
            <Stat label="故障转移检测 p50" value={ms(m?.failover_detect_ms_p50)} icon={<IconPulse width={16} height={16} />} />
            <Stat label="故障转移恢复 p50" value={ms(m?.failover_recover_ms_p50)} icon={<IconPulse width={16} height={16} />} />
            <Stat label="故障转移次数" value={m?.failovers ?? 0} icon={<IconPulse width={16} height={16} />} />
            <Stat label="成功次数" value={m?.successes ?? 0} icon={<IconPulse width={16} height={16} />} />
          </div>

          <Card>
            <CardHeader title="熔断端点" subtitle="每个 provider endpoint 的当前熔断状态" icon={<IconPulse width={18} height={18} />} />
            <div className="mt-4">
              {loading && !data ? (
                <div className="flex items-center justify-center py-10 text-muted">
                  <Spinner /> <span className="ml-2 text-[13px]">加载中…</span>
                </div>
              ) : profiles.length === 0 ? (
                <EmptyState
                  icon={<IconPulse />}
                  title="暂无数据"
                  hint="尚未发起任何 LLM 调用，或服务刚重启。触发一次查询后再刷新。"
                />
              ) : (
                <table className="w-full text-sm">
                  <thead className="text-left text-[12px] text-muted">
                    <tr>
                      <th className="py-2">provider</th>
                      <th>model</th>
                      <th>api_base</th>
                      <th>状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {profiles.map((p, i) => (
                      <tr key={`${p.provider}-${p.model}-${i}`} className="border-t border-line">
                        <td className="py-2 font-mono text-[12px] text-ink">{p.provider}</td>
                        <td className="font-mono text-[12px]">{p.model}</td>
                        <td className="font-mono text-[12px] text-muted">{p.api_base ?? "—"}</td>
                        <td>
                          <Badge tone={STATE_TONE[p.state]} dot>
                            {STATE_LABEL[p.state]}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
```

> **Note for the implementer:** confirm `Spinner` is exported from `../components/ui` (it is used elsewhere, e.g. `QueryPage`); if it lives in `../components/icons`, adjust the import. Confirm the `btn btn-ghost btn-sm` class exists (it does — `QueryPage` uses it). The `Badge` `tone` values `success` / `danger` / `warning` are all valid per `Tone` in `lib/status.ts`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd web && npx vitest run src/pages/LlmHealthPage.test.tsx`
Expected: PASS (all five tests).

- [ ] **Step 5: Type-check + build**

Run: `cd web && npm run build`
Expected: `tsc -b && vite build` succeeds.

- [ ] **Step 6: Commit**

```bash
git add web/src/pages/LlmHealthPage.tsx web/src/pages/LlmHealthPage.test.tsx
git commit -m "$(cat <<'EOF'
feat(ui): LLM health page (breaker states + gateway metrics)

Renders GET /llm/health: color-coded per-endpoint breaker table
(closed/open/half_open) + metrics cards (TTFT/failover p50s + counts),
with a prominent server-process-only / reset-on-restart caveat and a
manual refresh button (useAsync.reload). On-load fetch, no polling.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Route + nav + title wiring

**Files:**
- Modify: `web/src/App.tsx` (import + route)
- Modify: `web/src/lib/nav.ts` (nav item under "分析与监控")
- Modify: `web/src/components/AppShell.tsx` (title-map entry)
- Test: `web/src/App.test.tsx` (assert the nav label renders)

**Interfaces:**
- Consumes: `LlmHealthPage` default export (Task 2); the existing `NAV_GROUPS` shape and AppShell title map.
- Produces: a reachable `/llm-health` route + nav entry.

- [ ] **Step 1: Write the failing test**

Append to `web/src/App.test.tsx`:

```tsx
test("renders the LLM health nav item", async () => {
  server.use(
    http.get("/llm/health", () =>
      HttpResponse.json({ profiles: [], metrics: { ttft_ms_p50: null, failover_detect_ms_p50: null, failover_recover_ms_p50: null, failovers: 0, successes: 0 } }),
    ),
  );
  render(
    <MemoryRouter>
      <App />
    </MemoryRouter>,
  );
  expect(await screen.findByText("LLM 健康")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd web && npx vitest run src/App.test.tsx`
Expected: FAIL — "LLM 健康" not found (no nav item yet).

- [ ] **Step 3: Add the route**

In `web/src/App.tsx`: add the import `import LlmHealthPage from "./pages/LlmHealthPage";` (next to the other page imports), and inside the `<Route element={<AppShell />}>` group add (next to the other top-level routes like `/analytics`):

```tsx
        <Route path="/llm-health" element={<LlmHealthPage />} />
```

- [ ] **Step 4: Add the nav item**

In `web/src/lib/nav.ts`, append to the "分析与监控" group's `items` array (after the `/cost` item):

```ts
      { to: "/llm-health", label: "LLM 健康", icon: IconPulse },
```

(`IconPulse` is already imported at the top of `nav.ts`.)

- [ ] **Step 5: Add the title-map entry**

In `web/src/components/AppShell.tsx`, add to the title map (next to `"/cost": "成本统计"`):

```tsx
  "/llm-health": "LLM 健康",
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd web && npx vitest run src/App.test.tsx`
Expected: PASS (all existing tests + the new nav-item test).

- [ ] **Step 7: Full frontend suite + build**

Run: `cd web && npm test && npm run build`
Expected: vitest all green; `tsc -b && vite build` succeeds.

- [ ] **Step 8: Commit**

```bash
git add web/src/App.tsx web/src/lib/nav.ts web/src/components/AppShell.tsx web/src/App.test.tsx
git commit -m "$(cat <<'EOF'
feat(ui): wire /llm-health route + nav item + title

Adds the LLM 健康 page to the 分析与监控 nav group and the AppShell
title map; registers the /llm-health route.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Integration gate

**Files:** none (verification only).

- [ ] **Step 1: Full frontend suite + build**

Run: `cd web && npm test && npm run build`
Expected: vitest all green; build clean.

- [ ] **Step 2: Backend unaffected — quick sanity**

Run (from repo root): `uv run python -m pytest tests/test_api_llm_health.py -q` (or, if that filename differs, `uv run python -m pytest -k llm_health -q`)
Expected: PASS (confirms the consumed endpoint still behaves; this branch changed no backend, so this is a no-regression sanity check).

- [ ] **Step 3: Manual smoke (optional)**

Start the API server + open `/llm-health` in the dashboard. Confirm: the server-only caveat is visible; after triggering any query (or with prior calls), the breaker table + metric cards populate; the refresh button re-fetches.

---

## Self-Review

**Spec coverage** — every spec requirement maps to a task:

- Data source `getLlmHealth()` + types → **Task 1**.
- Page (header + caveat + metrics cards + breaker table + empty/error + refresh) → **Task 2**.
- Route + nav (分析与监控 group, IconPulse) + title mapping → **Task 3**.
- Hard-requirement server-only caveat → **Task 2** Step 3 (prominent warning-soft block) + asserted in Task 2 Step 1 happy test.
- State color mapping (closed/open/half_open) → **Task 2** Step 3 `STATE_TONE`/`STATE_LABEL`, asserted across all three states in Step 1.
- Null metrics → "—" → **Task 2** Step 3 `ms()` helper + Step 1 null-metric test.
- Manual refresh (no polling) → **Task 2** `useAsync.reload` + Step 1 refresh test.
- No charts / no backend changes → honored (no chart code, no backend files touched).

**Placeholder scan:** every code step shows complete code. The three "Note for the implementer" callouts (Task 2 Step 3 Spinner/btn-class/Badge-tone sanity; Task 4 Step 2 filename locate) are concrete verify-against-existing-pattern instructions, not hand-waves.

**Type consistency:** `LlmHealth` / `LlmHealthProfile` / `LlmHealthMetrics` / `LlmHealthState` are identical between Task 1 (definition) and Task 2 (consumption). `getLlmHealth()` signature is identical between Task 1 (definition) and Task 2/Task 3 (consumption). The state→tone→label maps (`STATE_TONE`/`STATE_LABEL`) match the spec's color mapping verbatim. The `/llm/health` JSON shape is identical between the Task 1 client test fixture, the Task 2 page-test fixtures, and the Task 3 nav-test fixture.

**Scope:** three small implementation tasks + one gate; each task independently testable with its own green-bar commit. Task 2 depends on Task 1 (types + client fn); Task 3 depends on Task 2 (the page component) — linear ordering respects that. No backend, no migration, no graphrag seam.
