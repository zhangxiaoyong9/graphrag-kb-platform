import logging

import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import JobStatus, StepKind, StepStatus
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.failure_diagnostics import collect_failure_diagnostics
from kb_platform.engine.orchestrator import Orchestrator
from kb_platform.engine.spec import StepSpec
from kb_platform.graph.adapter import FakeGraphAdapter


def _repo(tmp_path) -> Repository:
    engine = create_engine(f"sqlite:///{tmp_path}/diagnostics.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as session:
        session.add(
            KnowledgeBase(
                name="diagnostics",
                method="standard",
                settings_json="{}",
                data_root=str(tmp_path),
            )
        )
    return Repository(engine)


def test_failure_diagnostics_are_bounded_grouped_and_private(tmp_path):
    repo = _repo(tmp_path)
    job = repo.create_job(
        kb_id=1,
        type="full",
        specs=[StepSpec("extract_graph", StepKind.UNIT_FANOUT)],
    )
    step = job.steps[0]
    repo.add_units(
        step.id,
        [("entity", "private customer name"), ("entity", "another private name")],
        kind="extract_graph",
    )
    first, second = repo.list_units(step.id)
    repo.set_unit_failed(
        first.id,
        "RuntimeError: Authorization: Bearer very-secret-token upstream rejected",
    )
    repo.set_unit_failed(second.id, "HTTP 429: rate limited")

    diagnostics = collect_failure_diagnostics(repo, step.id, limit=1)

    assert diagnostics.total == 2
    assert diagnostics.omitted == 1
    assert diagnostics.type_counts == {"RuntimeError": 1, "HTTP429": 1}
    assert diagnostics.samples[0].subject_hash != "private customer name"
    assert "private customer name" not in repr(diagnostics)
    assert "very-secret-token" not in diagnostics.samples[0].error
    assert "[REDACTED]" in diagnostics.samples[0].error
    assert "first_error='RuntimeError: Authorization: Bearer [REDACTED]" in diagnostics.summary


@pytest.mark.asyncio
async def test_resumed_failed_units_log_persisted_reasons(tmp_path, caplog):
    class ExistingFailureStrategy:
        kind = "extract_graph"

        def next_units_batch(self, repo, step):  # noqa: ARG002
            return None

        def finalize(self, repo, adapter, step, data_root, min_success_ratio):  # noqa: ARG002
            return StepStatus.PARTIALLY_FAILED

    repo = _repo(tmp_path)
    job = repo.create_job(
        kb_id=1,
        type="full",
        specs=[StepSpec("extract_graph", StepKind.UNIT_FANOUT)],
    )
    step = job.steps[0]
    unit = repo.add_unit(step.id, "entity", "private entity title", kind="extract_graph")
    repo.set_unit_failed(unit.id, "RuntimeError: upstream response was invalid")
    orchestrator = Orchestrator(
        repo=repo,
        adapter=FakeGraphAdapter(),
        data_root=str(tmp_path),
        strategies={"extract_graph": ExistingFailureStrategy()},
    )

    with caplog.at_level(logging.INFO):
        await orchestrator.run(job.id)

    messages = [record.getMessage() for record in caplog.records]
    assert repo.get_job(job.id).status == JobStatus.FAILED
    assert any(
        "unit.failure_summary" in message
        and f"unit={unit.id}" in message
        and "RuntimeError" in message
        and "upstream response was invalid" in message
        for message in messages
    )
    assert any(
        "status=partially_failed" in message
        and "failed=1" in message
        and "failure_types=RuntimeError:1" in message
        and "first_error='RuntimeError: upstream response was invalid'" in message
        for message in messages
    )
    assert any("job" in message and "stopping at step" in message for message in messages)
    assert all("private entity title" not in message for message in messages)


@pytest.mark.asyncio
async def test_worker_failed_job_log_names_step_and_reason(tmp_path, caplog, monkeypatch):
    from kb_platform.worker import run_worker_once

    repo = _repo(tmp_path)
    job = repo.create_job_pending(kb_id=1, method="standard")

    async def fail_with_persisted_reason(self, job_id, min_success_ratio=1.0):  # noqa: ARG001
        step = self.repo.get_steps(job_id)[0]
        self.repo.set_step_status(
            step.id,
            StepStatus.PARTIALLY_FAILED,
            error="failed_units=19 failure_types=RuntimeError:19 sample_unit_ids=1,2",
        )
        self.repo.set_job_status(job_id, JobStatus.FAILED)

    monkeypatch.setattr(Orchestrator, "run", fail_with_persisted_reason)

    with caplog.at_level(logging.INFO):
        await run_worker_once(repo=repo, adapter_factory=lambda kb: FakeGraphAdapter())

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        f"job {job.id} done" in message
        and "status=failed" in message
        and "step_name=chunk_documents" in message
        and "failed_units=19" in message
        for message in messages
    )
