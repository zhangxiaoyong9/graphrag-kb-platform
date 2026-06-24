import pytest

from kb_platform.db.enums import (
    JobStatus,
    UnitStatus,
    allowed_transitions,
    transition,
)


def test_job_transitions():
    assert transition(JobStatus.PENDING, JobStatus.RUNNING) == JobStatus.RUNNING
    assert JobStatus.SUCCEEDED in allowed_transitions(JobStatus.RUNNING)


def test_unit_retry_resets_to_pending():
    assert transition(UnitStatus.FAILED, UnitStatus.PENDING) == UnitStatus.PENDING


def test_illegal_transition_raises():
    with pytest.raises(ValueError):
        transition(JobStatus.SUCCEEDED, JobStatus.RUNNING)
