"""Background worker: polls SQLite for pending jobs, runs them with crash recovery."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable

from sqlalchemy import select

from kb_platform.db.engine import session_scope
from kb_platform.db.enums import JobStatus
from kb_platform.db.models import KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.orchestrator import Orchestrator
from kb_platform.graph.adapter import GraphAdapter

if TYPE_CHECKING:
    import threading

logger = logging.getLogger(__name__)


@dataclass
class _SettingsKb:
    """Lightweight, ORM-detached carrier for the KB fields the adapter factory needs.

    Closing the session_scope that loaded a real ORM ``KnowledgeBase`` would detach
    it; passing that object to ``adapter_factory`` risks lazy-load errors. This
    dataclass carries the plain-column fields production needs so the adapter
    factory reads attributes with no DB access: ``settings_json`` / ``data_root``
    for chunking + output paths, plus ``llm_profile_id`` / ``embedding_profile_id``
    which ``build_adapter_for_kb`` -> ``assemble_kb_settings`` resolves into provider
    profiles + decrypted keys (profile ids are Integer columns, so reading them
    inside the load session is safe — no lazy load).
    """

    settings_json: str
    data_root: str
    llm_profile_id: int | None = None
    embedding_profile_id: int | None = None
    llm_fallback_profile_ids: str | None = None


async def run_worker_once(
    *,
    repo: Repository,
    adapter_factory: Callable[[KnowledgeBase], GraphAdapter],
    heartbeat_interval: float = 5.0,
    stale_seconds: float = 30.0,
    recover: bool = False,
    concurrency: int = 4,
) -> None:
    """Recover (optional), claim one pending job, and run it to completion.

    Per-job exception isolation: any error while claiming or running a job
    (including an orphan job whose ``kb_id`` no longer exists) is caught, the
    job is marked FAILED, and this call returns normally so the poll loop can
    continue with the next job instead of crashing.
    """
    if recover:
        n_units = repo.recover_stale_units(datetime.now() - timedelta(seconds=stale_seconds))
        n_jobs = repo.recover_stale_jobs()
        if n_units or n_jobs:
            logger.info("recovered %d stale units, %d stale jobs", n_units, n_jobs)

    job = repo.claim_one_pending_job()
    if job is None:
        return

    try:
        with session_scope(repo.engine) as s:
            kb = s.scalar(select(KnowledgeBase).where(KnowledgeBase.id == job.kb_id))
            if kb is None:
                raise ValueError(f"job {job.id} references missing kb {job.kb_id}")
            data_root = kb.data_root
            settings_json = kb.settings_json
            llm_profile_id = kb.llm_profile_id
            embedding_profile_id = kb.embedding_profile_id
            llm_fallback_profile_ids = kb.llm_fallback_profile_ids

        adapter = adapter_factory(
            _SettingsKb(
                settings_json=settings_json,
                data_root=data_root,
                llm_profile_id=llm_profile_id,
                embedding_profile_id=embedding_profile_id,
                llm_fallback_profile_ids=llm_fallback_profile_ids,
            )
        )
        orch = Orchestrator(
            repo=repo,
            adapter=adapter,
            data_root=data_root,
            concurrency=_parse_concurrency(settings_json, concurrency),
        )
        await orch.run(job.id, min_success_ratio=_parse_min_ratio(settings_json))
    except Exception:  # noqa: BLE001
        logger.exception("job %s failed; marking FAILED", job.id)
        repo.set_job_status(job.id, JobStatus.FAILED)


def _parse_concurrency(settings_json: str, default: int = 4) -> int:
    import json

    try:
        val = json.loads(settings_json or "{}").get("concurrency", default)
        return max(1, int(val))
    except Exception:
        return default


def _parse_min_ratio(settings_json: str) -> float:
    import json

    try:
        return float(json.loads(settings_json or "{}").get("min_unit_success_ratio", 1.0))
    except Exception:
        return 1.0


def run_worker(
    *,
    repo: Repository,
    adapter_factory: Callable[[KnowledgeBase], GraphAdapter],
    poll_interval: float = 2.0,
    stop_event: threading.Event | None = None,
    install_signal_handlers: bool = True,
    **kw,
) -> None:
    """Production entry: loop until stopped, recovering + claiming one job per iteration.

    Installs SIGTERM/SIGINT handlers (unless ``install_signal_handlers`` is False)
    that set ``stop_event``. On stop, the in-flight ``run_worker_once`` finishes,
    then the loop returns so the process can exit cleanly. Hard kills (SIGKILL)
    are still recovered on the next start via stale RUNNING -> PENDING reset.
    """
    import signal
    import threading

    from kb_platform.logging_config import setup_logging

    # Centralized logging for the worker process BEFORE the LLM bootstrap runs
    # (so the bootstrap's own logs are captured). Tests call run_worker_once
    # directly and don't want this side effect, hence it lives here only.
    setup_logging("worker")

    # Register kb_native factories before any adapter is built (idempotent).
    # Import is inside run_worker so unit tests that import the worker module
    # don't pay the registry cost / trigger import-time side effects.
    #
    # Note on the HealthProbe: bootstrap() also tries to start one long-lived
    # HealthProbe per process, but that path is a no-op in the WORKER process.
    # bootstrap() is invoked here from sync code (before any asyncio.run), so
    # `_try_start_probe()` sees no running loop and defers; run_worker_once
    # (which runs inside asyncio.run) never re-calls bootstrap(), so the
    # deferred start never happens. The long-lived HealthProbe is hosted by
    # the SERVER process only (uvicorn's single persistent loop). Worker
    # breakers are therefore TRAFFIC-DRIVEN: real indexing calls go through
    # NativeCompletion -> breaker_registry, advancing each breaker and
    # triggering failover on failures — which provides indexing resilience
    # without an idle probe. (breaker_registry is per-process / in-memory, so
    # the server's probe does NOT warm the worker's breakers; each process
    # holds its own.)
    from kb_platform.llm.bootstrap import bootstrap as _bootstrap_llm

    _bootstrap_llm()

    if stop_event is None:
        stop_event = threading.Event()

    def _stop(signum, frame):  # noqa: ARG001
        stop_event.set()

    if install_signal_handlers:
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

    while not stop_event.is_set():
        asyncio.run(run_worker_once(repo=repo, adapter_factory=adapter_factory, recover=True, **kw))
        if stop_event.wait(poll_interval):
            break

    # Defensive shutdown of the process-wide HealthProbe. In the WORKER process
    # this is a NO-OP today: bootstrap() is called from sync code above (no
    # running loop), and run_worker_once never re-calls bootstrap(), so no
    # HealthProbe is ever started here — see the note on bootstrap() above.
    # The long-lived probe lives in the SERVER process. Kept (rather than
    # deleted) for symmetry with the server lifespan and so a future
    # persistent-loop refactor of the worker starts/stops the probe
    # correctly without re-plumbing shutdown. stop_probe() is itself a
    # safe no-op when `_probe is None`.
    try:
        from kb_platform.llm.bootstrap import close_clients, stop_probe
        asyncio.run(stop_probe())
        asyncio.run(close_clients())
    except Exception:  # noqa: BLE001 - shutdown must not crash
        logger.debug("probe/client stop on worker shutdown failed", exc_info=True)


if __name__ == "__main__":
    import os
    import sys

    from kb_platform.db.engine import create_engine
    from kb_platform.graph.graphrag_adapter import build_adapter_for_kb

    db = sys.argv[1] if len(sys.argv) > 1 else "kb.db"
    os.environ.setdefault("KB_DB_URL", f"sqlite:///{db}")
    repo = Repository(create_engine(f"sqlite:///{db}"))
    run_worker(
        repo=repo,
        adapter_factory=lambda kb: build_adapter_for_kb(kb, repo),
    )
