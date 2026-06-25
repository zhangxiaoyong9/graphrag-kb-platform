"""Background worker: polls SQLite for pending jobs, runs them with crash recovery."""

import asyncio
import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import select

from kb_platform.db.engine import session_scope
from kb_platform.db.models import KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.engine.orchestrator import Orchestrator
from kb_platform.graph.adapter import GraphAdapter
from typing import Callable

logger = logging.getLogger(__name__)


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

    ``adapter_factory`` always takes the KnowledgeBase and returns a GraphAdapter.
    """
    if recover:
        n_units = repo.recover_stale_units(datetime.now() - timedelta(seconds=stale_seconds))
        n_jobs = repo.recover_stale_jobs()
        if n_units or n_jobs:
            logger.info("recovered %d stale units, %d stale jobs", n_units, n_jobs)

    job = repo.claim_one_pending_job()
    if job is None:
        return

    with session_scope(repo.engine) as s:
        kb = s.scalar(select(KnowledgeBase).where(KnowledgeBase.id == job.kb_id))
        data_root = kb.data_root
        min_ratio = _parse_min_ratio(kb.settings_json)

    adapter = adapter_factory(kb)
    orch = Orchestrator(repo=repo, adapter=adapter, data_root=data_root)
    await orch.run(job.id, min_success_ratio=min_ratio)


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
    **kw,
) -> None:
    """Production entry: loop forever, recovering + claiming one job per iteration."""
    while True:
        asyncio.run(run_worker_once(repo=repo, adapter_factory=adapter_factory, recover=True, **kw))
        time.sleep(poll_interval)


if __name__ == "__main__":
    import sys

    from kb_platform.db.engine import create_engine
    from kb_platform.graph.graphrag_adapter import build_adapter_from_settings

    db = sys.argv[1] if len(sys.argv) > 1 else "kb.db"
    repo = Repository(create_engine(f"sqlite:///{db}"))
    run_worker(repo=repo, adapter_factory=lambda kb: build_adapter_from_settings(kb.settings_json, kb.data_root))
