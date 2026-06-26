"""Data access for the control plane."""

from datetime import datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import selectinload

from kb_platform.db.engine import session_scope
from kb_platform.db.enums import JobStatus, StepStatus, UnitStatus
from kb_platform.db.models import Chunk, Document, Job, KnowledgeBase, Step, Unit
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
                kb_id=kb_id,
                title=title,
                source_uri=source_uri,
                content_hash=hashlib.sha512(text.encode()).hexdigest(),
                status="parsed",
                bytes=len(text),
                text=text,
            )
            s.add(doc)
            s.flush()
            return doc

    def get_documents(self, kb_id: int) -> list[Document]:
        with session_scope(self.engine) as s:
            return list(s.scalars(select(Document).where(Document.kb_id == kb_id)))

    def get_document(self, kb_id: int, doc_id: int) -> Document | None:
        with session_scope(self.engine) as s:
            return s.scalar(select(Document).where(Document.id == doc_id, Document.kb_id == kb_id))

    def get_document_chunks(self, kb_id: int, doc_id: int) -> list[Chunk]:
        with session_scope(self.engine) as s:
            return list(
                s.scalars(
                    select(Chunk)
                    .where(Chunk.kb_id == kb_id, Chunk.document_id == doc_id)
                    .order_by(Chunk.ordinal)
                )
            )

    def add_chunks(self, chunks: list[Chunk]) -> None:
        with session_scope(self.engine) as s:
            for c in chunks:
                s.add(c)

    def get_chunks(self, kb_id: int) -> list[Chunk]:
        with session_scope(self.engine) as s:
            return list(
                s.scalars(select(Chunk).where(Chunk.kb_id == kb_id).order_by(Chunk.ordinal))
            )

    def delete_document(self, kb_id: int, doc_id: int) -> bool:
        """Delete a document AND its chunks (application-level cascade).

        The graph/index is NOT shrunk (no reverse extraction); only the
        control-plane rows are removed. Returns True if a row was deleted.
        """
        from sqlalchemy import delete as sa_delete

        with session_scope(self.engine) as s:
            doc = s.get(Document, doc_id)
            if doc is None or doc.kb_id != kb_id:
                return False
            s.execute(sa_delete(Chunk).where(Chunk.document_id == doc_id))
            s.delete(doc)
        return True

    def chunk_counts_by_document(self, kb_id: int) -> dict[int, int]:
        """Map of {document_id: chunk_count} for one KB."""
        from sqlalchemy import func

        with session_scope(self.engine) as s:
            rows = s.execute(
                select(Chunk.document_id, func.count())
                .where(Chunk.kb_id == kb_id)
                .group_by(Chunk.document_id)
            ).all()
        return {int(d): int(c) for d, c in rows}

    # ---- jobs / steps ----
    def create_job(
        self, kb_id: int, type: str, specs: list[StepSpec], method: str = "standard"
    ) -> Job:
        with session_scope(self.engine) as s:
            job = Job(kb_id=kb_id, type=type, method=method, status=JobStatus.PENDING)
            s.add(job)
            s.flush()
            for ordinal, spec in enumerate(specs):
                s.add(
                    Step(
                        job_id=job.id,
                        name=spec.name,
                        ordinal=ordinal,
                        kind=spec.kind,
                        status=StepStatus.PENDING,
                    )
                )
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
    def add_units(self, step_id: int, subjects: list[tuple[str, str]], kind: str) -> None:
        with session_scope(self.engine) as s:
            for subject_type, subject_id in subjects:
                s.add(
                    Unit(
                        step_id=step_id,
                        kind=kind,
                        subject_type=subject_type,
                        subject_id=subject_id,
                        status=UnitStatus.PENDING,
                        attempt_no=0,
                    )
                )

    def claim_pending_units(self, step_id: int) -> list[Unit]:
        with session_scope(self.engine) as s:
            units = list(
                s.scalars(
                    select(Unit).where(Unit.step_id == step_id, Unit.status == UnitStatus.PENDING)
                )
            )
            for u in units:
                u.status = UnitStatus.RUNNING
                u.attempt_no += 1
            return units

    def list_units(self, step_id: int) -> list[Unit]:
        with session_scope(self.engine) as s:
            return list(s.scalars(select(Unit).where(Unit.step_id == step_id)))

    def list_units_page(
        self, step_id: int, status: str | None, limit: int, offset: int
    ) -> tuple[list[Unit], int]:
        """Paginated units for display: status filter + LIMIT/OFFSET + COUNT in SQL.

        (list_units(step_id) — the unpaginated all-units method — stays for retry.py.)
        """
        with session_scope(self.engine) as s:
            q = select(Unit).where(Unit.step_id == step_id)
            cq = select(func.count()).select_from(Unit).where(Unit.step_id == step_id)
            if status:
                q = q.where(Unit.status == status)
                cq = cq.where(Unit.status == status)
            total = s.scalar(cq) or 0
            items = list(s.scalars(q.order_by(Unit.id).limit(limit).offset(offset)))
            return items, total

    def set_unit_succeeded(
        self,
        unit_id: int,
        *,
        input_hash: str | None = None,
        cost_json: str | None = None,
        llm_raw_output: str | None = None,
    ) -> None:
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
            units = list(
                s.scalars(
                    select(Unit).where(Unit.step_id == step_id, Unit.status == UnitStatus.FAILED)
                )
            )
            for u in units:
                u.status = UnitStatus.PENDING
                u.error = None
            return len(units)

    def reset_step_if_succeeded_for_unit(self, unit_id: int) -> None:
        """If the unit's step is SUCCEEDED, set it to PARTIALLY_FAILED so the
        orchestrator re-runs it (picks up the PENDING retried unit; other units
        use cached results). Needed because the orchestrator skips SUCCEEDED steps."""
        with session_scope(self.engine) as s:
            unit = s.get(Unit, unit_id)
            if unit is None:
                return
            step = s.get(Step, unit.step_id)
            if step is not None and step.status == StepStatus.SUCCEEDED:
                step.status = StepStatus.PARTIALLY_FAILED

    def get_chunk_texts(self, chunk_ids: list[str]) -> dict[str, str]:
        """Batch lookup chunk texts by chunk_id (for unit request-content preview)."""
        if not chunk_ids:
            return {}
        with session_scope(self.engine) as s:
            rows = s.scalars(select(Chunk).where(Chunk.chunk_id.in_(chunk_ids)))
            return {r.chunk_id: r.text for r in rows}

    def reactivate_job_for_unit(self, unit_id: int) -> None:
        """Re-queue a FAILED job so the worker re-claims a retried unit.

        Retry resets the unit to PENDING, but the worker only claims PENDING
        *jobs* — so without this, a retried unit in a terminal-failed job sits
        at PENDING forever ('stuck on processing'). Idempotent: only flips
        FAILED -> PENDING; leaves running/succeeded jobs untouched.
        """
        with session_scope(self.engine) as s:
            unit = s.get(Unit, unit_id)
            if unit is None:
                return
            step = s.get(Step, unit.step_id)
            if step is None:
                return
            job = s.get(Job, step.job_id)
            if job is not None and job.status == JobStatus.FAILED:
                job.status = JobStatus.PENDING

    def reactivate_job_for_step(self, step_id: int) -> None:
        """Same as reactivate_job_for_unit, keyed by step (for retry-step)."""
        with session_scope(self.engine) as s:
            step = s.get(Step, step_id)
            if step is None:
                return
            job = s.get(Job, step.job_id)
            if job is not None and job.status == JobStatus.FAILED:
                job.status = JobStatus.PENDING

    def get_unit_by_subject(self, step_id: int, subject_type: str, subject_id: str) -> Unit | None:
        with session_scope(self.engine) as s:
            return s.scalar(
                select(Unit).where(
                    Unit.step_id == step_id,
                    Unit.subject_type == subject_type,
                    Unit.subject_id == subject_id,
                )
            )

    def add_unit(self, step_id: int, subject_type: str, subject_id: str, kind: str) -> Unit:
        with session_scope(self.engine) as s:
            u = Unit(
                step_id=step_id,
                kind=kind,
                subject_type=subject_type,
                subject_id=subject_id,
                status=UnitStatus.PENDING,
                attempt_no=0,
            )
            s.add(u)
            s.flush()
            return u

    def set_unit_running(
        self, unit_id: int, worker_id: str | None = None, heartbeat_at=None
    ) -> None:
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

    def list_jobs_by_kb(self, kb_id: int) -> list:
        with session_scope(self.engine) as s:
            return list(s.scalars(select(Job).where(Job.kb_id == kb_id).order_by(Job.id.desc())))

    def unit_counts_by_status(self, step_id: int) -> dict:
        with session_scope(self.engine) as s:
            units = list(s.scalars(select(Unit).where(Unit.step_id == step_id)))
        counts = {"pending": 0, "running": 0, "succeeded": 0, "failed": 0, "total": len(units)}
        for u in units:
            if u.status in counts:
                counts[u.status] += 1
        return counts

    def mark_needs_reconsolidation(self, unit_id: int) -> None:
        with session_scope(self.engine) as s:
            s.get(Unit, unit_id).needs_reconsolidation = True

    # ---- cross-job input_hash lookups (delta strategies) ----
    def last_succeeded_input_hash(
        self, kb_id: int, kind: str, subject_type: str, subject_id: str
    ) -> str | None:
        """Most recent SUCCEEDED unit input_hash for (kb, kind, subject) across all jobs.

        Delta strategies diff the current input against this to decide whether to
        re-run. Units are per-step/per-job, so the lookup joins through Step->Job.
        """
        with session_scope(self.engine) as s:
            return s.scalar(
                select(Unit.input_hash)
                .join(Step, Unit.step_id == Step.id)
                .join(Job, Step.job_id == Job.id)
                .where(
                    Job.kb_id == kb_id,
                    Unit.kind == kind,
                    Unit.subject_type == subject_type,
                    Unit.subject_id == subject_id,
                    Unit.status == UnitStatus.SUCCEEDED,
                )
                .order_by(Unit.id.desc())
                .limit(1)
            )

    def has_succeeded_input_hash(self, kb_id: int, kind: str, input_hash: str) -> bool:
        """True if any SUCCEEDED unit (kb, kind) recorded this input_hash.

        Used by delta community_reports, where community_id is unstable across
        re-clustering, so matching is by ctx-content hash, not subject_id.
        """
        with session_scope(self.engine) as s:
            row = s.scalar(
                select(Unit.id)
                .join(Step, Unit.step_id == Step.id)
                .join(Job, Step.job_id == Job.id)
                .where(
                    Job.kb_id == kb_id,
                    Unit.kind == kind,
                    Unit.input_hash == input_hash,
                    Unit.status == UnitStatus.SUCCEEDED,
                )
                .limit(1)
            )
        return row is not None

    # ---- knowledge base ----
    def update_kb(
        self, kb_id: int, *, name: str, method: str, settings_json: str
    ) -> KnowledgeBase | None:
        """Full-replace name/method/settings_json. Returns the KB or None if missing."""
        with session_scope(self.engine) as s:
            kb = s.get(KnowledgeBase, kb_id)
            if kb is None:
                return None
            kb.name = name
            kb.method = method
            kb.settings_json = settings_json
            return kb

    # ---- worker: pending-job creation / claim / crash recovery ----
    def create_job_pending(self, kb_id: int, method: str = "standard", type: str = "full") -> Job:
        from kb_platform.engine.orchestrator import Orchestrator

        specs = (
            Orchestrator.plan_incremental() if type == "incremental" else Orchestrator.plan_full()
        )
        return self.create_job(kb_id=kb_id, type=type, specs=specs, method=method)

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

    def worker_status(self, stale_seconds: float) -> dict:
        """Newest RUNNING-unit heartbeat, and whether it is stale.

        Returns ``{"last_heartbeat_at": iso | None, "stale": bool}``. No RUNNING
        units -> idle (last_heartbeat_at None, stale False).
        """
        with session_scope(self.engine) as s:
            row = s.scalar(
                select(Unit.heartbeat_at)
                .where(Unit.status == UnitStatus.RUNNING)
                .order_by(Unit.heartbeat_at.desc().nulls_last())
                .limit(1)
            )
        if row is None:
            return {"last_heartbeat_at": None, "stale": False}
        stale = (datetime.now() - row).total_seconds() > stale_seconds
        return {"last_heartbeat_at": row.isoformat(), "stale": stale}

    # ---- cost aggregation ----
    @staticmethod
    def _sum_cost(rows):
        """Aggregate ``Unit.cost_json`` rows into a cost summary dict.

        ``rows`` is an iterable of ``(step_name, cost_json_str)`` tuples (the
        ``cost_json_str`` may be ``None`` for non-LLM units, which are skipped).

        Returns ``{total_usd, by_step:{name:usd}, by_model:{model:{prompt_tokens,
        completion_tokens, usd}}}``. A ``None`` ``estimated_cost_usd`` (or
        ``total_usd``) latches that model's ``usd`` (and the overall total) to
        ``None`` — the caller sees "unknown" rather than a misleading zero.
        """
        import json

        total = 0.0
        known = True
        by_step: dict[str, float] = {}
        by_model: dict[str, dict] = {}
        for step_name, cj in rows:
            if not cj:
                continue
            try:
                data = json.loads(cj)
            except Exception:  # noqa: BLE001
                continue
            if data.get("total_usd") is None:
                known = False
            else:
                total += float(data["total_usd"])
                by_step[step_name] = by_step.get(step_name, 0.0) + float(data["total_usd"])
            for it in data.get("items", []):
                m = it.get("model", "?")
                slot = by_model.setdefault(
                    m, {"prompt_tokens": 0, "completion_tokens": 0, "usd": 0.0, "known": True}
                )
                slot["prompt_tokens"] += int(it.get("prompt_tokens", 0) or 0)
                slot["completion_tokens"] += int(it.get("completion_tokens", 0) or 0)
                if it.get("estimated_cost_usd") is None:
                    slot["known"] = False
                else:
                    slot["usd"] += float(it["estimated_cost_usd"])
        for slot in by_model.values():
            if not slot.pop("known", True):
                slot["usd"] = None
        return {"total_usd": total if known else None, "by_step": by_step, "by_model": by_model}

    def job_cost(self, job_id: int) -> dict:
        """Aggregate SUCCEEDED-unit costs for one job, by step name and model."""
        with session_scope(self.engine) as s:
            rows = s.execute(
                select(Step.name, Unit.cost_json)
                .join(Unit, Unit.step_id == Step.id)
                .where(Step.job_id == job_id, Unit.status == UnitStatus.SUCCEEDED)
            ).all()
        return self._sum_cost(rows)

    def kb_cost(self, kb_id: int) -> dict:
        """Aggregate SUCCEEDED-unit costs across all jobs in a KB.

        Same shape as :meth:`job_cost`, plus ``by_job:{job_id: usd}``.
        """
        with session_scope(self.engine) as s:
            rows = s.execute(
                select(Job.id, Step.name, Unit.cost_json)
                .join(Step, Step.job_id == Job.id)
                .join(Unit, Unit.step_id == Step.id)
                .where(Job.kb_id == kb_id, Unit.status == UnitStatus.SUCCEEDED)
            ).all()
        # Aggregate overall (via _sum_cost) plus a per-job total breakout.
        by_job: dict[int, float] = {}
        overall_rows = []
        for jid, step_name, cj in rows:
            overall_rows.append((step_name, cj))
            if not cj:
                continue
            try:
                import json

                d = json.loads(cj)
                v = d.get("total_usd")
                if v is not None:
                    by_job[jid] = by_job.get(jid, 0.0) + float(v)
            except Exception:  # noqa: BLE001
                pass
        out = self._sum_cost(overall_rows)
        out["by_job"] = by_job
        return out
