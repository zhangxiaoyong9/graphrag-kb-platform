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
