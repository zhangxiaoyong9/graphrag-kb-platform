"""Data access for the control plane."""

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import selectinload

from kb_platform.db.engine import session_scope
from kb_platform.db.enums import JobStatus, StepKind, StepStatus, UnitStatus
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

    def get_job(self, job_id: int) -> Job:
        with session_scope(self.engine) as s:
            return s.scalars(
                select(Job).where(Job.id == job_id).options(selectinload(Job.steps))
            ).one()

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

    def set_unit_succeeded(self, unit_id: int, result: str) -> None:
        with session_scope(self.engine) as s:
            u = s.get(Unit, unit_id)
            u.status = UnitStatus.SUCCEEDED
            u.result = result

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
