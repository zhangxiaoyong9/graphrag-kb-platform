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
        kb = KnowledgeBase(
            name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)
        )
        s.add(kb)
    return Repository(engine)


def test_create_job_and_claim_units(repo):
    with session_scope(repo.engine) as s:
        kb = s.query(KnowledgeBase).one()
        # Insert a real Document so chunk FKs are satisfied now that FK
        # enforcement is on (PRAGMA foreign_keys=ON).
        doc = Document(
            kb_id=kb.id,
            title="d",
            source_uri="",
            content_hash="x",
            status="parsed",
            bytes=2,
            text="t1",
        )
        s.add(doc)
        s.flush()
        # 预置 chunks 供 extract_graph 使用
        s.add(Chunk(chunk_id="c1", kb_id=kb.id, document_id=doc.id, ordinal=0, text="t1"))
        s.add(Chunk(chunk_id="c2", kb_id=kb.id, document_id=doc.id, ordinal=1, text="t2"))

    job = repo.create_job(
        kb_id=1,
        type="full",
        specs=[
            StepSpec("chunk_documents", StepKind.ATOMIC),
            StepSpec("extract_graph", StepKind.UNIT_FANOUT),
        ],
    )
    extract_step = [s for s in job.steps if s.name == "extract_graph"][0]
    # 手动预置两个单元
    repo.add_units(extract_step.id, [("chunk", "c1"), ("chunk", "c2")], kind="extract_graph")

    claimed = repo.claim_pending_units(extract_step.id)
    assert {u.subject_id for u in claimed} == {"c1", "c2"}
    assert all(u.status == UnitStatus.RUNNING for u in claimed)

    # 再申领应空
    assert repo.claim_pending_units(extract_step.id) == []


def test_unit_retry_resets_to_pending(repo):
    job = repo.create_job(
        kb_id=1, type="full", specs=[StepSpec("extract_graph", StepKind.UNIT_FANOUT)]
    )
    repo.add_units(job.steps[0].id, [("chunk", "c1")], kind="extract_graph")
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
    u = repo.add_unit(step.id, "chunk", "c1", kind="extract_graph")
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
    u = repo.add_unit(step.id, "chunk", "c1", kind="extract_graph")
    repo.set_unit_succeeded(u.id, input_hash="h", cost_json='{"t":1}', llm_raw_output="raw")
    fresh = repo.get_unit_by_subject(step.id, "chunk", "c1")
    assert fresh.status == UnitStatus.SUCCEEDED
    assert (
        fresh.input_hash == "h" and fresh.cost_json == '{"t":1}' and fresh.llm_raw_output == "raw"
    )
    repo.mark_needs_reconsolidation(u.id)
    assert repo.get_unit_by_subject(step.id, "chunk", "c1").needs_reconsolidation is True


def test_new_unit_kinds_exist():
    assert UnitKind.SUMMARIZE_DESCRIPTIONS == "summarize_descriptions"
    assert UnitKind.COMMUNITY_REPORT == "community_report"


def _seed_unit(s, *, uid, step_id, kind, stype, sid, status, input_hash):
    from kb_platform.db.models import Unit

    s.add(
        Unit(
            id=uid,
            step_id=step_id,
            kind=kind,
            subject_type=stype,
            subject_id=sid,
            status=status,
            input_hash=input_hash,
        )
    )


def test_last_succeeded_input_hash(tmp_path):
    from kb_platform.db.engine import create_engine, session_scope
    from kb_platform.db.repository import Repository
    from kb_platform.db.models import Base, KnowledgeBase, Job, Step
    from kb_platform.db.enums import JobStatus, StepStatus, UnitKind, UnitStatus

    engine = create_engine(f"sqlite:///{tmp_path}/db.sqlite")
    Base.metadata.create_all(engine)
    repo = Repository(engine)
    with session_scope(repo.engine) as s:
        s.add(KnowledgeBase(id=1, name="k", settings_json="{}", data_root=str(tmp_path)))
        s.flush()
        s.add(Job(id=1, kb_id=1, type="full", status=JobStatus.SUCCEEDED))
        s.add(Job(id=2, kb_id=1, type="incremental", status=JobStatus.SUCCEEDED))
        s.flush()
        s.add(
            Step(
                id=10,
                job_id=1,
                name="summarize_descriptions",
                ordinal=0,
                kind="unit_fanout",
                status=StepStatus.SUCCEEDED,
            )
        )
        s.add(
            Step(
                id=20,
                job_id=2,
                name="summarize_descriptions",
                ordinal=0,
                kind="unit_fanout",
                status=StepStatus.SUCCEEDED,
            )
        )
        s.flush()
        _seed_unit(
            s,
            uid=1,
            step_id=10,
            kind=UnitKind.SUMMARIZE_DESCRIPTIONS,
            stype="entity",
            sid="E",
            status=UnitStatus.SUCCEEDED,
            input_hash="old",
        )
        _seed_unit(
            s,
            uid=2,
            step_id=20,
            kind=UnitKind.SUMMARIZE_DESCRIPTIONS,
            stype="entity",
            sid="E",
            status=UnitStatus.SUCCEEDED,
            input_hash="new",
        )
        s.flush()
    # Most recent succeeded wins (higher unit id):
    assert repo.last_succeeded_input_hash(1, "summarize_descriptions", "entity", "E") == "new"
    assert repo.last_succeeded_input_hash(1, "summarize_descriptions", "entity", "MISSING") is None
    assert repo.has_succeeded_input_hash(1, "summarize_descriptions", "old") is True
    assert repo.has_succeeded_input_hash(1, "summarize_descriptions", "never") is False


def test_last_succeeded_input_hash_isolated_per_kb(tmp_path):
    """The Job.kb_id JOIN must exclude units belonging to a different KB:
    a kb=2 unit sharing kind/subject_id/input_hash with a kb=1 unit must NOT
    leak across the boundary, and vice versa."""
    from kb_platform.db.engine import create_engine, session_scope
    from kb_platform.db.repository import Repository
    from kb_platform.db.models import Base, KnowledgeBase, Job, Step
    from kb_platform.db.enums import JobStatus, StepStatus, UnitKind, UnitStatus

    engine = create_engine(f"sqlite:///{tmp_path}/db.sqlite")
    Base.metadata.create_all(engine)
    repo = Repository(engine)
    with session_scope(repo.engine) as s:
        s.add(KnowledgeBase(id=1, name="k1", settings_json="{}", data_root=str(tmp_path)))
        s.add(KnowledgeBase(id=2, name="k2", settings_json="{}", data_root=str(tmp_path)))
        s.flush()
        # kb=1 job/step with a succeeded unit (entity E, hash "kb1-hash")
        s.add(Job(id=1, kb_id=1, type="full", status=JobStatus.SUCCEEDED))
        # kb=2 job/step with a succeeded unit (SAME kind/subject_id, hash "kb2-hash")
        s.add(Job(id=2, kb_id=2, type="full", status=JobStatus.SUCCEEDED))
        s.flush()
        s.add(
            Step(
                id=10,
                job_id=1,
                name="summarize_descriptions",
                ordinal=0,
                kind="unit_fanout",
                status=StepStatus.SUCCEEDED,
            )
        )
        s.add(
            Step(
                id=20,
                job_id=2,
                name="summarize_descriptions",
                ordinal=0,
                kind="unit_fanout",
                status=StepStatus.SUCCEEDED,
            )
        )
        s.flush()
        _seed_unit(
            s,
            uid=1,
            step_id=10,
            kind=UnitKind.SUMMARIZE_DESCRIPTIONS,
            stype="entity",
            sid="E",
            status=UnitStatus.SUCCEEDED,
            input_hash="kb1-hash",
        )
        _seed_unit(
            s,
            uid=2,
            step_id=20,
            kind=UnitKind.SUMMARIZE_DESCRIPTIONS,
            stype="entity",
            sid="E",
            status=UnitStatus.SUCCEEDED,
            input_hash="kb2-hash",
        )
        s.flush()
    # kb=2 lookup must return the kb=2 unit's hash, NOT the kb=1 one.
    assert repo.last_succeeded_input_hash(2, "summarize_descriptions", "entity", "E") == "kb2-hash"
    # has_succeeded_input_hash is also kb-scoped: kb=2 sees only its own hash.
    assert repo.has_succeeded_input_hash(2, "summarize_descriptions", "kb2-hash") is True
    assert repo.has_succeeded_input_hash(2, "summarize_descriptions", "kb1-hash") is False


def test_job_cost_aggregates_by_step_and_model(tmp_path):
    import json

    from kb_platform.db.engine import create_engine, session_scope
    from kb_platform.db.enums import JobStatus, StepStatus, UnitKind, UnitStatus
    from kb_platform.db.models import Base, Job, KnowledgeBase, Step, Unit
    from kb_platform.db.repository import Repository

    engine = create_engine(f"sqlite:///{tmp_path}/db.sqlite")
    Base.metadata.create_all(engine)
    repo = Repository(engine)
    with session_scope(repo.engine) as s:
        s.add(KnowledgeBase(id=1, name="k", settings_json="{}", data_root=str(tmp_path)))
        s.flush()
        s.add(Job(id=1, kb_id=1, type="full", status=JobStatus.SUCCEEDED))
        s.add(
            Step(
                id=10,
                job_id=1,
                name="extract_graph",
                ordinal=0,
                kind="unit_fanout",
                status=StepStatus.SUCCEEDED,
            )
        )
        s.add(
            Step(
                id=11,
                job_id=1,
                name="summarize_descriptions",
                ordinal=1,
                kind="unit_fanout",
                status=StepStatus.SUCCEEDED,
            )
        )
        s.flush()
        s.add(
            Unit(
                step_id=10,
                kind=UnitKind.EXTRACT_GRAPH,
                subject_type="chunk",
                subject_id="c1",
                status=UnitStatus.SUCCEEDED,
                cost_json=json.dumps(
                    {
                        "items": [
                            {
                                "model": "deepseek-chat",
                                "prompt_tokens": 100,
                                "completion_tokens": 20,
                                "estimated_cost_usd": 0.01,
                            }
                        ],
                        "total_usd": 0.01,
                    }
                ),
            )
        )
        s.add(
            Unit(
                step_id=11,
                kind=UnitKind.SUMMARIZE_DESCRIPTIONS,
                subject_type="entity",
                subject_id="E",
                status=UnitStatus.SUCCEEDED,
                cost_json=json.dumps(
                    {
                        "items": [
                            {
                                "model": "deepseek-chat",
                                "prompt_tokens": 40,
                                "completion_tokens": 10,
                                "estimated_cost_usd": 0.004,
                            }
                        ],
                        "total_usd": 0.004,
                    }
                ),
            )
        )
        s.add(
            Unit(
                step_id=11,
                kind=UnitKind.SUMMARIZE_DESCRIPTIONS,
                subject_type="entity",
                subject_id="F",
                status=UnitStatus.SUCCEEDED,
                cost_json=None,
            )
        )
        s.flush()
    out = repo.job_cost(1)
    assert out["total_usd"] == 0.014
    assert out["by_step"]["extract_graph"] == 0.01
    assert out["by_step"]["summarize_descriptions"] == 0.004
    assert out["by_model"]["deepseek-chat"]["prompt_tokens"] == 140
    assert out["by_model"]["deepseek-chat"]["usd"] == 0.014


def test_kb_cost_aggregates_across_jobs(tmp_path):
    """Two jobs under one KB: kb_cost totals both, and by_job breaks them out."""
    import json

    from kb_platform.db.engine import create_engine, session_scope
    from kb_platform.db.enums import JobStatus, StepStatus, UnitKind, UnitStatus
    from kb_platform.db.models import Base, Job, KnowledgeBase, Step, Unit
    from kb_platform.db.repository import Repository

    engine = create_engine(f"sqlite:///{tmp_path}/db.sqlite")
    Base.metadata.create_all(engine)
    repo = Repository(engine)
    with session_scope(repo.engine) as s:
        s.add(KnowledgeBase(id=1, name="k", settings_json="{}", data_root=str(tmp_path)))
        s.flush()
        # Job 1: one extract_graph unit costing 0.02
        s.add(Job(id=1, kb_id=1, type="full", status=JobStatus.SUCCEEDED))
        s.add(
            Step(
                id=10,
                job_id=1,
                name="extract_graph",
                ordinal=0,
                kind="unit_fanout",
                status=StepStatus.SUCCEEDED,
            )
        )
        s.flush()
        s.add(
            Unit(
                step_id=10,
                kind=UnitKind.EXTRACT_GRAPH,
                subject_type="chunk",
                subject_id="c1",
                status=UnitStatus.SUCCEEDED,
                cost_json=json.dumps(
                    {
                        "items": [
                            {
                                "model": "deepseek-chat",
                                "prompt_tokens": 200,
                                "completion_tokens": 50,
                                "estimated_cost_usd": 0.02,
                            }
                        ],
                        "total_usd": 0.02,
                    }
                ),
            )
        )
        # Job 2: one summarize_descriptions unit costing 0.005
        s.add(Job(id=2, kb_id=1, type="incremental", status=JobStatus.SUCCEEDED))
        s.add(
            Step(
                id=20,
                job_id=2,
                name="summarize_descriptions",
                ordinal=0,
                kind="unit_fanout",
                status=StepStatus.SUCCEEDED,
            )
        )
        s.flush()
        s.add(
            Unit(
                step_id=20,
                kind=UnitKind.SUMMARIZE_DESCRIPTIONS,
                subject_type="entity",
                subject_id="E",
                status=UnitStatus.SUCCEEDED,
                cost_json=json.dumps(
                    {
                        "items": [
                            {
                                "model": "deepseek-chat",
                                "prompt_tokens": 60,
                                "completion_tokens": 15,
                                "estimated_cost_usd": 0.005,
                            }
                        ],
                        "total_usd": 0.005,
                    }
                ),
            )
        )
        s.flush()
    out = repo.kb_cost(1)
    # Overall totals sum both jobs
    assert out["total_usd"] == 0.025
    assert out["by_step"]["extract_graph"] == 0.02
    assert out["by_step"]["summarize_descriptions"] == 0.005
    # by_model aggregates tokens across both jobs
    assert out["by_model"]["deepseek-chat"]["prompt_tokens"] == 260
    assert out["by_model"]["deepseek-chat"]["completion_tokens"] == 65
    assert out["by_model"]["deepseek-chat"]["usd"] == 0.025
    # by_job breaks out per-job totals
    assert out["by_job"][1] == 0.02
    assert out["by_job"][2] == 0.005
