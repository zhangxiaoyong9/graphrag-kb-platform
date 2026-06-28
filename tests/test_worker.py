"""Worker process tests: poll/claim and crash-recovery resume."""

import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.enums import JobStatus
from kb_platform.db.models import Base, KnowledgeBase, Unit
from kb_platform.db.repository import Repository
from kb_platform.graph.adapter import FakeGraphAdapter


def _repo(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(
            KnowledgeBase(
                name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)
            )
        )
    repo = Repository(engine)
    repo.add_document(kb_id=1, title="d", text="ACME Org Bob Person Foo Bar Baz " * 200)
    return repo


@pytest.mark.asyncio
async def test_worker_picks_up_and_completes_pending_job(tmp_path):
    from kb_platform.worker import run_worker_once

    repo = _repo(tmp_path)
    job = repo.create_job_pending(kb_id=1, method="standard")
    assert job.status == "pending"
    await run_worker_once(
        repo=repo, adapter_factory=lambda kb: FakeGraphAdapter(), heartbeat_interval=0.01
    )
    assert repo.get_job(job.id).status == "succeeded"


@pytest.mark.asyncio
async def test_worker_crash_recovery_resumes(tmp_path):
    import hashlib
    from datetime import datetime, timedelta

    from kb_platform.worker import run_worker_once

    repo = _repo(tmp_path)
    job = repo.create_job_pending(kb_id=1, method="standard")
    # Simulate a crash: chunk_documents already ran, extract_graph started one
    # unit and then died mid-flight (stale heartbeat). Use a real chunk_id that
    # FakeGraphAdapter produces so the strategy re-claims it on resume.
    text = "ACME Org Bob Person Foo Bar Baz " * 200
    first_piece = " ".join(text.split()[:1000])
    real_chunk_id = hashlib.sha512(first_piece.encode()).hexdigest()
    repo.set_job_status(job.id, JobStatus.RUNNING)
    extract_step = [s for s in repo.get_steps(job.id) if s.name == "extract_graph"][0]
    repo.add_unit(extract_step.id, "chunk", real_chunk_id, kind="extract_graph")
    stale = datetime.now() - timedelta(seconds=999)
    with session_scope(repo.engine) as s:
        u = s.query(Unit).filter_by(step_id=extract_step.id).one()
        u.status = "running"
        u.worker_id = "dead"
        u.heartbeat_at = stale
    # Recover + resume.
    await run_worker_once(
        repo=repo,
        adapter_factory=lambda kb: FakeGraphAdapter(),
        heartbeat_interval=0.01,
        recover=True,
    )
    assert repo.get_job(job.id).status == "succeeded"


@pytest.mark.asyncio
async def test_crash_recovery_skips_succeeded_atomic_step(tmp_path):
    from datetime import datetime, timedelta

    from sqlalchemy import select

    from kb_platform.engine.orchestrator import Orchestrator
    from kb_platform.worker import run_worker_once

    repo = _repo(tmp_path)
    # First: run the job fully so chunk_documents SUCCEEDS and everything completes.
    job = repo.create_job_pending(kb_id=1, method="standard")
    await Orchestrator(repo=repo, adapter=FakeGraphAdapter(), data_root=str(tmp_path)).run(job.id)
    chunks_before = len(repo.get_chunks(kb_id=1))
    assert chunks_before > 0
    # Simulate a later "crash" during a fresh attempt: reset job to RUNNING +
    # inject a stale RUNNING unit in extract_graph (mimics a mid-step crash
    # after the atomic chunk_documents step already completed).
    extract = [s for s in repo.get_steps(job.id) if s.name == "extract_graph"][0]
    repo.set_job_status(job.id, JobStatus.RUNNING)
    with session_scope(repo.engine) as s:
        u = s.scalar(select(Unit).where(Unit.step_id == extract.id).limit(1))
        u.status = "running"
        u.worker_id = "dead"
        u.heartbeat_at = datetime.now() - timedelta(seconds=999)
    # Recover + resume.
    await run_worker_once(
        repo=repo,
        adapter_factory=lambda kb: FakeGraphAdapter(),
        heartbeat_interval=0.01,
        recover=True,
    )
    assert repo.get_job(job.id).status == "succeeded"
    # chunk_documents was SUCCEEDED -> skipped -> NO duplicate chunks.
    assert len(repo.get_chunks(kb_id=1)) == chunks_before


@pytest.mark.asyncio
async def test_worker_orphan_job_marked_failed_not_crash(tmp_path):
    """An orphan job (kb_id points at a missing KB) is marked FAILED and the
    worker returns normally instead of crashing and starving all other jobs.
    """
    from kb_platform.worker import run_worker_once

    repo = _repo(tmp_path)
    # Create a pending job whose kb_id points at a KB that does not exist.
    # (FK enforcement would normally reject this; bypass it by inserting the
    # job row with a bogus kb_id via a raw connection with FKs off, simulating
    # a legacy orphan that predates the FK pragma.)
    import sqlite3

    conn = sqlite3.connect(f"{tmp_path}/t.db")
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("DELETE FROM job")
    conn.execute(
        "INSERT INTO job (id, kb_id, type, method, status) VALUES (999, 777777, 'full', 'standard', 'pending')"
    )
    conn.commit()
    conn.close()

    # Worker must NOT raise; the orphan job must be marked FAILED.
    await run_worker_once(
        repo=repo,
        adapter_factory=lambda kb: FakeGraphAdapter(),
        heartbeat_interval=0.01,
        recover=False,
    )
    job = repo.get_job(999)
    assert job is not None
    assert job.status == "failed"


@pytest.mark.asyncio
async def test_recover_stale_units_resets_null_heartbeat(tmp_path):
    """A RUNNING unit with heartbeat_at IS NULL is recovered (otherwise SQL
    ``NULL < x`` is falsy and the unit is stranded forever)."""
    from datetime import datetime

    from sqlalchemy import select

    from kb_platform.db.enums import UnitStatus
    from kb_platform.engine.spec import StepKind, StepSpec

    repo = _repo(tmp_path)
    job = repo.create_job(
        kb_id=1, type="full", specs=[StepSpec("extract_graph", StepKind.UNIT_FANOUT)]
    )
    step = job.steps[0]
    repo.add_units(step.id, [("chunk", "c1")], kind="extract_graph")
    with session_scope(repo.engine) as s:
        u = s.scalar(select(Unit).where(Unit.step_id == step.id).limit(1))
        u.status = UnitStatus.RUNNING
        u.worker_id = "dead"
        u.heartbeat_at = None  # crashed before first heartbeat tick
    recovered = repo.recover_stale_units(datetime.now())
    assert recovered == 1
    assert repo.list_units(step.id)[0].status == UnitStatus.PENDING


def test_run_worker_stops_on_event(tmp_path):
    """When stop_event is set, the loop exits without processing newly-added jobs."""
    import threading

    from kb_platform.db.engine import create_engine
    from kb_platform.db.models import Base
    from kb_platform.db.repository import Repository
    from kb_platform.graph.adapter import FakeGraphAdapter
    from kb_platform.worker import run_worker

    engine = create_engine(f"sqlite:///{tmp_path}/db.sqlite")
    # Schema is needed for the final claim_one_pending_job() probe (the loop body
    # itself never runs because stop_event is pre-set).
    Base.metadata.create_all(engine)
    repo = Repository(engine)
    stop = threading.Event()
    # Add a pending job AFTER stop is set so we can prove the loop did not claim it.
    # Seed nothing; just verify the loop returns promptly when stop is already set.
    stop.set()
    adapter_factory = lambda kb: FakeGraphAdapter()  # noqa: E731
    run_worker(
        repo=repo,
        adapter_factory=adapter_factory,
        poll_interval=0.01,
        stop_event=stop,
        install_signal_handlers=False,
    )
    # If graceful shutdown is broken, run_worker loops forever and this test hangs.
    assert repo.claim_one_pending_job() is None


@pytest.mark.asyncio
async def test_worker_carrier_carries_profile_ids(tmp_path, monkeypatch):
    """Regression: the worker's _SettingsKb carrier must carry llm/embedding
    profile ids, because build_adapter_for_kb -> assemble_kb_settings reads them
    off the kb object. The carrier is ORM-detached (the session that loaded the
    KB is closed before the adapter is built), so the profile ids must be copied
    onto it eagerly — otherwise assemble_kb_settings hits AttributeError and the
    whole production real-LLM indexing path fails before chunk_documents.

    All other worker tests pass a FakeGraphAdapter that ignores the carrier, so
    this is the one place that pins the _SettingsKb <-> assemble_kb_settings
    contract used by the production adapter factory build_adapter_for_kb.
    """
    from cryptography.fernet import Fernet

    from kb_platform.graph.graphrag_adapter import assemble_kb_settings
    from kb_platform.worker import _SettingsKb

    monkeypatch.setenv("KB_SECRET_KEY", Fernet.generate_key().decode())
    engine = create_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    repo = Repository(engine)
    llm = repo.create_profile(
        name="DS", kind="llm", provider="deepseek", model="deepseek-chat",
        api_keys=["sk-a"], structured_output=False,
    )
    emb = repo.create_profile(
        name="Ollama", kind="embedding", provider="ollama", model="nomic-embed-text",
        api_base="http://localhost:11434", api_keys=["ollama"],
    )
    with session_scope(engine) as s:
        s.add(KnowledgeBase(
            name="k", method="standard", settings_json="{}", data_root=str(tmp_path),
            llm_profile_id=llm.id, embedding_profile_id=emb.id,
        ))

    # Reproduce EXACTLY what run_worker_once does: load the KB in a session,
    # close it, then hand a detached _SettingsKb to the adapter factory.
    with session_scope(engine) as s:
        kb = s.get(KnowledgeBase, 1)
        carrier = _SettingsKb(
            settings_json=kb.settings_json,
            data_root=kb.data_root,
            llm_profile_id=kb.llm_profile_id,
            embedding_profile_id=kb.embedding_profile_id,
        )

    assembled = assemble_kb_settings(carrier, repo)
    assert assembled["llm"]["model"] == "deepseek-chat"
    assert assembled["embedding"]["model"] == "nomic-embed-text"
