"""Control-plane status enums and state-machine transitions."""

from enum import StrEnum


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIALLY_FAILED = "partially_failed"
    FAILED = "failed"


class StepKind(StrEnum):
    ATOMIC = "atomic"
    UNIT_FANOUT = "unit_fanout"


class UnitStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class UnitKind(StrEnum):
    EXTRACT_GRAPH = "extract_graph"
    SUMMARIZE_DESCRIPTIONS = "summarize_descriptions"
    COMMUNITY_REPORT = "community_report"


_TRANSITIONS: dict[StrEnum, set[StrEnum]] = {
    JobStatus.PENDING: {JobStatus.RUNNING, JobStatus.CANCELLED},
    JobStatus.RUNNING: {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED},
    JobStatus.SUCCEEDED: set(),
    JobStatus.FAILED: set(),
    JobStatus.CANCELLED: set(),
    StepStatus.PENDING: {StepStatus.RUNNING},
    StepStatus.RUNNING: {
        StepStatus.SUCCEEDED,
        StepStatus.PARTIALLY_FAILED,
        StepStatus.FAILED,
    },
    StepStatus.PARTIALLY_FAILED: {StepStatus.RUNNING},  # 重试失败单元时回到 running
    StepStatus.SUCCEEDED: set(),
    StepStatus.FAILED: set(),
    UnitStatus.PENDING: {UnitStatus.RUNNING},
    UnitStatus.RUNNING: {UnitStatus.SUCCEEDED, UnitStatus.FAILED},
    UnitStatus.FAILED: {UnitStatus.PENDING},  # 手动重试
    UnitStatus.SUCCEEDED: set(),
}


def allowed_transitions(status: StrEnum) -> set[StrEnum]:
    """Return the set of statuses reachable from ``status``."""
    return _TRANSITIONS.get(status, set())


def transition(current: StrEnum, target: StrEnum) -> StrEnum:
    """Validate a state transition; return ``target`` or raise ``ValueError``."""
    if target not in _TRANSITIONS.get(current, set()):
        msg = f"Illegal transition: {current!r} -> {target!r}"
        raise ValueError(msg)
    return target
