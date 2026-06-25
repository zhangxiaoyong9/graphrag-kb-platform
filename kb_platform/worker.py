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
    dataclass carries only ``settings_json`` / ``data_root`` so production's
    ``build_adapter_from_settings`` reads plain attributes with no DB access.
    """

    settings_json: str
    data_root: str


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

        adapter = adapter_factory(_SettingsKb(settings_json=settings_json, data_root=data_root))
        orch = Orchestrator(
            repo=repo,
            adapter=adapter,
            data_root=data_root,
            concurrency=concurrency,
        )
        await orch.run(job.id, min_success_ratio=_parse_min_ratio(settings_json))
    except Exception:  # noqa: BLE001
        logger.exception("job %s failed; marking FAILED", job.id)
        repo.set_job_status(job.id, JobStatus.FAILED)


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


if __name__ == "__main__":
    import sys

    from kb_platform.db.engine import create_engine
    from kb_platform.graph.graphrag_adapter import build_adapter_from_settings

    db = sys.argv[1] if len(sys.argv) > 1 else "kb.db"
    repo = Repository(create_engine(f"sqlite:///{db}"))
    run_worker(
        repo=repo,
        adapter_factory=lambda kb: build_adapter_from_settings(kb.settings_json, kb.data_root),
    )
