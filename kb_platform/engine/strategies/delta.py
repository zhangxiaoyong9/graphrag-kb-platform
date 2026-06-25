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
