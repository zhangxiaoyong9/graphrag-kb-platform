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
    from kb_platform.db.engine import session_scope
    from kb_platform.db.models import KnowledgeBase

    with session_scope(repo.engine) as s:
        s.add(KnowledgeBase(name="k", method="standard", settings_json="{}", data_root="."))
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
