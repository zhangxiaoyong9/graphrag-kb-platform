import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import StepKind, UnitKind, UnitStatus
from kb_platform.db.models import Base, Document, KnowledgeBase, Chunk
from kb_platform.db.repository import Repository
from kb_platform.engine.spec import StepSpec


@pytest.fixture()
def repo(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        kb = KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path))
        s.add(kb)
    return Repository(engine)


def test_create_job_and_claim_units(repo):
    with session_scope(repo.engine) as s:
        kb = s.query(KnowledgeBase).one()
        # Insert a real Document so chunk FKs are satisfied now that FK
        # enforcement is on (PRAGMA foreign_keys=ON).
        doc = Document(kb_id=kb.id, title="d", source_uri="", content_hash="x", status="parsed", bytes=2, text="t1")
        s.add(doc)
        s.flush()
        # 预置 chunks 供 extract_graph 使用
        s.add(Chunk(chunk_id="c1", kb_id=kb.id, document_id=doc.id, ordinal=0, text="t1"))
        s.add(Chunk(chunk_id="c2", kb_id=kb.id, document_id=doc.id, ordinal=1, text="t2"))

    job = repo.create_job(kb_id=1, type="full", specs=[StepSpec("chunk_documents", StepKind.ATOMIC), StepSpec("extract_graph", StepKind.UNIT_FANOUT)])
    extract_step = [s for s in job.steps if s.name == "extract_graph"][0]
    # 手动预置两个单元
    repo.add_units(extract_step.id, [("chunk", "c1"), ("chunk", "c2")])

    claimed = repo.claim_pending_units(extract_step.id)
    assert {u.subject_id for u in claimed} == {"c1", "c2"}
    assert all(u.status == UnitStatus.RUNNING for u in claimed)

    # 再申领应空
    assert repo.claim_pending_units(extract_step.id) == []


def test_unit_retry_resets_to_pending(repo):
    job = repo.create_job(kb_id=1, type="full", specs=[StepSpec("extract_graph", StepKind.UNIT_FANOUT)])
    repo.add_units(job.steps[0].id, [("chunk", "c1")])
    uid = repo.list_units(job.steps[0].id)[0].id
    repo.set_unit_failed(uid, "boom")
    repo.reset_unit_to_pending(uid)
    assert repo.list_units(job.steps[0].id)[0].status == UnitStatus.PENDING


def test_get_or_create_and_running(repo):
    job = repo.create_job(
        kb_id=1,
        type="full",
        specs=[StepSpec("extract_graph", StepKind.UNIT_FANOUT)],
    )
    step = job.steps[0]
    u = repo.add_unit(step.id, "chunk", "c1")
    assert u.status == UnitStatus.PENDING
    assert repo.get_unit_by_subject(step.id, "chunk", "c1").id == u.id
    repo.set_unit_running(u.id)
    assert repo.get_unit_by_subject(step.id, "chunk", "c1").status == UnitStatus.RUNNING


def test_set_unit_succeeded_stores_meta_and_reconsolidation(repo):
    job = repo.create_job(
        kb_id=1,
        type="full",
        specs=[StepSpec("extract_graph", StepKind.UNIT_FANOUT)],
    )
    step = job.steps[0]
    u = repo.add_unit(step.id, "chunk", "c1")
    repo.set_unit_succeeded(u.id, input_hash="h", cost_json='{"t":1}', llm_raw_output="raw")
    fresh = repo.get_unit_by_subject(step.id, "chunk", "c1")
    assert fresh.status == UnitStatus.SUCCEEDED
    assert fresh.input_hash == "h" and fresh.cost_json == '{"t":1}' and fresh.llm_raw_output == "raw"
    repo.mark_needs_reconsolidation(u.id)
    assert repo.get_unit_by_subject(step.id, "chunk", "c1").needs_reconsolidation is True


def test_new_unit_kinds_exist():
    assert UnitKind.SUMMARIZE_DESCRIPTIONS == "summarize_descriptions"
    assert UnitKind.COMMUNITY_REPORT == "community_report"
