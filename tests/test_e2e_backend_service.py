"""End-to-end backend service: API creates KB + doc + job, worker runs it to SUCCEEDED."""

import os

import pytest
from fastapi.testclient import TestClient

from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine
from kb_platform.db.models import Base
from kb_platform.db.repository import Repository
from kb_platform.graph.adapter import FakeGraphAdapter
from kb_platform.worker import run_worker_once
from conftest import seed_profile


@pytest.mark.asyncio
async def test_full_backend_service_with_fake_adapter(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    repo = Repository(engine)
    client = TestClient(create_app(repo, data_root=str(tmp_path)))
    _pid = seed_profile(client)

    r = client.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}", "llm_profile_id": _pid})
    assert r.status_code == 201
    r = client.post(
        "/kbs/1/documents",
        json={"title": "d", "text": "ACME Org Bob Person Foo Bar Baz " * 200},
    )
    assert r.status_code == 201
    job_id = client.post("/kbs/1/jobs", json={"method": "standard"}).json()["id"]
    assert client.get(f"/jobs/{job_id}").json()["status"] == "pending"

    # Worker claims and runs the job (FakeGraphAdapter injected -> no real LLM).
    await run_worker_once(
        repo=repo, adapter_factory=lambda kb: FakeGraphAdapter(), heartbeat_interval=0.01
    )

    assert client.get(f"/jobs/{job_id}").json()["status"] == "succeeded"
    steps = client.get(f"/jobs/{job_id}/steps").json()
    extract = [s for s in steps if s["name"] == "extract_graph"][0]
    units = client.get(f"/steps/{extract['id']}/units").json()["items"]
    assert len(units) >= 1 and all(u["status"] == "succeeded" for u in units)
    # Four parquet outputs.
    for name in ("entities", "relationships", "communities", "community_reports"):
        assert os.path.exists(f"{tmp_path}/{name}.parquet")
