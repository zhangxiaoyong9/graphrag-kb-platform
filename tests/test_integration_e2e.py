import os

import pandas as pd
import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import StepKind, UnitStatus
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
    # 1400 词 → FakeGraphAdapter(默认 chunk_size=1000)切成 2 个 chunk,
    # 这样"部分失败 + 单 chunk 重试"能真正验证合并是否覆盖所有成功单元。
    repo.add_document(kb_id=1, title="d1", text="ACME Org Bob Person Foo Bar Baz " * 200)
    return repo, str(tmp_path)


@pytest.mark.asyncio
async def test_full_index_then_retry_single_chunk(kb):
    repo, data_root = kb
    # 首跑:让第一个 chunk 失败(第二个成功)
    failing_adapter = FakeGraphAdapter()
    chunks_preview = failing_adapter.chunk_document(1, repo.get_documents(1)[0].text)
    assert len(chunks_preview) >= 2  # 前置:确实有多个 chunk
    fail_id = chunks_preview[0].chunk_id
    orch = Orchestrator(repo=repo, adapter=FakeGraphAdapter(fail_on={fail_id}), data_root=data_root)
    job = repo.create_job(kb_id=1, type="full", specs=Orchestrator.plan())
    await orch.run(job.id)

    # job 因 extract_graph 步 partially_failed 而失败(阈值 1.0)
    assert repo.get_job(job.id).status == "failed"
    extract_step = [s for s in repo.get_steps(job.id) if s.name == "extract_graph"][0]
    assert extract_step.status == "partially_failed"
    failed_units = [u for u in repo.list_units(extract_step.id) if u.status == UnitStatus.FAILED]
    assert len(failed_units) == 1
    assert failed_units[0].subject_id == fail_id  # 失败的正是被注入的 chunk
    assert os.path.exists(f"{data_root}/entities.parquet") is False  # 有失败不写 parquet

    # 手动重试(用不失败的 adapter)
    retry = RetryService(repo=repo, adapter=FakeGraphAdapter(), data_root=data_root)
    retry.retry_unit(failed_units[0].id)
    await retry.rerun_step(extract_step.id)
    assert repo.get_step(extract_step.id).status == "succeeded"

    # parquet 已写;两个 chunk 的同名实体已合并。
    # FakeGraphAdapter 对每个出现的单词发射一行实体(chunk 0 含 143 个 "ACME",
    # chunk 1 含 57 个),合并后 frequency=143+57=200。
    # 该值只有在「重试失败的 chunk 0」成功后才能达到 ——
    # 若重试未生效只会剩 chunk 1 的 57,因此这是真正的「合并覆盖所有成功单元」不变量校验。
    entities = pd.read_parquet(f"{data_root}/entities.parquet")
    assert not entities.empty
    assert entities["title"].is_unique  # 同名实体已合并
    assert int(entities.loc[entities["title"] == "ACME", "frequency"].iloc[0]) == 200


@pytest.mark.asyncio
async def test_successful_index_no_parquet_gaps(kb):
    repo, data_root = kb
    orch = Orchestrator(repo=repo, adapter=FakeGraphAdapter(), data_root=data_root)
    job = repo.create_job(kb_id=1, type="full", specs=Orchestrator.plan())
    await orch.run(job.id)
    assert repo.get_job(job.id).status == "succeeded"
    assert os.path.exists(f"{data_root}/entities.parquet")
    assert os.path.exists(f"{data_root}/relationships.parquet")
    # 所有 extract_graph 单元均成功
    step = [s for s in repo.get_steps(job.id) if s.name == "extract_graph"][0]
    assert {u.status for u in repo.list_units(step.id)} == {UnitStatus.SUCCEEDED}
