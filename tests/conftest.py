"""Shared pytest fixtures/helpers."""
import logging

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


@pytest.fixture(autouse=True)
def _isolate_logging():
    """Snapshot/restore root logger so setup_logging() calls don't leak between tests.

    Also detaches any pre-existing handlers (e.g. pytest's ``_LiveLoggingNullHandler``
    FileHandler pointed at /dev/null) during the test so the suite sees a truly clean
    root logger; they are restored on teardown.
    """
    root = logging.getLogger()
    snap_handlers = list(root.handlers)
    snap_level = root.level
    snap_levels = {
        n: logging.getLogger(n).level
        for n in ("httpx", "httpcore", "urllib3", "sqlalchemy", "uvicorn", "uvicorn.access")
    }
    for h in snap_handlers:
        root.removeHandler(h)
    try:
        yield
    finally:
        for h in list(root.handlers):
            try:
                h.close()
            except Exception:  # noqa: BLE001
                pass
            root.removeHandler(h)
        for h in snap_handlers:
            root.addHandler(h)
        root.setLevel(snap_level)
        for n, lv in snap_levels.items():
            logging.getLogger(n).setLevel(lv)
