"""ExtractGraphStrategy: per-chunk LLM extraction (refactored from Phase 1)."""

import hashlib
import json
from pathlib import Path

import pandas as pd

from kb_platform.db.enums import StepStatus, UnitKind, UnitStatus
from kb_platform.db.models import Chunk
from kb_platform.db.repository import Repository
from kb_platform.engine.strategy import Subject, UnitResult
from kb_platform.graph.adapter import ExtractionResult


class ExtractGraphStrategy:
    kind = UnitKind.EXTRACT_GRAPH

    def next_units_batch(self, repo: Repository, step) -> list[Subject] | None:
        job = repo.get_job(step.job_id)
        chunks = repo.get_chunks(job.kb_id)
        pending = []
        for c in chunks:
            u = repo.get_unit_by_subject(step.id, "chunk", c.chunk_id)
            if u is None or u.status != UnitStatus.SUCCEEDED:
                pending.append(Subject("chunk", c.chunk_id))
        return pending or None

    async def run_unit(self, adapter, unit, repo: Repository) -> UnitResult:
        from kb_platform.db.engine import session_scope

        from sqlalchemy import select

        job = repo.get_job(repo.get_step(unit.step_id).job_id)
        with session_scope(repo.engine) as s:
            chunk = s.scalar(select(Chunk).where(Chunk.chunk_id == unit.subject_id, Chunk.kb_id == job.kb_id))
            text = chunk.text if chunk else ""
        result = await adapter.extract_chunk(unit.subject_id, text)
        return UnitResult(payload=result, input_hash=hashlib.sha512(text.encode()).hexdigest())

    def persist(self, data_root: Path, unit, result: UnitResult) -> None:
        d = data_root / "extractions"
        d.mkdir(parents=True, exist_ok=True)
        er: ExtractionResult = result.payload
        (d / f"{unit.subject_id}.json").write_text(json.dumps({
            "entities": er.entities.to_dict("records"),
            "relationships": er.relationships.to_dict("records"),
        }))

    def finalize(self, repo: Repository, adapter, step, data_root: Path, min_success_ratio: float) -> StepStatus:
        units = repo.list_units(step.id)
        if not units:
            return StepStatus.PARTIALLY_FAILED
        succeeded = [u for u in units if u.status == UnitStatus.SUCCEEDED]
        ratio = len(succeeded) / len(units)
        if ratio < min_success_ratio:
            return StepStatus.PARTIALLY_FAILED
        extractions = []
        for u in succeeded:
            path = data_root / "extractions" / f"{u.subject_id}.json"
            if path.exists():
                raw = json.loads(path.read_text())
                extractions.append(ExtractionResult(entities=pd.DataFrame(raw["entities"]), relationships=pd.DataFrame(raw["relationships"])))
        entities, relationships = adapter.merge_extractions(extractions)
        entities.to_parquet(data_root / "entities.parquet")
        relationships.to_parquet(data_root / "relationships.parquet")
        return StepStatus.SUCCEEDED
