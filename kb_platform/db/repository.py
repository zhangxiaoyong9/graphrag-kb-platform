"""Data access for the control plane."""

from sqlalchemy import or_, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import selectinload

from kb_platform.db.engine import session_scope
from kb_platform.db.enums import JobStatus, StepStatus, UnitStatus
from kb_platform.db.models import Chunk, Document, Job, Step, Unit
from kb_platform.engine.spec import StepSpec


class Repository:
    """Thin DAO over the control-plane models."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # ---- documents / chunks ----
    def add_document(self, kb_id: int, title: str, text: str, source_uri: str = "") -> Document:
        import hashlib

        with session_scope(self.engine) as s:
            doc = Document(
                kb_id=kb_id, title=title, source_uri=source_uri,
                content_hash=hashlib.sha512(text.encode()).hexdigest(),
                status="parsed", bytes=len(text), text=text,
            )
            s.add(doc)
            s.flush()
            return doc

    def get_documents(self, kb_id: int) -> list[Document]:
        with session_scope(self.engine) as s:
            return list(s.scalars(select(Document).where(Document.kb_id == kb_id)))

    def add_chunks(self, chunks: list[Chunk]) -> None:
        with session_scope(self.engine) as s:
            for c in chunks:
                s.add(c)

    def get_chunks(self, kb_id: int) -> list[Chunk]:
        with session_scope(self.engine) as s:
            return list(s.scalars(select(Chunk).where(Chunk.kb_id == kb_id).order_by(Chunk.ordinal)))

    # ---- jobs / steps ----
    def create_job(self, kb_id: int, type: str, specs: list[StepSpec], method: str = "standard") -> Job:
        with session_scope(self.engine) as s:
            job = Job(kb_id=kb_id, type=type, method=method, status=JobStatus.PENDING)
            s.add(job)
            s.flush()
            for ordinal, spec in enumerate(specs):
                s.add(Step(job_id=job.id, name=spec.name, ordinal=ordinal, kind=spec.kind, status=StepStatus.PENDING))
            s.flush()
            # Touch the relationship so it is loaded before the session closes
            # (expire_on_commit=False keeps it accessible afterwards).
            list(job.steps)
            return job

    def get_job(self, job_id: int) -> Job | None:
        with session_scope(self.engine) as s:
            return s.scalars(
                select(Job).where(Job.id == job_id).options(selectinload(Job.steps))
            ).one_or_none()

    def get_step(self, step_id: int) -> Step:
        with session_scope(self.engine) as s:
            return s.get(Step, step_id)

    def get_steps(self, job_id: int) -> list[Step]:
        with session_scope(self.engine) as s:
            return list(s.scalars(select(Step).where(Step.job_id == job_id).order_by(Step.ordinal)))

    def set_step_status(self, step_id: int, status: StepStatus, error: str | None = None) -> None:
        with session_scope(self.engine) as s:
            step = s.get(Step, step_id)
            step.status = status
            if error is not None:
                step.error = error

    def set_job_status(self, job_id: int, status: JobStatus) -> None:
        with session_scope(self.engine) as s:
            s.get(Job, job_id).status = status

    # ---- units ----
    def add_units(self, step_id: int, subjects: list[tuple[str, str]]) -> None:
        with session_scope(self.engine) as s:
            for subject_type, subject_id in subjects:
                s.add(Unit(step_id=step_id, subject_type=subject_type, subject_id=subject_id, status=UnitStatus.PENDING, attempt_no=0))

    def claim_pending_units(self, step_id: int) -> list[Unit]:
        with session_scope(self.engine) as s:
            units = list(s.scalars(select(Unit).where(Unit.step_id == step_id, Unit.status == UnitStatus.PENDING)))
            for u in units:
                u.status = UnitStatus.RUNNING
                u.attempt_no += 1
            return units

    def list_units(self, step_id: int) -> list[Unit]:
        with session_scope(self.engine) as s:
            return list(s.scalars(select(Unit).where(Unit.step_id == step_id)))

    def set_unit_succeeded(self, unit_id: int, *, input_hash: str | None = None, cost_json: str | None = None, llm_raw_output: str | None = None) -> None:
        with session_scope(self.engine) as s:
            u = s.get(Unit, unit_id)
            u.status = UnitStatus.SUCCEEDED
            u.input_hash, u.cost_json, u.llm_raw_output = input_hash, cost_json, llm_raw_output

    def set_unit_failed(self, unit_id: int, error: str) -> None:
        with session_scope(self.engine) as s:
            u = s.get(Unit, unit_id)
            u.status = UnitStatus.FAILED
            u.error = error

    def reset_unit_to_pending(self, unit_id: int) -> None:
        with session_scope(self.engine) as s:
            u = s.get(Unit, unit_id)
            u.status = UnitStatus.PENDING
            u.error = None

    def reset_failed_units_to_pending(self, step_id: int) -> int:
        with session_scope(self.engine) as s:
            units = list(s.scalars(select(Unit).where(Unit.step_id == step_id, Unit.status == UnitStatus.FAILED)))
            for u in units:
                u.status = UnitStatus.PENDING
                u.error = None
            return len(units)

    def get_unit_by_subject(self, step_id: int, subject_type: str, subject_id: str) -> Unit | None:
        with session_scope(self.engine) as s:
            return s.scalar(
                select(Unit).where(
                    Unit.step_id == step_id,
                    Unit.subject_type == subject_type,
                    Unit.subject_id == subject_id,
                )
            )

    def add_unit(self, step_id: int, subject_type: str, subject_id: str) -> Unit:
        with session_scope(self.engine) as s:
            u = Unit(step_id=step_id, subject_type=subject_type, subject_id=subject_id, status=UnitStatus.PENDING, attempt_no=0)
            s.add(u)
            s.flush()
            return u

    def set_unit_running(self, unit_id: int, worker_id: str | None = None, heartbeat_at=None) -> None:
        with session_scope(self.engine) as s:
            u = s.get(Unit, unit_id)
            u.status = UnitStatus.RUNNING
            u.attempt_no += 1
            if worker_id is not None:
                u.worker_id = worker_id
            if heartbeat_at is not None:
                u.heartbeat_at = heartbeat_at

    def touch_unit_heartbeat(self, unit_id: int, heartbeat_at) -> None:
        with session_scope(self.engine) as s:
            s.get(Unit, unit_id).heartbeat_at = heartbeat_at

    def mark_needs_reconsolidation(self, unit_id: int) -> None:
        with session_scope(self.engine) as s:
            s.get(Unit, unit_id).needs_reconsolidation = True

    # ---- worker: pending-job creation / claim / crash recovery ----
    def create_job_pending(self, kb_id: int, method: str = "standard") -> Job:
        from kb_platform.engine.orchestrator import Orchestrator

        return self.create_job(kb_id=kb_id, type="full", specs=Orchestrator.plan(), method=method)

    def claim_one_pending_job(self) -> Job | None:
        """Atomically claim one PENDING job (PENDING -> RUNNING) and return it, or None."""
        with session_scope(self.engine) as s:
            job = s.scalars(
                select(Job).where(Job.status == JobStatus.PENDING).order_by(Job.id).limit(1)
            ).first()
            if job is None:
                return None
            s.execute(update(Job).where(Job.id == job.id).values(status=JobStatus.RUNNING))
            return s.get(Job, job.id)

    def recover_stale_units(self, stale_before) -> int:
        """Reset RUNNING units whose heartbeat is older than ``stale_before`` back to PENDING.

        A RUNNING unit with a NULL ``heartbeat_at`` (e.g. crashed before its
        first heartbeat tick) is also recovered, because SQL ``NULL < x`` is
        otherwise falsy and would leave such units stranded forever.
        """
        with session_scope(self.engine) as s:
            stale = list(
                s.scalars(
                    select(Unit).where(
                        Unit.status == UnitStatus.RUNNING,
                        or_(Unit.heartbeat_at < stale_before, Unit.heartbeat_at.is_(None)),
                    )
                )
            )
            for u in stale:
                u.status = UnitStatus.PENDING
            return len(stale)

    def recover_stale_jobs(self) -> int:
        """Reset all RUNNING jobs back to PENDING so a worker can resume them."""
        with session_scope(self.engine) as s:
            jobs = list(s.scalars(select(Job).where(Job.status == JobStatus.RUNNING)))
            for j in jobs:
                j.status = JobStatus.PENDING
            return len(jobs)
