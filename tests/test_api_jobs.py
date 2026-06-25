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


def test_step_units_filtered_by_status(client):
    job_id = client.post("/kbs/1/jobs", json={"method": "standard"}).json()["id"]
    steps = client.get(f"/jobs/{job_id}/steps").json()
    extract = [s for s in steps if s["name"] == "extract_graph"][0]
    units = client.get(f"/steps/{extract['id']}/units").json()
    assert len(units) >= 1


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
