"""Tests for cost aggregation endpoints (GET /kbs/{id}/jobs/{jid}/cost, /kbs/{id}/cost)."""

import json

import pytest
from fastapi.testclient import TestClient

from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import JobStatus, StepStatus, UnitKind, UnitStatus
from kb_platform.db.models import Base, Job, KnowledgeBase, Step, Unit
from kb_platform.db.repository import Repository


def _cost_json(total_usd, prompt_tokens, completion_tokens, model="deepseek-chat", usd=None):
    if usd is None:
        usd = total_usd
    return json.dumps(
        {
            "items": [
                {
                    "model": model,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "estimated_cost_usd": usd,
                }
            ],
            "total_usd": total_usd,
        }
    )


def _seed(engine, tmp_path):
    """Seed KB(id=1) + Job(id=1) + two steps with cost-bearing units.

    Returns (kb_id=1, job_id=1).  extract_graph step has one unit costing 0.01;
    summarize_descriptions step has one unit costing 0.004 (total 0.014).
    """
    repo = Repository(engine)
    with session_scope(repo.engine) as s:
        s.add(KnowledgeBase(id=1, name="k", settings_json="{}", data_root=str(tmp_path)))
        s.flush()
        s.add(Job(id=1, kb_id=1, type="full", status=JobStatus.SUCCEEDED))
        s.add(
            Step(
                id=10,
                job_id=1,
                name="extract_graph",
                ordinal=0,
                kind="unit_fanout",
                status=StepStatus.SUCCEEDED,
            )
        )
        s.add(
            Step(
                id=11,
                job_id=1,
                name="summarize_descriptions",
                ordinal=1,
                kind="unit_fanout",
                status=StepStatus.SUCCEEDED,
            )
        )
        s.flush()
        s.add(
            Unit(
                step_id=10,
                kind=UnitKind.EXTRACT_GRAPH,
                subject_type="chunk",
                subject_id="c1",
                status=UnitStatus.SUCCEEDED,
                cost_json=_cost_json(0.01, 100, 20),
            )
        )
        s.add(
            Unit(
                step_id=11,
                kind=UnitKind.SUMMARIZE_DESCRIPTIONS,
                subject_type="entity",
                subject_id="E",
                status=UnitStatus.SUCCEEDED,
                cost_json=_cost_json(0.004, 40, 10),
            )
        )
    return repo


@pytest.fixture()
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    repo = _seed(engine, tmp_path)
    return TestClient(create_app(repo, data_root=str(tmp_path)))


def test_get_job_cost(client):
    r = client.get("/kbs/1/jobs/1/cost")
    assert r.status_code == 200
    body = r.json()
    assert body["total_usd"] == pytest.approx(0.014)
    assert body["by_step"]["extract_graph"] == pytest.approx(0.01)
    assert body["by_step"]["summarize_descriptions"] == pytest.approx(0.004)
    # by_model entry has the CostItem shape (model is the key).
    item = body["by_model"]["deepseek-chat"]
    assert item["prompt_tokens"] == 140
    assert item["completion_tokens"] == 30
    assert item["usd"] == pytest.approx(0.014)


def test_get_kb_cost(client, tmp_path):
    """kb_cost adds a by_job breakout. Seed a second job to confirm aggregation."""
    repo = client.app.state.repo
    with session_scope(repo.engine) as s:
        # Second job under the same KB with one unit costing 0.005.
        s.add(Job(id=2, kb_id=1, type="incremental", status=JobStatus.SUCCEEDED))
        s.add(
            Step(
                id=20,
                job_id=2,
                name="extract_graph",
                ordinal=0,
                kind="unit_fanout",
                status=StepStatus.SUCCEEDED,
            )
        )
        s.flush()
        s.add(
            Unit(
                step_id=20,
                kind=UnitKind.EXTRACT_GRAPH,
                subject_type="chunk",
                subject_id="c2",
                status=UnitStatus.SUCCEEDED,
                cost_json=_cost_json(0.005, 60, 15),
            )
        )
    r = client.get("/kbs/1/cost")
    assert r.status_code == 200
    body = r.json()
    assert body["total_usd"] == pytest.approx(0.019)
    # by_step aggregates across jobs (extract_graph: 0.01 + 0.005 = 0.015).
    assert body["by_step"]["extract_graph"] == pytest.approx(0.015)
    # by_job breakout.
    assert body["by_job"]["1"] == pytest.approx(0.014)
    assert body["by_job"]["2"] == pytest.approx(0.005)
