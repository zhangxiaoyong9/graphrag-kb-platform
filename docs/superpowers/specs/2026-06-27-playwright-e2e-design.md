# Playwright E2E Design

Date: 2026-06-27

## Summary

Add a Playwright E2E test layer to the KB platform that exercises the real
SPA against a real FastAPI server backed by a **no-LLM** fake indexing
worker (`FakeGraphAdapter`) and an injected fake query engine
(`FakeQueryEngine`). The primary motivation is covering what vitest cannot
— the canvas-based `GraphView` — plus the core happy-path integration
seams (create KB → paste doc → trigger job → watch it succeed → browse
graph / document detail / query) that unit tests mock out.

## Goals

1. Cover the canvas-based `GraphView` (render, `zoomToFit`, node click) that
   vitest cannot test because it globally mocks `canvas`.
2. Cover the end-to-end integration of SPA ↔ REST API ↔ worker for the core
   user flows, catching bugs that component-level vitest (MSW-mocked) cannot.
3. Run with **zero LLM cost and zero provider key** by using the existing
   `FakeGraphAdapter` (deterministic indexing) and `FakeQueryEngine` (canned
   answers).
4. Keep the fast vitest suite unaffected: E2E is a separate, opt-in command.

## Non-goals

- Cross-browser matrix. Chromium only (internal tool).
- Re-asserting behavior already covered by vitest at the component level
  (cost panel, export download, job-detail unit table mechanics). E2E stays
  focused on integration + canvas.
- Visual regression / screenshot diffing.
- Performance/load testing.
- CI wiring (this repo has no CI today; the design is CI-ready but does not
  add a workflow).

## Current context

- Frontend: React 18 + TS + Vite + Tailwind SPA in `web/`. Unit tests use
  vitest + MSW + Testing Library; canvas is globally mocked in
  `setupTests.ts`, so `GraphView` (react-force-graph-2d, canvas-rendered)
  is untestable there.
- Backend: FastAPI app built by `create_app(repo, data_root, query_engine=None)`
  in `kb_platform/api/app.py`. The API server never runs indexing; a separate
  worker does. The worker's `run_worker(repo, adapter_factory=...)` accepts
  any adapter factory, so it can run with `FakeGraphAdapter` (no LLM).
- `create_app` already supports an injected `query_engine` (non-None =
  injected, used by tests); the query route prefers the injected engine over
  building a real `GraphRagQueryEngine`.
- No E2E infrastructure exists today (`web/package.json` has only vitest).
- `FakeGraphAdapter` (`kb_platform/graph/adapter.py`) does deterministic
  chunking + extraction (one entity per capitalized word + relationships) —
  enough to populate graph/document-detail views. `FakeQueryEngine` exists
  from the Phase 3b query seam.

## Architecture

### Fake-server harness (Python)

New script `scripts/e2e_server.py` (importable + CLI-runnable) that:

1. Creates a temp directory holding a temp SQLite DB path + temp `data_root`.
2. Runs alembic migrations against the temp DB (programmatically, via
   `alembic.config` + `command.upgrade`, with the DB path injected through
   `-x db=...` — the pattern the repo already uses).
3. Builds the app with `create_app(repo, data_root, query_engine=FakeQueryEngine())`
   so the query route returns canned answers with no LLM.
4. Serves it with `uvicorn` on `127.0.0.1:18000` (fixed E2E port; documented).
5. Starts the fake worker in a background thread:
   `run_worker(repo=repo, adapter_factory=lambda kb: FakeGraphAdapter(), ...)`
   so any triggered job is processed with deterministic, no-LLM data.
6. **Seeds a baseline KB**: creates one KB ("E2E 基线"), adds a document with
   multi-chunk text, triggers a `full` job, and waits until the worker marks
   it `succeeded` (poll the job status). This leaves the graph parquets
   (entities/relationships/communities/community_reports) on disk — what the
   graph, document-detail, and entity views read. (`FakeGraphAdapter` runs the
   whole pipeline to `succeeded`, verified by `test_full_pipeline_produces_all_four_parquets`.)
7. Prints the server URL, then blocks (serves) until killed.

The harness reuses `create_app`, `run_worker`, `FakeGraphAdapter`, and
`FakeQueryEngine` — it introduces **no new production code path**, only a
test-only composition of existing seams.

### Frontend

E2E runs against the **production build** (`npm run build` → `web/dist`),
which the API server already serves via `create_app`'s static mount +
history fallback. No vite dev server. Tests what users actually see.

### Playwright config

New `web/playwright.config.ts`:

- Single browser: `chromium` (internal tool).
- `webServer`: launches `python scripts/e2e_server.py` (from repo root),
  with `url: http://127.0.0.1:18000`, `reuseExistingServer: true` (so manual
  `npm run e2e:server` debugging works), and a generous `timeout` for the
  migration + seed step.
- `testDir: ./e2e`, specs in `web/e2e/*.spec.ts`.
- Excludes E2E from vitest (different directory; vitest's `include` already
  scopes to `src`).

## Test data & isolation strategy

- The harness seeds **one shared baseline KB** ("E2E 基线") already indexed.
  Read-mostly specs (graph canvas, document detail, entity page, query,
  smoke) operate against it.
- **State-changing specs** (create KB, paste document, trigger job) create
  their **own KB** with a unique name, so they never trample the baseline or
  each other.
- The fake worker runs continuously for the whole test run, so triggered
  jobs are processed automatically; the trigger-job spec polls until the job
  reaches `succeeded`.
- Browser isolation is per-spec (Playwright's default fresh context). There
  is **no per-test DB reset** (too slow); isolation is achieved by the
  unique-KB convention above, not by resetting state.

## Specs (scope B, ~9 specs)

| # | Spec | State | Notes |
|---|------|-------|-------|
| 1 | Smoke: SPA loads, sidebar brand, health pill green | read | baseline KB |
| 2 | Create KB: name → submit → lands on KB detail | **new KB** | unique name |
| 3 | Paste document: paste text → row appears in doc list | **new KB** | |
| 4 | Trigger job + watch green: full job → poll → step timeline advances → `succeeded` | **new KB** | fake worker really runs it |
| 5 | GraphView canvas: renders nodes/edges, `zoomToFit`, click node | read | the canvas gap vitest can't cover |
| 6 | Document detail + evidence drawer: open doc → click citation → drawer shows matched + before/after → switch citation replaces → close keeps body | read | baseline KB doc with chunks |
| 7 | Entity/relation page: from doc detail → entities → click entity → relations filter | read | baseline KB |
| 8 | Query: pick method → type → Ask → result area renders an answer | read | FakeQueryEngine canned answer |
| 9 | Navigation IA: sidebar groups navigate; KB detail tabs switch | read | |

Export download and cost panel are deliberately excluded (already covered by
vitest at the component level).

## Integration & dependencies

- `web/package.json` new devDependency: `@playwright/test`.
- Browser binaries are **not** auto-installed. New scripts:
  - `npm run e2e` = `npm run build && playwright test` (full: build SPA +
    start harness + run specs).
  - `npm run e2e:server` = start only the fake-server harness (for manual
    debugging against a running server).
  - `npm run e2e:install` = `playwright install chromium`.
- **E2E is separate from `npm test`** (vitest). The default unit suite stays
  fast and free of any browser-download dependency; E2E is opt-in via
  `npm run e2e`.

## Error handling

- The harness must wait for `/health` to return `ok` before Playwright begins
  (Playwright's `webServer.url` polling handles this).
- The seed step must verify the baseline job reaches `succeeded` (poll with a
  timeout); if it does not, the harness exits non-zero so the failure is
  obvious rather than producing flaky specs.
- Specs use Playwright's default auto-waiting + retries (no bespoke
  sleeps). State-changing specs use `expect(...).toBeVisible()`-style polls
  for the UI consequence of async worker work.

## Rollout order

1. Add `@playwright/test` devDep + `playwright.config.ts` + npm scripts.
2. Write the fake-server harness (`scripts/e2e_server.py`) + verify it serves
   the SPA and seeds the baseline KB over HTTP (curl).
3. Add the read-mostly specs (smoke, graph canvas, document detail, entity,
  query, nav) against the baseline.
4. Add the state-changing specs (create KB, paste doc, trigger job).
5. Run the full E2E suite green; document the run command in the README
  development section.

## Open implementation questions (resolved by this design)

- _Scope of flows_: B (core happy paths) — chosen.
- _Frontend serving_: production build, not dev server.
- _Query without LLM_: inject `FakeQueryEngine` via `create_app`'s existing
  seam (no LLM key needed for the query spec).
- _Isolation_: shared baseline KB for reads; unique KB per state-changing
  spec; no per-test DB reset.
