"""Unit-step strategy abstraction."""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from kb_platform.db.enums import StepStatus, UnitKind
from kb_platform.db.repository import Repository


@dataclass
class Subject:
    subject_type: str
    subject_id: str


def subject_filename(subject_id: str) -> str:
    """Filesystem-safe JSON filename (incl. ``.json``) for a unit ``subject_id``.

    The SUMMARIZE_DESCRIPTIONS subject is an entity *title*, which LLM extraction
    can fill with '/', ':' (URLs, paths), or any unicode — using it verbatim as a
    filename turns ``summaries/"http:/x/y.json"`` into a non-existent subdirectory
    and raises ``FileNotFoundError``. We sanitize to a path-safe stem and append a
    short sha256 suffix so two distinct subjects never collide even when their
    sanitized stems are identical. Deterministic, so persist and finalize always
    agree on the same path.
    """
    key = str(subject_id)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", key)[:48]
    return f"{stem}__{digest}.json"


@dataclass
class UnitResult:
    payload: Any
    input_hash: str | None = None
    cost_json: str | None = None
    llm_raw_output: str | None = None


class UnitStepStrategy(Protocol):
    kind: UnitKind

    def next_units_batch(self, repo: Repository, step) -> list[Subject] | None: ...

    async def run_unit(self, adapter, unit, repo: Repository) -> UnitResult: ...

    def persist(self, data_root: Path, unit, result: UnitResult) -> None: ...

    def finalize(
        self, repo: Repository, adapter, step, data_root: Path, min_success_ratio: float
    ) -> StepStatus: ...


STRATEGIES: dict[str, UnitStepStrategy] = {}


def register_strategy(name: str, strategy: UnitStepStrategy) -> None:
    STRATEGIES[name] = strategy


def default_strategies() -> dict[str, UnitStepStrategy]:
    """The built-in strategy set for a full-index pipeline.

    Constructed afresh each call (no module-global mutation). Tests and the
    incremental wiring override entries by copying this dict.
    """
    from kb_platform.engine.strategies.community_reports import CommunityReportsStrategy
    from kb_platform.engine.strategies.extract_graph import ExtractGraphStrategy
    from kb_platform.engine.strategies.summarize_descriptions import SummarizeDescriptionsStrategy

    return {
        "extract_graph": ExtractGraphStrategy(),
        "summarize_descriptions": SummarizeDescriptionsStrategy(),
        "community_reports": CommunityReportsStrategy(),
    }
