import pandas as pd
import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import StepKind
from kb_platform.db.models import Base, Chunk, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.spec import StepSpec
from kb_platform.engine.unit_worker import UnitWorker
from kb_platform.graph.adapter import FakeGraphAdapter
from kb_platform.retry import RetryService


@pytest.fixture()
def failed_step(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        kb = KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path))
        s.add(kb)
        s.flush()
        s.add(Chunk(chunk_id="c1", kb_id=kb.id, document_id=1, ordinal=0, text="Foo Bar"))
        s.add(Chunk(chunk_id="c2", kb_id=kb.id, document_id=1, ordinal=1, text="Baz Qux"))
    repo = Repository(engine)
    step = repo.create_job(kb_id=1, type="full", specs=[StepSpec("extract_graph", StepKind.UNIT_FANOUT)]).steps[0]
    # 预置单元(run_unit_fanout 首跑时会跳过创建,直接处理已存在的 pending 单元)
    repo.add_units(step.id, [("chunk", "c1"), ("chunk", "c2")])
    worker = UnitWorker(repo=repo, adapter=FakeGraphAdapter(fail_on={"c2"}), data_root=str(tmp_path))
    return repo, step.id, str(tmp_path), worker


@pytest.mark.asyncio
async def test_retry_failed_unit_then_rerun_recovers(failed_step):
    repo, step_id, data_root, worker = failed_step
    await worker.run_unit_fanout(repo.get_step(step_id))  # 首跑:c2 失败
    assert repo.get_step(step_id).status == "partially_failed"

    retry = RetryService(repo=repo, adapter=FakeGraphAdapter(), data_root=data_root)  # 不再失败
    n = retry.retry_step(step_id)  # 重置 failed 单元
    assert n == 1
    await retry.rerun_step(step_id)
    assert repo.get_step(step_id).status == "succeeded"
    assert pd.read_parquet(f"{data_root}/entities.parquet").empty is False


def test_retry_unit_resets_single_unit(failed_step):
    repo, step_id, _, _ = failed_step
    units = repo.list_units(step_id)
    # 预置一个 failed 单元
    repo.set_unit_failed(units[0].id, "x")
    retry = RetryService(repo=repo, adapter=FakeGraphAdapter(), data_root=".")
    retry.retry_unit(units[0].id)
    assert repo.list_units(step_id)[0].status == "pending"
