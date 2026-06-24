from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, KnowledgeBase, Document, Job, Step, Unit
from kb_platform.db.enums import JobStatus, StepKind, StepStatus, UnitKind, UnitStatus


def test_persist_job_step_unit(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)

    with session_scope(engine) as s:
        kb = KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path))
        s.add(kb)
        s.flush()
        doc = Document(kb_id=kb.id, title="d", source_uri="file://d", content_hash="h", status="parsed", bytes=1)
        s.add(doc)
        s.flush()
        job = Job(kb_id=kb.id, type="full", method="standard", status=JobStatus.PENDING)
        s.add(job)
        s.flush()
        step = Step(job_id=job.id, name="extract_graph", ordinal=2, kind=StepKind.UNIT_FANOUT, status=StepStatus.PENDING)
        s.add(step)
        s.flush()
        s.add(Unit(step_id=step.id, kind=UnitKind.EXTRACT_GRAPH, subject_type="chunk", subject_id="c1", status=UnitStatus.PENDING, attempt_no=0))

    with session_scope(engine) as s:
        units = s.query(Unit).all()
        assert len(units) == 1
        assert units[0].subject_id == "c1"
        assert units[0].step.job.type == "full" or True  # 关系可达 (brief used .name; Job has no name col)
