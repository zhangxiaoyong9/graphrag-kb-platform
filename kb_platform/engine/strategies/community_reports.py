"""CommunityReportsStrategy: generate community reports bottom-up by level."""

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
from kb_platform.graph.adapter import CommunityReport


def _data_root(repo: Repository, step) -> Path:
    job = repo.get_job(step.job_id)
    with session_scope(repo.engine) as s:
        kb = s.scalar(select(KnowledgeBase).where(KnowledgeBase.id == job.kb_id))
        return Path(kb.data_root)


class CommunityReportsStrategy:
    """Generate community reports bottom-up by level.

    ``next_units_batch`` returns the deepest level whose communities still need
    a report (unit None/PENDING). Once a level is terminal (all SUCCEEDED/FAILED)
    the next call advances to the parent level, whose context then reads the
    already-persisted child reports from disk.
    """

    kind = UnitKind.COMMUNITY_REPORT

    def _read(self, root: Path):
        return (
            pd.read_parquet(root / "communities.parquet"),
            pd.read_parquet(root / "entities.parquet"),
            pd.read_parquet(root / "relationships.parquet"),
        )

    def next_units_batch(self, repo: Repository, step) -> list[Subject] | None:
        root = _data_root(repo, step)
        comms, _, _ = self._read(root)
        levels = sorted(comms["level"].unique(), reverse=True)  # 最深(叶子)先
        for level in levels:
            rows = comms[comms["level"] == level]
            pending = []
            for _, row in rows.iterrows():
                u = repo.get_unit_by_subject(step.id, "community", row["community_id"])
                # Corrected filter: only PENDING/None units are re-emitted so a
                # FAILED community does not reappear every iteration (which would
                # infinite-loop the worker). A level whose communities are all
                # SUCCEEDED/FAILED yields no pending -> loop advances to parent.
                if u is None or u.status == UnitStatus.PENDING:
                    pending.append(Subject("community", row["community_id"]))
            if pending:
                return pending
        return None

    def _context(self, root: Path, comm_id: str) -> dict:
        comms, ents, rels = self._read(root)
        row = comms[comms["community_id"] == comm_id].iloc[0]
        members = list(row["entity_ids"])
        ent_rows = ents[ents["title"].isin(members)][["title", "description"]].to_dict("records")
        rel_rows = rels[rels["source"].isin(members) & rels["target"].isin(members)][["source", "target", "description"]].to_dict("records")
        # Children = communities whose parent is this community (excluding self).
        child_ids = [c for c in list(comms[comms["parent"] == comm_id]["community_id"]) if c != comm_id]
        sub_reports = []
        for cid in child_ids:
            p = root / "reports" / f"{cid}.json"
            if p.exists():
                sub_reports.append(json.loads(p.read_text()))
        return {
            "community": comm_id,
            "level": int(row["level"]),
            "entities": ent_rows,
            "relationships": rel_rows,
            "sub_reports": sub_reports,
        }

    async def run_unit(self, adapter, unit, repo: Repository) -> UnitResult:
        root = _data_root(repo, repo.get_step(unit.step_id))
        ctx = self._context(root, unit.subject_id)
        report: CommunityReport = await adapter.report_community(ctx)
        return UnitResult(
            payload=report,
            input_hash=hashlib.sha512(json.dumps(ctx, default=str).encode()).hexdigest(),
            llm_raw_output=report.full_content,
        )

    def persist(self, data_root: Path, unit, result: UnitResult) -> None:
        d = data_root / "reports"
        d.mkdir(parents=True, exist_ok=True)
        rep: CommunityReport = result.payload
        (d / f"{unit.subject_id}.json").write_text(json.dumps({
            "title": rep.title,
            "summary": rep.summary,
            "findings": rep.findings,
            "rank": rep.rank,
            "full_content": rep.full_content,
            "level": rep.level,
            "community": rep.community,
        }))

    def finalize(self, repo: Repository, adapter, step, data_root: Path, min_success_ratio: float) -> StepStatus:
        units = repo.list_units(step.id)
        if not units:
            return StepStatus.PARTIALLY_FAILED
        succeeded = [u for u in units if u.status == UnitStatus.SUCCEEDED]
        if len(succeeded) / len(units) < min_success_ratio:
            return StepStatus.PARTIALLY_FAILED
        rows = []
        for u in succeeded:
            p = data_root / "reports" / f"{u.subject_id}.json"
            if p.exists():
                rows.append(json.loads(p.read_text()))
        pd.DataFrame(rows).to_parquet(data_root / "community_reports.parquet")
        return StepStatus.SUCCEEDED
