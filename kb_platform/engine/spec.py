"""Step specification used to build a job's step list."""

from dataclasses import dataclass

from kb_platform.db.enums import StepKind


@dataclass
class StepSpec:
    name: str
    kind: StepKind
