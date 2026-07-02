"""Process-wide circuit-breaker registry keyed by endpoint identity.

Breakers in here live ACROSS NativeCompletion instances (each
``NativeCompletion`` is transient — built per ``create_completion`` call). The
HealthProbe (``kb_platform/llm/health.py``) drives these shared breakers so a
half-open probe success closes the breaker for every future gateway.

Identity = endpoint health: ``(provider, model, api_base, api_version)``. Keys
are NOT part of the identity (key rotation does not change endpoint health).
The LATEST full ``ProviderConfig`` (with a usable key) is stored alongside the
breaker and refreshed every time a ``NativeCompletion`` is built, so the probe
always has fresh credentials to issue its tiny completion.
"""

from __future__ import annotations

import threading

from kb_platform.llm.circuit_breaker import CircuitBreaker
from kb_platform.llm.request import ProviderConfig

_LOCK = threading.Lock()
# endpoint-key -> (breaker, latest ProviderConfig with a usable key)
_ENTRIES: dict[tuple, tuple[CircuitBreaker, ProviderConfig]] = {}


def breaker_key(cfg: ProviderConfig) -> tuple:
    """Return the endpoint-identity key for a ``ProviderConfig``."""
    return (cfg.provider, cfg.model, cfg.api_base, cfg.api_version)


def breaker_for(
    cfg: ProviderConfig,
    *,
    failure_threshold: int = 5,
    open_seconds: float = 30.0,
) -> CircuitBreaker:
    """Return the shared breaker for ``cfg``'s endpoint.

    Refreshes the stored config each call so the HealthProbe always sees fresh
    keys (the breaker instance itself is stable across calls for the same
    endpoint identity).
    """
    k = breaker_key(cfg)
    with _LOCK:
        entry = _ENTRIES.get(k)
        if entry is None:
            cb = CircuitBreaker(
                failure_threshold=failure_threshold, open_seconds=open_seconds
            )
            entry = (cb, cfg)
            _ENTRIES[k] = entry
        else:
            cb, _old = entry
            # refresh latest config (fresh keys) — keep the existing breaker
            entry = (cb, cfg)
            _ENTRIES[k] = entry
        return entry[0]


def snapshot() -> dict[tuple, tuple[CircuitBreaker, ProviderConfig]]:
    """Return a shallow copy of the registry (breaker refs are shared)."""
    with _LOCK:
        return dict(_ENTRIES)


def _reset_for_test() -> None:  # test-only
    with _LOCK:
        _ENTRIES.clear()


__all__ = ["breaker_key", "breaker_for", "snapshot", "_reset_for_test"]
