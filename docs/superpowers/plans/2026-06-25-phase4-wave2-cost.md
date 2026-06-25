# Phase 4 — Wave 2 (Cost) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture per-unit LLM cost (tokens + estimated USD), aggregate it by step/model, expose it via API, and visualize it in the dashboard with pure-CSS bars.

**Architecture:** A `CostCapturingCompletion` wraps graphrag-llm's `LLMCompletion` (the same wrapper pattern as `LanceDBVectorStoreWrapper`). Every `completion_async` call delegates to the real completion, reads `response.usage`, and appends `{model, prompt_tokens, completion_tokens, cost}` to a unit-scoped `CostRecorder` held in a `contextvars.ContextVar`. The worker's `_process` sets a fresh recorder before `strategy.run_unit`, reads its total after, and writes `result.cost_json`. Because asyncio tasks each get their own context copy, concurrent units are isolated. Cost is computed via graphrag-llm's `model_cost_registry.get_model_costs(model_id)` (`input_cost_per_token`/`output_cost_per_token`); unknown models contribute tokens but `cost=None`. Aggregation is a pure-Python sum over `Unit.cost_json`; the API serves it; the frontend renders CSS bars (no chart library).

**Design note (refinement of spec §5):** the spec said "graphrag-llm middleware + contextvar." graphrag-llm's `with_metrics` middleware reads a per-call `metrics` kwarg that graphrag's extractors do NOT forward, and `create_completion` defaults to `NoopMetricsStore` — so the native metrics path captures nothing through the extractors. A completion wrapper is the robust equivalent (captures every call regardless of caller) and reuses the same `model_cost_registry`.

**Tech Stack:** Python 3.11, contextvars, graphrag-llm (`LLMCompletion.completion_async`, `model_cost_registry`), SQLAlchemy, FastAPI/pydantic, React+TS+Vite.

**Spec:** `docs/superpowers/specs/2026-06-25-phase4-polish-design.md` §5.

## Global Constraints

- License header on every new `.py` (`# Copyright (c) 2024 Microsoft Corporation.` / `# Licensed under the MIT License.`).
- Static check: **ruff** (`uv run ruff check .` clean; `uv run ruff format --check` clean on Wave-2-touched files). kb-platform has **no poethepoet/pyright/semversioner** (graphrag-monorepo conventions) — ignore any plan text referencing them.
- `uv run pytest -q` (backend) and `cd web && npm test && npm run build` (frontend) must be green.
- Cost capture must be **non-fatal**: a provider that omits `usage`, or a model absent from the cost registry, must never crash a unit — it contributes tokens (or nothing) and `cost=None`.
- `FakeGraphAdapter` has no completion → its units keep `cost_json=None`; engine tests stay green.
- Do not edit package `version =`.

## File Structure

**Create:**
- `kb_platform/graph/cost_capture.py` — `CostRecorder`, `CostCapturingCompletion`, the contextvar.
- `kb_platform/api/routes_cost.py` — `GET /kbs/{id}/cost`, `GET /kbs/{id}/jobs/{jid}/cost`.
- `web/src/components/CostPanel.tsx` (+ `.test.tsx`).
- `tests/test_cost_capture.py`, `tests/test_api_cost.py`.

**Modify:**
- `kb_platform/graph/graphrag_adapter.py` — wrap `completion` in `build_default_adapter`.
- `kb_platform/engine/unit_worker.py` — `_process` sets/reads the recorder → `result.cost_json`.
- `kb_platform/db/repository.py` — `job_cost` / `kb_cost`.
- `kb_platform/api/models.py` — `CostItem`, `JobCostOut`, `KbCostOut`.
- `kb_platform/api/app.py` — mount cost router.
- `web/src/api/types.ts` — cost types; `web/src/api/client.ts` — `getJobCost`/`getKbCost`.
- `web/src/pages/JobDetailPage.tsx`, `web/src/pages/KbDetailPage.tsx` — render `CostPanel`.

---

### Task 1: Cost capture core (`CostRecorder` + `CostCapturingCompletion`)

**Files:**
- Create: `kb_platform/graph/cost_capture.py`
- Test: `tests/test_cost_capture.py`

**Interfaces:**
- Produces: `CostRecorder` (accumulates items, `to_json()` → the `cost_json` string); `CostCapturingCompletion(inner, model_id)` exposing `completion_async`/`completion` that delegate + record; module contextvar `current_recorder()` + `use_recorder()`.

- [ ] **Step 1: Write the failing test**

`tests/test_cost_capture.py`:

```python
def test_recorder_accumulates_and_serializes():
    from kb_platform.graph.cost_capture import CostRecorder

    r = CostRecorder()
    r.add(model="deepseek-chat", prompt_tokens=100, completion_tokens=50, cost=0.001)
    r.add(model="deepseek-chat", prompt_tokens=200, completion_tokens=10, cost=0.002)
    js = r.to_json()
    import json
    d = json.loads(js)
    # Same-model adds aggregate into one item; tokens sum; cost sums:
    assert len(d["items"]) == 1
    item = d["items"][0]
    assert item["model"] == "deepseek-chat"
    assert item["prompt_tokens"] == 300
    assert item["completion_tokens"] == 60
    assert abs(item["estimated_cost_usd"] - 0.003) < 1e-9
    assert abs(d["total_usd"] - 0.003) < 1e-9


def test_recorder_unknown_cost_makes_total_none():
    """A model with ANY unknown-cost call -> that model's cost (and the total) is None."""
    from kb_platform.graph.cost_capture import CostRecorder
    import json

    r = CostRecorder()
    r.add(model="mystery-model", prompt_tokens=10, completion_tokens=5, cost=None)
    d = json.loads(r.to_json())
    assert d["total_usd"] is None
    assert d["items"][0]["estimated_cost_usd"] is None
    assert d["items"][0]["prompt_tokens"] == 10  # tokens still recorded


def test_completion_wrapper_captures_usage():
    """A wrapped completion's completion_async records response.usage into the current recorder."""
    import asyncio
    from kb_platform.graph.cost_capture import CostCapturingCompletion, use_recorder

    class FakeUsage:
        prompt_tokens = 120
        completion_tokens = 30

    class FakeResp:
        usage = FakeUsage()
        output = "ok"

    class FakeInner:
        async def completion_async(self, **kw):
            return FakeResp()

    async def main():
        with use_recorder() as rec:
            wrapper = CostCapturingCompletion(FakeInner(), model_id="deepseek-chat")
            resp = await wrapper.completion_async(messages="hi", response_format=None)
            assert resp.output == "ok"  # passthrough unchanged
        # monkeypatch the registry to a known cost so the assertion is deterministic:
        return rec

    rec = asyncio.run(main())
    import json
    d = json.loads(rec.to_json())
    assert d["items"][0]["prompt_tokens"] == 120
    assert d["items"][0]["completion_tokens"] == 30
```

(For the wrapper test, stub `model_cost_registry.get_model_costs` via monkeypatch so the cost is deterministic — see Step 3.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_cost_capture.py -v`
Expected: FAIL — module `kb_platform.graph.cost_capture` missing.

- [ ] **Step 3: Implement**

`kb_platform/graph/cost_capture.py`:

```python
# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""Per-unit LLM cost capture (Phase 4 Wave 2).

A ``CostCapturingCompletion`` wraps graphrag-llm's ``LLMCompletion`` so every
``completion_async`` call records ``response.usage`` into a unit-scoped
``CostRecorder`` held in a contextvar. The worker sets a fresh recorder per
unit (asyncio tasks each get their own context copy, so concurrent units are
isolated), then reads ``recorder.to_json()`` into ``Unit.cost_json``.

Cost is computed via graphrag-llm's ``model_cost_registry``; a model absent
from the registry contributes tokens with ``estimated_cost_usd=None`` (never
raises).
"""

from __future__ import annotations

import contextvars
import json
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass
class _Accum:
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float | None = 0.0


@dataclass
class CostRecorder:
    """Accumulates per-model token + cost totals for one unit."""

    _by_model: dict[str, _Accum] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self._by_model)

    def add(self, *, model: str, prompt_tokens: int, completion_tokens: int, cost: float | None) -> None:
        a = self._by_model.setdefault(model, _Accum(model=model))
        a.prompt_tokens += prompt_tokens
        a.completion_tokens += completion_tokens
        if cost is None:
            a.estimated_cost_usd = None
        elif a.estimated_cost_usd is not None:
            a.estimated_cost_usd += cost

    def to_json(self) -> str:
        items = []
        total = 0.0
        known = True
        for a in self._by_model.values():
            items.append({
                "model": a.model,
                "prompt_tokens": a.prompt_tokens,
                "completion_tokens": a.completion_tokens,
                "estimated_cost_usd": a.estimated_cost_usd,
            })
            if a.estimated_cost_usd is None:
                known = False
            else:
                total += a.estimated_cost_usd
        return json.dumps({"items": items, "total_usd": total if known else None})


_recorder_var: contextvars.ContextVar[CostRecorder | None] = contextvars.ContextVar("cost_recorder", default=None)


def current_recorder() -> CostRecorder | None:
    return _recorder_var.get()


@contextmanager
def use_recorder():
    rec = CostRecorder()
    token = _recorder_var.set(rec)
    try:
        yield rec
    finally:
        _recorder_var.reset(token)


def _compute_cost(model_id: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    try:
        from graphrag_llm.model_cost_registry import model_cost_registry

        costs = model_cost_registry.get_model_costs(model_id)
    except Exception:  # noqa: BLE001
        return None
    if not costs:
        return None
    try:
        return (
            prompt_tokens * float(costs.get("input_cost_per_token", 0))
            + completion_tokens * float(costs.get("output_cost_per_token", 0))
        )
    except Exception:  # noqa: BLE001
        return None


class CostCapturingCompletion:
    """Delegates completion calls to ``inner`` and records usage into the current recorder."""

    def __init__(self, inner, *, model_id: str) -> None:
        self._inner = inner
        self._model_id = model_id

    def _record(self, response) -> None:
        rec = current_recorder()
        if rec is None:
            return
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        pt = int(getattr(usage, "prompt_tokens", 0) or 0)
        ct = int(getattr(usage, "completion_tokens", 0) or 0)
        rec.add(model=self._model_id, prompt_tokens=pt, completion_tokens=ct, cost=_compute_cost(self._model_id, pt, ct))

    async def completion_async(self, **kwargs):
        resp = await self._inner.completion_async(**kwargs)
        self._record(resp)
        return resp

    def completion(self, **kwargs):
        resp = self._inner.completion(**kwargs)
        self._record(resp)
        return resp

    def __getattr__(self, name):  # proxy anything else (e.g. model_id) to the inner
        return getattr(self._inner, name)
```

For the wrapper test, monkeypatch `_compute_cost` (or `model_cost_registry.get_model_costs`) to return a fixed per-token rate so cost is deterministic; assert tokens primarily (cost depends on the live registry).

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_cost_capture.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kb_platform/graph/cost_capture.py tests/test_cost_capture.py
git commit -m "feat(cost): CostRecorder + CostCapturingCompletion (per-unit usage capture)"
```

---

### Task 2: Wire capture into adapter + worker

**Files:**
- Modify: `kb_platform/graph/graphrag_adapter.py` (`build_default_adapter` wraps `completion`)
- Modify: `kb_platform/engine/unit_worker.py:78-90` (`_process` uses `use_recorder`, writes `result.cost_json`)
- Test: `tests/test_unit_worker.py` (add a cost-capture integration test with a fake completion)

**Interfaces:**
- Consumes: `CostCapturingCompletion`, `use_recorder` (Task 1).
- Produces: `result.cost_json` populated from the recorder when LLM calls occurred.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_unit_worker.py`:

```python
async def test_unit_worker_records_cost_from_completion():
    """A strategy whose run_unit calls a CostCapturingCompletion yields a cost_json on the unit."""
    from kb_platform.graph.cost_capture import CostCapturingCompletion

    class FakeUsage:
        prompt_tokens = 10
        completion_tokens = 5

    class FakeResp:
        usage = FakeUsage()

    class FakeCompletion:
        async def completion_async(self, **kw):
            return FakeResp()

    captured = {}

    class CostStrategy:
        kind = "extract_graph"
        def next_units_batch(self, repo, step):
            return None
        async def run_unit(self, adapter, unit, repo):
            # Simulate a strategy that calls the wrapped completion:
            await adapter.completion.completion_async(messages="x")
            from kb_platform.engine.strategy import UnitResult
            return UnitResult(payload=None)
        def persist(self, data_root, unit, result):
            return None
        def finalize(self, repo, adapter, step, data_root, min_success_ratio):
            from kb_platform.db.enums import StepStatus
            return StepStatus.SUCCEEDED

    # adapter holds the wrapped completion:
    from kb_platform.graph.cost_capture import CostCapturingCompletion
    adapter = type("A", (), {"completion": CostCapturingCompletion(FakeCompletion(), model_id="gpt-4o-mini")})()
    # run _process on one unit and assert cost_json was written (tokens recorded):
    # (build a minimal repo/unit fixture; assert the unit's cost_json is non-null and parses)
    ...
```

(Complete the fixture: create an engine + KB + Job + Step + Unit with `kind="extract_graph"`, run `await UnitWorker(...).run_unit_fanout(step, 1.0)` with the cost strategy, then assert `repo.list_units(step.id)[0].cost_json` parses with `prompt_tokens==10`, `completion_tokens==5`.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_unit_worker.py::test_unit_worker_records_cost_from_completion -v`
Expected: FAIL — `cost_json` is None (recorder not wired).

- [ ] **Step 3: Wire the worker**

In `kb_platform/engine/unit_worker.py`, change `_process`:

```python
    async def _process(self, strategy, unit) -> None:
        from kb_platform.graph.cost_capture import use_recorder

        try:
            with use_recorder() as rec:
                result = await strategy.run_unit(self.adapter, unit, self.repo)
            if result.cost_json is None and rec:
                result.cost_json = rec.to_json()
            strategy.persist(self.data_root, unit, result)
            self.repo.set_unit_succeeded(
                unit.id,
                input_hash=result.input_hash,
                cost_json=result.cost_json,
                llm_raw_output=result.llm_raw_output,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("unit %s failed: %s", unit.id, e)
            self.repo.set_unit_failed(unit.id, str(e))
```

(`CostRecorder.__bool__` from Task 1 gates this — empty recorder under `FakeGraphAdapter` leaves `cost_json=None`. If a strategy ever sets `cost_json` itself, it wins.)

- [ ] **Step 4: Wire the adapter**

In `kb_platform/graph/graphrag_adapter.py` `build_default_adapter`, after `completion = create_completion(model_config)`, wrap it before passing to the factories:

```python
    from kb_platform.graph.cost_capture import CostCapturingCompletion

    model_id = model_config.model
    completion = CostCapturingCompletion(completion, model_id=model_id)
```

All factory closures (`extractor_factory`, `summarize_factory`, `report_factory`) now capture the wrapped `completion`. `build_adapter_from_settings` is unchanged (it calls `build_default_adapter`).

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_unit_worker.py -q`
Expected: PASS.

- [ ] **Step 6: Run the full backend suite**

Run: `uv run pytest -q`
Expected: all green (FakeGraphAdapter units keep cost_json=None — `rec.items` is empty).

- [ ] **Step 7: Commit**

```bash
git add kb_platform/graph/graphrag_adapter.py kb_platform/engine/unit_worker.py tests/test_unit_worker.py
git commit -m "feat(cost): wire CostCapturingCompletion + per-unit recorder into the run path"
```

---

### Task 3: Aggregation (`Repository.job_cost` / `kb_cost`)

**Files:**
- Modify: `kb_platform/db/repository.py`
- Test: `tests/test_repository.py`

**Interfaces:**
- Produces: `Repository.job_cost(job_id) -> dict` (`{total_usd, by_step:{name:usd}, by_model:{model:{prompt_tokens, completion_tokens, usd}}}`); `Repository.kb_cost(kb_id) -> dict` (same shape + `by_job:{job_id:usd}`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_repository.py`:

```python
def test_job_cost_aggregates_by_step_and_model(tmp_path):
    import json
    from kb_platform.db.engine import create_engine, session_scope
    from kb_platform.db.models import KnowledgeBase, Job, Step, Unit
    from kb_platform.db.enums import JobStatus, StepStatus, UnitStatus, UnitKind
    from kb_platform.db.repository import Repository

    repo = Repository(create_engine(f"sqlite:///{tmp_path}/db.sqlite"))
    with session_scope(repo.engine) as s:
        s.add(KnowledgeBase(id=1, name="k", settings_json="{}", data_root=str(tmp_path)))
        s.add(Job(id=1, kb_id=1, type="full", status=JobStatus.SUCCEEDED))
        s.add(Step(id=10, job_id=1, name="extract_graph", ordinal=0, kind="unit_fanout", status=StepStatus.SUCCEEDED))
        s.add(Step(id=11, job_id=1, name="summarize_descriptions", ordinal=1, kind="unit_fanout", status=StepStatus.SUCCEEDED))
        s.flush()
        s.add(Unit(step_id=10, kind=UnitKind.EXTRACT_GRAPH, subject_type="chunk", subject_id="c1", status=UnitStatus.SUCCEEDED,
                   cost_json=json.dumps({"items":[{"model":"deepseek-chat","prompt_tokens":100,"completion_tokens":20,"estimated_cost_usd":0.01}],"total_usd":0.01})))
        s.add(Unit(step_id=11, kind=UnitKind.SUMMARIZE_DESCRIPTIONS, subject_type="entity", subject_id="E", status=UnitStatus.SUCCEEDED,
                   cost_json=json.dumps({"items":[{"model":"deepseek-chat","prompt_tokens":40,"completion_tokens":10,"estimated_cost_usd":0.004}],"total_usd":0.004})))
        s.add(Unit(step_id=11, kind=UnitKind.SUMMARIZE_DESCRIPTIONS, subject_type="entity", subject_id="F", status=UnitStatus.SUCCEEDED, cost_json=None))
        s.flush()
    out = repo.job_cost(1)
    assert out["total_usd"] == 0.014
    assert out["by_step"]["extract_graph"] == 0.01
    assert out["by_step"]["summarize_descriptions"] == 0.004
    assert out["by_model"]["deepseek-chat"]["prompt_tokens"] == 140
    assert out["by_model"]["deepseek-chat"]["usd"] == 0.014


def test_kb_cost_aggregates_across_jobs(tmp_path):
    # seed two jobs under one KB; assert kb_cost totals both and by_job breaks them out.
    ...
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_repository.py::test_job_cost_aggregates_by_step_and_model -v`
Expected: FAIL — `job_cost` missing.

- [ ] **Step 3: Implement**

In `kb_platform/db/repository.py`, add a private helper + two methods:

```python
    @staticmethod
    def _sum_cost(rows):
        """rows: iterable of (step_name, cost_json_str). Returns the aggregate dict."""
        import json

        total = 0.0
        known = True
        by_step: dict[str, float] = {}
        by_model: dict[str, dict] = {}
        for step_name, cj in rows:
            if not cj:
                continue
            try:
                data = json.loads(cj)
            except Exception:  # noqa: BLE001
                continue
            if data.get("total_usd") is None:
                known = False
            else:
                total += float(data["total_usd"])
                by_step[step_name] = by_step.get(step_name, 0.0) + float(data["total_usd"])
            for it in data.get("items", []):
                m = it.get("model", "?")
                slot = by_model.setdefault(m, {"prompt_tokens": 0, "completion_tokens": 0, "usd": 0.0, "known": True})
                slot["prompt_tokens"] += int(it.get("prompt_tokens", 0) or 0)
                slot["completion_tokens"] += int(it.get("completion_tokens", 0) or 0)
                if it.get("estimated_cost_usd") is None:
                    slot["known"] = False
                else:
                    slot["usd"] += float(it["estimated_cost_usd"])
        for slot in by_model.values():
            if not slot.pop("known", True):
                slot["usd"] = None
        return {"total_usd": total if known else None, "by_step": by_step, "by_model": by_model}

    def job_cost(self, job_id: int) -> dict:
        with session_scope(self.engine) as s:
            rows = s.execute(
                select(Step.name, Unit.cost_json)
                .join(Unit, Unit.step_id == Step.id)
                .where(Step.job_id == job_id, Unit.status == UnitStatus.SUCCEEDED)
            ).all()
        return self._sum_cost(rows)

    def kb_cost(self, kb_id: int) -> dict:
        with session_scope(self.engine) as s:
            rows = s.execute(
                select(Job.id, Step.name, Unit.cost_json)
                .join(Step, Step.job_id == Job.id)
                .join(Unit, Unit.step_id == Step.id)
                .where(Job.kb_id == kb_id, Unit.status == UnitStatus.SUCCEEDED)
            ).all()
        # aggregate overall + per-job
        by_job: dict[int, float] = {}
        overall_rows = []
        for job_id, step_name, cj in rows:
            overall_rows.append((step_name, cj))
            try:
                import json
                d = json.loads(cj) if cj else {}
                v = d.get("total_usd")
                if v is not None:
                    by_job[job_id] = by_job.get(job_id, 0.0) + float(v)
            except Exception:  # noqa: BLE001
                pass
        out = self._sum_cost(overall_rows)
        out["by_job"] = by_job
        return out
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_repository.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kb_platform/db/repository.py tests/test_repository.py
git commit -m "feat(cost): Repository.job_cost/kb_cost aggregation (by step/model/job)"
```

---

### Task 4: Cost API

**Files:**
- Modify: `kb_platform/api/models.py` (`CostItem`, `JobCostOut`, `KbCostOut`)
- Create: `kb_platform/api/routes_cost.py`
- Modify: `kb_platform/api/app.py` (mount router)
- Test: `tests/test_api_cost.py`

**Interfaces:**
- Produces: `GET /kbs/{id}/jobs/{jid}/cost` → `JobCostOut`; `GET /kbs/{id}/cost` → `KbCostOut`.

- [ ] **Step 1: Write the failing test**

`tests/test_api_cost.py` — seed a KB + job + units with `cost_json` (reuse the Task 3 fixture shape), then:

```python
def test_get_job_cost(tmp_path):
    # ... build repo + app (Base.metadata.create_all), seed cost_json units ...
    r = TestClient(app).get(f"/kbs/{kb_id}/jobs/{job_id}/cost")
    assert r.status_code == 200
    body = r.json()
    assert body["total_usd"] == 0.014
    assert body["by_step"]["extract_graph"] == 0.01


def test_get_kb_cost(tmp_path):
    # ... assert kb_cost endpoint returns total + by_job ...
    ...
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_api_cost.py -v`
Expected: FAIL — routes 404.

- [ ] **Step 3: Add models + route**

In `kb_platform/api/models.py`:

```python
class CostItem(BaseModel):
    model: str
    prompt_tokens: int
    completion_tokens: int
    usd: float | None


class JobCostOut(BaseModel):
    total_usd: float | None
    by_step: dict[str, float]
    by_model: dict[str, CostItem]


class KbCostOut(JobCostOut):
    by_job: dict[int, float]
```

`kb_platform/api/routes_cost.py`:

```python
# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""Cost aggregation endpoints."""

from fastapi import APIRouter, HTTPException, Request

from kb_platform.api.models import JobCostOut, KbCostOut

router = APIRouter()


@router.get("/kbs/{kb_id}/jobs/{job_id}/cost", response_model=JobCostOut)
def job_cost(kb_id: int, job_id: int, request: Request) -> JobCostOut:
    repo = request.app.state.repo
    return JobCostOut(**repo.job_cost(job_id))


@router.get("/kbs/{kb_id}/cost", response_model=KbCostOut)
def kb_cost(kb_id: int, request: Request) -> KbCostOut:
    repo = request.app.state.repo
    return KbCostOut(**repo.kb_cost(kb_id))
```

Mount in `kb_platform/api/app.py`: `from kb_platform.api.routes_cost import router as cost_router` + `app.include_router(cost_router)` (before the SPA catch-all).

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_api_cost.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kb_platform/api/models.py kb_platform/api/routes_cost.py kb_platform/api/app.py tests/test_api_cost.py
git commit -m "feat(api): GET /kbs/{id}/cost + /jobs/{jid}/cost"
```

---

### Task 5: Frontend `CostPanel`

**Files:**
- Modify: `web/src/api/types.ts`, `web/src/api/client.ts`
- Create: `web/src/components/CostPanel.tsx`, `web/src/components/CostPanel.test.tsx`
- Modify: `web/src/pages/JobDetailPage.tsx`, `web/src/pages/KbDetailPage.tsx`

**Interfaces:**
- Produces: `CostPanel({ totalUsd, byStep })` rendering total + per-step CSS bars; `getJobCost(jobId)`, `getKbCost(kbId)`.

- [ ] **Step 1: Add types + client**

`web/src/api/types.ts`:

```typescript
export interface CostItem { model: string; prompt_tokens: number; completion_tokens: number; usd: number | null }
export interface JobCost { total_usd: number | null; by_step: Record<string, number>; by_model: Record<string, CostItem> }
export interface KbCost extends JobCost { by_job: Record<string, number> }
```

`web/src/api/client.ts`:

```typescript
export const getJobCost = (jobId: number) => req<JobCost>(`/kbs/0/jobs/${jobId}/cost`);
```

(The kb_id segment is unused by the route beyond path matching; if that's awkward, add a `getJobCost(jobId)` that the page calls with the known kb_id — prefer threading the real kb_id: `getJobCost(kbId, jobId)`.) Resolve to `getJobCost(kbId: number, jobId: number)` and `getKbCost(kbId: number)`.

- [ ] **Step 2: Write the failing test**

`web/src/components/CostPanel.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { CostPanel } from "./CostPanel";

test("renders total and per-step bars", () => {
  render(<CostPanel totalUsd={0.014} byStep={{ extract_graph: 0.01, summarize_descriptions: 0.004 }} />);
  expect(screen.getByText(/\$0\.014/)).toBeInTheDocument();
  expect(screen.getByText(/extract_graph/)).toBeInTheDocument();
});

test("renders em-dash when cost unknown", () => {
  render(<CostPanel totalUsd={null} byStep={{}} />);
  expect(screen.getByText(/—/)).toBeInTheDocument();
});
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd web && npm test -- CostPanel`
Expected: FAIL — component missing.

- [ ] **Step 4: Implement `CostPanel`**

`web/src/components/CostPanel.tsx:

```tsx
interface Props {
  totalUsd: number | null;
  byStep: Record<string, number>;
}

export function CostPanel({ totalUsd, byStep }: Props) {
  const entries = Object.entries(byStep).sort((a, b) => b[1] - a[1]);
  const max = entries.length ? Math.max(...entries.map(([, v]) => v)) : 0;
  return (
    <div className="cost-panel">
      <h4>Cost</h4>
      <div className="cost-total">{totalUsd == null ? "—" : `$${totalUsd.toFixed(4)}`}</div>
      {entries.map(([step, usd]) => (
        <div key={step} className="cost-row">
          <span className="cost-step">{step}</span>
          <span className="cost-bar" style={{ width: `${max ? (usd / max) * 100 : 0}%` }} />
          <span className="cost-val">${usd.toFixed(4)}</span>
        </div>
      ))}
    </div>
  );
}
```

(Add minimal `.cost-bar`/`.cost-row` styles to `index.css` if Tailwind isn't used for this; match the existing styling approach in `JobDetailPage`.)

- [ ] **Step 5: Wire into pages**

- `JobDetailPage.tsx`: fetch `getJobCost(kbId, jobId)` (alongside the job poll) and render `<CostPanel totalUsd={cost.total_usd} byStep={cost.by_step} />`.
- `KbDetailPage.tsx`: fetch `getKbCost(kbId)` once on mount; render `<CostPanel totalUsd={cost.total_usd} byStep={cost.by_step} />` (cumulative). (kb cumulative uses the same by-step view; `by_job` can be a later refinement.)

- [ ] **Step 6: Run frontend tests + build**

Run: `cd web && npm test && npm run build`
Expected: PASS + build clean.

- [ ] **Step 7: Commit**

```bash
git add web/src/api/types.ts web/src/api/client.ts web/src/components/CostPanel.tsx web/src/components/CostPanel.test.tsx web/src/pages/JobDetailPage.tsx web/src/pages/KbDetailPage.tsx web/src/index.css
git commit -m "feat(web): CostPanel (per-step CSS bars) on job + kb detail pages"
```

---

### Task 6: Integration gate

**Files:** none (verification + a SPA-served check if FastAPI serves the rebuilt bundle).

- [ ] **Step 1: Full backend suite**

Run: `uv run pytest -q`
Expected: all green.

- [ ] **Step 2: Ruff**

Run: `uv run ruff check . && uv run ruff format --check $(git diff --name-only ce78641..HEAD | grep -E '\.(py|ts|tsx)$')`
Expected: lint clean; Wave-2-touched files format-clean.

- [ ] **Step 3: Frontend**

Run: `cd web && npm test && npm run build`
Expected: green + build clean.

- [ ] **Step 4: Manual sanity (no real LLM)**

Confirm the cost path is wired but idle under `FakeGraphAdapter` (units have `cost_json=None`; `/cost` returns `total_usd=0`, empty `by_step`). Real-LLM cost capture is a manual acceptance smoke (DeepSeek/OpenAI) — note the provider+observed numbers in the commit if run.

- [ ] **Step 5: Commit any cleanup**

```bash
git commit --allow-empty -m "chore(wave2): integration gate green"
```
(Skip if nothing to commit.)

---

## Out of scope for Wave 2

- Real-LLM cost smoke (acceptance criteria, manual).
- Per-job cost breakdown in the KB panel (`by_job` returned by API but UI shows cumulative only — refine later).
- Wave 3 (C/D/B) and J (Playwright E2E).
