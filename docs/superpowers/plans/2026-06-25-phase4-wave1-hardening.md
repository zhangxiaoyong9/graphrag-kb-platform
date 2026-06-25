# Phase 4 — Wave 1 (Hardening) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the correctness/ops layer of Phase 4 — tighten the job-type API, add `/health` + worker graceful shutdown, refactor strategy resolution to dependency injection (no globals), add a `structured_output` toggle so DeepSeek-style providers can produce community reports, and delta-scope summarize/community_reports in incremental jobs.

**Architecture:** All changes extend existing seams (`Repository`, `UnitStepStrategy`, `GraphAdapter`, FastAPI routers). Strategy resolution moves from a module-global `STRATEGIES` dict mutated by the orchestrator to a `dict` injected into `UnitWorker`/`Orchestrator`. Delta strategies inherit their parent strategy and override only `next_units_batch` (+ `finalize`/`persist` for carry-over), reusing the existing `Unit.input_hash` column as the cross-job diff signal. No new tables/columns.

**Tech Stack:** Python 3.11, SQLAlchemy 2 (SQLite), FastAPI, pydantic, pandas, graphrag 3.1 / graphrag-llm (`LLMCompletion.completion_async`).

**Spec:** `docs/superpowers/specs/2026-06-25-phase4-polish-design.md` §4 (Wave 1). Read §4.3 (I), §4.4 (G), §4.5 (H, incl. the 2026-06-25 erratum) before starting.

## Global Constraints

- License header on every new `.py`: `# Copyright (c) 2024 Microsoft Corporation.` + `# Licensed under the MIT License.` (match existing files).
- Lint: `uv run poe check` (ruff preview + pyright) must be clean. Tests relax `S/D/ANN/T201/ASYNC`.
- Enum values are lowercase `StrEnum` (`UnitKind.SUMMARIZE_DESCRIPTIONS == "summarize_descriptions"`, `UnitKind.COMMUNITY_REPORT == "community_report"`).
- `Unit.kind` stores the `UnitKind` value; `Unit.input_hash` is `sha512` hex of the unit's serialized input.
- FK enforcement is ON (`PRAGMA foreign_keys=ON`).
- Existing tests must stay green after every task. Do not edit package `version =`.

## File Structure

**Create:**
- `kb_platform/api/routes_health.py` — `GET /health` (db ping + worker staleness).
- `kb_platform/engine/strategies/delta.py` — `SummarizeDeltaStrategy`, `CommunityReportsDeltaStrategy`.
- `tests/test_api_health.py`, `tests/test_delta_summarize.py`, `tests/test_delta_community_reports.py`.

**Modify:**
- `kb_platform/api/models.py` — `JobCreate.type: Literal[...]`.
- `kb_platform/api/app.py` — mount health router.
- `kb_platform/db/repository.py` — `worker_status`, `last_succeeded_input_hash`, `has_succeeded_input_hash`.
- `kb_platform/engine/strategy.py` — `default_strategies()`.
- `kb_platform/engine/unit_worker.py` — accept injected `strategies` dict (drop global `STRATEGIES` use).
- `kb_platform/engine/orchestrator.py` — `strategies` param; resolve per-step; local extract-delta swap; drop `register_strategy` calls.
- `kb_platform/worker.py` — construct delta strategies for incremental; graceful shutdown.
- `kb_platform/engine/strategies/community_reports.py` — `structured_output` branch + plain-parse helper.
- `kb_platform/graph/adapter.py` — `FakeGraphAdapter.report_community_plain`.
- `kb_platform/graph/graphrag_adapter.py` — store `completion`; `report_community_plain`; wire through `build_default_adapter`.

---

### Task 1: `JobCreate.type` → `Literal` (E)

**Files:**
- Modify: `kb_platform/api/models.py:42-44`
- Test: `tests/test_api_jobs.py`

**Interfaces:**
- Produces: `JobCreate.type: Literal["full", "incremental"]` (FastAPI returns 422 on any other value).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api_jobs.py`:

```python
def test_trigger_job_rejects_bad_type(tmp_path):
    from kb_platform.api.app import create_app
    from kb_platform.db.engine import create_engine
    from kb_platform.db.repository import Repository

    repo = Repository(create_engine(f"sqlite:///{tmp_path}/db.sqlite"))
    from kb_platform.db.models import KnowledgeBase
    from kb_platform.db.engine import session_scope
    with session_scope(repo.engine) as s:
        s.add(KnowledgeBase(name="k", method="standard", settings_json="{}", data_root=str(tmp_path)))
        s.flush()
        kb_id = s.get(KnowledgeBase, 1).id
    from fastapi.testclient import TestClient
    client = TestClient(create_app(repo, data_root=str(tmp_path)))
    r = client.post(f"/kbs/{kb_id}/jobs", json={"type": "bogus"})
    assert r.status_code == 422
    r2 = client.post(f"/kbs/{kb_id}/jobs", json={"type": "incremental"})
    assert r2.status_code == 202
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_api_jobs.py::test_trigger_job_rejects_bad_type -v`
Expected: FAIL — `"bogus"` is currently accepted (`type: str`), so the first assertion fails (status 202, not 422).

- [ ] **Step 3: Write minimal implementation**

In `kb_platform/api/models.py`, change the import and the field:

```python
from typing import Literal
```

```python
class JobCreate(BaseModel):
    method: str = "standard"
    type: Literal["full", "incremental"] = "full"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_api_jobs.py::test_trigger_job_rejects_bad_type -v`
Expected: PASS.

- [ ] **Step 5: Run the full api-jobs suite**

Run: `uv run pytest tests/test_api_jobs.py -q`
Expected: all pass (existing `type="full"` default still valid).

- [ ] **Step 6: Commit**

```bash
git add kb_platform/api/models.py tests/test_api_jobs.py
git commit -m "feat(api): validate JobCreate.type as Literal[full, incremental]"
```

---

### Task 2: `/health` endpoint (F, part 1)

**Files:**
- Modify: `kb_platform/db/repository.py` (add `worker_status`)
- Create: `kb_platform/api/routes_health.py`
- Modify: `kb_platform/api/app.py:17-46` (mount router)
- Test: `tests/test_api_health.py`

**Interfaces:**
- Produces: `Repository.worker_status(stale_seconds: float) -> dict` returning `{"last_heartbeat_at": str | None, "stale": bool}`; `GET /health` returning `{"status", "db", "worker": {...}}`.

- [ ] **Step 1: Write the failing test**

`tests/test_api_health.py`:

```python
from datetime import datetime, timedelta


def _app(tmp_path):
    from kb_platform.api.app import create_app
    from kb_platform.db.engine import create_engine
    from kb_platform.db.repository import Repository
    return Repository(create_engine(f"sqlite:///{tmp_path}/db.sqlite")), create_app(
        Repository(create_engine(f"sqlite:///{tmp_path}/db.sqlite")), data_root=str(tmp_path)
    )


def test_health_ok_no_running_units(tmp_path):
    from fastapi.testclient import TestClient
    repo, app = _app(tmp_path)
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["worker"]["stale"] is False
    assert body["worker"]["last_heartbeat_at"] is None


def test_worker_status_stale(tmp_path):
    from kb_platform.db.repository import Repository
    from kb_platform.db.engine import create_engine, session_scope
    from kb_platform.db.models import Job, Step, Unit
    from kb_platform.db.enums import JobStatus, StepStatus, UnitStatus, UnitKind
    repo = Repository(create_engine(f"sqlite:///{tmp_path}/db.sqlite"))
    with session_scope(repo.engine) as s:
        s.add(Job(id=1, kb_id=1, type="full", status=JobStatus.RUNNING))
        s.add(Step(id=1, job_id=1, name="extract_graph", ordinal=0, kind="unit_fanout", status=StepStatus.RUNNING))
        s.add(Unit(id=1, step_id=1, kind=UnitKind.EXTRACT_GRAPH, subject_type="chunk", subject_id="c1",
                   status=UnitStatus.RUNNING, heartbeat_at=datetime.now() - timedelta(seconds=120)))
        s.flush()
    st = repo.worker_status(stale_seconds=60.0)
    assert st["stale"] is True
    assert st["last_heartbeat_at"] is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_api_health.py -v`
Expected: FAIL — `worker_status` missing / `/health` 404.

- [ ] **Step 3: Add `Repository.worker_status`**

In `kb_platform/db/repository.py`, add (inside `Repository`):

```python
def worker_status(self, stale_seconds: float) -> dict:
    """Newest RUNNING-unit heartbeat, and whether it is stale.

    Returns ``{"last_heartbeat_at": iso | None, "stale": bool}``. No RUNNING
    units -> idle (last_heartbeat_at None, stale False).
    """
    from sqlalchemy import select

    with session_scope(self.engine) as s:
        row = s.scalar(
            select(Unit.heartbeat_at)
            .where(Unit.status == UnitStatus.RUNNING)
            .order_by(Unit.heartbeat_at.desc().nulls_last())
            .limit(1)
        )
    if row is None:
        return {"last_heartbeat_at": None, "stale": False}
    stale = (datetime.now() - row).total_seconds() > stale_seconds
    return {"last_heartbeat_at": row.isoformat(), "stale": stale}
```

Add `from datetime import datetime` to the imports at the top of `repository.py` if not present.

- [ ] **Step 4: Add the route**

`kb_platform/api/routes_health.py`:

```python
# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""Health endpoint: process liveness + DB reachability + worker freshness."""

from fastapi import APIRouter, Request
from sqlalchemy import text

router = APIRouter()


@router.get("/health")
def health(request: Request, stale_seconds: float = 60.0) -> dict:
    repo = request.app.state.repo
    db = "ok"
    try:
        with repo.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db = "down"
    worker = repo.worker_status(stale_seconds)
    status = "ok" if db == "ok" and not worker["stale"] else "degraded"
    return {"status": status, "db": db, "worker": worker}
```

- [ ] **Step 5: Mount the router**

In `kb_platform/api/app.py`, add the import and include before the SPA catch-all:

```python
from kb_platform.api.routes_health import router as health_router
```

After the other `app.include_router(...)` lines:

```python
    app.include_router(health_router)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_api_health.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add kb_platform/db/repository.py kb_platform/api/routes_health.py kb_platform/api/app.py tests/test_api_health.py
git commit -m "feat(api): add GET /health (db ping + worker staleness)"
```

---

### Task 3: Worker graceful shutdown (F, part 2)

**Files:**
- Modify: `kb_platform/worker.py:92-113`
- Test: `tests/test_worker.py`

**Interfaces:**
- Produces: `run_worker(*, repo, adapter_factory, poll_interval, stop_event=None, **kw)` — installs SIGTERM/SIGINT handlers that set `stop_event`; the loop exits when `stop_event` is set, finishing the in-flight `run_worker_once`. Default `stop_event` is created internally when `None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_worker.py`:

```python
def test_run_worker_stops_on_event(tmp_path):
    """When stop_event is set, the loop exits without processing newly-added jobs."""
    import asyncio
    import threading

    from kb_platform.db.engine import create_engine
    from kb_platform.db.repository import Repository
    from kb_platform.graph.adapter import FakeGraphAdapter
    from kb_platform.worker import run_worker

    repo = Repository(create_engine(f"sqlite:///{tmp_path}/db.sqlite"))
    stop = threading.Event()
    # Add a pending job AFTER stop is set so we can prove the loop did not claim it.
    # Seed nothing; just verify the loop returns promptly when stop is already set.
    stop.set()
    adapter_factory = lambda kb: FakeGraphAdapter()
    run_worker(repo=repo, adapter_factory=adapter_factory, poll_interval=0.01, stop_event=stop)
    # If graceful shutdown is broken, run_worker loops forever and this test hangs.
    assert repo.claim_one_pending_job() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_worker.py::test_run_worker_stops_on_event -v --timeout=10`
Expected: FAIL / hang — current `run_worker` loops `while True` and ignores any stop signal.

- [ ] **Step 3: Implement graceful shutdown**

Replace `run_worker` in `kb_platform/worker.py` with:

```python
def run_worker(
    *,
    repo: Repository,
    adapter_factory: Callable[[KnowledgeBase], GraphAdapter],
    poll_interval: float = 2.0,
    stop_event: "threading.Event | None" = None,
    install_signal_handlers: bool = True,
    **kw,
) -> None:
    """Production entry: loop until stopped, recovering + claiming one job per iteration.

    Installs SIGTERM/SIGINT handlers (unless ``install_signal_handlers`` is False)
    that set ``stop_event``. On stop, the in-flight ``run_worker_once`` finishes,
    then the loop returns so the process can exit cleanly. Hard kills (SIGKILL)
    are still recovered on the next start via stale RUNNING -> PENDING reset.
    """
    import signal
    import threading

    if stop_event is None:
        stop_event = threading.Event()

    def _stop(signum, frame):  # noqa: ARG001
        stop_event.set()

    if install_signal_handlers:
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

    while not stop_event.is_set():
        asyncio.run(run_worker_once(repo=repo, adapter_factory=adapter_factory, recover=True, **kw))
        if stop_event.wait(poll_interval):
            break
```

Add `import threading` is not needed at module top (imported locally). The existing `if __name__ == "__main__":` block stays unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_worker.py::test_run_worker_stops_on_event -v --timeout=10`
Expected: PASS (returns promptly).

- [ ] **Step 5: Run the full worker suite**

Run: `uv run pytest tests/test_worker.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add kb_platform/worker.py tests/test_worker.py
git commit -m "feat(worker): graceful shutdown on SIGTERM/SIGINT via stop_event"
```

---

### Task 4: Strategy dependency injection (I) — refactor

Removes the orchestrator's global `register_strategy` mutations; `UnitWorker` resolves the strategy from an injected dict. **Behavior unchanged** (incremental still full-summarize/full-report; extract-delta still applied). Delta summarize/reports land in Task 8.

**Files:**
- Modify: `kb_platform/engine/strategy.py:33-42` (add `default_strategies`)
- Modify: `kb_platform/engine/unit_worker.py:10-31` (inject dict, drop global)
- Modify: `kb_platform/engine/orchestrator.py:14-120` (strategies param + local swap)
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Produces: `default_strategies() -> dict[str, UnitStepStrategy]`; `UnitWorker.__init__(..., strategies: dict[str, UnitStepStrategy])`; `Orchestrator.__init__(..., strategies: dict | None = None)`.

- [ ] **Step 1: Write the failing test (isolation)**

Append to `tests/test_orchestrator.py`:

```python
def test_default_strategies_and_injection_independence():
    """default_strategies() returns the three built-ins; injecting a spy doesn't mutate the base."""
    from kb_platform.engine.strategy import default_strategies
    from kb_platform.engine.strategies.community_reports import CommunityReportsStrategy
    from kb_platform.engine.strategies.extract_graph import ExtractGraphStrategy
    from kb_platform.engine.strategies.summarize_descriptions import SummarizeDescriptionsStrategy

    base = default_strategies()
    assert isinstance(base["extract_graph"], ExtractGraphStrategy)
    assert isinstance(base["summarize_descriptions"], SummarizeDescriptionsStrategy)
    assert isinstance(base["community_reports"], CommunityReportsStrategy)

    class Spy:
        kind = "extract_graph"

    strat = {**base, "extract_graph": Spy()}
    assert isinstance(strat["extract_graph"], Spy)
    assert isinstance(base["extract_graph"], ExtractGraphStrategy)  # base untouched by the override
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_orchestrator.py::test_default_strategies_and_injection_independence -v`
Expected: FAIL — `default_strategies` not defined.

- [ ] **Step 3: Add `default_strategies`**

In `kb_platform/engine/strategy.py`, after `register_strategy`:

```python
def default_strategies() -> dict[str, "UnitStepStrategy"]:
    """The built-in strategy set for a full-index pipeline.

    Constructed afresh each call (no module-global mutation). Tests and the
    incremental wiring override entries by copying this dict.
    """
    from kb_platform.engine.strategies.community_reports import CommunityReportsStrategy
    from kb_platform.engine.strategies.extract_graph import ExtractGraphStrategy
    from kb_platform.engine.strategies.summarize_descriptions import SummarizeDescriptionsStrategy

    return {
        "extract_graph": ExtractGraphStrategy(),
        "summarize_descriptions": SummarizeDescriptionsStrategy(),
        "community_reports": CommunityReportsStrategy(),
    }
```

- [ ] **Step 4: Make `UnitWorker` take the dict**

In `kb_platform/engine/unit_worker.py`:

- Remove the line `import kb_platform.engine.strategies  # noqa: F401,E402`.
- Change the import `from kb_platform.engine.strategy import STRATEGIES, Subject` → `from kb_platform.engine.strategy import Subject`.
- Add `strategies: dict` to `__init__` and store `self.strategies = strategies`.
- In `run_unit_fanout`, change `strategy = STRATEGIES[step.name]` → `strategy = self.strategies[step.name]`.

The new `__init__` signature:

```python
    def __init__(self, *, repo, adapter, data_root, strategies, concurrency=4, worker_id="worker", heartbeat_interval=5.0):
        self.repo = repo
        self.adapter = adapter
        self.data_root = Path(data_root)
        self.strategies = strategies
        self.concurrency = concurrency
        self.worker_id = worker_id
        self.heartbeat_interval = heartbeat_interval
```

- [ ] **Step 5: Make `Orchestrator` own resolution + drop globals**

In `kb_platform/engine/orchestrator.py`:

Add `strategies: dict | None = None` to `__init__` and store `self._base = strategies` (the raw override or `None`). Add two resolver methods + a module helper:

```python
def _resolved_base(strategies) -> dict:
    from kb_platform.engine.strategy import default_strategies

    return strategies if strategies is not None else default_strategies()
```

On the class:

```python
    def _base_strategies(self) -> dict:
        return _resolved_base(self._base)

    def _strategies_for(self, job) -> dict:
        # Task 4: base for both full and incremental. Task 9 swaps delta
        # summarize/community_reports in for incremental jobs.
        return self._base_strategies()
```

Replace the entire UNIT_FANOUT branch of `_run_step` (currently lines ~96-120) with:

```python
        else:
            from kb_platform.engine.unit_worker import UnitWorker

            job = self.repo.get_job(step.job_id)
            strategies = self._strategies_for(job)
            if step.name == "extract_graph" and job.type == "incremental":
                from kb_platform.engine.incremental import ExtractGraphDeltaStrategy, read_delta_manifest

                new_ids = read_delta_manifest(self.data_root)
                strategies = {**strategies, "extract_graph": ExtractGraphDeltaStrategy(new_ids)}
            worker = UnitWorker(
                repo=self.repo,
                adapter=self.adapter,
                data_root=self.data_root,
                concurrency=self.concurrency,
                strategies=strategies,
            )
            await worker.run_unit_fanout(step, min_success_ratio=min_success_ratio)
```

Delete the now-dead `register_strategy` import usages inside `_run_step` (the two `from kb_platform.engine.strategy import register_strategy` branches). The `strategies/__init__.py` global registration can stay (harmless; `default_strategies()` is now authoritative) — leave it untouched to avoid touching unrelated import side-effects.

- [ ] **Step 6: Run the new test + full engine suite**

Run: `uv run pytest tests/test_orchestrator.py tests/test_unit_worker.py tests/test_integration_full_pipeline.py tests/test_incremental_pipeline.py -q`
Expected: PASS (behavior unchanged; existing incremental still full-summarize/full-report, extract-delta applied via the local swap).

- [ ] **Step 7: Lint**

Run: `uv run poe check`
Expected: clean (fix any unused-import ruff flags from the removed `STRATEGIES` import).

- [ ] **Step 8: Commit**

```bash
git add kb_platform/engine/strategy.py kb_platform/engine/unit_worker.py kb_platform/engine/orchestrator.py tests/test_orchestrator.py
git commit -m "refactor(engine): inject strategies into UnitWorker/Orchestrator (drop global mutation)"
```

---

### Task 5: `community_reports.structured_output` toggle (G)

When the KB setting `community_reports.structured_output` is false, the strategy calls a plain-text completion + lenient JSON parse instead of graphrag's structured-output extractor (DeepSeek rejects `response_format=json_schema`).

**Files:**
- Modify: `kb_platform/engine/strategies/community_reports.py:83-91` (branch)
- Modify: `kb_platform/graph/adapter.py:138-153` (Fake plain method)
- Modify: `kb_platform/graph/graphrag_adapter.py:73-90, 218-225` (real plain method + wire `completion`)
- Test: `tests/test_strategy_community_reports.py`

**Interfaces:**
- Produces: `GraphRagAdapter.report_community_plain(context) -> CommunityReport`; `FakeGraphAdapter.report_community_plain`; KB setting read via `_structured_output(repo, step) -> bool`.

- [ ] **Step 1: Write the failing test (parse logic)**

Append to `tests/test_strategy_community_reports.py`:

```python
def test_parse_report_json_extracts_object():
    from kb_platform.graph.graphrag_adapter import _parse_report_json

    text = 'noise before {"title":"T","summary":"S","findings":["a","b"],"rating":7.5} trailing'
    rep = _parse_report_json(text, {"community": "9", "level": 0})
    assert rep.title == "T"
    assert rep.summary == "S"
    assert rep.findings == ["a", "b"]
    assert abs(rep.rank - 0.75) < 1e-9  # rating 0-10 -> 0-1
    assert rep.community == "9"


def test_parse_report_json_fallback_on_garbage():
    from kb_platform.graph.graphrag_adapter import _parse_report_json

    rep = _parse_report_json("not json at all", {"community": "1", "level": 0})
    assert rep.community == "1"
    assert rep.title  # non-empty default
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_strategy_community_reports.py::test_parse_report_json_extracts_object tests/test_strategy_community_reports.py::test_parse_report_json_fallback_on_garbage -v`
Expected: FAIL — `_parse_report_json` not defined.

- [ ] **Step 3: Implement the parser + plain method on `GraphRagAdapter`**

In `kb_platform/graph/graphrag_adapter.py`:

Add `completion` to `__init__`:

```python
    def __init__(self, *, chunker, extractor_factory, entity_types,
                 summarize_factory=None, cluster_fn=None, finalize_fn=None,
                 report_factory=None, embed_factory=None, completion=None):
        # ... existing assignments ...
        self._completion = completion
```

Add the plain report method (after `report_community`):

```python
    async def report_community_plain(self, context: dict) -> CommunityReport:
        """Structured-output-free community report (for providers that reject json_schema).

        Asks the model to return a JSON object, then leniently parses it. Falls
        back to a minimal report so a single bad community cannot fail the step.
        """
        if self._completion is None:
            raise RuntimeError("completion not configured for plain community reports")
        prompt = (
            _format_community_context(context)
            + "\n\nWrite a concise report. Respond with ONLY a JSON object, no prose, "
            'of the shape: {"title": str, "summary": str, "findings": [str], '
            '"rating": <0-10 float>}.'
        )
        resp = await self._completion.completion_async(messages=prompt, response_format=None)
        return _parse_report_json(getattr(resp, "output", "") or "", context)
```

Add the module-level parser near `_format_community_context`:

```python
def _parse_report_json(text: str, context: dict) -> CommunityReport:
    """Best-effort parse of a plain-text JSON report into a CommunityReport.

    Extracts the first ``{...}`` block (regex) so leading/trailing prose is
    tolerated, maps ``rating`` (0-10) -> ``rank`` (0-1). Always returns a report
    (defaults on any parse failure) so one malformed community degrades, not crashes.
    """
    import json
    import re

    data: dict = {}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
        except Exception:  # noqa: BLE001
            data = {}
    title = str(data.get("title") or f"Community {context['community']}")
    summary = str(data.get("summary") or title)
    findings = data.get("findings") or [summary]
    if not isinstance(findings, list):
        findings = [str(findings)]
    try:
        rank = float(data.get("rating", 0.0)) / 10.0
    except Exception:  # noqa: BLE001
        rank = 0.0
    rank = max(0.0, min(1.0, rank))
    return CommunityReport(
        title=title,
        summary=summary,
        findings=[str(f) for f in findings],
        rank=rank,
        full_content=str(data.get("full_content") or summary),
        level=int(context["level"]),
        community=str(context["community"]),
    )
```

Wire `completion` through in `build_default_adapter` — change the `return GraphRagAdapter(...)` call to include `completion=completion`.

- [ ] **Step 4: Write the strategy-branch test**

Append to `tests/test_strategy_community_reports.py`:

```python
def test_strategy_uses_plain_when_setting_false(tmp_path):
    import json as _json
    from kb_platform.db.engine import create_engine, session_scope
    from kb_platform.db.models import KnowledgeBase, Job, Step
    from kb_platform.db.enums import JobStatus, StepStatus
    from kb_platform.db.repository import Repository
    from kb_platform.engine.strategies.community_reports import CommunityReportsStrategy, _structured_output

    repo = Repository(create_engine(f"sqlite:///{tmp_path}/db.sqlite"))
    settings = _json.dumps({"community_reports": {"structured_output": False}})
    with session_scope(repo.engine) as s:
        s.add(KnowledgeBase(id=1, name="k", settings_json=settings, data_root=str(tmp_path)))
        s.add(Job(id=1, kb_id=1, type="full", status=JobStatus.RUNNING))
        s.add(Step(id=1, job_id=1, name="community_reports", ordinal=0, kind="unit_fanout", status=StepStatus.RUNNING))
        s.flush()
        step = s.get(Step, 1)
    assert _structured_output(repo, step) is False


def test_strategy_default_structured_true(tmp_path):
    from kb_platform.db.engine import create_engine, session_scope
    from kb_platform.db.models import KnowledgeBase, Job, Step
    from kb_platform.db.enums import JobStatus, StepStatus
    from kb_platform.db.repository import Repository
    from kb_platform.engine.strategies.community_reports import _structured_output

    repo = Repository(create_engine(f"sqlite:///{tmp_path}/db.sqlite"))
    with session_scope(repo.engine) as s:
        s.add(KnowledgeBase(id=1, name="k", settings_json="{}", data_root=str(tmp_path)))
        s.add(Job(id=1, kb_id=1, type="full", status=JobStatus.RUNNING))
        s.add(Step(id=1, job_id=1, name="community_reports", ordinal=0, kind="unit_fanout", status=StepStatus.RUNNING))
        s.flush()
        step = s.get(Step, 1)
    assert _structured_output(repo, step) is True
```

- [ ] **Step 5: Run to verify it fails**

Run: `uv run pytest tests/test_strategy_community_reports.py -v`
Expected: FAIL — `_structured_output` not defined.

- [ ] **Step 6: Implement the branch in the strategy**

In `kb_platform/engine/strategies/community_reports.py`, add the helper near `_data_root`:

```python
def _structured_output(repo: Repository, step) -> bool:
    """KB setting community_reports.structured_output (default True)."""
    import json

    job = repo.get_job(step.job_id)
    with session_scope(repo.engine) as s:
        kb = s.scalar(select(KnowledgeBase).where(KnowledgeBase.id == job.kb_id))
        settings = json.loads(kb.settings_json or "{}")
    cr = settings.get("community_reports", {}) or {}
    return bool(cr.get("structured_output", True))
```

Change `CommunityReportsStrategy.run_unit` to branch:

```python
    async def run_unit(self, adapter, unit, repo: Repository) -> UnitResult:
        step = repo.get_step(unit.step_id)
        root = _data_root(repo, step)
        ctx = self._context(root, unit.subject_id)
        if _structured_output(repo, step):
            report: CommunityReport = await adapter.report_community(ctx)
        else:
            report = await adapter.report_community_plain(ctx)
        return UnitResult(
            payload=report,
            input_hash=hashlib.sha512(json.dumps(ctx, default=str).encode()).hexdigest(),
            llm_raw_output=report.full_content,
        )
```

- [ ] **Step 7: Add `report_community_plain` to `FakeGraphAdapter`**

In `kb_platform/graph/adapter.py`, after `report_community`:

```python
    async def report_community_plain(self, context: dict) -> CommunityReport:
        return self.report_community_sync(context)
```

- [ ] **Step 8: Run the full community-reports suite**

Run: `uv run pytest tests/test_strategy_community_reports.py tests/test_integration_full_pipeline.py -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add kb_platform/engine/strategies/community_reports.py kb_platform/graph/adapter.py kb_platform/graph/graphrag_adapter.py tests/test_strategy_community_reports.py
git commit -m "feat(reports): structured_output KB toggle w/ plain-text fallback for DeepSeek"
```

---

### Task 6: Repository cross-job `input_hash` lookups (H, part 1)

**Files:**
- Modify: `kb_platform/db/repository.py`
- Test: `tests/test_repository.py`

**Interfaces:**
- Produces: `Repository.last_succeeded_input_hash(kb_id, kind, subject_type, subject_id) -> str | None`; `Repository.has_succeeded_input_hash(kb_id, kind, input_hash) -> bool`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_repository.py`:

```python
def _seed_unit(s, *, uid, step_id, kind, stype, sid, status, input_hash):
    from kb_platform.db.models import Unit
    from kb_platform.db.enums import UnitStatus
    s.add(Unit(id=uid, step_id=step_id, kind=kind, subject_type=stype, subject_id=sid,
               status=status, input_hash=input_hash))


def test_last_succeeded_input_hash(tmp_path):
    from kb_platform.db.engine import create_engine, session_scope
    from kb_platform.db.repository import Repository
    from kb_platform.db.models import KnowledgeBase, Job, Step
    from kb_platform.db.enums import JobStatus, StepStatus, UnitKind, UnitStatus
    repo = Repository(create_engine(f"sqlite:///{tmp_path}/db.sqlite"))
    with session_scope(repo.engine) as s:
        s.add(KnowledgeBase(id=1, name="k", settings_json="{}", data_root=str(tmp_path)))
        s.add(Job(id=1, kb_id=1, type="full", status=JobStatus.SUCCEEDED))
        s.add(Job(id=2, kb_id=1, type="incremental", status=JobStatus.SUCCEEDED))
        s.add(Step(id=10, job_id=1, name="summarize_descriptions", ordinal=0, kind="unit_fanout", status=StepStatus.SUCCEEDED))
        s.add(Step(id=20, job_id=2, name="summarize_descriptions", ordinal=0, kind="unit_fanout", status=StepStatus.SUCCEEDED))
        _seed_unit(s, uid=1, step_id=10, kind=UnitKind.SUMMARIZE_DESCRIPTIONS, stype="entity", sid="E", status=UnitStatus.SUCCEEDED, input_hash="old")
        _seed_unit(s, uid=2, step_id=20, kind=UnitKind.SUMMARIZE_DESCRIPTIONS, stype="entity", sid="E", status=UnitStatus.SUCCEEDED, input_hash="new")
        s.flush()
    # Most recent succeeded wins (higher unit id):
    assert repo.last_succeeded_input_hash(1, "summarize_descriptions", "entity", "E") == "new"
    assert repo.last_succeeded_input_hash(1, "summarize_descriptions", "entity", "MISSING") is None
    assert repo.has_succeeded_input_hash(1, "summarize_descriptions", "old") is True
    assert repo.has_succeeded_input_hash(1, "summarize_descriptions", "never") is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_repository.py::test_last_succeeded_input_hash -v`
Expected: FAIL — methods missing.

- [ ] **Step 3: Implement**

In `kb_platform/db/repository.py`, add (inside `Repository`):

```python
    def last_succeeded_input_hash(self, kb_id: int, kind: str, subject_type: str, subject_id: str) -> str | None:
        """Most recent SUCCEEDED unit input_hash for (kb, kind, subject) across all jobs.

        Delta strategies diff the current input against this to decide whether to
        re-run. Units are per-step/per-job, so the lookup joins through Step->Job.
        """
        with session_scope(self.engine) as s:
            return s.scalar(
                select(Unit.input_hash)
                .join(Step, Unit.step_id == Step.id)
                .join(Job, Step.job_id == Job.id)
                .where(
                    Job.kb_id == kb_id,
                    Unit.kind == kind,
                    Unit.subject_type == subject_type,
                    Unit.subject_id == subject_id,
                    Unit.status == UnitStatus.SUCCEEDED,
                )
                .order_by(Unit.id.desc())
                .limit(1)
            )

    def has_succeeded_input_hash(self, kb_id: int, kind: str, input_hash: str) -> bool:
        """True if any SUCCEEDED unit (kb, kind) recorded this input_hash.

        Used by delta community_reports, where community_id is unstable across
        re-clustering, so matching is by ctx-content hash, not subject_id.
        """
        with session_scope(self.engine) as s:
            row = s.scalar(
                select(Unit.id)
                .join(Step, Unit.step_id == Step.id)
                .join(Job, Step.job_id == Job.id)
                .where(
                    Job.kb_id == kb_id,
                    Unit.kind == kind,
                    Unit.input_hash == input_hash,
                    Unit.status == UnitStatus.SUCCEEDED,
                )
                .limit(1)
            )
        return row is not None
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_repository.py::test_last_succeeded_input_hash -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kb_platform/db/repository.py tests/test_repository.py
git commit -m "feat(repo): cross-job input_hash lookups for delta strategies"
```

---

### Task 7: `SummarizeDeltaStrategy` (H, part 2)

**Files:**
- Create: `kb_platform/engine/strategies/delta.py`
- Test: `tests/test_delta_summarize.py`

**Interfaces:**
- Consumes: `Repository.last_succeeded_input_hash`, `SummarizeDescriptionsStrategy` (`_entities`, `_resolve_data_root`, `_desc_count`, `run_unit`, `persist`).
- Produces: `SummarizeDeltaStrategy` with overridden `next_units_batch` (only changed entities) and `finalize` (carry-over on-disk summaries).

- [ ] **Step 1: Write the failing test**

`tests/test_delta_summarize.py`:

```python
import hashlib
import json


def _entities_parquet(tmp_path, rows):
    import pandas as pd
    pd.DataFrame(rows).to_parquet(tmp_path / "entities.parquet")


def test_delta_summarize_skips_unchanged_emits_changed(tmp_path, monkeypatch):
    """Only entities whose description hash differs from the last succeeded unit are emitted."""
    from kb_platform.db.engine import create_engine, session_scope
    from kb_platform.db.models import KnowledgeBase, Job, Step
    from kb_platform.db.enums import JobStatus, StepStatus
    from kb_platform.db.repository import Repository
    from kb_platform.engine.strategies.delta import SummarizeDeltaStrategy

    repo = Repository(create_engine(f"sqlite:///{tmp_path}/db.sqlite"))
    _entities_parquet(tmp_path, [
        {"title": "A", "type": "X", "description": ["a1", "a2"]},   # 2 descs -> candidate
        {"title": "B", "type": "X", "description": ["b1", "b2"]},   # 2 descs -> candidate
    ])
    with session_scope(repo.engine) as s:
        s.add(KnowledgeBase(id=1, name="k", settings_json="{}", data_root=str(tmp_path)))
        s.add(Job(id=1, kb_id=1, type="incremental", status=JobStatus.RUNNING))
        s.add(Step(id=1, job_id=1, name="summarize_descriptions", ordinal=0, kind="unit_fanout", status=StepStatus.RUNNING))
        s.flush()
        step = s.get(Step, 1)

    # Pretend A was already summarized with its CURRENT description set -> unchanged.
    a_hash = hashlib.sha512(json.dumps(["a1", "a2"]).encode()).hexdigest()
    monkeypatch.setattr(repo, "last_succeeded_input_hash",
                        lambda kb, kind, stype, sid: a_hash if sid == "A" else None)

    strat = SummarizeDeltaStrategy()
    batch = strat.next_units_batch(repo, step)
    subjects = {s.subject_id for s in (batch or [])}
    assert subjects == {"B"}  # A skipped (hash matches), B emitted (no history)


def test_delta_summarize_finalize_carries_over(tmp_path):
    """Finalize merges this job's summaries AND on-disk summaries for unchanged entities."""
    import pandas as pd
    from kb_platform.db.engine import create_engine, session_scope
    from kb_platform.db.models import KnowledgeBase, Job, Step, Unit
    from kb_platform.db.enums import JobStatus, StepStatus, UnitStatus, UnitKind
    from kb_platform.db.repository import Repository
    from kb_platform.engine.strategies.delta import SummarizeDeltaStrategy

    repo = Repository(create_engine(f"sqlite:///{tmp_path}/db.sqlite"))
    _entities_parquet(tmp_path, [
        {"title": "A", "type": "X", "description": ["a1", "a2"]},
        {"title": "B", "type": "X", "description": ["b1", "b2"]},
    ])
    (tmp_path / "summaries").mkdir()
    # B was summarized in a PRIOR job (carry-over, not a unit in this job):
    (tmp_path / "summaries" / "B.json").write_text(json.dumps({"summary": "B carried over"}))
    with session_scope(repo.engine) as s:
        s.add(KnowledgeBase(id=1, name="k", settings_json="{}", data_root=str(tmp_path)))
        s.add(Job(id=1, kb_id=1, type="incremental", status=JobStatus.RUNNING))
        s.add(Step(id=1, job_id=1, name="summarize_descriptions", ordinal=0, kind="unit_fanout", status=StepStatus.RUNNING))
        # This job summarized A only:
        s.add(Unit(step_id=1, kind=UnitKind.SUMMARIZE_DESCRIPTIONS, subject_type="entity", subject_id="A", status=UnitStatus.SUCCEEDED))
        s.flush()
        step = s.get(Step, 1)
    (tmp_path / "summaries" / "A.json").write_text(json.dumps({"summary": "A fresh"}))

    from kb_platform.graph.adapter import FakeGraphAdapter
    status = SummarizeDeltaStrategy().finalize(repo, FakeGraphAdapter(), step, tmp_path, 1.0)
    assert str(status).endswith("succeeded")
    out = pd.read_parquet(tmp_path / "entities.parquet")
    desc = dict(zip(out["title"], out["description"]))
    assert desc["A"] == "A fresh"
    assert desc["B"] == "B carried over"  # carried over from disk despite no unit this job
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_delta_summarize.py -v`
Expected: FAIL — module `delta` missing.

- [ ] **Step 3: Implement `SummarizeDeltaStrategy`**

`kb_platform/engine/strategies/delta.py`:

```python
# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""Delta-scoped strategies for incremental jobs (Phase 4 H).

Each reuses its parent strategy's run_unit/persist and overrides only the unit
selection (+ finalize carry-over), so the incremental cost is proportional to
what changed, not to the whole graph. The diff signal is Unit.input_hash, looked
up across jobs via Repository.last_succeeded_input_hash / has_succeeded_input_hash.
"""

import hashlib
import json

from kb_platform.db.enums import StepStatus, UnitStatus
from kb_platform.engine.strategy import Subject
from kb_platform.engine.strategies.summarize_descriptions import SummarizeDescriptionsStrategy


class SummarizeDeltaStrategy(SummarizeDescriptionsStrategy):
    """Only re-summarize entities whose description set changed since the last success."""

    def next_units_batch(self, repo, step) -> list[Subject] | None:
        data_root = self._resolve_data_root(repo, step)
        ents = self._entities(data_root)
        pending: list[Subject] = []
        for _, row in ents.iterrows():
            if self._desc_count(row["description"]) <= 1:
                continue
            descriptions = [str(d) for d in row["description"]]
            current = hashlib.sha512(json.dumps(descriptions).encode()).hexdigest()
            job = repo.get_job(step.job_id)
            prev = repo.last_succeeded_input_hash(job.kb_id, "summarize_descriptions", "entity", row["title"])
            if prev == current:
                continue  # unchanged -> reuse the on-disk summary (carry-over in finalize)
            from kb_platform.db.enums import UnitStatus

            u = repo.get_unit_by_subject(step.id, "entity", row["title"])
            if u is None or u.status == UnitStatus.PENDING:
                pending.append(Subject("entity", row["title"]))
        return pending or None

    def finalize(self, repo, adapter, step, data_root, min_success_ratio: float) -> StepStatus:
        import pandas as pd

        ents = self._entities(data_root).copy()
        summaries: dict[str, str] = {}
        # Carry-over: every on-disk summary (from prior jobs) for entities in this graph.
        sdir = data_root / "summaries"
        for title in ents["title"]:
            p = sdir / f"{title}.json"
            if p.exists():
                summaries[str(title)] = json.loads(p.read_text())["summary"]
        # min_success_ratio applies to THIS job's units only:
        units = repo.list_units(step.id)
        if units:
            succeeded = [u for u in units if u.status == UnitStatus.SUCCEEDED]
            if len(succeeded) / len(units) < min_success_ratio:
                return StepStatus.PARTIALLY_FAILED

        def _desc(title, current):
            if str(title) in summaries:
                return summaries[str(title)]
            if isinstance(current, str):
                return current
            try:
                values = list(current)
            except TypeError:
                return current
            return values[0] if values else current

        ents["description"] = [_desc(t, c) for t, c in zip(ents["title"], ents["description"])]
        ents.to_parquet(data_root / "entities.parquet")
        return StepStatus.SUCCEEDED
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_delta_summarize.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kb_platform/engine/strategies/delta.py tests/test_delta_summarize.py
git commit -m "feat(incremental): delta-scoped summarize (carry-over finalize)"
```

---

### Task 8: `CommunityReportsDeltaStrategy` (H, part 3)

**Files:**
- Modify: `kb_platform/engine/strategies/delta.py`
- Test: `tests/test_delta_community_reports.py`

**Interfaces:**
- Consumes: `Repository.has_succeeded_input_hash`, `CommunityReportsStrategy` (`_read`, `next_units_batch` level loop, `_context`, `run_unit`).
- Produces: `CommunityReportsDeltaStrategy` — level-by-level, skips communities whose ctx hash already succeeded (this KB); `persist` writes a `reports_by_hash/{hash}.json` sidecar; `finalize` reuses sidecar hits (remapping `community`) else this job's report.

- [ ] **Step 1: Write the failing test**

`tests/test_delta_community_reports.py`:

```python
import hashlib
import json


def test_delta_reports_skips_seen_ctx(tmp_path, monkeypatch):
    from kb_platform.db.engine import create_engine, session_scope
    from kb_platform.db.models import KnowledgeBase, Job, Step
    from kb_platform.db.enums import JobStatus, StepStatus
    from kb_platform.db.repository import Repository
    from kb_platform.engine.strategies.delta import CommunityReportsDeltaStrategy
    import pandas as pd

    repo = Repository(create_engine(f"sqlite:///{tmp_path}/db.sqlite"))
    pd.DataFrame([
        {"level": 0, "community_id": "0", "parent": "0", "entity_ids": ["A", "B"]},
        {"level": 0, "community_id": "1", "parent": "1", "entity_ids": ["C"]},
    ]).to_parquet(tmp_path / "communities.parquet")
    pd.DataFrame([{"title": "A", "description": "a"}, {"title": "B", "description": "b"}, {"title": "C", "description": "c"}]).to_parquet(tmp_path / "entities.parquet")
    pd.DataFrame(columns=["source", "target", "description"]).to_parquet(tmp_path / "relationships.parquet")
    with session_scope(repo.engine) as s:
        s.add(KnowledgeBase(id=1, name="k", settings_json="{}", data_root=str(tmp_path)))
        s.add(Job(id=1, kb_id=1, type="incremental", status=JobStatus.RUNNING))
        s.add(Step(id=1, job_id=1, name="community_reports", ordinal=0, kind="unit_fanout", status=StepStatus.RUNNING))
        s.flush()
        step = s.get(Step, 1)

    strat = CommunityReportsDeltaStrategy()
    # Compute community 0's ctx hash, then pretend it already succeeded:
    ctx0 = strat._context(tmp_path, "0")
    h0 = hashlib.sha512(json.dumps(ctx0, default=str).encode()).hexdigest()
    monkeypatch.setattr(repo, "has_succeeded_input_hash", lambda kb, kind, h: h == h0)

    batch = strat.next_units_batch(repo, step)
    subjects = {s.subject_id for s in (batch or [])}
    assert subjects == {"1"}  # community 0 skipped (ctx seen), community 1 emitted


def test_delta_reports_finalize_reuses_sidecar(tmp_path):
    import pandas as pd
    from kb_platform.db.engine import create_engine, session_scope
    from kb_platform.db.models import KnowledgeBase, Job, Step, Unit
    from kb_platform.db.enums import JobStatus, StepStatus, UnitStatus, UnitKind
    from kb_platform.db.repository import Repository
    from kb_platform.engine.strategies.delta import CommunityReportsDeltaStrategy

    repo = Repository(create_engine(f"sqlite:///{tmp_path}/db.sqlite"))
    pd.DataFrame([{"level": 0, "community_id": "0", "parent": "0", "entity_ids": ["A"]}]).to_parquet(tmp_path / "communities.parquet")
    pd.DataFrame([{"title": "A", "description": "a"}]).to_parquet(tmp_path / "entities.parquet")
    pd.DataFrame(columns=["source", "target", "description"]).to_parquet(tmp_path / "relationships.parquet")
    with session_scope(repo.engine) as s:
        s.add(KnowledgeBase(id=1, name="k", settings_json="{}", data_root=str(tmp_path)))
        s.add(Job(id=1, kb_id=1, type="incremental", status=JobStatus.RUNNING))
        s.add(Step(id=1, job_id=1, name="community_reports", ordinal=0, kind="unit_fanout", status=StepStatus.RUNNING))
        s.flush()
        step = s.get(Step, 1)
    strat = CommunityReportsDeltaStrategy()
    ctx0 = strat._context(tmp_path, "0")
    h0 = hashlib.sha512(json.dumps(ctx0, default=str).encode()).hexdigest()
    # Sidecar: prior report content for this exact ctx:
    (tmp_path / "reports_by_hash").mkdir()
    (tmp_path / "reports_by_hash" / f"{h0}.json").write_text(json.dumps({
        "title": "OLD TITLE", "summary": "OLD", "findings": ["OLD"], "rank": 0.1,
        "full_content": "OLD FULL", "level": 0, "community": "9",
    }))
    # No unit for community 0 this job (it was skipped as seen). finalize must still emit it.
    status = strat.finalize(repo, repo.get_job(1), step, tmp_path, 1.0)
    out = pd.read_parquet(tmp_path / "community_reports.parquet")
    assert "OLD FULL" in list(out["full_content"])
    assert str(out.iloc[0]["community"]) == "0"  # remapped to the NEW community id
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_delta_community_reports.py -v`
Expected: FAIL — `CommunityReportsDeltaStrategy` missing.

- [ ] **Step 3: Implement `CommunityReportsDeltaStrategy`**

Append to `kb_platform/engine/strategies/delta.py`:

```python
from kb_platform.engine.strategies.community_reports import CommunityReportsStrategy


class CommunityReportsDeltaStrategy(CommunityReportsStrategy):
    """Only report communities whose ctx (members + descriptions + sub-reports) is new.

    Community ids are reassigned by Leiden on every re-cluster, so delta matching
    is by ctx-content hash (the same hash run_unit records as input_hash), via
    Repository.has_succeeded_input_hash. A reports_by_hash/ sidecar lets finalize
    reuse a prior report for an unchanged community even when its id changed.
    """

    def _ctx_hash(self, root, comm_id) -> str:
        ctx = self._context(root, comm_id)
        return hashlib.sha512(json.dumps(ctx, default=str).encode()).hexdigest()

    def next_units_batch(self, repo, step) -> list[Subject] | None:
        from kb_platform.db.enums import UnitStatus

        root = _delta_data_root(repo, step)
        comms, _, _ = self._read(root)
        job = repo.get_job(step.job_id)
        for level in sorted(comms["level"].unique(), reverse=True):
            rows = comms[comms["level"] == level]
            pending = []
            for _, row in rows.iterrows():
                cid = row["community_id"]
                if repo.has_succeeded_input_hash(job.kb_id, "community_report", self._ctx_hash(root, cid)):
                    continue  # exact same community context already reported -> reuse
                u = repo.get_unit_by_subject(step.id, "community", cid)
                if u is None or u.status == UnitStatus.PENDING:
                    pending.append(Subject("community", cid))
            if pending:
                return pending
        return None

    def persist(self, data_root, unit, result) -> None:
        super().persist(data_root, unit, result)
        # Sidecar keyed by input_hash so a later incremental job can reuse this
        # report even after Leiden reassigns the community id.
        h = result.input_hash
        if h:
            d = data_root / "reports_by_hash"
            d.mkdir(parents=True, exist_ok=True)
            rep = result.payload
            import json as _json

            (d / f"{h}.json").write_text(_json.dumps({
                "title": rep.title, "summary": rep.summary, "findings": rep.findings,
                "rank": rep.rank, "full_content": rep.full_content,
                "level": rep.level, "community": rep.community,
            }))

    def finalize(self, repo, adapter, step, data_root, min_success_ratio: float) -> StepStatus:
        import pandas as pd

        root = data_root
        comms, _, _ = self._read(root)
        rows = []
        # min_success_ratio over this job's units:
        units = repo.list_units(step.id)
        succeeded = [u for u in units if u.status == UnitStatus.SUCCEEDED] if units else []
        if units and len(succeeded) / len(units) < min_success_ratio:
            return StepStatus.PARTIALLY_FAILED
        this_job = {u.subject_id: u for u in succeeded}
        for _, row in comms.iterrows():
            cid = row["community_id"]
            p = data_root / "reports" / f"{cid}.json"
            if p.exists():
                rows.append(json.loads(p.read_text()))
                continue
            # Carry-over via sidecar (community id changed but ctx identical):
            h = self._ctx_hash(root, cid)
            sp = data_root / "reports_by_hash" / f"{h}.json"
            if sp.exists():
                rec = json.loads(sp.read_text())
                rec["community"] = cid  # remap to the new community id
                rows.append(rec)
        pd.DataFrame(rows).to_parquet(data_root / "community_reports.parquet")
        return StepStatus.SUCCEEDED


def _delta_data_root(repo, step):
    from kb_platform.engine.strategies.community_reports import _data_root

    return _data_root(repo, step)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_delta_community_reports.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kb_platform/engine/strategies/delta.py tests/test_delta_community_reports.py
git commit -m "feat(incremental): delta-scoped community_reports w/ ctx-hash sidecar"
```

---

### Task 9: Wire delta strategies into the incremental pipeline (H, part 4)

Plumb `SummarizeDeltaStrategy` / `CommunityReportsDeltaStrategy` into the orchestrator's incremental path via the injection introduced in Task 4.

**Files:**
- Modify: `kb_platform/engine/orchestrator.py` (`_run_step` UNIT_FANOUT resolution)
- Test: `tests/test_incremental_pipeline.py`

**Interfaces:**
- Consumes: `SummarizeDeltaStrategy`, `CommunityReportsDeltaStrategy` (Task 7/8); `default_strategies` (Task 4).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_incremental_pipeline.py`:

```python
def test_incremental_uses_delta_strategies():
    """Incremental summarize/community_reports resolve to the Delta variants; full does not."""
    from kb_platform.engine import orchestrator as orch_mod
    from kb_platform.engine.strategies.delta import (
        CommunityReportsDeltaStrategy, SummarizeDeltaStrategy,
    )

    o = orch_mod.Orchestrator(repo=object(), adapter=object(), data_root=".", strategies=None)

    class _Inc:
        type = "incremental"

    class _Full:
        type = "full"

    inc = o._strategies_for(_Inc())
    assert isinstance(inc["summarize_descriptions"], SummarizeDeltaStrategy)
    assert isinstance(inc["community_reports"], CommunityReportsDeltaStrategy)
    full = o._strategies_for(_Full())
    assert not isinstance(full["summarize_descriptions"], SummarizeDeltaStrategy)
    assert not isinstance(full["community_reports"], CommunityReportsDeltaStrategy)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_incremental_pipeline.py::test_incremental_uses_delta_strategies -v`
Expected: FAIL — `_strategies_for` (from Task 4) returns the base set for incremental too, so the Delta assertions fail.

- [ ] **Step 3: Implement the wiring**

In `kb_platform/engine/orchestrator.py`, add the module-level `incremental_strategies` helper:

```python
def incremental_strategies(base: dict) -> dict:
    """Base strategy set with summarize/community_reports swapped to Delta variants."""
    from kb_platform.engine.strategies.delta import (
        CommunityReportsDeltaStrategy, SummarizeDeltaStrategy,
    )

    return {
        **base,
        "summarize_descriptions": SummarizeDeltaStrategy(),
        "community_reports": CommunityReportsDeltaStrategy(),
    }
```

Update `Orchestrator._strategies_for` (added in Task 4) to swap for incremental:

```python
    def _strategies_for(self, job) -> dict:
        base = self._base_strategies()
        if getattr(job, "type", "full") == "incremental":
            return incremental_strategies(base)
        return base
```

`_run_step` already resolves via `self._strategies_for(job)` (Task 4), so no change there — the extract-delta local swap continues to sit on top of whatever `_strategies_for` returns.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_incremental_pipeline.py::test_incremental_uses_delta_strategies -v`
Expected: PASS.

- [ ] **Step 5: Update the incremental integration test expectations**

Open `tests/test_incremental_pipeline.py` and find any assertion that incremental summarize/community_reports process ALL entities/communities (the pre-H behavior). Update them to assert the **Delta** behavior: after adding one document, only entities/communities touched by the new document's extraction are (re-)summarized/(re-)reported, and the carry-over leaves `entities.parquet`/`community_reports.parquet` covering the full graph. If a test seeds no prior succeeded units, the first incremental still processes all candidates (no history) — keep those as-is.

Run: `uv run pytest tests/test_incremental_pipeline.py tests/test_integration_e2e.py tests/test_integration_full_pipeline.py -q`
Expected: PASS.

- [ ] **Step 6: Run the whole suite + lint**

Run: `uv run poe test_unit && uv run poe check`
Expected: green + clean.

- [ ] **Step 7: Commit**

```bash
git add kb_platform/engine/orchestrator.py tests/test_incremental_pipeline.py
git commit -m "feat(incremental): wire delta summarize/community_reports strategies"
```

---

### Task 10: Wave-1 integration check + semversioner

**Files:**
- `.changes/<next>/` (semversioner change file)

- [ ] **Step 1: Full unit + integration suite**

Run: `uv run poe test_unit && uv run poe test_integration`
Expected: all green.

- [ ] **Step 2: Static checks**

Run: `uv run poe check`
Expected: clean.

- [ ] **Step 3: Manual delta check (no real LLM)**

Run a FakeGraphAdapter full job, then an incremental job adding one document, and print the unit counts for the summarize and community_reports steps. Confirm the incremental summarize/report unit counts are **strictly less** than the full job's (delta-scoped), while `entities.parquet` and `community_reports.parquet` still cover every entity/community (carry-over). Record the numbers in the commit message.

- [ ] **Step 4: semversioner change file**

Run: `uv run semversioner add-change -t minor -d "Phase 4 Wave 1: JobCreate Literal type, /health + worker graceful shutdown, injected strategy resolution, community_reports structured_output toggle, delta-scoped incremental summarize/community_reports."`

- [ ] **Step 5: Commit**

```bash
git add .changes
git commit -m "chore: semversioner change for phase 4 wave 1"
```

---

## Out of scope for Wave 1 (later waves)

- **A** cost capture/visualization → Wave 2 (wraps the worker `run_unit`; build on Task 4's stable injection).
- **C** document management + markitdown upload, **D** export, **B** graph viz → Wave 3.
- **J** Playwright E2E → cross-cutting, after Wave 3.
- DeepSeek real-LLM smoke of the G plain-report path is a manual acceptance check (acceptance criteria in spec §9), not a CI test.
