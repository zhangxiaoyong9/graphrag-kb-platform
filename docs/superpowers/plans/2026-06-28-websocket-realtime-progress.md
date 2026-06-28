# WebSocket 实时进度推送 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 job 详情页通过 WebSocket 接收 step 状态 + 各 step unit 进度的实时推送,替换浏览器轮询(轮询保留为兜底)。

**Architecture:** server 端一个全局 `RealtimeHub`,其 asyncio poller 每 ~500ms 轮询有订阅者的 job,与上一帧 diff,有变化才经 WS 推给浏览器;worker 零改动,不引入新组件。WS 事件直接复用 `StepOut` 形状(带 `progress`),使前端 `useJobEvents.data` 即 `JobOut`。

**Tech Stack:** FastAPI WebSocket、Starlette `WebSocketDisconnect`、FastAPI `lifespan`、`asyncio`、React hook、vitest(mock WebSocket)、`fastapi.testclient.TestClient.websocket_connect`。

## Global Constraints

- 轮询周期读 `KB_POLL_INTERVAL_MS` 环境变量(默认 `500`,单位毫秒),lifespan 启动时读取一次;`RealtimeHub(interval=…)` 单位为**秒**。
- 无新增 DB 字段、无新增 alembic 迁移、无新增 Python/npm 依赖。
- Python ≥ 3.11,`ruff check .` 通过(line-length 100);前端 `npm run build` 与 `npm test` 通过。
- WS 是增强,不是依赖:`useJobEvents` 不可用时 `JobDetailPage` 必须能靠现有 `useJobPolling`(REST)正常工作。
- worker(`kb_platform/worker.py`)不得改动。
- 遵循现有代码风格;新增 UI 文案用中文,与现有页面一致。

---

## File Structure

- **Create** `kb_platform/api/realtime.py` — `JobBroadcaster`(单 job 的订阅者 + diff)、`RealtimeHub`(registry + poll 循环)、`_step_dict`(复用 `StepOut`/`UnitProgress` 形状)。纯逻辑 + asyncio,无 FastAPI 依赖(除 `WebSocket` 类型注解)。
- **Create** `kb_platform/api/routes_realtime.py` — `@router.websocket("/jobs/{job_id}/events")`,从 `app.state.realtime` 取 hub。
- **Modify** `kb_platform/api/app.py` — 给 `create_app` 加 `lifespan`(startup 建 hub + `start()`、shutdown `stop()`),注册 realtime router。
- **Create** `tests/test_realtime.py` — broadcaster/hub 单测。
- **Modify** `tests/test_api_realtime.py`(新建)— WS 端点集成测试(用 `with TestClient(app)` 触发 lifespan)。
- **Create** `web/src/hooks/useJobEvents.ts` — WS 订阅 hook,返回 `{ connected, data }`,`data` 为 `JobOut | null`。
- **Create** `web/src/hooks/useJobEvents.test.tsx` — vitest(mock `WebSocket`)。
- **Modify** `web/src/pages/JobDetailPage.tsx` — 叠加 `useJobEvents`,`current = (live.connected && live.data) ? live.data : job`。
- **Modify** `web/vite.config.ts` — `/jobs` 代理改为 `{ target, ws: true }`,dev 支持 WS。
- **Create** `docs/verify-websocket-2026-06-28.md` — 手动冒烟验证记录。

---

## Task 1: `JobBroadcaster`(snapshot / diff / broadcast)

**Files:**
- Create: `kb_platform/api/realtime.py`
- Test: `tests/test_realtime.py`

**Interfaces:**
- Consumes: `Repository.get_job(job_id)->Job|None`、`Repository.get_steps(job_id)->list[Step]`、`Repository.unit_counts_by_status(step_id)->dict`(已存在);`kb_platform.api.models.StepOut`、`UnitProgress`(已存在)。
- Produces: `JobBroadcaster(job_id, repo)`;`.snapshot()->dict`;`.diff_and_emit()->dict|None`;`async .broadcast(event)->None`;属性 `.subscribers:set`。

- [ ] **Step 1: Write the failing tests** — append to new `tests/test_realtime.py`:

```python
"""Tests for kb_platform.api.realtime (broadcaster/hub), no real WS."""
import asyncio

import pytest

from kb_platform.db.engine import create_engine
from kb_platform.db.enums import StepStatus
from kb_platform.db.models import Base
from kb_platform.db.repository import Repository


@pytest.fixture()
def repo(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    return Repository(engine)


def _seed_job(repo):
    from kb_platform.db.models import KnowledgeBase
    from kb_platform.db.engine import session_scope

    with session_scope(repo.engine) as s:
        kb = KnowledgeBase(name="k", method="standard", settings_json="{}", data_root=".")
        s.add(kb)
    from kb_platform.engine.spec import StepSpec
    from kb_platform.db.enums import StepKind

    job = repo.create_job_pending(kb_id=1, method="standard", type="full")
    return job.id


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, obj):
        self.sent.append(obj)


def test_snapshot_returns_current_steps_and_progress(repo):
    from kb_platform.api.realtime import JobBroadcaster

    job_id = _seed_job(repo)
    steps = repo.get_steps(job_id)
    extract = [s for s in steps if s.name == "extract_graph"][0]
    repo.add_units(extract.id, [("chunk", "c1"), ("chunk", "c2")], kind="extract_graph")

    bc = JobBroadcaster(job_id=job_id, repo=repo)
    snap = bc.snapshot()
    assert snap["type"] == "snapshot"
    assert snap["job"]["status"] == "pending"
    ex = [s for s in snap["steps"] if s["name"] == "extract_graph"][0]
    assert ex["progress"]["total"] == 2 and ex["progress"]["pending"] == 2


def test_diff_emits_only_changed_steps(repo):
    from kb_platform.api.realtime import JobBroadcaster

    job_id = _seed_job(repo)
    bc = JobBroadcaster(job_id=job_id, repo=repo)
    bc.snapshot()  # establish baseline frame
    # No change -> None
    assert bc.diff_and_emit() is None
    # Change one step's status -> delta with that step
    first = repo.get_steps(job_id)[0]
    repo.set_step_status(first.id, StepStatus.RUNNING)
    evt = bc.diff_and_emit()
    assert evt is not None and evt["type"] == "delta"
    changed = [s for s in evt["steps"] if s["id"] == first.id]
    assert len(changed) == 1 and changed[0]["status"] == "running"
    # After absorbing, no further change -> None
    assert bc.diff_and_emit() is None


def test_broadcast_sends_to_all_and_drops_dead(repo):
    from kb_platform.api.realtime import JobBroadcaster

    job_id = _seed_job(repo)
    bc = JobBroadcaster(job_id=job_id, repo=repo)
    alive = _FakeWS()
    dead = _FakeWS()

    async def boom(obj):
        raise RuntimeError("dead")

    dead.send_json = boom  # type: ignore
    bc.subscribers.update({alive, dead})
    asyncio.run(bc.broadcast({"type": "delta", "steps": []}))
    assert alive.sent == [{"type": "delta", "steps": []}]
    assert dead not in bc.subscribers
    assert alive in bc.subscribers
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_realtime.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kb_platform.api.realtime'`.

- [ ] **Step 3: Implement `kb_platform/api/realtime.py`** (broadcaster + helpers only; hub added in Task 2):

```python
"""WebSocket realtime progress: poll SQLite, diff, push step/unit progress to subscribers.

The worker writes job/step/unit status to SQLite; this module is the server-side
bridge that turns those changes into WS events. A global RealtimeHub (Task 2)
polls jobs that have subscribers, diffs against the last-sent frame, and pushes
only what changed. Worker code is never touched.

Events carry StepOut-shaped data (with `progress`) so the frontend can treat a
snapshot/delta as a JobOut directly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from kb_platform.api.models import StepOut, UnitProgress
from kb_platform.db.repository import Repository

logger = logging.getLogger(__name__)


def _step_dict(repo: Repository, s) -> dict:
    """Serialize one Step to a StepOut-shaped dict (mirrors routes_jobs._step_out)."""
    progress = None
    if s.kind == "unit_fanout":
        progress = UnitProgress(**repo.unit_counts_by_status(s.id)).model_dump()
    return StepOut(
        id=s.id, name=s.name, ordinal=s.ordinal, kind=s.kind, status=s.status, progress=progress
    ).model_dump()


def _job_state(repo: Repository, job_id: int) -> tuple[str, dict[int, dict]]:
    """Current (job_status, {step_id: step_dict}) from the DB — the source of truth."""
    job = repo.get_job(job_id)
    if job is None:
        return "", {}
    return job.status, {s.id: _step_dict(repo, s) for s in repo.get_steps(job_id)}


@dataclass
class JobBroadcaster:
    """One job's subscriber set + last-sent frame, used to diff and push deltas."""

    job_id: int
    repo: Repository
    subscribers: set = field(default_factory=set)
    _last_job_status: str | None = None
    _last_steps: dict[int, dict] = field(default_factory=dict)

    def snapshot(self) -> dict:
        """Full current frame; also seeds the diff baseline. Sent on subscribe."""
        job_status, steps = _job_state(self.repo, self.job_id)
        self._last_job_status = job_status
        self._last_steps = steps
        return {
            "type": "snapshot",
            "job": {"id": self.job_id, "status": job_status},
            "steps": list(steps.values()),
        }

    def diff_and_emit(self) -> dict | None:
        """Return a delta event for changed steps/job, or None if nothing changed.

        Reads the live DB each call and compares to the last frame, so intermediate
        states are never silently lost (worst case: the first poll after a burst
        emits the net change in one delta).
        """
        job_status, steps = _job_state(self.repo, self.job_id)
        changed = [s for sid, s in steps.items() if s != self._last_steps.get(sid)]
        job_changed = job_status != self._last_job_status
        self._last_job_status = job_status
        self._last_steps = steps
        if not changed and not job_changed:
            return None
        event: dict = {"type": "delta", "steps": changed}
        if job_changed:
            event["job"] = {"id": self.job_id, "status": job_status}
        return event

    async def broadcast(self, event: dict) -> None:
        """Send to all subscribers; drop any that error (don't let one break others)."""
        dead = []
        for ws in list(self.subscribers):
            try:
                await ws.send_json(event)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            logger.debug("dropping dead subscriber on job %s", self.job_id)
            self.subscribers.discard(ws)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_realtime.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add kb_platform/api/realtime.py tests/test_realtime.py
git commit -m "feat(realtime): JobBroadcaster snapshot/diff/broadcast"
```

---

## Task 2: `RealtimeHub`(registry + poll loop + lifecycle)

**Files:**
- Modify: `kb_platform/api/realtime.py`
- Test: `tests/test_realtime.py`

**Interfaces:**
- Consumes: `JobBroadcaster`(Task 1);`Repository`(已存在)。
- Produces: `RealtimeHub(repo, interval: float)`;`.start()`、`async .stop()`、`.subscribe(job_id, ws)->dict`(立即返回 snapshot)、`.unsubscribe(job_id, ws)`、`.broadcasters` 属性。

- [ ] **Step 1: Write the failing tests** — append to `tests/test_realtime.py`:

```python
def test_subscribe_returns_snapshot_and_creates_broadcaster(repo):
    from kb_platform.api.realtime import RealtimeHub

    job_id = _seed_job(repo)
    hub = RealtimeHub(repo=repo, interval=0.01)
    snap = hub.subscribe(job_id, _FakeWS())
    assert snap["type"] == "snapshot"
    assert job_id in hub.broadcasters


def test_unsubscribe_removes_broadcaster_when_empty(repo):
    from kb_platform.api.realtime import RealtimeHub

    job_id = _seed_job(repo)
    hub = RealtimeHub(repo=repo, interval=0.01)
    ws = _FakeWS()
    hub.subscribe(job_id, ws)
    hub.unsubscribe(job_id, ws)
    assert job_id not in hub.broadcasters


def test_poll_loop_pushes_delta(repo):
    from kb_platform.api.realtime import RealtimeHub

    job_id = _seed_job(repo)
    hub = RealtimeHub(repo=repo, interval=0.01)
    ws = _FakeWS()
    hub.subscribe(job_id, ws)  # baseline frame = pending
    first = repo.get_steps(job_id)[0]
    repo.set_step_status(first.id, StepStatus.RUNNING)  # change the poller will see

    async def go():
        hub.start()
        await asyncio.sleep(0.05)  # let >=1 poll cycle run
        await hub.stop()

    asyncio.run(go())
    deltas = [m for m in ws.sent if m.get("type") == "delta"]
    assert any(any(s["id"] == first.id for s in m["steps"]) for m in deltas)


def test_poll_loop_survives_broadcaster_error(repo):
    """A broadcaster-level exception must NOT kill the loop."""
    from kb_platform.api.realtime import RealtimeHub

    job_id = _seed_job(repo)
    hub = RealtimeHub(repo=repo, interval=0.01)
    ws = _FakeWS()
    hub.subscribe(job_id, ws)
    calls = {"n": 0}
    orig = hub.broadcasters[job_id].diff_and_emit

    def boom():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return orig()

    hub.broadcasters[job_id].diff_and_emit = boom  # type: ignore

    async def go():
        hub.start()
        await asyncio.sleep(0.05)  # first cycle throws; subsequent cycles keep going
        await hub.stop()

    asyncio.run(go())
    assert calls["n"] >= 2  # the loop ran again after the first throw -> it survived
    assert hub._task is None  # stop() cancelled & cleared the task
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_realtime.py -v -k "subscribe or unsubscribe or poll_loop"`
Expected: FAIL — `ImportError: cannot import name 'RealtimeHub'`.

- [ ] **Step 3: Add `RealtimeHub` to `kb_platform/api/realtime.py`** — append after `JobBroadcaster`:

```python
import asyncio  # add to the imports at top of file if not already present


class RealtimeHub:
    """Owns one poll loop for all jobs that currently have subscribers."""

    def __init__(self, repo: Repository, interval: float = 0.5) -> None:
        self.repo = repo
        self.interval = interval
        self.broadcasters: dict[int, JobBroadcaster] = {}
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def subscribe(self, job_id: int, ws) -> dict:
        bc = self.broadcasters.get(job_id)
        if bc is None:
            bc = JobBroadcaster(job_id=job_id, repo=self.repo)
            self.broadcasters[job_id] = bc
        bc.subscribers.add(ws)
        return bc.snapshot()

    def unsubscribe(self, job_id: int, ws) -> None:
        bc = self.broadcasters.get(job_id)
        if bc is not None:
            bc.subscribers.discard(ws)
            if not bc.subscribers:
                del self.broadcasters[job_id]

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            for job_id in list(self.broadcasters):
                try:
                    bc = self.broadcasters[job_id]
                    if not bc.subscribers:
                        continue
                    event = bc.diff_and_emit()
                    if event is not None:
                        await bc.broadcast(event)
                except Exception:  # noqa: BLE001 — per-job isolation; never kill the loop
                    logger.exception("realtime poll failed for job %s; continuing", job_id)
```

(Add `import asyncio` to the top import block of the file.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_realtime.py -v`
Expected: PASS (all 7 tests: 3 broadcaster + 2 hub subscribe/unsubscribe + 2 poll loop).

- [ ] **Step 5: Commit**

```bash
git add kb_platform/api/realtime.py tests/test_realtime.py
git commit -m "feat(realtime): RealtimeHub poll loop + lifecycle"
```

---

## Task 3: WS endpoint + lifespan wiring + integration test

**Files:**
- Create: `kb_platform/api/routes_realtime.py`
- Modify: `kb_platform/api/app.py`
- Test: `tests/test_api_realtime.py`

**Interfaces:**
- Consumes: `RealtimeHub`(Task 2);`app.state.repo`。
- Produces: `GET ws://host/jobs/{job_id}/events`;`app.state.realtime: RealtimeHub`(由 lifespan 设置)。

- [ ] **Step 1: Write the failing integration test** — `tests/test_api_realtime.py`:

```python
"""Integration test for the realtime WS endpoint (lifespan must run)."""
import pytest
from fastapi.testclient import TestClient

from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine
from kb_platform.db.enums import StepStatus
from kb_platform.db.models import Base
from kb_platform.db.repository import Repository
from conftest import seed_profile


@pytest.fixture()
def app_and_client(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_POLL_INTERVAL_MS", "20")  # fast polls for the test
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    repo = Repository(engine)
    app = create_app(repo, data_root=str(tmp_path))
    with TestClient(app) as client:  # context manager => lifespan runs (starts poller)
        seed_profile(client)
        client.post("/kbs", json={"name": "kb1", "method": "standard",
                                  "settings_yaml": "{}", "llm_profile_id": 1})
        client.post("/kbs/1/documents", json={"title": "d", "text": "ACME Org Bob Foo Bar " * 200})
        yield client


def test_ws_sends_snapshot_then_delta_on_change(app_and_client):
    client = app_and_client
    repo = client.app.state.repo
    job_id = client.post("/kbs/1/jobs", json={"method": "standard"}).json()["id"]

    with client.websocket_connect(f"/jobs/{job_id}/events") as ws:
        snap = ws.receive_json()
        assert snap["type"] == "snapshot"
        assert snap["job"]["status"] == "pending"

        # Change something the poller will see within a poll cycle.
        first = repo.get_steps(job_id)[0]
        repo.set_step_status(first.id, StepStatus.RUNNING)
        evt = ws.receive_json()  # arrives within ~20ms poll cycle
        assert evt["type"] == "delta"
        assert any(s["id"] == first.id and s["status"] == "running" for s in evt["steps"])


def test_ws_terminal_job_sends_snapshot(app_and_client):
    client = app_and_client
    repo = client.app.state.repo
    job_id = client.post("/kbs/1/jobs", json={"method": "standard"}).json()["id"]
    from kb_platform.db.enums import JobStatus

    repo.set_job_status(job_id, JobStatus.SUCCEEDED)
    with client.websocket_connect(f"/jobs/{job_id}/events") as ws:
        snap = ws.receive_json()
        assert snap["type"] == "snapshot"
        assert snap["job"]["status"] == "succeeded"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_api_realtime.py -v`
Expected: FAIL — `WebSocketDisconnect`/404 on `/jobs/{id}/events` (endpoint not registered; `app.state.realtime` absent).

- [ ] **Step 3: Create `kb_platform/api/routes_realtime.py`**:

```python
"""WebSocket endpoint: per-job realtime step/unit progress."""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/jobs/{job_id}/events")
async def job_events(websocket: WebSocket, job_id: int):
    await websocket.accept()
    hub = getattr(websocket.app.state, "realtime", None)
    if hub is None:
        # Lifespan didn't run (e.g. a non-context-manager test client). Close cleanly.
        await websocket.close()
        return
    await websocket.send_json(hub.subscribe(job_id, websocket))
    try:
        while True:
            await websocket.receive_text()  # keep-alive; client text ignored
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(job_id, websocket)
```

- [ ] **Step 4: Wire lifespan + router into `kb_platform/api/app.py`**

Replace the top of `create_app` and the `FastAPI(...)` / router block. Full new `app.py`:

```python
"""FastAPI app factory with repo + data_root dependency injection.

When the built SPA (`web/dist`) exists, the app also serves it:
`/assets/*` are served as static files (Vite hashed assets), and a
catch-all `/{full_path:path}` returns `index.html` to support SPA history
routing (e.g. `/kbs/1/jobs/5`). API routers are registered BEFORE the
catch-all, so explicit API routes (like `GET /kbs`) always win.

A `lifespan` starts/stops the realtime hub (WebSocket progress push); the hub
lives on `app.state.realtime` and is only present when the app is entered as a
context manager (production uvicorn + `with TestClient(app)` in tests).
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from kb_platform.api.routes_cost import router as cost_router
from kb_platform.api.routes_export import router as export_router
from kb_platform.api.routes_graph import router as graph_router
from kb_platform.api.routes_health import router as health_router
from kb_platform.api.routes_jobs import router as jobs_router
from kb_platform.api.routes_kbs import router
from kb_platform.api.routes_profiles import router as profiles_router
from kb_platform.api.routes_query import router as query_router
from kb_platform.api.routes_realtime import router as realtime_router
from kb_platform.db.repository import Repository
from kb_platform.query.engine import QueryEngine

# Module-level so tests can monkeypatch `kb_platform.api.app.WEB_DIST`.
WEB_DIST = os.environ.get(
    "KB_WEB_DIST",
    str(Path(__file__).resolve().parents[2] / "web" / "dist"),
)


def create_app(
    repo: Repository, data_root: str = ".", query_engine: QueryEngine | None = None
) -> FastAPI:
    """Build a FastAPI app with repo and data_root injected via app.state."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from kb_platform.api.realtime import RealtimeHub

        interval_ms = float(os.environ.get("KB_POLL_INTERVAL_MS", "500"))
        hub = RealtimeHub(repo=app.state.repo, interval=interval_ms / 1000.0)
        app.state.realtime = hub
        hub.start()
        try:
            yield
        finally:
            await hub.stop()

    app = FastAPI(title="KB Platform", lifespan=lifespan)
    app.state.repo = repo
    app.state.data_root = data_root
    app.state.query_engine = (
        query_engine  # None = build real per-KB (production); non-None = injected (tests)
    )

    # API routers registered first -> matched before the catch-all below.
    app.include_router(router)
    app.include_router(jobs_router)
    app.include_router(query_router)
    app.include_router(cost_router)
    app.include_router(health_router)
    app.include_router(export_router)
    app.include_router(graph_router)
    app.include_router(profiles_router)
    app.include_router(realtime_router)

    dist = Path(WEB_DIST)
    if dist.exists():
        assets_dir = dist / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str, request: Request):  # noqa: ARG001
            return FileResponse(dist / "index.html")

    return app
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_api_realtime.py tests/test_realtime.py -v`
Expected: PASS (all).

- [ ] **Step 6: Run full backend regression to ensure lifespan change didn't break existing tests**

Run: `uv run pytest -q`
Expected: PASS (no new failures). Note: existing tests using `TestClient(create_app(...))` without a context manager do NOT get `app.state.realtime`, but they don't hit the WS endpoint, so they are unaffected.

- [ ] **Step 7: Commit**

```bash
git add kb_platform/api/routes_realtime.py kb_platform/api/app.py tests/test_api_realtime.py
git commit -m "feat(api): realtime WS endpoint + lifespan hub wiring"
```

---

## Task 4: Frontend `useJobEvents` hook

**Files:**
- Create: `web/src/hooks/useJobEvents.ts`
- Test: `web/src/hooks/useJobEvents.test.tsx`

**Interfaces:**
- Consumes: `JobOut`、`StepOut`(from `../api/types`);浏览器 `WebSocket`。
- Produces: `useJobEvents(jobId: number | null): { connected: boolean; data: JobOut | null }`。事件:`{type:"snapshot", job:{id,status}, steps:StepOut[]}` 与 `{type:"delta", job?:{id,status}, steps:StepOut[]}`。

- [ ] **Step 1: Write the failing test** — `web/src/hooks/useJobEvents.test.tsx`:

```tsx
import { renderHook, waitFor, act } from "@testing-library/react";
import { useJobEvents } from "./useJobEvents";
import type { JobOut } from "../api/types";

class MockWS {
  static last: MockWS | null = null;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) {
    MockWS.last = this;
  }
  close() {
    this.closed = true;
    this.onclose?.();
  }
  emit(obj: unknown) {
    this.onmessage?.({ data: JSON.stringify(obj) });
  }
}

beforeEach(() => {
  MockWS.last = null;
  (global as unknown as { WebSocket: unknown }).WebSocket = MockWS;
});
afterEach(() => {
  delete (global as unknown as { WebSocket?: unknown }).WebSocket;
});

test("snapshot sets data, delta merges steps, terminal closes socket", async () => {
  const { result } = renderHook(() => useJobEvents(7));
  await waitFor(() => expect(MockWS.last).not.toBeNull());
  const ws = MockWS.last!;
  act(() => {
    ws.onopen?.();
    ws.emit({
      type: "snapshot", job: { id: 7, status: "running" },
      steps: [{ id: 1, name: "x", ordinal: 0, kind: "atomic", status: "pending", progress: null }],
    });
  });
  await waitFor(() => expect(result.current.connected).toBe(true));
  expect(result.current.data?.status).toBe("running");

  act(() => ws.emit({
    type: "delta", job: { id: 7, status: "running" },
    steps: [{ id: 1, name: "x", ordinal: 0, kind: "atomic", status: "succeeded", progress: null }],
  }));
  await waitFor(() => expect(result.current.data?.steps[0].status).toBe("succeeded"));

  act(() => ws.emit({ type: "delta", job: { id: 7, status: "succeeded" }, steps: [] }));
  await waitFor(() => expect(result.current.data?.status).toBe("succeeded"));
  expect(ws.closed).toBe(true);
});

test("disconnect sets connected=false", async () => {
  const { result } = renderHook(() => useJobEvents(8));
  await waitFor(() => expect(MockWS.last).not.toBeNull());
  act(() => MockWS.last!.onclose?.());
  await waitFor(() => expect(result.current.connected).toBe(false));
});

test("null jobId does nothing", () => {
  const { result } = renderHook(() => useJobEvents(null));
  expect(result.current.connected).toBe(false);
  expect(result.current.data).toBeNull();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run src/hooks/useJobEvents.test.tsx`
Expected: FAIL — `Cannot find module './useJobEvents'`.

- [ ] **Step 3: Implement `web/src/hooks/useJobEvents.ts`**:

```ts
import { useEffect, useRef, useState } from "react";
import type { JobOut, StepOut } from "../api/types";

/** WS events share StepOut's shape so a snapshot/delta maps straight to JobOut. */
interface SnapshotEvent {
  type: "snapshot";
  job: { id: number; status: string };
  steps: StepOut[];
}
interface DeltaEvent {
  type: "delta";
  job?: { id: number; status: string };
  steps: StepOut[];
}
type Event = SnapshotEvent | DeltaEvent;

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);
const RECONNECT_MS = 1000;

export interface JobEventsState {
  connected: boolean;
  data: JobOut | null;
}

function wsUrl(jobId: number): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/jobs/${jobId}/events`;
}

export function useJobEvents(jobId: number | null): JobEventsState {
  const [state, setState] = useState<JobEventsState>({ connected: false, data: null });
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (jobId == null) return;
    let closed = false;

    const connect = () => {
      const ws = new WebSocket(wsUrl(jobId));
      ws.onopen = () => {
        if (!closed) setState((s) => ({ ...s, connected: true }));
      };
      ws.onmessage = (e) => {
        if (closed) return;
        const evt = JSON.parse(e.data) as Event;
        if (evt.type === "snapshot") {
          setState({ connected: true, data: { id: jobId, status: evt.job.status, steps: evt.steps } });
        } else {
          setState((s) => {
            if (!s.data) return s;
            const byId = new Map(s.data.steps.map((st) => [st.id, st]));
            for (const st of evt.steps) byId.set(st.id, st);
            const status = evt.job?.status ?? s.data.status;
            return { connected: true, data: { ...s.data, status, steps: [...byId.values()] } };
          });
          if (evt.job?.status && TERMINAL.has(evt.job.status)) ws.close();
        }
      };
      ws.onclose = () => {
        if (closed) return;
        setState((s) => ({ ...s, connected: false }));
        timerRef.current = setTimeout(connect, RECONNECT_MS);
      };
      ws.onerror = () => ws.close();
    };

    connect();
    return () => {
      closed = true;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [jobId]);

  return state;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && npx vitest run src/hooks/useJobEvents.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add web/src/hooks/useJobEvents.ts web/src/hooks/useJobEvents.test.tsx
git commit -m "feat(web): useJobEvents WebSocket hook"
```

---

## Task 5: Wire `JobDetailPage` (realtime-first, polling fallback) + vite ws proxy

**Files:**
- Modify: `web/src/pages/JobDetailPage.tsx`
- Modify: `web/vite.config.ts`
- Test: `web/src/pages/JobDetailPage.test.tsx` (new)

**Interfaces:**
- Consumes: `useJobEvents`(Task 4)、`useJobPolling`(已存在)。
- Produces: JobDetailPage 优先用 WS 数据,WS 不可用回退轮询。

- [ ] **Step 1: Write the failing test** — `web/src/pages/JobDetailPage.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import JobDetailPage from "./JobDetailPage";

// REST fallback returns a pending job with one running step.
const server = setupServer(
  http.get("/kbs/1/jobs/5/cost", () => HttpResponse.json({ total_usd: 0, by_step: {}, by_model: {} })),
  http.get("/jobs/5", () =>
    HttpResponse.json({
      id: 5, status: "pending",
      steps: [{ id: 9, name: "extract_graph", ordinal: 0, kind: "unit_fanout", status: "running",
                progress: { pending: 1, running: 0, succeeded: 0, failed: 0, total: 1 } }],
    }),
  ),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/kbs/:id/jobs/:jobId" element={<JobDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

test("renders job from REST when WS has no data yet", async () => {
  // WS is absent in jsdom, so useJobEvents stays {connected:false, data:null}
  // and the page must fall back to the polled REST job.
  renderAt("/kbs/1/jobs/5");
  await waitFor(() => expect(screen.getByText(/任务 #5/)).toBeInTheDocument());
  expect(screen.getByText("extract_graph")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run src/pages/JobDetailPage.test.tsx`
Expected: FAIL — page still uses `useJobPolling` only; the test should still pass if the fallback already works. (If it passes already, keep it as a regression guard for the wiring change in Step 3.) If it fails on missing `getJobCost` shape or route, adjust the MSW handler to match; the intent is a green guard before the edit.

> If Step 2 already passes (the page renders via polling), that's expected — this test is a **regression guard** ensuring the Task-5 wiring keeps polling working when WS is offline. Proceed to Step 3 and re-run.

- [ ] **Step 3: Edit `web/src/pages/JobDetailPage.tsx`**

3a. Add the import and the live/fallback line. Replace:

```tsx
import { useJobPolling } from "../hooks/useJobPolling";
```

with:

```tsx
import { useJobPolling } from "../hooks/useJobPolling";
import { useJobEvents } from "../hooks/useJobEvents";
```

3b. Inside the component, replace:

```tsx
  const job = useJobPolling(jobIdNum);
```

with:

```tsx
  const polled = useJobPolling(jobIdNum);
  const live = useJobEvents(jobIdNum);
  const job = live.connected && live.data ? live.data : polled;
```

(All downstream references to `job` stay valid — `job` is now the realtime-or-polled value.)

3c. (Optional indicator) In the `<h1>` row, after `<StatusBadge status={job.status} />`, add a small live tag:

```tsx
                {live.connected ? (
                  <span className="rounded-full bg-brand-grad-soft px-2 py-0.5 text-[11px] text-brand">实时</span>
                ) : null}
```

- [ ] **Step 4: Enable WS through the vite dev proxy** — in `web/vite.config.ts`, replace:

```ts
      "/jobs": "http://localhost:8000",
```

with:

```ts
      "/jobs": { target: "http://localhost:8000", ws: true },
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd web && npx vitest run src/pages/JobDetailPage.test.tsx`
Expected: PASS.

- [ ] **Step 6: Run full frontend test + type-check/build**

Run: `cd web && npm test && npm run build`
Expected: PASS (vitest green, `tsc -b && vite build` succeeds).

- [ ] **Step 7: Commit**

```bash
git add web/src/pages/JobDetailPage.tsx web/src/pages/JobDetailPage.test.tsx web/vite.config.ts
git commit -m "feat(web): JobDetailPage realtime-first with polling fallback"
```

---

## Task 6: Full verification + verify doc

**Files:**
- Create: `docs/verify-websocket-2026-06-28.md`

- [ ] **Step 1: Backend full regression**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all PASS, ruff clean.

- [ ] **Step 2: Frontend full regression**

Run: `cd web && npm test && npm run build`
Expected: PASS; `web/dist` rebuilt.

- [ ] **Step 3: Manual WS smoke (fake server, no LLM)**

Run in two terminals:

```bash
# T1
uv run python -m kb_platform.server kb.db . 127.0.0.1 8000
# T2
uv run python -m kb_platform.worker kb.db
```

Then (after `cd web && npm run build`): open `http://127.0.0.1:8000`, create a KB (pick an LLM profile), add a document, trigger a full job, open the **job detail** page, and confirm:
- The "实时" tag appears next to the job status while the job runs.
- Step statuses / unit progress update without manual page refresh as the worker advances.
- Stop the worker mid-job: the "实时" tag disappears (falls back to polling), page still updates every ~2s; restart the worker and "实时" returns.

- [ ] **Step 4: Write `docs/verify-websocket-2026-06-28.md`** recording: env, the manual smoke observations above (incl. the fallback-on-worker-stop behavior), and the pytest/vitest/build results. Follow the style of `docs/verify-deepseek-ollama-2026-06-28.md`.

- [ ] **Step 5: Commit**

```bash
git add docs/verify-websocket-2026-06-28.md
git commit -m "docs(verify): websocket realtime progress smoke"
```

---

## Notes for the implementer

- The WS endpoint reads `app.state.realtime`, which **only exists when lifespan ran**. Production `uvicorn` runs lifespan automatically; tests must use `with TestClient(app) as client:`. The endpoint guards a missing hub with a clean close (no 500).
- `diff_and_emit` reads the live DB every cycle and compares to the last frame — bursts of changes collapse into one delta, but no state is silently dropped.
- `JobBroadcaster.broadcast` swallows per-subscriber errors so one dead socket can't stall others; the poll loop swallows per-job exceptions so one bad job can't kill the loop.
- Do **not** touch `kb_platform/worker.py`.
