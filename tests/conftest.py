"""Shared pytest fixtures/helpers."""
import pytest
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def _kb_secret_key(monkeypatch):
    """Give every test a stable Fernet master key so provider-profile crypto works.

    Tests that need a specific key override the env themselves. The cached
    ``_fernet`` is cleared so each test re-initializes with the current key.
    """
    monkeypatch.setenv("KB_SECRET_KEY", Fernet.generate_key().decode())
    from kb_platform.db import crypto

    crypto._fernet.cache_clear()


def seed_profile(client, *, name="P", kind="llm", provider="openai",
                 model="gpt-4o-mini", api_keys=None, structured_output=True) -> int:
    """Create a provider profile via the API and return its id."""
    api_keys = ["sk-test"] if api_keys is None else api_keys
    r = client.post("/provider-profiles", json={
        "name": name, "kind": kind, "provider": provider, "model": model,
        "api_keys": api_keys, "structured_output": structured_output,
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]
