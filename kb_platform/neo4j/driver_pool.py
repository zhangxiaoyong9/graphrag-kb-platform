"""Process-level async Neo4j driver pool, keyed by full connection identity.

Drivers are expensive to create, so they are reused across requests. The key is
``(uri, username, password)`` — a rotated password therefore picks up a fresh
driver, while the (common) steady-state reuses one driver per KB. Mirrors
``kb_platform/llm/http_client.py``: a module-level dict + lock + lazy ``close_all``.

``import neo4j`` is lazy (inside ``_build_driver``) so the platform runs unchanged
without the ``[neo4j]`` extra. ``close_all`` is wired into bootstrap.close_clients.
"""

from __future__ import annotations

import threading
from typing import Any

_LOCK = threading.Lock()
_DRIVERS: dict[tuple[str, str, str], Any] = {}


def _build_driver(uri: str, username: str, password: str):
    """Construct a real neo4j async driver. Imported lazily so callers that only
    resolve engines (no live query) don't require the [neo4j] extra."""
    import neo4j  # noqa: PLC0415 - lazy on purpose

    return neo4j.AsyncGraphDatabase.driver(uri, auth=(username, password))


def get_driver(uri: str, username: str, password: str):
    """Return the pooled async driver for this (uri, username, password)."""
    key = (uri, username, password)
    with _LOCK:
        d = _DRIVERS.get(key)
        if d is None:
            d = _build_driver(uri, username, password)
            _DRIVERS[key] = d
        return d


async def close_all() -> None:
    """Close every pooled driver. Called on process shutdown."""
    with _LOCK:
        drivers = list(_DRIVERS.values())
        _DRIVERS.clear()
    for d in drivers:
        await d.close()


def _reset_for_test() -> None:
    with _LOCK:
        _DRIVERS.clear()
