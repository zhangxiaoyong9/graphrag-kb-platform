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

    Snapshot the levels of ALL existing loggers (not just a fixed noisy-lib list) so
    arbitrary per-logger overrides applied by ``setup_logging`` (via
    ``KB_LOG_LEVELS=foo.bar=WARNING``) cannot leak across tests. Loggers that didn't
    exist before the test (e.g. created when setup_logging applies KB_LOG_LEVELS to
    a fresh name) are reset to NOTSET on teardown so they inherit the root level.
    Without this, test order would matter: ``test_per_logger_override`` lowers
    arbitrary loggers, then later lifecycle tests would have to locally reset those
    names to see their logs.
    """
    root = logging.getLogger()
    snap_handlers = list(root.handlers)
    snap_level = root.level
    # Logger.manager.loggerDict holds both Logger instances and PlaceHolder objects;
    # only actual Loggers carry a mutable level worth snapshotting.
    snap_levels = {
        name: logger.level
        for name, logger in logging.Logger.manager.loggerDict.items()
        if isinstance(logger, logging.Logger)
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
        # Restore known loggers to their pre-test level. Loggers created during
        # the test (e.g. by setup_logging applying KB_LOG_LEVELS to a name that
        # didn't exist before) are reset to NOTSET so they inherit the root
        # level instead of leaking an override into later tests.
        for name, logger in list(logging.Logger.manager.loggerDict.items()):
            try:
                if not isinstance(logger, logging.Logger):
                    continue
                if name in snap_levels:
                    logger.setLevel(snap_levels[name])
                else:
                    logger.setLevel(logging.NOTSET)
            except Exception:  # noqa: BLE001
                pass
