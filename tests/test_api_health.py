from datetime import datetime, timedelta


def _app(tmp_path):
    from kb_platform.api.app import create_app
    from kb_platform.db.engine import create_engine
    from kb_platform.db.models import Base
    from kb_platform.db.repository import Repository

    engine = create_engine(f"sqlite:///{tmp_path}/db.sqlite")
    Base.metadata.create_all(engine)
    repo = Repository(engine)
    return repo, create_app(repo, data_root=str(tmp_path))


def test_health_ok_no_running_units(tmp_path):
    from fastapi.testclient import TestClient

    repo, app = _app(tmp_path)
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["worker"]["stale"] is False
    assert body["worker"]["last_heartbeat_at"] is None


def test_worker_status_stale(tmp_path):
    from kb_platform.db.repository import Repository
    from kb_platform.db.engine import create_engine, session_scope
    from kb_platform.db.models import Base, Job, KnowledgeBase, Step, Unit
    from kb_platform.db.enums import JobStatus, StepStatus, UnitStatus, UnitKind

    engine = create_engine(f"sqlite:///{tmp_path}/db.sqlite")
    Base.metadata.create_all(engine)
    repo = Repository(engine)
    with session_scope(repo.engine) as s:
        s.add(KnowledgeBase(id=1, name="kb1", data_root=str(tmp_path)))
        s.flush()
        s.add(Job(id=1, kb_id=1, type="full", status=JobStatus.RUNNING))
        s.add(
            Step(
                id=1,
                job_id=1,
                name="extract_graph",
                ordinal=0,
                kind="unit_fanout",
                status=StepStatus.RUNNING,
            )
        )
        s.add(
            Unit(
                id=1,
                step_id=1,
                kind=UnitKind.EXTRACT_GRAPH,
                subject_type="chunk",
                subject_id="c1",
                status=UnitStatus.RUNNING,
                heartbeat_at=datetime.now() - timedelta(seconds=120),
            )
        )
        s.flush()
    st = repo.worker_status(stale_seconds=60.0)
    assert st["stale"] is True
    assert st["last_heartbeat_at"] is not None
