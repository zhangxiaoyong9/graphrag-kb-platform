import pandas as pd
import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import StepKind, StepStatus
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.spec import StepSpec
from kb_platform.engine.unit_worker import UnitWorker
from kb_platform.graph.adapter import FakeGraphAdapter


class _SummarizeFailAdapter(FakeGraphAdapter):
    def __init__(self, fail_entity: str):
        super().__init__()
        self._fail_entity = fail_entity

    async def summarize_entity(self, name, descriptions):
        if name == self._fail_entity:
            raise RuntimeError(f"injected summarize failure for {name}")
        return await super().summarize_entity(name, descriptions)


class _ReportFailAdapter(FakeGraphAdapter):
    def __init__(self, fail_community: str):
        super().__init__()
        self._fail_community = fail_community

    async def report_community(self, context):
        if context["community"] == self._fail_community:
            raise RuntimeError(f"injected report failure for {context['community']}")
        return await super().report_community(context)


def _kb(tmp_path, entities=None, communities=None):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(
            KnowledgeBase(
                name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)
            )
        )
    if entities is not None:
        pd.DataFrame(entities).to_parquet(f"{tmp_path}/entities.parquet")
    if communities is not None:
        pd.DataFrame(communities).to_parquet(f"{tmp_path}/communities.parquet")
    return Repository(engine), str(tmp_path)


@pytest.mark.asyncio
async def test_summarize_persistent_failure_terminates_partial(tmp_path):
    # 两个多描述实体,其中一个持续失败
    repo, data_root = _kb(
        tmp_path,
        entities=[
            {
                "title": "ACME",
                "type": "ORG",
                "description": ["d1", "d2"],
                "text_unit_ids": ["c1", "c2"],
                "frequency": 2,
            },
            {
                "title": "BETA",
                "type": "ORG",
                "description": ["d3", "d4"],
                "text_unit_ids": ["c3", "c4"],
                "frequency": 2,
            },
        ],
    )
    step = repo.create_job(
        kb_id=1, type="full", specs=[StepSpec("summarize_descriptions", StepKind.UNIT_FANOUT)]
    ).steps[0]
    from kb_platform.engine.strategy import default_strategies

    worker = UnitWorker(
        repo=repo,
        adapter=_SummarizeFailAdapter("ACME"),
        data_root=data_root,
        strategies=default_strategies(),
    )
    await worker.run_unit_fanout(step)  # must TERMINATE, not hang
    assert repo.get_step(step.id).status == StepStatus.PARTIALLY_FAILED


@pytest.mark.asyncio
async def test_community_reports_persistent_failure_terminates_partial(tmp_path):
    repo, data_root = _kb(
        tmp_path,
        entities=[
            {
                "title": "A",
                "type": "T",
                "description": "da",
                "text_unit_ids": ["c1"],
                "frequency": 1,
                "degree": 1,
            }
        ],
        communities=[{"level": 0, "community_id": "C0", "parent": "C0", "entity_ids": ["A"]}],
    )
    # relationships needed by community_reports context
    pd.DataFrame(
        [
            {
                "source": "A",
                "target": "A",
                "weight": 1.0,
                "description": ["d"],
                "text_unit_ids": ["c1"],
                "combined_degree": 2,
            }
        ]
    ).to_parquet(f"{tmp_path}/relationships.parquet")
    step = repo.create_job(
        kb_id=1, type="full", specs=[StepSpec("community_reports", StepKind.UNIT_FANOUT)]
    ).steps[0]
    from kb_platform.engine.strategy import default_strategies

    worker = UnitWorker(
        repo=repo,
        adapter=_ReportFailAdapter("C0"),
        data_root=data_root,
        strategies=default_strategies(),
    )
    await worker.run_unit_fanout(step)  # must TERMINATE, not hang
    assert repo.get_step(step.id).status == StepStatus.PARTIALLY_FAILED
