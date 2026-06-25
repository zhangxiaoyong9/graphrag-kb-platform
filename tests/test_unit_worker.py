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
        kb = KnowledgeBase(
            name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)
        )
        s.add(kb)
        s.flush()
        from kb_platform.db.models import Chunk, Document

        # Insert a real Document so chunk FKs are satisfied now that FK
        # enforcement is on (PRAGMA foreign_keys=ON).
        doc = Document(
            kb_id=kb.id,
            title="d",
            source_uri="",
            content_hash="x",
            status="parsed",
            bytes=2,
            text="Foo Bar",
        )
        s.add(doc)
        s.flush()
        s.add(Chunk(chunk_id="c1", kb_id=kb.id, document_id=doc.id, ordinal=0, text="Foo Bar"))
        s.add(Chunk(chunk_id="c2", kb_id=kb.id, document_id=doc.id, ordinal=1, text="Baz Qux"))
    repo = Repository(engine)
    job = repo.create_job(
        kb_id=1, type="full", specs=[StepSpec("extract_graph", StepKind.UNIT_FANOUT)]
    )
    return repo, job.steps[0].id, str(tmp_path)


@pytest.mark.asyncio
async def test_all_units_succeed_writes_parquet(setup):
    from kb_platform.engine.strategy import default_strategies

    repo, step_id, data_root = setup
    worker = UnitWorker(
        repo=repo, adapter=FakeGraphAdapter(), data_root=data_root, strategies=default_strategies()
    )
    step = repo.get_step(step_id)
    await worker.run_unit_fanout(step)
    assert repo.get_step(step_id).status == "succeeded"
    entities = pd.read_parquet(f"{data_root}/entities.parquet")
    relationships = pd.read_parquet(f"{data_root}/relationships.parquet")
    assert not entities.empty
    assert not relationships.empty


@pytest.mark.asyncio
async def test_failed_unit_marks_step_partially_failed(setup):
    from kb_platform.engine.strategy import default_strategies

    repo, step_id, data_root = setup
    # 让 c2 失败
    worker = UnitWorker(
        repo=repo,
        adapter=FakeGraphAdapter(fail_on={"c2"}),
        data_root=data_root,
        strategies=default_strategies(),
    )
    step = repo.get_step(step_id)
    await worker.run_unit_fanout(step)
    assert repo.get_step(step_id).status == "partially_failed"
    units = repo.list_units(step_id)
    assert {u.status for u in units} == {"succeeded", "failed"}
    # parquet 不写(有失败)
    import os

    assert not os.path.exists(f"{data_root}/entities.parquet")


@pytest.mark.asyncio
async def test_unit_running_stamps_worker_id_and_heartbeat(setup):
    repo, step_id, data_root = setup  # 复用 2a 的 fixture(2 chunk)
    from kb_platform.engine.strategy import default_strategies
    from kb_platform.engine.unit_worker import UnitWorker
    from kb_platform.graph.adapter import FakeGraphAdapter

    worker = UnitWorker(
        repo=repo,
        adapter=FakeGraphAdapter(),
        data_root=data_root,
        worker_id="w1",
        heartbeat_interval=0.01,
        strategies=default_strategies(),
    )
    await worker.run_unit_fanout(repo.get_step(step_id))
    units = repo.list_units(step_id)
    assert all(u.worker_id == "w1" for u in units)
    assert all(u.heartbeat_at is not None for u in units)


@pytest.mark.asyncio
async def test_unit_worker_records_cost_from_completion(setup):
    """A strategy whose run_unit calls a CostCapturingCompletion yields cost_json on the unit."""
    import json

    from kb_platform.db.enums import StepStatus
    from kb_platform.engine.strategy import Subject, UnitResult
    from kb_platform.graph.cost_capture import CostCapturingCompletion

    repo, step_id, data_root = setup

    class FakeUsage:
        prompt_tokens = 10
        completion_tokens = 5

    class FakeResp:
        usage = FakeUsage()

    class FakeCompletion:
        async def completion_async(self, **kw):
            return FakeResp()

    class CostStrategy:
        kind = "extract_graph"

        def __init__(self):
            self._done = False

        def next_units_batch(self, repo, step):
            if self._done:
                return None
            self._done = True
            # One unit over chunk c1 (seeded by the fixture).
            return [Subject("chunk", "c1")]

        async def run_unit(self, adapter, unit, repo):
            await adapter.completion.completion_async(messages="x")
            return UnitResult(payload=None)

        def persist(self, data_root, unit, result):
            return None

        def finalize(self, repo, adapter, step, data_root, min_success_ratio):
            return StepStatus.SUCCEEDED

    adapter = type(
        "A", (), {"completion": CostCapturingCompletion(FakeCompletion(), model_id="gpt-4o-mini")}
    )()
    worker = UnitWorker(
        repo=repo,
        adapter=adapter,
        data_root=data_root,
        strategies={"extract_graph": CostStrategy()},
    )
    step = repo.get_step(step_id)
    await worker.run_unit_fanout(step, 1.0)

    units = repo.list_units(step_id)
    assert len(units) == 1
    assert units[0].cost_json is not None
    cost = json.loads(units[0].cost_json)
    assert cost["items"][0]["prompt_tokens"] == 10
    assert cost["items"][0]["completion_tokens"] == 5
    assert cost["items"][0]["model"] == "gpt-4o-mini"
