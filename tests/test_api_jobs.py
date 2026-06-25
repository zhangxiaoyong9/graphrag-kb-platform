import pytest
from fastapi.testclient import TestClient

from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine
from kb_platform.db.models import Base
from kb_platform.db.repository import Repository


@pytest.fixture()
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    c = TestClient(create_app(Repository(engine), data_root=str(tmp_path)))
    c.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}"})
    c.post(
        "/kbs/1/documents",
        json={"title": "d", "text": "ACME Org Bob Foo Bar Baz " * 200},
    )
    return c


def test_trigger_job_creates_pending(client):
    r = client.post("/kbs/1/jobs", json={"method": "standard"})
    assert r.status_code == 202
    job_id = r.json()["id"]
    assert client.get(f"/jobs/{job_id}").json()["status"] == "pending"
    assert len(client.get(f"/jobs/{job_id}/steps").json()) == 6


def test_trigger_job_missing_kb_returns_404(client):
    """Defense in depth: posting a job against a non-existent KB must 404
    rather than creating an orphan job that the worker would later crash on."""
    r = client.post("/kbs/999999/jobs", json={"method": "standard"})
    assert r.status_code == 404


def test_step_units_filtered_by_status(client):
    repo = client.app.state.repo
    job_id = client.post("/kbs/1/jobs", json={"method": "standard"}).json()["id"]
    # trigger_job only creates a PENDING job; seed units in the test fixture.
    steps = client.get(f"/jobs/{job_id}/steps").json()
    extract = [s for s in steps if s["name"] == "extract_graph"][0]
    # No units yet -- job is pending and the worker has not run.
    assert client.get(f"/steps/{extract['id']}/units").json() == []
    # Seed units directly via the repo (the worker would do this).
    repo.add_units(extract["id"], [("chunk", "c1"), ("chunk", "c2")])
    units = client.get(f"/steps/{extract['id']}/units").json()
    assert len(units) == 2
    # status= filter: mark one failed and filter for pending.
    unit_id = units[0]["id"]
    repo.set_unit_failed(unit_id, "boom")
    pending = client.get(f"/steps/{extract['id']}/units?status=pending").json()
    failed = client.get(f"/steps/{extract['id']}/units?status=failed").json()
    assert len(pending) == 1
    assert len(failed) == 1


def test_retry_unit_resets_to_pending(client):
    """Set a unit FAILED via repo, then POST /units/{id}/retry resets it to pending."""
    repo = client.app.state.repo
    job_id = client.post("/kbs/1/jobs", json={"method": "standard"}).json()["id"]
    steps = client.get(f"/jobs/{job_id}/steps").json()
    extract = [s for s in steps if s["name"] == "extract_graph"][0]
    # Seed a unit for the step and force it to FAILED.
    unit = repo.add_unit(step_id=extract["id"], subject_type="chunk", subject_id="c1")
    repo.set_unit_failed(unit.id, "boom")
    r = client.post(f"/units/{unit.id}/retry")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    after = [u for u in client.get(f"/steps/{extract['id']}/units").json() if u["id"] == unit.id][0]
    assert after["status"] == "pending"
    assert after["error"] is None


def test_retry_step_resets_failed_units(client):
    """POST /steps/{id}/retry resets all FAILED units and returns the count."""
    repo = client.app.state.repo
    job_id = client.post("/kbs/1/jobs", json={"method": "standard"}).json()["id"]
    steps = client.get(f"/jobs/{job_id}/steps").json()
    extract = [s for s in steps if s["name"] == "extract_graph"][0]
    u1 = repo.add_unit(step_id=extract["id"], subject_type="chunk", subject_id="c1")
    u2 = repo.add_unit(step_id=extract["id"], subject_type="chunk", subject_id="c2")
    repo.set_unit_failed(u1.id, "boom")
    repo.set_unit_failed(u2.id, "boom")
    r = client.post(f"/steps/{extract['id']}/retry")
    assert r.status_code == 200
    assert r.json()["reset"] == 2


def test_trigger_job_422_on_bad_body(client):
    r = client.post("/kbs/1/jobs", json={"method": 123})  # wrong type
    assert r.status_code == 422


def test_trigger_job_response_shape(client):
    r = client.post("/kbs/1/jobs", json={"method": "standard"})
    assert r.status_code == 202
    assert set(r.json().keys()) == {"id", "status"}


def test_list_jobs_by_kb(client):
    j1 = client.post("/kbs/1/jobs", json={"method": "standard"}).json()["id"]
    j2 = client.post("/kbs/1/jobs", json={"method": "standard"}).json()["id"]
    jobs = client.get("/kbs/1/jobs").json()
    assert {j["id"] for j in jobs} == {j1, j2}
    # newest-first ordering
    assert [j["id"] for j in jobs] == [j2, j1]
    # each item has exactly id + status
    assert set(jobs[0].keys()) == {"id", "status"}


def test_list_jobs_by_kb_empty(client):
    jobs = client.get("/kbs/1/jobs").json()
    assert jobs == []


def test_job_progress_per_step(client):
    job_id = client.post("/kbs/1/jobs", json={"method": "standard"}).json()["id"]
    # 手动给 extract_graph 步种几个 unit
    steps = client.get(f"/jobs/{job_id}/steps").json()
    extract = [s for s in steps if s["name"] == "extract_graph"][0]
    repo = client.app.state.repo
    repo.add_units(extract["id"], [("chunk", "c1"), ("chunk", "c2")])
    body = client.get(f"/jobs/{job_id}").json()
    ex = [s for s in body["steps"] if s["name"] == "extract_graph"][0]
    assert ex["progress"]["total"] == 2 and ex["progress"]["pending"] == 2
    assert ex["progress"]["running"] == 0
    assert ex["progress"]["succeeded"] == 0
    assert ex["progress"]["failed"] == 0


def test_job_progress_steps_endpoint(client):
    """GET /jobs/{id}/steps also fills progress for unit_fanout steps."""
    job_id = client.post("/kbs/1/jobs", json={"method": "standard"}).json()["id"]
    steps = client.get(f"/jobs/{job_id}/steps").json()
    extract = [s for s in steps if s["name"] == "extract_graph"][0]
    repo = client.app.state.repo
    repo.add_units(extract["id"], [("chunk", "c1"), ("chunk", "c2")])
    # mark one failed
    units = repo.list_units(extract["id"])
    repo.set_unit_failed(units[0].id, "boom")
    steps2 = client.get(f"/jobs/{job_id}/steps").json()
    ex = [s for s in steps2 if s["name"] == "extract_graph"][0]
    assert ex["progress"] is not None
    assert ex["progress"]["total"] == 2
    assert ex["progress"]["pending"] == 1
    assert ex["progress"]["failed"] == 1


def test_atomic_step_progress_is_none(client):
    """Atomic steps should have progress=None."""
    job_id = client.post("/kbs/1/jobs", json={"method": "standard"}).json()["id"]
    body = client.get(f"/jobs/{job_id}").json()
    atomic_steps = [s for s in body["steps"] if s["kind"] == "atomic"]
    assert len(atomic_steps) > 0
    for s in atomic_steps:
        assert s["progress"] is None
