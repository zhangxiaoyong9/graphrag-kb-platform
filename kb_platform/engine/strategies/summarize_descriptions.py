"""SummarizeDescriptionsStrategy: merge multi-chunk entity descriptions."""

import hashlib
import json
from pathlib import Path

import pandas as pd
from sqlalchemy import select

from kb_platform.db.engine import session_scope
from kb_platform.db.enums import StepStatus, UnitKind, UnitStatus
from kb_platform.db.models import KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.strategy import Subject, UnitResult


class SummarizeDescriptionsStrategy:
    kind = UnitKind.SUMMARIZE_DESCRIPTIONS

    def _entities(self, data_root: Path) -> pd.DataFrame:
        return pd.read_parquet(data_root / "entities.parquet")

    @staticmethod
    def _desc_count(desc) -> int:
        """Number of chunk-level descriptions. Parquet round-trips list columns as
        numpy arrays, so accept any sequence-like (list/ndarray) with a length."""
        if isinstance(desc, str):
            return 1
        try:
            return len(desc)
        except TypeError:
            return 1

    def next_units_batch(self, repo: Repository, step) -> list[Subject] | None:
        data_root = self._resolve_data_root(repo, step)
        ents = self._entities(data_root)
        pending = []
        for _, row in ents.iterrows():
            desc = row["description"]
            n = self._desc_count(desc)
            if n > 1:
                u = repo.get_unit_by_subject(step.id, "entity", row["title"])
                if u is None or u.status == UnitStatus.PENDING:
                    pending.append(Subject("entity", row["title"]))
        return pending or None

    @staticmethod
    def _resolve_data_root(repo: Repository, step) -> Path:
        job = repo.get_job(step.job_id)
        with session_scope(repo.engine) as s:
            kb = s.scalar(select(KnowledgeBase).where(KnowledgeBase.id == job.kb_id))
            return Path(kb.data_root)

    async def run_unit(self, adapter, unit, repo: Repository) -> UnitResult:
        data_root = self._resolve_data_root(repo, repo.get_step(unit.step_id))
        ents = self._entities(data_root)
        row = ents[ents["title"] == unit.subject_id].iloc[0]
        descriptions = [str(d) for d in row["description"]]
        merged = await adapter.summarize_entity(unit.subject_id, descriptions)
        return UnitResult(
            payload=merged,
            input_hash=hashlib.sha512(json.dumps(descriptions).encode()).hexdigest(),
            llm_raw_output=merged,
        )

    def persist(self, data_root: Path, unit, result: UnitResult) -> None:
        d = data_root / "summaries"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{unit.subject_id}.json").write_text(json.dumps({"summary": result.payload}))

    def finalize(self, repo: Repository, adapter, step, data_root: Path, min_success_ratio: float) -> StepStatus:
        units = repo.list_units(step.id)
        ents = self._entities(data_root).copy()
        if units:
            succeeded = [u for u in units if u.status == UnitStatus.SUCCEEDED]
            if len(succeeded) / len(units) < min_success_ratio:
                return StepStatus.PARTIALLY_FAILED
            summaries = {}
            for u in succeeded:
                p = data_root / "summaries" / f"{u.subject_id}.json"
                if p.exists():
                    summaries[u.subject_id] = json.loads(p.read_text())["summary"]

            def _desc(title, current):
                if title in summaries:
                    return summaries[title]
                # Preserve single-description entities as the underlying string.
                if isinstance(current, str):
                    return current
                try:
                    values = list(current)
                except TypeError:
                    return current
                return values[0] if values else current

            ents["description"] = [_desc(t, c) for t, c in zip(ents["title"], ents["description"])]
        ents.to_parquet(data_root / "entities.parquet")
        return StepStatus.SUCCEEDED
