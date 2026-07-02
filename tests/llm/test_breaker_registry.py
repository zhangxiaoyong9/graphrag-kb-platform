"""breaker_registry: process-wide shared breakers keyed by endpoint identity."""

from __future__ import annotations

from kb_platform.llm import breaker_registry
from kb_platform.llm.request import ProviderConfig


def _cfg(provider: str, model: str, api_base: str, key: str) -> ProviderConfig:
    return ProviderConfig(
        provider=provider, model=model, api_base=api_base,
        api_version=None, key=key, ssl_verify=True,
    )


def test_same_endpoint_returns_same_breaker_regardless_of_key():
    """Key rotation must NOT change identity — same breaker returned."""
    cfg_a = _cfg("openai", "m", "https://e.example/v1", "key-a")
    cfg_b = _cfg("openai", "m", "https://e.example/v1", "key-b")
    cb_a = breaker_registry.breaker_for(cfg_a)
    cb_b = breaker_registry.breaker_for(cfg_b)
    assert cb_a is cb_b


def test_different_endpoints_get_different_breakers():
    cfg_a = _cfg("openai", "m", "https://a.example/v1", "k")
    cfg_b = _cfg("openai", "m", "https://b.example/v1", "k")
    cb_a = breaker_registry.breaker_for(cfg_a)
    cb_b = breaker_registry.breaker_for(cfg_b)
    assert cb_a is not cb_b


def test_breaker_key_excludes_key():
    cfg_a = _cfg("openai", "m", "https://e.example/v1", "key-a")
    cfg_b = _cfg("openai", "m", "https://e.example/v1", "key-b")
    assert breaker_registry.breaker_key(cfg_a) == breaker_registry.breaker_key(cfg_b)


def test_breaker_key_includes_provider_model_base_version():
    base = ("openai", "m", "https://e.example/v1", None)
    assert breaker_registry.breaker_key(_cfg("openai", "m", "https://e.example/v1", "k")) == base
    assert breaker_registry.breaker_key(_cfg("deepseek", "m", "https://e.example/v1", "k")) != base
    assert breaker_registry.breaker_key(_cfg("openai", "other", "https://e.example/v1", "k")) != base


def test_snapshot_returns_copy():
    breaker_registry.breaker_for(_cfg("openai", "m", "https://e.example/v1", "k"))
    snap = breaker_registry.snapshot()
    snap.clear()
    # original registry untouched
    assert breaker_registry.snapshot()


def test_latest_config_refreshed():
    """breaker_for refreshes the stored config so the probe sees fresh keys."""
    cfg_old = _cfg("openai", "m", "https://e.example/v1", "old-key")
    breaker_registry.breaker_for(cfg_old)
    cfg_new = _cfg("openai", "m", "https://e.example/v1", "new-key")
    breaker_registry.breaker_for(cfg_new)
    snap = breaker_registry.snapshot()
    key = breaker_registry.breaker_key(cfg_new)
    _cb, stored = snap[key]
    assert stored.key == "new-key"
