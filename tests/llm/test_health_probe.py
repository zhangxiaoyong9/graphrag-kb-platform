"""HealthProbe: shared-breaker driving via an injected probe_fn.

Covers the spec §4.6 contract: one HealthProbe per process, ticks every
interval, drives the SHARED breakers in breaker_registry (the same ones
NativeCompletion's gateway reads). No real network — probe_fn is injected.
"""

from __future__ import annotations

import asyncio

import pytest

from kb_platform.llm import breaker_registry
from kb_platform.llm.health import HealthProbe
from kb_platform.llm.request import ProviderConfig


def _cfg(api_base: str) -> ProviderConfig:
    return ProviderConfig(
        provider="openai",
        model="m",
        api_base=api_base,
        api_version=None,
        key="k",
        ssl_verify=True,
    )


@pytest.mark.asyncio
async def test_probe_drives_breaker_open_then_closed():
    """Unhealthy endpoint's breaker opens after threshold ticks; healthy stays
    closed; once the unhealthy probe flips to healthy, record_success closes it.
    """
    healthy = _cfg("https://healthy.example/v1")
    unhealthy = _cfg("https://unhealthy.example/v1")

    # Register both endpoints with a low threshold so the probe can trip it.
    cb_healthy = breaker_registry.breaker_for(healthy, failure_threshold=3)
    cb_unhealthy = breaker_registry.breaker_for(unhealthy, failure_threshold=3)

    # probe_fn: healthy -> True, unhealthy -> False.
    async def probe_fn(cfg: ProviderConfig) -> bool:
        return cfg.api_base == healthy.api_base

    probe = HealthProbe(interval=0.01, probe_fn=probe_fn)

    # Three ticks: unhealthy breaker should now be open; healthy still closed.
    for _ in range(3):
        await probe.tick()

    assert cb_unhealthy.state == "open", cb_unhealthy.state
    assert cb_healthy.state == "closed", cb_healthy.state

    # Now flip the unhealthy endpoint to healthy. A couple of record_success
    # calls should close it (record_success closes immediately).
    async def probe_fn_recovered(cfg: ProviderConfig) -> bool:
        return True

    probe._probe_fn = probe_fn_recovered
    await probe.tick()
    assert cb_unhealthy.state == "closed", cb_unhealthy.state


@pytest.mark.asyncio
async def test_probe_starts_and_stops_cleanly():
    """start() creates a task; stop() cancels it without error."""
    breaker_registry.breaker_for(_cfg("https://e.example/v1"))

    async def probe_fn(cfg: ProviderConfig) -> bool:
        return True

    probe = HealthProbe(interval=0.01, probe_fn=probe_fn)
    probe.start()
    assert probe._task is not None and not probe._task.done()

    await asyncio.sleep(0.02)  # let one tick land
    await probe.stop()
    assert probe._task is None


@pytest.mark.asyncio
async def test_probe_fn_exception_treated_as_failure():
    """A raising probe_fn must NOT crash the loop; it counts as a failure."""

    raising = _cfg("https://raising.example/v1")
    cb = breaker_registry.breaker_for(raising, failure_threshold=2)

    async def probe_fn(cfg: ProviderConfig) -> bool:
        raise RuntimeError("boom")

    probe = HealthProbe(interval=0.01, probe_fn=probe_fn)
    await probe.tick()
    await probe.tick()
    assert cb.state == "open", cb.state


@pytest.mark.asyncio
async def test_breaker_registry_shares_breaker_across_lookups():
    """Two breaker_for calls for the same endpoint return the SAME breaker."""
    cfg = _cfg("https://shared.example/v1")
    cb1 = breaker_registry.breaker_for(cfg, failure_threshold=5)
    cb2 = breaker_registry.breaker_for(cfg, failure_threshold=99)
    assert cb1 is cb2
    # Config refreshed, identity stable.
    snap = breaker_registry.snapshot()
    assert len(snap) == 1
