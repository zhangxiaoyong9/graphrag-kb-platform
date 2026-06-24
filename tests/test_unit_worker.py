import pandas as pd
import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import StepKind
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
        kb = KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path))
        s.add(kb)
        s.flush()
        from kb_platform.db.models import Chunk

        s.add(Chunk(chunk_id="c1", kb_id=kb.id, document_id=1, ordinal=0, text="Foo Bar"))
        s.add(Chunk(chunk_id="c2", kb_id=kb.id, document_id=1, ordinal=1, text="Baz Qux"))
    repo = Repository(engine)
    job = repo.create_job(kb_id=1, type="full", specs=[StepSpec("extract_graph", StepKind.UNIT_FANOUT)])
    return repo, job.steps[0].id, str(tmp_path)


@pytest.mark.asyncio
async def test_all_units_succeed_writes_parquet(setup):
    repo, step_id, data_root = setup
    worker = UnitWorker(repo=repo, adapter=FakeGraphAdapter(), data_root=data_root)
    step = repo.get_step(step_id)
    await worker.run_unit_fanout(step)
    assert repo.get_step(step_id).status == "succeeded"
    entities = pd.read_parquet(f"{data_root}/entities.parquet")
    relationships = pd.read_parquet(f"{data_root}/relationships.parquet")
    assert not entities.empty
    assert not relationships.empty


@pytest.mark.asyncio
async def test_failed_unit_marks_step_partially_failed(setup):
    repo, step_id, data_root = setup
    # 让 c2 失败
    worker = UnitWorker(repo=repo, adapter=FakeGraphAdapter(fail_on={"c2"}), data_root=data_root)
    step = repo.get_step(step_id)
    await worker.run_unit_fanout(step)
    assert repo.get_step(step_id).status == "partially_failed"
    units = repo.list_units(step_id)
    assert {u.status for u in units} == {"succeeded", "failed"}
    # parquet 不写(有失败)
    import os

    assert not os.path.exists(f"{data_root}/entities.parquet")
