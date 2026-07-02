"""Process-wide shared httpx.AsyncClient pool, keyed by ssl_verify.

NativeCompletion/NativeEmbedding are transient (one instance per
create_completion/create_embedding call), so per-instance clients would churn
connection pools and leak until GC (with noisy httpx teardown warnings). Clients
here are shared across every native LLM call in the process. `verify` is a
client-level setting, so we keep one client per ssl_verify value (True/False) —
at most two clients. Close on shutdown via close_all() (bootstrap.close_clients).
"""

from __future__ import annotations

import threading
from typing import Any  # noqa: F401  (kept for parity with sibling modules)

import httpx

_LOCK = threading.Lock()
_CLIENTS: dict[bool, httpx.AsyncClient] = {}
_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


def get_client(ssl_verify: bool = True) -> httpx.AsyncClient:
    """Return the shared client for this ssl_verify (created lazily)."""
    with _LOCK:
        c = _CLIENTS.get(ssl_verify)
        if c is None:
            c = httpx.AsyncClient(timeout=_TIMEOUT, verify=ssl_verify)
            _CLIENTS[ssl_verify] = c
        return c


async def close_all() -> None:
    """Close every pooled client. Called on process shutdown."""
    with _LOCK:
        clients = list(_CLIENTS.values())
        _CLIENTS.clear()
    for c in clients:
        await c.aclose()


def _reset_for_test() -> None:
    """Sync test reset. Tests inject their own MockTransport clients for real
    network paths, so the pooled clients here are never used for live calls —
    clearing the dict is enough for isolation."""
    with _LOCK:
        _CLIENTS.clear()
