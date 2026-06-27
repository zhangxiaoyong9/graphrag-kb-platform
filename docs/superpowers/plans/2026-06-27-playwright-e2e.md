# Playwright E2E Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Playwright E2E suite that runs the real SPA against a no-LLM fake server (`FakeGraphAdapter` worker + injected `FakeQueryEngine`), covering the canvas-based `GraphView` and the core happy-path integration flows.

**Architecture:** A Python harness (`scripts/e2e_server.py`) builds a temp SQLite DB + temp `data_root`, seeds one baseline KB with a completed full job, starts a background `FakeGraphAdapter` worker (so triggered jobs complete with no LLM), injects `FakeQueryEngine` via `create_app`, and serves the production SPA build via uvicorn on `127.0.0.1:18000`. Playwright (Chromium only) drives the SPA; read-mostly specs use the shared baseline KB, state-changing specs create their own KBs.

**Tech Stack:** Playwright (`@playwright/test`), React 18 SPA (production build), FastAPI + uvicorn, SQLAlchemy/SQLite, `FakeGraphAdapter`, `FakeQueryEngine`, vitest (existing, unchanged).

## Global Constraints

- E2E runs with **no LLM and no provider key** (fake adapter + injected fake query engine).
- Chromium only; single browser.
- Frontend under test is the **production build** (`npm run build` → `web/dist`); no vite dev server.
- E2E is **separate from `npm test`** (vitest); opt-in via `npm run e2e`.
- Browser binaries are not auto-installed; provide `npm run e2e:install`.
- Reuse existing seams only: `create_app`, `run_worker`, `FakeGraphAdapter`, `FakeQueryEngine`, `Base.metadata.create_all`. No new production code paths.
- Read-mostly specs use the shared baseline KB ("E2E 基线"); state-changing specs create a KB with a unique name.
- No per-test DB reset; isolation is via unique KB names + Playwright's fresh browser context per spec.
- Fixed E2E server URL: `http://127.0.0.1:18000`.
- E2E specs assert against existing UI; "RED" means a selector/flow needs adjusting, not that production code is missing.

---

## File Structure

- Create: `scripts/e2e_server.py` — fake-server harness (temp DB + migrations via `create_all` + seeded baseline KB + background `FakeGraphAdapter` worker + `FakeQueryEngine`-injected app + uvicorn).
- Create: `web/playwright.config.ts` — Chromium project + `webServer` that launches the harness.
- Create: `web/e2e/fixtures.ts` — shared Playwright fixtures/helpers (baseline KB name, unique-KB generator, goto helpers).
- Create: `web/e2e/*.spec.ts` — one file per spec group.
- Modify: `web/package.json` — `@playwright/test` devDep + `e2e` / `e2e:server` / `e2e:install` scripts.
- Modify: `README.md` + `README.zh.md` — document the E2E command in the Development section.

---

## Task 1: Playwright dependency, config, and npm scripts

**Files:**
- Modify: `web/package.json`
- Create: `web/playwright.config.ts`

**Interfaces:**
- Produces: `npm run e2e`, `npm run e2e:server`, `npm run e2e:install`, and a Playwright config whose `webServer` launches `scripts/e2e_server.py` from the repo root.

- [ ] **Step 1: Add devDependency + scripts to `web/package.json`**

Add to `devDependencies`:
```json
"@playwright/test": "^1.48.0"
```

Add to `scripts`:
```json
"e2e": "npm run build && playwright test",
"e2e:server": "cd .. && uv run python scripts/e2e_server.py",
"e2e:install": "playwright install chromium"
```

- [ ] **Step 2: Create `web/playwright.config.ts`**

```ts
import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

const PORT = 18000;
const baseURL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false, // shared baseline KB + single fake worker
  workers: 1,
  retries: 0,
  reporter: "list",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  use: { baseURL, trace: "on-first-retry" },
  webServer: {
    command: "uv run python scripts/e2e_server.py",
    cwd: path.resolve(__dirname, ".."),
    url: baseURL,
    reuseExistingServer: true,
    timeout: 120_000,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
```

- [ ] **Step 3: Install the dep + browser, verify the CLI**

Run:
```bash
cd web && npm install && npm run e2e:install
npx playwright --version
```
Expected: npm install succeeds; `playwright install chromium` downloads Chromium; `--version` prints a version string.

- [ ] **Step 4: Commit**

```bash
git add web/package.json web/package-lock.json web/playwright.config.ts
git commit -m "test(web): add Playwright config + e2e scripts"
```

---

## Task 2: Fake-server harness

**Files:**
- Create: `scripts/e2e_server.py`

**Interfaces:**
- Consumes: `create_engine`, `Base.metadata.create_all`, `Repository`, `create_app` (with `query_engine=`), `FakeGraphAdapter`, `FakeQueryEngine`, `run_worker`, `session_scope`, `KnowledgeBase`, `Orchestrator.plan_full` shapes (via `create_job_pending`).
- Produces: a long-running server at `http://127.0.0.1:18000` with a seeded baseline KB whose full job has `succeeded`; any later triggered job is processed by the background worker.

- [ ] **Step 1: Write the harness**

Create `scripts/e2e_server.py`:
```python
#!/usr/bin/env python
# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""E2E fake server: temp DB + FakeGraphAdapter worker + FakeQueryEngine.

No LLM, no provider key. Seeds one baseline KB ("E2E 基线") with a completed
full job, serves the built SPA + REST API on 127.0.0.1:18000, and runs a
background FakeGraphAdapter worker so any later triggered job completes too.
"""
import os
import tempfile
import threading
import time
from pathlib import Path

import uvicorn

from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.graph.adapter import FakeGraphAdapter
from kb_platform.query.engine import FakeQueryEngine
from kb_platform.worker import run_worker

HOST = "127.0.0.1"
PORT = 18000
BASELINE_NAME = "E2E 基线"
# Multi-entity text so FakeGraphAdapter extracts several entities + relationships.
BASELINE_DOC = "ACME Org Bob Person ACME Org Alice Person Foo Bar Baz " * 200


def _wait_job(repo: Repository, job_id: int, timeout: float = 90.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = repo.get_job(job_id)
        if job and job.status in ("succeeded", "failed"):
            return job.status
        time.sleep(0.5)
    raise RuntimeError(f"baseline job {job_id} did not finish within {timeout}s")


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="kb-e2e-")
    db_path = os.path.join(tmp, "kb.db")
    data_root = os.path.join(tmp, "data")
    Path(data_root).mkdir(parents=True, exist_ok=True)
    print(f"[e2e] tmp={tmp}", flush=True)

    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    repo = Repository(engine)

    # Seed the baseline KB + document + a pending full job.
    with session_scope(engine) as s:
        kb = KnowledgeBase(
            name=BASELINE_NAME, method="standard", settings_json="{}", data_root=data_root
        )
        s.add(kb)
        s.flush()
        kb_id = kb.id
    repo.add_document(kb_id=kb_id, title="baseline.md", text=BASELINE_DOC)
    baseline_job = repo.create_job_pending(kb_id=kb_id, method="standard", type="full")

    # Background FakeGraphAdapter worker (no signal handlers in a thread).
    stop_event = threading.Event()
    worker_thread = threading.Thread(
        target=run_worker,
        kwargs=dict(
            repo=repo,
            adapter_factory=lambda kb: FakeGraphAdapter(),
            stop_event=stop_event,
            install_signal_handlers=False,
        ),
        daemon=True,
    )
    worker_thread.start()

    status = _wait_job(repo, baseline_job.id)
    if status != "succeeded":
        raise RuntimeError(f"baseline job ended {status}; fake server not usable")
    print(f"[e2e] baseline KB id={kb_id} job={baseline_job.id} {status}", flush=True)

    app = create_app(repo, data_root=data_root, query_engine=FakeQueryEngine())
    uvicorn.run(app, host=HOST, port=PORT, loop="asyncio")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the harness serves the SPA + REST over HTTP**

Run (separate terminal, from repo root):
```bash
cd web && npm run build          # ensure web/dist exists
npm run e2e:server               # starts the harness; blocks
```
In another terminal:
```bash
curl -s http://127.0.0.1:18000/health
curl -s http://127.0.0.1:18000/kbs | python -c "import sys,json; print(json.load(sys.stdin))"
curl -s http://127.0.0.1:18000/ -o /tmp/idx.html && head -c 200 /tmp/idx.html
```
Expected: `/health` returns `{"status":"ok",...}`; `/kbs` lists a KB named `E2E 基线`; `/` returns the SPA HTML (`<div id="root">`). Stop the server (Ctrl-C).

- [ ] **Step 3: Commit**

```bash
git add scripts/e2e_server.py
git commit -m "feat(e2e): fake-server harness (FakeGraphAdapter worker + FakeQueryEngine)"
```

---

## Task 3: Shared fixtures + smoke + navigation specs

**Files:**
- Create: `web/e2e/fixtures.ts`
- Create: `web/e2e/smoke.spec.ts`
- Create: `web/e2e/navigation.spec.ts`

**Interfaces:**
- Produces: a `uniqueKbName()` helper and a `createKbViaApi(page, name)` helper reused by later specs. (Read-mostly specs target the baseline KB at `/kbs/1` — the harness seeds it first, so its id is 1.)

- [ ] **Step 1: Write shared fixtures**

Create `web/e2e/fixtures.ts`:
```ts
import type { Page } from "@playwright/test";

const PREFIX = "e2e-kb";

/** Unique KB name for state-changing specs (never collides with the baseline). */
export function uniqueKbName(label = "kb"): string {
  return `${PREFIX}-${label}-${Date.now()}`;
}

/** Create a KB via the REST API and return its id. */
export async function createKbViaApi(page: Page, name: string): Promise<number> {
  const r = await page.request.post("/kbs", { data: { name, method: "standard" } });
  const body = await r.json();
  return body.id as number;
}
```

- [ ] **Step 2: Write the smoke spec**

Create `web/e2e/smoke.spec.ts`:
```ts
import { test, expect } from "@playwright/test";

test("SPA loads, brand + green health pill visible", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("知识库平台")).toBeVisible();
  // Health pill turns green when /health is ok
  await expect(page.locator("text=健康").or(page.locator("[class*=success]").first())).toBeVisible({ timeout: 15000 });
});
```

- [ ] **Step 3: Write the navigation spec**

Create `web/e2e/navigation.spec.ts`:
```ts
import { test, expect } from "@playwright/test";

test("sidebar group navigation works", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "任务管理" }).click();
  await expect(page).toHaveURL(/\/jobs/);
  await page.getByRole("link", { name: "检索测试" }).click();
  await expect(page).toHaveURL(/\/query/);
  await page.getByRole("link", { name: "知识库管理" }).click();
  await expect(page).toHaveURL(/\/kbs$/);
});

test("KB detail tabs switch", async ({ page }) => {
  await page.goto("/kbs/1");
  await expect(page.getByRole("link", { name: /文档/ }).first()).toBeVisible();
  await page.getByRole("link", { name: /^图谱/ }).first().click();
  await expect(page).toHaveURL(/\/kbs\/1\/graph/);
});
```

- [ ] **Step 4: Run the specs, adjust selectors as needed**

Run (with the harness already running via `npm run e2e:server`, OR let Playwright start it):
```bash
cd web && npx playwright test e2e/smoke.spec.ts e2e/navigation.spec.ts --project=chromium
```
Expected: PASS. If a selector misses, inspect the rendered DOM (`await page.locator("body").innerHTML()` in a `test.step`) and adjust — these specs assert existing UI.

- [ ] **Step 5: Commit**

```bash
git add web/e2e/fixtures.ts web/e2e/smoke.spec.ts web/e2e/navigation.spec.ts
git commit -m "test(e2e): smoke + navigation specs and shared fixtures"
```

---

## Task 4: Create-KB + paste-document + trigger-job specs (state-changing)

**Files:**
- Create: `web/e2e/create-kb.spec.ts`
- Create: `web/e2e/paste-doc.spec.ts`
- Create: `web/e2e/trigger-job.spec.ts`

**Interfaces:**
- Consumes: `uniqueKbName()`, `createKbViaApi()` from Task 3.

- [ ] **Step 1: Create-KB spec**

Create `web/e2e/create-kb.spec.ts`:
```ts
import { test, expect } from "@playwright/test";
import { uniqueKbName } from "./fixtures";

test("create a KB via the form and see it in the list", async ({ page }) => {
  const name = uniqueKbName("create");
  await page.goto("/kbs");
  await page.getByPlaceholder("请输入知识库名称").fill(name);
  await page.getByRole("button", { name: /创建知识库/ }).click();
  // The new KB appears in the management list
  await expect(page.getByText(name)).toBeVisible({ timeout: 15000 });
});
```

- [ ] **Step 2: Paste-document spec**

Create `web/e2e/paste-doc.spec.ts`:
```ts
import { test, expect } from "@playwright/test";
import { createKbViaApi, uniqueKbName } from "./fixtures";

test("paste a document and see it listed", async ({ page }) => {
  await page.goto("/kbs");
  const kbId = await createKbViaApi(page, uniqueKbName("paste"));
  await page.goto(`/kbs/${kbId}`);
  await page.getByPlaceholder("标题（可选）").fill("pasted.txt");
  await page.getByPlaceholder(/在此粘贴正文内容/).fill("Hello E2E pasted document body text.");
  await page.getByRole("button", { name: "添加文档" }).click();
  await expect(page.getByText("pasted.txt")).toBeVisible({ timeout: 15000 });
});
```

- [ ] **Step 3: Trigger-job spec**

Create `web/e2e/trigger-job.spec.ts`:
```ts
import { test, expect } from "@playwright/test";
import { createKbViaApi, uniqueKbName } from "./fixtures";

test("trigger a full job and watch it succeed", async ({ page }) => {
  const kbId = await createKbViaApi(page, uniqueKbName("job"));
  await page.goto(`/kbs/${kbId}`);
  // Seed a doc first (worker needs something to index)
  await page.request.post(`/kbs/${kbId}/documents`, { data: { title: "job.md", text: "ACME Org Bob Person Foo Bar " * 50 } });
  await page.goto(`/kbs/${kbId}`);
  await page.getByRole("button", { name: "全量索引" }).click();
  // "已创建任务 #N" confirms the trigger
  await expect(page.getByText(/已创建任务 #\d+/)).toBeVisible({ timeout: 15000 });
  // Open the job and poll until succeeded
  await page.getByRole("link", { name: /任务管理/ }).first().click();
  await expect(page.locator("text=succeeded").first()).toBeVisible({ timeout: 60000 });
});
```

- [ ] **Step 4: Run + adjust selectors**

```bash
cd web && npx playwright test e2e/create-kb.spec.ts e2e/paste-doc.spec.ts e2e/trigger-job.spec.ts --project=chromium
```
Expected: PASS. The trigger-job spec waits up to 60s for the fake worker to mark the job `succeeded`.

- [ ] **Step 5: Commit**

```bash
git add web/e2e/create-kb.spec.ts web/e2e/paste-doc.spec.ts web/e2e/trigger-job.spec.ts
git commit -m "test(e2e): create-KB, paste-document, trigger-job specs"
```

---

## Task 5: GraphView canvas spec

**Files:**
- Create: `web/e2e/graph-canvas.spec.ts`

**Interfaces:**
- Consumes: baseline KB (`/kbs/1`) with indexed graph data.

- [ ] **Step 1: Write the canvas spec**

Create `web/e2e/graph-canvas.spec.ts`:
```ts
import { test, expect } from "@playwright/test";

test("GraphView renders a canvas with the baseline graph", async ({ page }) => {
  await page.goto("/kbs/1/graph");
  // Not the empty state (baseline KB has data)
  await expect(page.getByText(/先触发一次索引任务/)).toHaveCount(0);
  // react-force-graph-2d renders a <canvas> element
  const canvas = page.locator("canvas").first();
  await expect(canvas).toBeVisible({ timeout: 15000 });
  const box = await canvas.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.width).toBeGreaterThan(100);
  expect(box!.height).toBeGreaterThan(100);
  // Clicking the canvas does not throw / page stays usable
  await canvas.click({ position: { x: box!.width / 2, y: box!.height / 2 } });
  await expect(canvas).toBeVisible();
});
```

- [ ] **Step 2: Run + adjust**

```bash
cd web && npx playwright test e2e/graph-canvas.spec.ts --project=chromium
```
Expected: PASS — a non-trivial `<canvas>` is present and interactive. This is the coverage vitest cannot provide (it mocks canvas globally).

- [ ] **Step 3: Commit**

```bash
git add web/e2e/graph-canvas.spec.ts
git commit -m "test(e2e): GraphView canvas render + interaction"
```

---

## Task 6: Document detail + evidence drawer + entity/relation specs

**Files:**
- Create: `web/e2e/document-detail.spec.ts`
- Create: `web/e2e/entity-relation.spec.ts`

**Interfaces:**
- Consumes: baseline KB (`/kbs/1`) document with chunk-backed citations.

- [ ] **Step 1: Document-detail + evidence-drawer spec**

Create `web/e2e/document-detail.spec.ts`:
```ts
import { test, expect } from "@playwright/test";

test("open a document, verify evidence in the drawer, close keeps body", async ({ page }) => {
  // Baseline KB document detail (doc id 1)
  await page.goto("/kbs/1/documents/1");
  await expect(page.getByText("baseline.md")).toBeVisible({ timeout: 15000 });

  // Open the evidence drawer via the first citation
  const firstCitation = page.getByRole("button", { name: /查看证据/ }).first();
  await firstCitation.click();
  await expect(page.getByText("证据详情")).toBeVisible();

  // If there is a second citation, switching replaces the drawer content
  const citations = page.getByRole("button", { name: /查看证据/ });
  if ((await citations.count()) > 1) {
    await citations.nth(1).click();
    await expect(page.getByText("证据详情")).toBeVisible();
  }

  // Close the drawer; the document body stays
  await page.getByRole("button", { name: "关闭证据抽屉" }).click();
  await expect(page.getByText("证据详情")).toHaveCount(0);
  await expect(page.getByText("baseline.md")).toBeVisible();
});
```

- [ ] **Step 2: Entity/relation spec**

Create `web/e2e/entity-relation.spec.ts`:
```ts
import { test, expect } from "@playwright/test";

test("entity/relation page lists entities and filters relations on select", async ({ page }) => {
  await page.goto("/kbs/1/documents/1/entities");
  await expect(page.getByText("实体 / 关系")).toBeVisible({ timeout: 15000 });
  // At least one entity card (button with accessible name "查看实体 ... 的关系")
  const firstEntity = page.getByRole("button", { name: /查看实体 .* 的关系/ }).first();
  await expect(firstEntity).toBeVisible();
  await firstEntity.click();
  await expect(page.getByText(/已选择实体：/)).toBeVisible();
});
```

- [ ] **Step 3: Run + adjust**

```bash
cd web && npx playwright test e2e/document-detail.spec.ts e2e/entity-relation.spec.ts --project=chromium
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add web/e2e/document-detail.spec.ts web/e2e/entity-relation.spec.ts
git commit -m "test(e2e): document detail + evidence drawer + entity/relation specs"
```

---

## Task 7: Query spec

**Files:**
- Create: `web/e2e/query.spec.ts`

**Interfaces:**
- Consumes: baseline KB + injected `FakeQueryEngine` (returns `"[{method}] You asked: {query}"`).

- [ ] **Step 1: Write the query spec**

Create `web/e2e/query.spec.ts`:
```ts
import { test, expect } from "@playwright/test";

test("run a local query and see the canned answer", async ({ page }) => {
  await page.goto("/kbs/1/query");
  // pick the local method button
  await page.getByRole("button", { name: /^local$/ }).click();
  await page.getByPlaceholder(/输入你的问题/).fill("hello e2e");
  await page.getByRole("button", { name: "提问" }).click();
  // FakeQueryEngine returns "[local] You asked: hello e2e"
  await expect(page.getByText("[local] You asked: hello e2e")).toBeVisible({ timeout: 15000 });
  await expect(page.getByText("回答")).toBeVisible();
});
```

- [ ] **Step 2: Run + adjust**

```bash
cd web && npx playwright test e2e/query.spec.ts --project=chromium
```
Expected: PASS — the answer card renders the exact canned string from `FakeQueryEngine`.

- [ ] **Step 3: Commit**

```bash
git add web/e2e/query.spec.ts
git commit -m "test(e2e): query spec (FakeQueryEngine canned answer)"
```

---

## Task 8: README docs + full green run

**Files:**
- Modify: `README.md`
- Modify: `README.zh.md`

- [ ] **Step 1: Document E2E in the English README Development section**

In `README.md`, after the existing frontend test line (`cd web && npm install && npm run build && npm test`), add:
```markdown
**E2E (Playwright, optional):** first install Chromium once — `cd web && npm run e2e:install`. Then `npm run e2e` builds the SPA and runs the suite against a no-LLM fake server (`FakeGraphAdapter` worker + injected `FakeQueryEngine`); no provider key required. The fake server can also be run standalone for debugging: `npm run e2e:server` (serves `http://127.0.0.1:18000`).
```

- [ ] **Step 2: Document E2E in the Chinese README Development section**

In `README.zh.md`, after the equivalent frontend test line, add:
```markdown
**E2E(Playwright,可选):** 先装一次 Chromium —— `cd web && npm run e2e:install`,再 `npm run e2e`(构建 SPA 后对一个无 LLM 的假服务器跑用例:`FakeGraphAdapter` worker + 注入 `FakeQueryEngine`,无需 provider key)。也可单独起假服务器调试:`npm run e2e:server`(监听 `http://127.0.0.1:18000`)。
```

- [ ] **Step 3: Run the full E2E suite green**

```bash
cd web && npm run e2e
```
Expected: all specs PASS (Playwright builds the SPA, starts the fake server via `webServer`, runs every `e2e/*.spec.ts`). If any selector drifted, fix it in the spec and re-run.

- [ ] **Step 4: Confirm vitest still green + ruff clean**

```bash
cd web && npm test                       # vitest unaffected
cd .. && uv run ruff check .             # harness is ruff-clean
```
Expected: vitest all green; ruff `All checks passed!`.

- [ ] **Step 5: Commit**

```bash
git add README.md README.zh.md
git commit -m "docs: document Playwright E2E command"
```

---

## Self-Review Notes

- Spec coverage: smoke (✓ Task 3), create KB / paste doc / trigger job (✓ Task 4), graph canvas (✓ Task 5 — the vitest gap), document detail + evidence drawer + entity/relation (✓ Task 6), query (✓ Task 7), navigation (✓ Task 3). All 9 spec rows from the design map to a task.
- Feasibility anchors (verified before writing): `FakeGraphAdapter` runs the full pipeline to `succeeded` (`tests/test_integration_full_pipeline.py`); `create_app(query_engine=)` injects the engine; `run_worker(adapter_factory=, stop_event=, install_signal_handlers=False)` loops in a thread; `Base.metadata.create_all` replaces alembic for the harness (matches test fixtures); `create_engine` sets `check_same_thread=False` + WAL so one repo serves both the uvicorn thread and the worker thread.
- Selector anchors (verified): name input `请输入知识库名称`, create button `创建知识库`, paste textarea `在此粘贴正文内容…`, add button `添加文档`, full-index button `全量索引`, success toast `已创建任务 #N`, query placeholder `输入你的问题…`, query button `提问`, evidence drawer `证据详情`, close button `关闭证据抽屉`, entity button `查看实体 … 的关系`, graph empty hint `先触发一次索引任务…`. E2E specs are characterization tests; selectors may need one round of run-and-adjust at execution time.
```
