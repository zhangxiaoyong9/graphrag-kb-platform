"""Safe, bounded diagnostics for failed fan-out units."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass

from kb_platform.db.enums import UnitStatus
from kb_platform.logging_config import redact_text

_EXCEPTION_PREFIX = re.compile(
    r"^(?P<name>[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Timeout))(?::|$)"
)
_HTTP_STATUS = re.compile(r"\bHTTP\s+(?P<status>[1-5][0-9]{2})\b", re.IGNORECASE)


@dataclass(frozen=True)
class FailedUnitSample:
    unit_id: int
    subject_type: str
    subject_hash: str
    attempt_no: int
    error_type: str
    error: str


@dataclass(frozen=True)
class FailureDiagnostics:
    total: int
    omitted: int
    type_counts: dict[str, int]
    samples: tuple[FailedUnitSample, ...]

    @property
    def summary(self) -> str:
        types = ",".join(f"{name}:{count}" for name, count in self.type_counts.items())
        ids = ",".join(str(sample.unit_id) for sample in self.samples)
        first_error = self.samples[0].error if self.samples else "<missing unit error>"
        return redact_text(
            f"failed_units={self.total} failure_types={types or 'unknown'} "
            f"first_error={first_error!r} sample_unit_ids={ids or '-'} "
            f"omitted={self.omitted}",
            limit=900,
        )


def _error_type(error: str | None) -> str:
    value = (error or "").strip()
    match = _EXCEPTION_PREFIX.match(value)
    if match:
        return match.group("name").rsplit(".", 1)[-1]
    http = _HTTP_STATUS.search(value)
    if http:
        return f"HTTP{http.group('status')}"
    lowered = value.lower()
    if "timeout" in lowered or "timed out" in lowered:
        return "Timeout"
    if "connection" in lowered or "refused" in lowered:
        return "ConnectionError"
    if "json" in lowered or "schema" in lowered or "parse" in lowered:
        return "InvalidResponse"
    return "UnknownError"


def collect_failure_diagnostics(repo, step_id: int, *, limit: int = 10) -> FailureDiagnostics:
    """Read persisted failures so resumed jobs retain actionable diagnostics.

    Subject values may contain document-derived entity names, so logs only expose
    a stable short hash. Full errors are bounded and pass through the shared
    redactor before they reach either logs or ``Step.error``.
    """

    failed = [unit for unit in repo.list_units(step_id) if unit.status == UnitStatus.FAILED]
    failed.sort(key=lambda unit: unit.id)
    type_counts = Counter(_error_type(unit.error) for unit in failed)
    samples = tuple(
        FailedUnitSample(
            unit_id=unit.id,
            subject_type=unit.subject_type,
            subject_hash=hashlib.sha256(str(unit.subject_id).encode()).hexdigest()[:12],
            attempt_no=unit.attempt_no,
            error_type=_error_type(unit.error),
            error=redact_text(unit.error or "<missing unit error>", limit=300),
        )
        for unit in failed[: max(0, limit)]
    )
    return FailureDiagnostics(
        total=len(failed),
        omitted=max(0, len(failed) - len(samples)),
        type_counts=dict(type_counts.most_common()),
        samples=samples,
    )
