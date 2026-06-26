"""Retry must re-queue a FAILED job so the worker re-claims the retried unit.

Without reactivation, a retried unit sits at PENDING forever in a terminal-failed
job (the worker only claims PENDING jobs) — the 'stuck on processing' bug.
"""
from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import JobStatus, StepKind, StepStatus, UnitKind, UnitStatus
from kb_platform.db.models import Base, Job, KnowledgeBase, Step, Unit
from kb_platform.db.repository import Repository


def _repo(tmp_path):
    repo = Repository(create_engine(f"sqlite:///{tmp_path}/t.db"))
    Base.metadata.create_all(repo.engine)
    return repo


def _seed_failed(repo):
    """A FAILED job with one FAILED unit in a partially-failed extract_graph step."""
    with session_scope(repo.engine) as s:
        kb = KnowledgeBase(name="k", method="standard", settings_json="{}", data_root=".")
        s.add(kb)
        s.flush()
        job = Job(kb_id=kb.id, type="full", method="standard", status=JobStatus.FAILED)
        s.add(job)
        s.flush()
        step = Step(
            job_id=job.id,
            name="extract_graph",
            ordinal=1,
            kind=StepKind.UNIT_FANOUT,
            status=StepStatus.PARTIALLY_FAILED,
        )
        s.add(step)
        s.flush()
        unit = Unit(
            step_id=step.id,
            subject_type="chunk",
            subject_id="c1",
            kind=UnitKind.EXTRACT_GRAPH,
            status=UnitStatus.FAILED,
            error="boom",
        )
        s.add(unit)
        s.flush()
        return job.id, step.id, unit.id


def _job_status(repo, job_id):
    with session_scope(repo.engine) as s:
        return s.get(Job, job_id).status


def _unit_status(repo, unit_id):
    with session_scope(repo.engine) as s:
        return s.get(Unit, unit_id).status


def test_retry_unit_reactivates_failed_job(tmp_path):
    repo = _repo(tmp_path)
    job_id, _step_id, unit_id = _seed_failed(repo)
    assert _job_status(repo, job_id) == JobStatus.FAILED

    repo.reset_unit_to_pending(unit_id)
    repo.reactivate_job_for_unit(unit_id)

    assert _unit_status(repo, unit_id) == UnitStatus.PENDING
    assert _job_status(repo, job_id) == JobStatus.PENDING  # re-queued -> worker will reclaim


def test_retry_step_reactivates_failed_job(tmp_path):
    repo = _repo(tmp_path)
    job_id, step_id, _unit_id = _seed_failed(repo)

    n = repo.reset_failed_units_to_pending(step_id)
    repo.reactivate_job_for_step(step_id)

    assert n == 1
    assert _job_status(repo, job_id) == JobStatus.PENDING


def test_reactivate_leaves_succeeded_job_alone(tmp_path):
    repo = _repo(tmp_path)
    with session_scope(repo.engine) as s:
        kb = KnowledgeBase(name="k", method="standard", settings_json="{}", data_root=".")
        s.add(kb)
        s.flush()
        job = Job(kb_id=kb.id, type="full", method="standard", status=JobStatus.SUCCEEDED)
        s.add(job)
        s.flush()
        step = Step(job_id=job.id, name="extract_graph", ordinal=1, kind=StepKind.UNIT_FANOUT, status=StepStatus.SUCCEEDED)
        s.add(step)
        s.flush()
        unit = Unit(step_id=step.id, subject_type="chunk", subject_id="c1", kind=UnitKind.EXTRACT_GRAPH, status=UnitStatus.SUCCEEDED)
        s.add(unit)
        s.flush()
        job_id, unit_id = job.id, unit.id

    repo.reactivate_job_for_unit(unit_id)
    assert _job_status(repo, job_id) == JobStatus.SUCCEEDED  # not disturbed
