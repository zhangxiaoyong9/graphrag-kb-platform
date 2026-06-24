"""UnitWorker: fan out a unit_fanout step, run units concurrently, settle + finalize."""

import asyncio
import json
import logging
from pathlib import Path

import pandas as pd

from kb_platform.db.enums import StepStatus, UnitStatus
from kb_platform.db.repository import Repository
from kb_platform.graph.adapter import ExtractionResult, GraphAdapter

logger = logging.getLogger(__name__)


class UnitWorker:
    def __init__(self, *, repo: Repository, adapter: GraphAdapter, data_root: str, concurrency: int = 4) -> None:
        self.repo = repo
        self.adapter = adapter
        self.data_root = Path(data_root)
        self.concurrency = concurrency

    async def run_unit_fanout(self, step) -> None:
        if not self.repo.list_units(step.id):
            self._create_units_for(step)
        await self._run_units(step.id)
        self._settle(step)

    def _create_units_for(self, step) -> None:
        if step.name != "extract_graph":
            msg = f"no unit plan for step {step.name}"
            raise ValueError(msg)
        job = self.repo.get_job(step.job_id)
        chunks = self.repo.get_chunks(job.kb_id)
        self.repo.add_units(step.id, [("chunk", c.chunk_id) for c in chunks])

    async def _run_units(self, step_id: int) -> None:
        units = self.repo.claim_pending_units(step_id)
        sem = asyncio.Semaphore(self.concurrency)

        async def handle(u):
            async with sem:
                await self._process_one(u, step_id)

        await asyncio.gather(*(handle(u) for u in units))

    async def _process_one(self, unit, step_id: int):
        try:
            job = self.repo.get_job(self.repo.get_step(step_id).job_id)
            from sqlalchemy import select

            from kb_platform.db.engine import session_scope
            from kb_platform.db.models import Chunk

            with session_scope(self.repo.engine) as s:
                chunk = s.scalars(select(Chunk).where(Chunk.chunk_id == unit.subject_id, Chunk.kb_id == job.kb_id)).first()
                text = chunk.text if chunk else ""
            result = await self.adapter.extract_chunk(unit.subject_id, text)
            self._persist_extraction(unit.subject_id, result)  # 持久化,供结算/重试汇集
            self.repo.set_unit_succeeded(unit.id, llm_raw_output=f"{len(result.entities)} entities")
        except Exception as e:  # noqa: BLE001
            logger.warning("unit %s failed: %s", unit.id, e)
            self.repo.set_unit_failed(unit.id, str(e))

    def _settle(self, step) -> None:
        units = self.repo.list_units(step.id)
        # 关键:从磁盘汇集该步"所有成功单元"的抽取结果(含历次成功单元),
        # 这样重试单个失败单元后,之前已成功的兄弟 chunk 不会被遗漏。
        if {u.status for u in units} == {UnitStatus.SUCCEEDED}:
            extractions = self._load_all_extractions(units)
            merged = self.adapter.merge_extractions(extractions)
            self._write_parquet(merged)
            self.repo.set_step_status(step.id, StepStatus.SUCCEEDED)
        else:
            self.repo.set_step_status(step.id, StepStatus.PARTIALLY_FAILED)

    def _persist_extraction(self, chunk_id: str, result: ExtractionResult) -> None:
        d = self.data_root / "extractions"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{chunk_id}.json").write_text(json.dumps({
            "entities": result.entities.to_dict("records"),
            "relationships": result.relationships.to_dict("records"),
        }))

    def _load_all_extractions(self, units) -> list[ExtractionResult]:
        out: list[ExtractionResult] = []
        for u in units:
            if u.status != UnitStatus.SUCCEEDED:
                continue
            path = self.data_root / "extractions" / f"{u.subject_id}.json"
            if path.exists():
                raw = json.loads(path.read_text())
                out.append(ExtractionResult(
                    entities=pd.DataFrame(raw["entities"]),
                    relationships=pd.DataFrame(raw["relationships"]),
                ))
        return out

    def _write_parquet(self, merged) -> None:
        entities, relationships = merged
        entities.to_parquet(self.data_root / "entities.parquet")
        relationships.to_parquet(self.data_root / "relationships.parquet")
