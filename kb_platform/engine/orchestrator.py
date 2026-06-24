"""Orchestrator: build the step plan and drive a job to completion."""

import logging

from kb_platform.db.enums import JobStatus, StepKind, StepStatus
from kb_platform.db.models import Chunk
from kb_platform.db.repository import Repository
from kb_platform.engine.spec import StepSpec
from kb_platform.graph.adapter import GraphAdapter

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, *, repo: Repository, adapter: GraphAdapter, data_root: str) -> None:
        self.repo = repo
        self.adapter = adapter
        self.data_root = data_root

    @staticmethod
    def plan() -> list[StepSpec]:
        return [StepSpec("chunk_documents", StepKind.ATOMIC), StepSpec("extract_graph", StepKind.UNIT_FANOUT)]

    async def run(self, job_id: int) -> None:
        self.repo.set_job_status(job_id, JobStatus.RUNNING)
        try:
            for step in self.repo.get_steps(job_id):
                await self._run_step(step)
                if step.status != StepStatus.SUCCEEDED:
                    self.repo.set_job_status(job_id, JobStatus.FAILED)
                    return
            self.repo.set_job_status(job_id, JobStatus.SUCCEEDED)
        except Exception:
            logger.exception("job %s failed", job_id)
            self.repo.set_job_status(job_id, JobStatus.FAILED)
            raise

    async def _run_step(self, step) -> None:
        self.repo.set_step_status(step.id, StepStatus.RUNNING)
        from kb_platform.engine.unit_worker import UnitWorker

        if step.kind == StepKind.ATOMIC:
            await self._run_atomic(step)
        else:
            worker = UnitWorker(repo=self.repo, adapter=self.adapter, data_root=self.data_root)
            await worker.run_unit_fanout(step)
        # 重新读取 step 状态(worker 已结算)
        fresh = self.repo.get_step(step.id)
        step.status = fresh.status

    async def _run_atomic(self, step) -> None:
        if step.name == "chunk_documents":
            await self._chunk_documents(step)
        else:
            msg = f"unknown atomic step: {step.name}"
            raise ValueError(msg)
        self.repo.set_step_status(step.id, StepStatus.SUCCEEDED)

    async def _chunk_documents(self, step) -> None:
        job = self.repo.get_job(step.job_id)
        chunks: list[Chunk] = []
        for doc in self.repo.get_documents(job.kb_id):
            for ordinal, piece in enumerate(self.adapter.chunk_document(doc.id, doc.text or "")):
                chunks.append(Chunk(chunk_id=piece.chunk_id, kb_id=job.kb_id, document_id=doc.id, ordinal=ordinal, text=piece.text))
        self.repo.add_chunks(chunks)
