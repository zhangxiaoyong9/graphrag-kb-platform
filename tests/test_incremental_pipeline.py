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

    # Phase 4 H end-to-end lock-in: the incremental summarize_descriptions step
    # must re-summarize FEWER entities than the full job, because the 6 entities
    # from doc A (ACME/ORG/BOB/FOO/BAR/BAZ) are unchanged by doc B and the Delta
    # strategy reuses their on-disk summaries. Only the 4 new entities from doc B
    # (GLOBEX/CORP/ALICE/QUX) get fresh summarize units. If the Delta skip were
    # broken (e.g. Unit.kind never populated so last_succeeded_input_hash always
    # misses), the incremental step would process all 10 merged entities and this
    # assertion would fail (10 > 6).
    def _step(job_id, name):
        return [s for s in repo.get_steps(job_id) if s.name == name][0]

    full_summ = _step(full.id, "summarize_descriptions")
    incr_summ = _step(incr.id, "summarize_descriptions")
    full_summ_units = repo.list_units(full_summ.id)
    incr_summ_units = repo.list_units(incr_summ.id)
    assert len(incr_summ_units) < len(full_summ_units), (
        f"delta summarize did not reduce units: full={len(full_summ_units)} "
        f"incr={len(incr_summ_units)}"
    )
    # The incremental summarize subjects must be exactly the new doc-B entities
    # (the unchanged doc-A entities are skipped by the Delta hash check).
    assert {u.subject_id for u in incr_summ_units} == {"GLOBEX", "CORP", "ALICE", "QUX"}
    # And every incremental summarize unit must have its kind recorded correctly
    # (this is what makes the cross-job input_hash lookup work).
    from kb_platform.db.enums import UnitKind

    assert all(u.kind == UnitKind.SUMMARIZE_DESCRIPTIONS for u in incr_summ_units)

    # A2: update_clean_state rebuilt text_units.parquet to include doc B's new
    # chunks (the incremental gap fix), and stats.json was written at job end.
    import json

    tu = pd.read_parquet(f"{data_root}/text_units.parquet")
    assert incr_chunk_ids.issubset(set(tu["id"])), "new chunks missing from text_units.parquet"
    from pathlib import Path

    stats = json.loads(Path(data_root, "stats.json").read_text())
    assert stats["entity_count"] >= 1


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


@pytest.mark.asyncio
async def test_delete_doc_shrinks_unique_entities_keeps_shared(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    repo = Repository(engine)
    repo.add_document(kb_id=1, title="A", text="SHARED ALPHA " * 200)   # 实体 SHARED, ALPHA
    repo.add_document(kb_id=1, title="B", text="SHARED BETA " * 200)    # 实体 SHARED, BETA
    orch = Orchestrator(repo=repo, adapter=FakeGraphAdapter(), data_root=str(tmp_path))
    full = repo.create_job_pending(kb_id=1, method="standard", type="full")
    await orch.run(full.id)
    import pandas as pd
    assert {"SHARED", "ALPHA", "BETA"} <= set(pd.read_parquet(f"{tmp_path}/entities.parquet")["title"])

    # 删文档 B，跑增量 → BETA（B 独有）消失，SHARED（A 也有）保留
    repo.delete_document(kb_id=1, doc_id=2)
    incr = repo.create_job_pending(kb_id=1, method="standard", type="incremental")
    await orch.run(incr.id)
    titles = set(pd.read_parquet(f"{tmp_path}/entities.parquet")["title"])
    assert "BETA" not in titles
    assert {"SHARED", "ALPHA"} <= titles


@pytest.mark.asyncio
async def test_delete_last_doc_yields_empty_graph(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    repo = Repository(engine)
    repo.add_document(kb_id=1, title="A", text="SHARED ALPHA " * 200)
    orch = Orchestrator(repo=repo, adapter=FakeGraphAdapter(), data_root=str(tmp_path))
    full = repo.create_job_pending(kb_id=1, method="standard", type="full")
    await orch.run(full.id)

    repo.delete_document(kb_id=1, doc_id=1)  # 删到空
    incr = repo.create_job_pending(kb_id=1, method="standard", type="incremental")
    await orch.run(incr.id)
    assert repo.get_job(incr.id).status == "succeeded"
    import pandas as pd
    assert len(pd.read_parquet(f"{tmp_path}/entities.parquet")) == 0
    assert len(pd.read_parquet(f"{tmp_path}/relationships.parquet")) == 0
    assert len(pd.read_parquet(f"{tmp_path}/communities.parquet")) == 0
    tu = pd.read_parquet(f"{tmp_path}/text_units.parquet")
    assert list(tu.columns) == ["id", "text", "document_ids", "n_tokens"] and len(tu) == 0
