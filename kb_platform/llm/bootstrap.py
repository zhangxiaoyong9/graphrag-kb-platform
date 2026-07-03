"""One-call entrypoint: register the kb_native types + start the HealthProbe.

Called from server.py and worker.py before any adapter/engine is built. The
HealthProbe start is lazy: it only spawns its background task if there is a
running asyncio loop (both server and worker run inside one). Importing this
module in a sync test context does NOT spawn a task.
"""

from __future__ import annotations

import asyncio
import logging

from kb_platform.llm.registry import register_native

logger = logging.getLogger(__name__)

_bootstrapped = False
_probe = None  # type: ignore[var-annotated]


def bootstrap() -> None:
    """Register kb_native factory entries; idempotently start the HealthProbe.

    The probe is only started when there is a running asyncio loop (server and
    worker both run inside loops). In sync contexts (importing the module in a
    unit test) no task is spawned.
    """
    global _bootstrapped, _probe
    if _bootstrapped:
        # Re-attempt probe start in case the first bootstrap() ran before any
        # loop existed (e.g. imported eagerly, then a loop started later).
        _try_start_probe()
        return
    register_native()
    _try_start_probe()
    _bootstrapped = True


def _try_start_probe() -> None:
    """Start ONE HealthProbe per process if a loop is running and none is yet."""
    global _probe
    if _probe is not None:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # no running loop — defer (lazy); will start on next bootstrap()
    from kb_platform.llm.health import HealthProbe

    _probe = HealthProbe(interval=60.0)
    _probe.start()
    logger.debug("HealthProbe started")


async def stop_probe() -> None:
    """Stop the process-wide HealthProbe (shutdown hook). Safe if never started."""
    global _probe
    if _probe is not None:
        await _probe.stop()
        _probe = None


async def close_clients() -> None:
    """Close the shared httpx client pool + the Neo4j driver pool (shutdown hook)."""
    from kb_platform.llm.http_client import close_all

    await close_all()
    # Neo4j driver pool is lazy: only present when the [neo4j] extra is installed
    # and a cypher/hybrid query has run. Swallow ImportError so this is a no-op
    # for installs without the extra.
    try:
        from kb_platform.neo4j import driver_pool  # noqa: PLC0415

        await driver_pool.close_all()
    except Exception:  # noqa: BLE001 - shutdown must not raise
        logger.debug("neo4j driver_pool close skipped (extra absent or empty)")


__all__ = ["bootstrap", "stop_probe", "close_clients"]
