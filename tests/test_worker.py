"""Worker process tests: poll/claim and crash-recovery resume."""

import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import JobStatus
from kb_platform.db.models import Base, KnowledgeBase, Unit
from kb_platform.db.repository import Repository
from kb_platform.graph.adapter import FakeGraphAdapter


def _repo(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    repo = Repository(engine)
    repo.add_document(kb_id=1, title="d", text="ACME Org Bob Person Foo Bar Baz " * 200)
    return repo


@pytest.mark.asyncio
async def test_worker_picks_up_and_completes_pending_job(tmp_path):
    from kb_platform.worker import run_worker_once

    repo = _repo(tmp_path)
    job = repo.create_job_pending(kb_id=1, method="standard")
    assert job.status == "pending"
    await run_worker_once(repo=repo, adapter_factory=lambda kb: FakeGraphAdapter(), heartbeat_interval=0.01)
    assert repo.get_job(job.id).status == "succeeded"


@pytest.mark.asyncio
async def test_worker_crash_recovery_resumes(tmp_path):
    import hashlib
    from datetime import datetime, timedelta

    from kb_platform.worker import run_worker_once

    repo = _repo(tmp_path)
    job = repo.create_job_pending(kb_id=1, method="standard")
    # Simulate a crash: chunk_documents already ran, extract_graph started one
    # unit and then died mid-flight (stale heartbeat). Use a real chunk_id that
    # FakeGraphAdapter produces so the strategy re-claims it on resume.
    text = "ACME Org Bob Person Foo Bar Baz " * 200
    first_piece = " ".join(text.split()[:1000])
    real_chunk_id = hashlib.sha512(first_piece.encode()).hexdigest()
    repo.set_job_status(job.id, JobStatus.RUNNING)
    extract_step = [s for s in repo.get_steps(job.id) if s.name == "extract_graph"][0]
    repo.add_unit(extract_step.id, "chunk", real_chunk_id)
    stale = datetime.now() - timedelta(seconds=999)
    with session_scope(repo.engine) as s:
        u = s.query(Unit).filter_by(step_id=extract_step.id).one()
        u.status = "running"
        u.worker_id = "dead"
        u.heartbeat_at = stale
    # Recover + resume.
    await run_worker_once(
        repo=repo,
        adapter_factory=lambda kb: FakeGraphAdapter(),
        heartbeat_interval=0.01,
        recover=True,
    )
    assert repo.get_job(job.id).status == "succeeded"
