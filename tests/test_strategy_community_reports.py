import pandas as pd
import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import StepKind, StepStatus
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.spec import StepSpec
from kb_platform.engine.unit_worker import UnitWorker
from kb_platform.graph.adapter import FakeGraphAdapter


@pytest.fixture()
def setup(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(
            KnowledgeBase(
                name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)
            )
        )
    pd.DataFrame(
        [
            {
                "title": "A",
                "type": "T",
                "description": "da",
                "text_unit_ids": ["c1"],
                "frequency": 1,
                "degree": 1,
            },
            {
                "title": "B",
                "type": "T",
                "description": "db",
                "text_unit_ids": ["c1"],
                "frequency": 1,
                "degree": 1,
            },
        ]
    ).to_parquet(f"{tmp_path}/entities.parquet")
    pd.DataFrame(
        [
            {
                "source": "A",
                "target": "B",
                "weight": 1.0,
                "description": ["d"],
                "text_unit_ids": ["c1"],
                "combined_degree": 2,
            }
        ]
    ).to_parquet(f"{tmp_path}/relationships.parquet")
    # 两层:level 1(叶子)C1={A,B},level 0(父)C0={A,B} parent=C0 children=C1
    pd.DataFrame(
        [
            {"level": 1, "community_id": "C1", "parent": "C0", "entity_ids": ["A", "B"]},
            {"level": 0, "community_id": "C0", "parent": "C0", "entity_ids": ["A", "B"]},
        ]
    ).to_parquet(f"{tmp_path}/communities.parquet")
    repo = Repository(engine)
    step = repo.create_job(
        kb_id=1, type="full", specs=[StepSpec("community_reports", StepKind.UNIT_FANOUT)]
    ).steps[0]
    return repo, step, str(tmp_path)


def test_batch_returns_deepest_level_first(setup):
    repo, step, _ = setup
    from kb_platform.engine.strategies.community_reports import CommunityReportsStrategy

    batch = CommunityReportsStrategy().next_units_batch(repo, step)
    assert batch is not None and {s.subject_id for s in batch} == {"C1"}  # 叶子层(level 1)先


@pytest.mark.asyncio
async def test_reports_written_and_parent_includes_child(setup):
    repo, step, data_root = setup
    from kb_platform.engine.strategy import default_strategies

    worker = UnitWorker(
        repo=repo, adapter=FakeGraphAdapter(), data_root=data_root, strategies=default_strategies()
    )
    await worker.run_unit_fanout(step)
    assert repo.get_step(step.id).status == StepStatus.SUCCEEDED
    reports = pd.read_parquet(f"{data_root}/community_reports.parquet")
    assert set(reports["community"]) == {"C0", "C1"}
    assert len(reports[reports["community"] == "C1"]) == 1
    assert len(reports[reports["community"] == "C0"]) == 1


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


def test_strategy_uses_plain_when_setting_false(tmp_path):
    import json as _json

    from kb_platform.db.engine import create_engine, session_scope
    from kb_platform.db.enums import JobStatus, StepStatus
    from kb_platform.db.models import KnowledgeBase, Job, Step
    from kb_platform.db.repository import Repository
    from kb_platform.engine.strategies.community_reports import _structured_output

    engine = create_engine(f"sqlite:///{tmp_path}/db.sqlite")
    Base.metadata.create_all(engine)
    repo = Repository(engine)
    settings = _json.dumps({"community_reports": {"structured_output": False}})
    with session_scope(repo.engine) as s:
        s.add(KnowledgeBase(id=1, name="k", settings_json=settings, data_root=str(tmp_path)))
        s.flush()
        s.add(Job(id=1, kb_id=1, type="full", status=JobStatus.RUNNING))
        s.add(
            Step(
                id=1,
                job_id=1,
                name="community_reports",
                ordinal=0,
                kind="unit_fanout",
                status=StepStatus.RUNNING,
            )
        )
        s.flush()
        step = s.get(Step, 1)
    assert _structured_output(repo, step) is False


def test_strategy_default_structured_true(tmp_path):
    from kb_platform.db.engine import create_engine, session_scope
    from kb_platform.db.models import KnowledgeBase, Job, Step
    from kb_platform.db.enums import JobStatus, StepStatus
    from kb_platform.db.repository import Repository
    from kb_platform.engine.strategies.community_reports import _structured_output

    engine = create_engine(f"sqlite:///{tmp_path}/db.sqlite")
    Base.metadata.create_all(engine)
    repo = Repository(engine)
    with session_scope(repo.engine) as s:
        s.add(KnowledgeBase(id=1, name="k", settings_json="{}", data_root=str(tmp_path)))
        s.flush()
        s.add(Job(id=1, kb_id=1, type="full", status=JobStatus.RUNNING))
        s.add(
            Step(
                id=1,
                job_id=1,
                name="community_reports",
                ordinal=0,
                kind="unit_fanout",
                status=StepStatus.RUNNING,
            )
        )
        s.flush()
        step = s.get(Step, 1)
    assert _structured_output(repo, step) is True
