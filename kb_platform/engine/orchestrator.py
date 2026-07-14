"""Orchestrator: build the step plan and drive a job to completion."""

import logging
import time

from kb_platform.db.enums import JobStatus, StepKind, StepStatus
from kb_platform.db.models import Chunk
from kb_platform.db.repository import Repository
from kb_platform.engine.failure_diagnostics import collect_failure_diagnostics
from kb_platform.engine.spec import StepSpec
from kb_platform.graph.adapter import GraphAdapter

logger = logging.getLogger(__name__)


def _resolved_base(strategies) -> dict:
    from kb_platform.engine.strategy import default_strategies

    return strategies if strategies is not None else default_strategies()


def incremental_strategies(base: dict) -> dict:
    """Base strategy set with summarize/community_reports swapped to Delta variants.

    Reused by RetryService so a retried unit in an incremental job resolves the
    same delta strategies the orchestrator would (e.g. CommunityReportsDeltaStrategy,
    whose persist writes the reports_by_hash/ sidecar the delta finalize reads).
    """
    from kb_platform.engine.strategies.delta import (
        CommunityReportsDeltaStrategy,
        SummarizeDeltaStrategy,
    )

    return {
        **base,
        "summarize_descriptions": SummarizeDeltaStrategy(),
        "community_reports": CommunityReportsDeltaStrategy(),
    }


class Orchestrator:
    def __init__(
        self,
        *,
        repo: Repository,
        adapter: GraphAdapter,
        data_root: str,
        concurrency: int = 4,
        vector_store=None,
        strategies: dict | None = None,
    ) -> None:
        self.repo = repo
        self.adapter = adapter
        self.data_root = data_root
        self.concurrency = concurrency
        # None -> production builds a real LanceDB store at <data_root>/vectors
        # (see _run_atomic). Engine tests inject a FakeVectorStore to keep the
        # graph-pipeline tests free of LanceDB I/O.
        self.vector_store = vector_store
        self._base = strategies

    def _base_strategies(self) -> dict:
        return _resolved_base(self._base)

    def _strategies_for(self, job) -> dict:
        base = self._base_strategies()
        if getattr(job, "type", "full") == "incremental":
            return incremental_strategies(base)
        return base

    @staticmethod
    def plan_full() -> list[StepSpec]:
        return [
            StepSpec("chunk_documents", StepKind.ATOMIC),
            StepSpec("extract_graph", StepKind.UNIT_FANOUT),
            StepSpec("summarize_descriptions", StepKind.UNIT_FANOUT),
            StepSpec("finalize_graph", StepKind.ATOMIC),
            StepSpec("create_communities", StepKind.ATOMIC),
            StepSpec("community_reports", StepKind.UNIT_FANOUT),
            StepSpec("generate_text_embeddings", StepKind.ATOMIC),
        ]

    @staticmethod
    def plan_incremental() -> list[StepSpec]:
        # delta 步:只对新 chunk 抽取 → 合并 → 重聚类/报告受影响社区 → 收尾
        return [
            StepSpec("load_update_documents", StepKind.ATOMIC),
            StepSpec("create_base_text_units", StepKind.ATOMIC),
            StepSpec("extract_graph", StepKind.UNIT_FANOUT),
            StepSpec("merge_delta", StepKind.ATOMIC),
            StepSpec("summarize_descriptions", StepKind.UNIT_FANOUT),
            StepSpec("finalize_graph", StepKind.ATOMIC),
            StepSpec("create_communities", StepKind.ATOMIC),
            StepSpec("community_reports", StepKind.UNIT_FANOUT),
            StepSpec("update_clean_state", StepKind.ATOMIC),
            StepSpec("generate_text_embeddings", StepKind.ATOMIC),
        ]

    async def run(self, job_id: int, min_success_ratio: float = 1.0) -> None:
        self.repo.set_job_status(job_id, JobStatus.RUNNING)
        try:
            job = self.repo.get_job(job_id)
            plan_name = "plan_incremental" if job.type == "incremental" else "plan_full"
            logger.info("job %s using %s", job_id, plan_name)
            # plan 只用于日志/校验;steps 在 create_job_pending 时已按 type 建好
            for step in self.repo.get_steps(job_id):
                if step.status == StepStatus.SUCCEEDED:
                    # crash recovery: skip steps already completed so
                    # non-idempotent atomic work (e.g. chunk_documents
                    # inserts) is not re-run on resume.
                    continue
                await self._run_step(step, min_success_ratio)
                current_step = self.repo.get_step(step.id)
                if current_step.status != StepStatus.SUCCEEDED:
                    counts = self.repo.unit_counts_by_status(step.id)
                    logger.error(
                        "job %s stopping at step %s [%s]; step_status=%s "
                        "ok=%s failed=%s reason=%r",
                        job_id, step.id, step.name, current_step.status,
                        counts.get("succeeded", 0), counts.get("failed", 0),
                        current_step.error or "step did not succeed",
                    )
                    self.repo.set_job_status(job_id, JobStatus.FAILED)
                    return
            self.repo.set_job_status(job_id, JobStatus.SUCCEEDED)
            # Incremental jobs may have late-succeeded units (e.g. retried
            # units whose extraction landed after merge_delta already ran).
            # Reconsolidate those cached extractions into the final parquet.
            if job.type == "incremental":
                from kb_platform.reconsolidate import reconsolidate

                await reconsolidate(self.repo, self.adapter, job.kb_id, self.data_root)
            # Graph-scale stats snapshot (best-effort; never fails the job).
            try:
                from kb_platform.engine.kb_stats import write_kb_stats

                write_kb_stats(self.repo, job.kb_id)
            except Exception:
                logger.exception("write_kb_stats failed for kb %s; stats may be stale", job.kb_id)
        except Exception:
            logger.exception("job %s failed", job_id)
            self.repo.set_job_status(job_id, JobStatus.FAILED)
            raise

    async def _run_step(self, step, min_success_ratio: float) -> None:
        from kb_platform.logging_config import bind_log_context

        self.repo.set_step_status(step.id, StepStatus.RUNNING)
        with bind_log_context(step_id=step.id):
            logger.info(
                "step %s [%s] start; kind=%s", step.id, step.name, step.kind
            )
            t0 = time.perf_counter()
            try:
                await self._dispatch_step(step, min_success_ratio)
            except Exception as exc:
                # A raised step must not be stranded at RUNNING. unit_fanout steps
                # normally reach a terminal status via strategy.finalize, but a blowup
                # before/inside finalize — and *any* atomic step failure (e.g.
                # generate_text_embeddings when the embed endpoint is down, which has
                # no units to record the error) — would otherwise leave the step row
                # at RUNNING forever, hiding the failure and blocking retry. Mark it
                # FAILED so the job-level handler and the retry path see a terminal step.
                from kb_platform.logging_config import redact_text

                error = redact_text(f"{type(exc).__name__}: {exc}", limit=900)
                self.repo.set_step_status(step.id, StepStatus.FAILED, error=error)
                logger.exception("step %s [%s] failed", step.id, step.name)
                raise
            counts = (
                self.repo.unit_counts_by_status(step.id)
                if step.kind == StepKind.UNIT_FANOUT
                else {}
            )
            ok = counts.get("succeeded", 0)
            failed = counts.get("failed", 0)
            current_step = self.repo.get_step(step.id)
            log_method = logger.info
            if failed:
                diagnostics = collect_failure_diagnostics(self.repo, step.id)
                log_method = (
                    logger.error
                    if current_step.status != StepStatus.SUCCEEDED
                    else logger.warning
                )
                for sample in diagnostics.samples:
                    log_method(
                        "unit.failure_summary unit=%s subject_type=%s subject_hash=%s "
                        "attempt=%s error_type=%s error=%r",
                        sample.unit_id, sample.subject_type, sample.subject_hash,
                        sample.attempt_no, sample.error_type, sample.error,
                    )
                if diagnostics.omitted:
                    log_method(
                        "unit.failure_summary omitted=%s; inspect failed units via API",
                        diagnostics.omitted,
                    )
            log_method(
                "step %s [%s] done in %.0fms; status=%s ok=%s failed=%s "
                "pending=%s running=%s reason=%r",
                step.id, step.name, (time.perf_counter() - t0) * 1000,
                current_step.status, ok, failed, counts.get("pending", 0),
                counts.get("running", 0), current_step.error or "-",
            )

    async def _dispatch_step(self, step, min_success_ratio: float) -> None:
        if step.kind == StepKind.ATOMIC:
            await self._run_atomic(step)
        else:
            from kb_platform.engine.unit_worker import UnitWorker

            job = self.repo.get_job(step.job_id)
            strategies = self._strategies_for(job)
            if step.name == "extract_graph" and job.type == "incremental":
                from kb_platform.engine.incremental import (
                    ExtractGraphDeltaStrategy,
                    read_delta_manifest,
                )

                new_ids = read_delta_manifest(self.data_root)
                strategies = {**strategies, "extract_graph": ExtractGraphDeltaStrategy(new_ids)}
            worker = UnitWorker(
                repo=self.repo,
                adapter=self.adapter,
                data_root=self.data_root,
                concurrency=self.concurrency,
                strategies=strategies,
            )
            await worker.run_unit_fanout(step, min_success_ratio=min_success_ratio)

    async def _run_atomic(self, step) -> None:
        from kb_platform.engine import atomic_steps

        if step.name == "chunk_documents":
            await self._chunk_documents(step)
        elif step.name == "finalize_graph":
            atomic_steps.finalize_graph(self.repo, self.adapter, step)
        elif step.name == "create_communities":
            atomic_steps.create_communities(self.repo, self.adapter, step)
        elif step.name == "merge_delta":
            atomic_steps.merge_delta(self.repo, self.adapter, step)
        elif step.name == "load_update_documents":
            from kb_platform.engine import incremental

            incremental.load_update_documents(self.repo, self.adapter, step)
        elif step.name == "update_clean_state":
            atomic_steps.update_clean_state(self.repo, self.adapter, step)
        elif step.name == "create_base_text_units":
            pass  # MVP:chunks already created by load_update_documents
        elif step.name == "generate_text_embeddings":
            from kb_platform.graph.vector_store import build_vector_store

            vs = self.vector_store or build_vector_store(self.data_root)
            vs.connect()
            await atomic_steps.generate_text_embeddings(self.repo, self.adapter, step, vs)
        else:
            msg = f"unknown atomic step: {step.name}"
            raise ValueError(msg)
        self.repo.set_step_status(step.id, StepStatus.SUCCEEDED)

    async def _chunk_documents(self, step) -> None:
        from kb_platform.db.engine import session_scope
        from kb_platform.db.models import KnowledgeBase

        from sqlalchemy import select

        job = self.repo.get_job(step.job_id)
        chunks: list[Chunk] = []
        for doc in self.repo.get_documents(job.kb_id):
            doc_chunks = 0
            for ordinal, piece in enumerate(self.adapter.chunk_document(doc.id, doc.text or "")):
                chunks.append(
                    Chunk(
                        chunk_id=piece.chunk_id,
                        kb_id=job.kb_id,
                        document_id=doc.id,
                        ordinal=ordinal,
                        text=piece.text,
                    )
                )
                doc_chunks += 1
            logger.info(
                "chunked doc=%s into %d chunks (kb=%s)", doc.id, doc_chunks, job.kb_id
            )
        self.repo.add_chunks(chunks)
        # Write text_units.parquet so the embeddings step can embed chunk text
        with session_scope(self.repo.engine) as s:
            kb = s.scalar(select(KnowledgeBase).where(KnowledgeBase.id == job.kb_id))
            data_root = kb.data_root
        if chunks:
            from pathlib import Path

            from kb_platform.engine.atomic_steps import write_text_units_parquet

            write_text_units_parquet(Path(data_root), chunks)
