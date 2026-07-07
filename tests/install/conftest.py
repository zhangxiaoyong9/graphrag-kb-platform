"""Per-directory fixtures for the installer test suite."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _restore_cwd():
    """Restore cwd after each install test.

    Several install tests ``os.chdir`` into ``tmp_path`` because project-scope
    writes are cwd-relative (``./.mcp.json``, ``./AGENTS.md``, …). Without
    restoring, cwd leaks into later modules — e.g. ``tests/test_migration*.py``
    shell out to ``python -m alembic``, which needs cwd = repo root to find
    ``alembic.ini`` and fails with "No 'script_location' key found" otherwise.
    """
    cwd = Path.cwd()
    yield
    os.chdir(cwd)
