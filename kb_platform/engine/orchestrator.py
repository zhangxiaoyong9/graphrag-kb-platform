"""Orchestrator: build the step plan and drive a job to completion."""

import logging

from kb_platform.db.enums import JobStatus, StepKind, StepStatus
from kb_platform.db.models import Chunk
from kb_platform.db.repository import Repository
from kb_platform.engine.spec import StepSpec
from kb_platform.graph.adapter import GraphAdapter

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, *, repo: Repository, adapter: GraphAdapter, data_root: str, concurrency: int = 4) -> None:
        self.repo = repo
        self.adapter = adapter
        self.data_root = data_root
        self.concurrency = concurrency

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
            logger.debug("job %s using %s", job_id, plan_name)
            # plan 只用于日志/校验;steps 在 create_job_pending 时已按 type 建好
            for step in self.repo.get_steps(job_id):
                if step.status == StepStatus.SUCCEEDED:
                    # crash recovery: skip steps already completed so
                    # non-idempotent atomic work (e.g. chunk_documents
                    # inserts) is not re-run on resume.
                    continue
                await self._run_step(step, min_success_ratio)
                if self.repo.get_step(step.id).status != StepStatus.SUCCEEDED:
                    self.repo.set_job_status(job_id, JobStatus.FAILED)
                    return
            self.repo.set_job_status(job_id, JobStatus.SUCCEEDED)
            # Incremental jobs may have late-succeeded units (e.g. retried
            # units whose extraction landed after merge_delta already ran).
            # Reconsolidate those cached extractions into the final parquet.
            if job.type == "incremental":
                from kb_platform.reconsolidate import reconsolidate

                await reconsolidate(self.repo, self.adapter, job.kb_id, self.data_root)
        except Exception:
            logger.exception("job %s failed", job_id)
            self.repo.set_job_status(job_id, JobStatus.FAILED)
            raise

    async def _run_step(self, step, min_success_ratio: float) -> None:
        self.repo.set_step_status(step.id, StepStatus.RUNNING)
        if step.kind == StepKind.ATOMIC:
            await self._run_atomic(step)
        else:
            from kb_platform.engine.unit_worker import UnitWorker

            if step.name == "extract_graph":
                job = self.repo.get_job(step.job_id)
                if job.type == "incremental":
                    from kb_platform.engine.incremental import ExtractGraphDeltaStrategy, read_delta_manifest
                    from kb_platform.engine.strategy import register_strategy

                    new_ids = read_delta_manifest(self.data_root)
                    register_strategy("extract_graph", ExtractGraphDeltaStrategy(new_ids))
                else:
                    from kb_platform.engine.strategy import register_strategy
                    from kb_platform.engine.strategies.extract_graph import ExtractGraphStrategy

                    register_strategy("extract_graph", ExtractGraphStrategy())
            worker = UnitWorker(repo=self.repo, adapter=self.adapter, data_root=self.data_root, concurrency=self.concurrency)
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
            pass  # MVP:空操作(state 合并留后续)
        elif step.name == "create_base_text_units":
            pass  # MVP:chunks already created by load_update_documents
        elif step.name == "generate_text_embeddings":
            from kb_platform.graph.vector_store import FakeVectorStore

            atomic_steps.generate_text_embeddings(self.repo, self.adapter, step, FakeVectorStore(dim=8))
        else:
            msg = f"unknown atomic step: {step.name}"
            raise ValueError(msg)
        self.repo.set_step_status(step.id, StepStatus.SUCCEEDED)

    async def _chunk_documents(self, step) -> None:
        import pandas as pd

        from kb_platform.db.engine import session_scope
        from kb_platform.db.models import KnowledgeBase

        from sqlalchemy import select

        job = self.repo.get_job(step.job_id)
        chunks: list[Chunk] = []
        for doc in self.repo.get_documents(job.kb_id):
            for ordinal, piece in enumerate(self.adapter.chunk_document(doc.id, doc.text or "")):
                chunks.append(Chunk(chunk_id=piece.chunk_id, kb_id=job.kb_id, document_id=doc.id, ordinal=ordinal, text=piece.text))
        self.repo.add_chunks(chunks)
        # Write text_units.parquet so the embeddings step can embed chunk text
        with session_scope(self.repo.engine) as s:
            kb = s.scalar(select(KnowledgeBase).where(KnowledgeBase.id == job.kb_id))
            data_root = kb.data_root
        if chunks:
            pd.DataFrame([
                {"id": c.chunk_id, "text": c.text, "document_ids": [str(c.document_id)], "n_tokens": 0}
                for c in chunks
            ]).to_parquet(f"{data_root}/text_units.parquet")
