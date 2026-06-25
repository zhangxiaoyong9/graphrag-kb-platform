import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.orchestrator import Orchestrator
from kb_platform.graph.adapter import FakeGraphAdapter


@pytest.fixture()
def kb(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(
            KnowledgeBase(
                name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)
            )
        )
    repo = Repository(engine)
    repo.add_document(kb_id=1, title="A", text="ACME Org Bob Foo Bar Baz " * 200)
    return repo, str(tmp_path)


@pytest.mark.asyncio
async def test_full_then_incremental_only_llms_new_chunks(kb):
    repo, data_root = kb
    # 1) full 索引文档 A
    orch = Orchestrator(repo=repo, adapter=FakeGraphAdapter(), data_root=data_root)
    full = repo.create_job_pending(kb_id=1, method="standard", type="full")
    await orch.run(full.id)
    assert repo.get_job(full.id).status == "succeeded"
    full_extract = [s for s in repo.get_steps(full.id) if s.name == "extract_graph"][0]
    full_chunk_ids = {u.subject_id for u in repo.list_units(full_extract.id)}

    # 2) 加文档 B,跑增量
    repo.add_document(kb_id=1, title="B", text="Globex Corp Alice Qux " * 200)
    incr = repo.create_job_pending(kb_id=1, method="standard", type="incremental")
    await orch.run(incr.id)
    assert repo.get_job(incr.id).status == "succeeded"
    incr_extract = [s for s in repo.get_steps(incr.id) if s.name == "extract_graph"][0]
    incr_chunk_ids = {u.subject_id for u in repo.list_units(incr_extract.id)}

    # 核心承诺:增量只处理新 chunk(与 full 的 chunk 不重叠)
    assert incr_chunk_ids.isdisjoint(full_chunk_ids)
    # merge 后实体表含 A+B 的实体
    import pandas as pd

    ents = pd.read_parquet(f"{data_root}/entities.parquet")
    assert "GLOBEX" in set(ents["title"])


def test_incremental_uses_delta_strategies():
    """Incremental summarize/community_reports resolve to the Delta variants; full does not."""
    from kb_platform.engine import orchestrator as orch_mod
    from kb_platform.engine.strategies.delta import (
        CommunityReportsDeltaStrategy,
        SummarizeDeltaStrategy,
    )

    o = orch_mod.Orchestrator(repo=object(), adapter=object(), data_root=".", strategies=None)

    class _Inc:
        type = "incremental"

    class _Full:
        type = "full"

    inc = o._strategies_for(_Inc())
    assert isinstance(inc["summarize_descriptions"], SummarizeDeltaStrategy)
    assert isinstance(inc["community_reports"], CommunityReportsDeltaStrategy)
    full = o._strategies_for(_Full())
    assert not isinstance(full["summarize_descriptions"], SummarizeDeltaStrategy)
    assert not isinstance(full["community_reports"], CommunityReportsDeltaStrategy)


def test_retry_resolves_delta_for_incremental_job(tmp_path):
    """A retried unit in an incremental job uses the Delta strategies (esp. community_reports)."""
    from kb_platform.engine.strategies.delta import CommunityReportsDeltaStrategy
    from kb_platform.retry import RetryService

    engine = create_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(
            KnowledgeBase(
                name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)
            )
        )
    repo = Repository(engine)
    job = repo.create_job_pending(kb_id=1, method="standard", type="incremental")
    step = repo.get_steps(job.id)[0]

    retry = RetryService(repo=repo, adapter=FakeGraphAdapter(), data_root=str(tmp_path))
    strats = retry._strategies(step)
    assert isinstance(strats["community_reports"], CommunityReportsDeltaStrategy)

    # Full job -> base strategies (not delta).
    full_job = repo.create_job_pending(kb_id=1, method="standard", type="full")
    full_step = repo.get_steps(full_job.id)[0]
    assert not isinstance(
        retry._strategies(full_step)["community_reports"], CommunityReportsDeltaStrategy
    )

    # Explicit override wins even for incremental jobs.
    from kb_platform.engine.strategy import default_strategies

    override = default_strategies()
    retry2 = RetryService(
        repo=repo, adapter=FakeGraphAdapter(), data_root=str(tmp_path), strategies=override
    )
    assert retry2._strategies(step) is override
