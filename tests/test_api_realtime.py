"""Integration test for the realtime WS endpoint (lifespan must run)."""
import pytest
from fastapi.testclient import TestClient

from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine
from kb_platform.db.enums import JobStatus, StepStatus
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
        client.post(
            "/kbs",
            json={"name": "kb1", "method": "standard", "settings_yaml": "{}", "llm_profile_id": 1},
        )
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
    repo.set_job_status(job_id, JobStatus.SUCCEEDED)
    with client.websocket_connect(f"/jobs/{job_id}/events") as ws:
        snap = ws.receive_json()
        assert snap["type"] == "snapshot"
        assert snap["job"]["status"] == "succeeded"
