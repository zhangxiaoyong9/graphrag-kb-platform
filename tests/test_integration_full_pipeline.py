import os

import pandas as pd
import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import UnitStatus
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.orchestrator import Orchestrator
from kb_platform.graph.adapter import FakeGraphAdapter
from kb_platform.retry import RetryService


@pytest.fixture()
def kb(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    repo = Repository(engine)
    # 多个实体 + 关系,确保聚出社区、有报告
    repo.add_document(kb_id=1, title="d1", text="ACME Org Bob Person ACME Org Alice Person Foo Bar Baz " * 200)
    return repo, str(tmp_path)


@pytest.mark.asyncio
async def test_full_pipeline_produces_all_four_parquets(kb):
    repo, data_root = kb
    orch = Orchestrator(repo=repo, adapter=FakeGraphAdapter(), data_root=data_root)
    job = repo.create_job(kb_id=1, type="full", specs=Orchestrator.plan_full())
    await orch.run(job.id)
    assert repo.get_job(job.id).status == "succeeded"
    for name in ("entities", "relationships", "communities", "community_reports"):
        assert os.path.exists(f"{data_root}/{name}.parquet"), f"missing {name}.parquet"
    ents = pd.read_parquet(f"{data_root}/entities.parquet")
    assert ents["title"].is_unique
    assert "degree" in ents.columns
    reports = pd.read_parquet(f"{data_root}/community_reports.parquet")
    assert not reports.empty


@pytest.mark.asyncio
async def test_proceed_on_failure_with_threshold(kb):
    repo, data_root = kb
    # 让第一个 chunk 抽取失败(community_reports/summarize 仍可推进,因为 extract 比例够)
    failing = FakeGraphAdapter()
    fail_id = failing.chunk_document(1, repo.get_documents(1)[0].text)[0].chunk_id
    orch = Orchestrator(repo=repo, adapter=FakeGraphAdapter(fail_on={fail_id}), data_root=data_root)
    job = repo.create_job(kb_id=1, type="full", specs=Orchestrator.plan_full())
    await orch.run(job.id, min_success_ratio=0.01)  # 极宽松,允许单 chunk 失败仍推进
    # extract_graph 步在宽松阈值下 SUCCEEDED(带着缺口),整个 job 应成功
    assert repo.get_job(job.id).status == "succeeded"
    extract = [s for s in repo.get_steps(job.id) if s.name == "extract_graph"][0]
    assert extract.status == "succeeded"
    # 仍有 failed 单元
    units = repo.list_units(extract.id)
    assert any(u.status == UnitStatus.FAILED for u in units)


@pytest.mark.asyncio
async def test_late_retry_marks_needs_reconsolidation(kb):
    repo, data_root = kb
    failing = FakeGraphAdapter()
    fail_id = failing.chunk_document(1, repo.get_documents(1)[0].text)[0].chunk_id
    orch = Orchestrator(repo=repo, adapter=FakeGraphAdapter(fail_on={fail_id}), data_root=data_root)
    job = repo.create_job(kb_id=1, type="full", specs=Orchestrator.plan_full())
    await orch.run(job.id, min_success_ratio=0.01)
    extract = [s for s in repo.get_steps(job.id) if s.name == "extract_graph"][0]
    failed_unit = [u for u in repo.list_units(extract.id) if u.status == UnitStatus.FAILED][0]
    # 步已结算后,重试该单元(用不失败的 adapter)
    retry = RetryService(repo=repo, adapter=FakeGraphAdapter(), data_root=data_root)
    retry.retry_unit(failed_unit.id)
    await retry.rerun_step(extract.id)
    fresh = repo.get_unit_by_subject(extract.id, "chunk", failed_unit.subject_id)
    assert fresh.status == UnitStatus.SUCCEEDED
    assert fresh.needs_reconsolidation is True
