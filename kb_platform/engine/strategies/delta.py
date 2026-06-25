# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""Delta-scoped strategies for incremental jobs (Phase 4 H).

Each reuses its parent strategy's run_unit/persist and overrides only the unit
selection (+ finalize carry-over), so the incremental cost is proportional to
what changed, not to the whole graph. The diff signal is Unit.input_hash, looked
up across jobs via Repository.last_succeeded_input_hash / has_succeeded_input_hash.
"""

import hashlib
import json
from pathlib import Path

from kb_platform.db.enums import StepStatus, UnitStatus
from kb_platform.engine.strategy import Subject
from kb_platform.engine.strategies.community_reports import (
    CommunityReportsStrategy,
    _data_root,
)
from kb_platform.engine.strategies.summarize_descriptions import SummarizeDescriptionsStrategy


class SummarizeDeltaStrategy(SummarizeDescriptionsStrategy):
    """Only re-summarize entities whose description set changed since the last success."""

    def next_units_batch(self, repo, step) -> list[Subject] | None:
        data_root = self._resolve_data_root(repo, step)
        ents = self._entities(data_root)
        job = repo.get_job(step.job_id)
        pending: list[Subject] = []
        for _, row in ents.iterrows():
            if self._desc_count(row["description"]) <= 1:
                continue
            descriptions = [str(d) for d in row["description"]]
            current = hashlib.sha512(json.dumps(descriptions).encode()).hexdigest()
            prev = repo.last_succeeded_input_hash(
                job.kb_id, "summarize_descriptions", "entity", row["title"]
            )
            if prev == current:
                continue  # unchanged -> reuse the on-disk summary (carry-over in finalize)
            u = repo.get_unit_by_subject(step.id, "entity", row["title"])
            if u is None or u.status == UnitStatus.PENDING:
                pending.append(Subject("entity", row["title"]))
        return pending or None

    def finalize(
        self, repo, adapter, step, data_root: Path, min_success_ratio: float
    ) -> StepStatus:
        ents = self._entities(data_root).copy()
        summaries: dict[str, str] = {}
        # Carry-over: every on-disk summary (from prior jobs) for entities in this graph.
        sdir = data_root / "summaries"
        for title in ents["title"]:
            p = sdir / f"{title}.json"
            if p.exists():
                summaries[str(title)] = json.loads(p.read_text())["summary"]
        # min_success_ratio applies to THIS job's units only:
        units = repo.list_units(step.id)
        if units:
            succeeded = [u for u in units if u.status == UnitStatus.SUCCEEDED]
            if len(succeeded) / len(units) < min_success_ratio:
                return StepStatus.PARTIALLY_FAILED

        def _desc(title, current):
            if str(title) in summaries:
                return summaries[str(title)]
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


def _delta_data_root(repo, step):
    return _data_root(repo, step)


class CommunityReportsDeltaStrategy(CommunityReportsStrategy):
    """Only report communities whose ctx (members + descriptions + sub-reports) is new.

    Community ids are reassigned by Leiden on every re-cluster, so delta matching
    is by ctx-content hash (the same hash run_unit records as input_hash), via
    Repository.has_succeeded_input_hash. A reports_by_hash/ sidecar lets finalize
    reuse a prior report for an unchanged community even when its id changed.
    """

    def _ctx_hash(self, root, comm_id) -> str:
        ctx = self._context(root, comm_id)
        return hashlib.sha512(json.dumps(ctx, default=str).encode()).hexdigest()

    def next_units_batch(self, repo, step) -> list[Subject] | None:
        root = _delta_data_root(repo, step)
        comms, _, _ = self._read(root)
        job = repo.get_job(step.job_id)
        for level in sorted(comms["level"].unique(), reverse=True):
            rows = comms[comms["level"] == level]
            pending = []
            for _, row in rows.iterrows():
                cid = row["community_id"]
                if repo.has_succeeded_input_hash(
                    job.kb_id, "community_report", self._ctx_hash(root, cid)
                ):
                    continue  # exact same community context already reported -> reuse
                u = repo.get_unit_by_subject(step.id, "community", cid)
                if u is None or u.status == UnitStatus.PENDING:
                    pending.append(Subject("community", cid))
            if pending:
                return pending
        return None

    def persist(self, data_root, unit, result) -> None:
        super().persist(data_root, unit, result)
        # Sidecar keyed by input_hash so a later incremental job can reuse this
        # report even after Leiden reassigns the community id.
        h = result.input_hash
        if h:
            d = data_root / "reports_by_hash"
            d.mkdir(parents=True, exist_ok=True)
            rep = result.payload
            (d / f"{h}.json").write_text(
                json.dumps(
                    {
                        "title": rep.title,
                        "summary": rep.summary,
                        "findings": rep.findings,
                        "rank": rep.rank,
                        "full_content": rep.full_content,
                        "level": rep.level,
                        "community": rep.community,
                    }
                )
            )

    def finalize(
        self, repo, adapter, step, data_root: Path, min_success_ratio: float
    ) -> StepStatus:
        import pandas as pd

        root = data_root
        comms, _, _ = self._read(root)
        rows = []
        # min_success_ratio over this job's units:
        units = repo.list_units(step.id)
        succeeded = [u for u in units if u.status == UnitStatus.SUCCEEDED] if units else []
        if units and len(succeeded) / len(units) < min_success_ratio:
            return StepStatus.PARTIALLY_FAILED
        for _, row in comms.iterrows():
            cid = row["community_id"]
            p = data_root / "reports" / f"{cid}.json"
            if p.exists():
                rows.append(json.loads(p.read_text()))
                continue
            # Carry-over via sidecar (community id changed but ctx identical):
            h = self._ctx_hash(root, cid)
            sp = data_root / "reports_by_hash" / f"{h}.json"
            if sp.exists():
                rec = json.loads(sp.read_text())
                rec["community"] = cid  # remap to the new community id
                rows.append(rec)
        pd.DataFrame(rows).to_parquet(data_root / "community_reports.parquet")
        return StepStatus.SUCCEEDED
