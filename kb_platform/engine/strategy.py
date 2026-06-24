"""Unit-step strategy abstraction."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from kb_platform.db.enums import StepStatus, UnitKind
from kb_platform.db.repository import Repository


@dataclass
class Subject:
    subject_type: str
    subject_id: str


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

    def finalize(self, repo: Repository, adapter, step, data_root: Path, min_success_ratio: float) -> StepStatus: ...


STRATEGIES: dict[str, UnitStepStrategy] = {}


def register_strategy(name: str, strategy: UnitStepStrategy) -> None:
    STRATEGIES[name] = strategy
