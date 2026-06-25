import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.orchestrator import Orchestrator


@pytest.fixture()
def setup(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    repo = Repository(engine)
    repo.add_document(kb_id=1, title="d1", text="Hello World Foo Bar " * 200)
    return repo, str(tmp_path)


@pytest.mark.asyncio
async def test_orchestrator_runs_pipeline_and_writes_parquet(setup):
    from kb_platform.graph.adapter import FakeGraphAdapter

    repo, data_root = setup
    adapter = FakeGraphAdapter()
    orch = Orchestrator(repo=repo, adapter=adapter, data_root=data_root)

    job = repo.create_job(kb_id=1, type="full", specs=Orchestrator.plan_full())
    await orch.run(job.id)

    # chunk 步产出 chunk 行
    chunks = repo.get_chunks(kb_id=1)
    assert len(chunks) >= 1
    # extract_graph 步产出 entities/relationships parquet
    import pandas as pd

    entities = pd.read_parquet(f"{data_root}/entities.parquet")
    assert not entities.empty
    job2 = repo.get_job(job.id)
    assert job2.status == "succeeded"


def test_plan_has_six_steps():
    from kb_platform.engine.orchestrator import Orchestrator

    names = [s.name for s in Orchestrator.plan_full()]
    assert names == ["chunk_documents", "extract_graph", "summarize_descriptions", "finalize_graph", "create_communities", "community_reports"]


def test_plan_full_unchanged():
    from kb_platform.engine.orchestrator import Orchestrator

    assert [s.name for s in Orchestrator.plan_full()] == [
        "chunk_documents", "extract_graph", "summarize_descriptions",
        "finalize_graph", "create_communities", "community_reports",
    ]


def test_plan_incremental_returns_delta_steps():
    from kb_platform.engine.orchestrator import Orchestrator

    names = [s.name for s in Orchestrator.plan_incremental()]
    # 起步:至少含 load_update_documents + extract_graph + merge_delta(后续任务补全)
    assert "extract_graph" in names
    assert "merge_delta" in names
    assert "load_update_documents" in names
    assert "update_clean_state" in names
