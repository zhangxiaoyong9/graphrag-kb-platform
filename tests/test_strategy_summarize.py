import pandas as pd
import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import StepKind
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.spec import StepSpec
from kb_platform.engine.strategies.summarize_descriptions import SummarizeDescriptionsStrategy
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
    # 预置 entities.parquet:description 为 list
    ents = pd.DataFrame(
        [
            {
                "title": "ACME",
                "type": "ORG",
                "description": ["d1", "d2"],
                "text_unit_ids": ["c1", "c2"],
                "frequency": 2,
            },
            {
                "title": "SOLO",
                "type": "ORG",
                "description": ["only"],
                "text_unit_ids": ["c1"],
                "frequency": 1,
            },
        ]
    )
    ents.to_parquet(f"{tmp_path}/entities.parquet")
    repo = Repository(engine)
    step = repo.create_job(
        kb_id=1, type="full", specs=[StepSpec("summarize_descriptions", StepKind.UNIT_FANOUT)]
    ).steps[0]
    return repo, step, str(tmp_path)


def test_only_multi_desc_entities_get_units(setup):
    repo, step, _ = setup
    strat = SummarizeDescriptionsStrategy()
    batch = strat.next_units_batch(repo, step)
    assert batch is not None and {s.subject_id for s in batch} == {"ACME"}  # SOLO 单描述,不出现在批


@pytest.mark.asyncio
async def test_summarize_writes_merged_descriptions_back(setup):
    repo, step, data_root = setup
    from kb_platform.engine.strategy import default_strategies
    from kb_platform.engine.unit_worker import UnitWorker

    worker = UnitWorker(
        repo=repo, adapter=FakeGraphAdapter(), data_root=data_root, strategies=default_strategies()
    )
    await worker.run_unit_fanout(step)
    from kb_platform.db.enums import StepStatus

    assert repo.get_step(step.id).status == StepStatus.SUCCEEDED
    ents = pd.read_parquet(f"{data_root}/entities.parquet")
    acme = ents[ents["title"] == "ACME"].iloc[0]
    assert (
        isinstance(acme["description"], str)
        and "d1" in acme["description"]
        and "d2" in acme["description"]
    )
    solo = ents[ents["title"] == "SOLO"].iloc[0]
    assert solo["description"] == "only"  # 未合并,原值保留
