"""Shared httpx client pool (keyed by ssl_verify) — lifecycle + sharing tests."""

from __future__ import annotations

import pytest

from kb_platform.llm import http_client
from kb_platform.llm.http_client import close_all, get_client


def test_get_client_shares_instance_per_ssl_verify():
    a = get_client(True)
    b = get_client(True)
    assert a is b, "same ssl_verify must return the same shared client"


def test_get_client_separates_by_ssl_verify():
    t = get_client(True)
    f = get_client(False)
    assert t is not f, "different ssl_verify must use different clients"


def test_pool_has_at_most_two_clients():
    get_client(True)
    get_client(True)
    get_client(False)
    get_client(False)
    assert set(http_client._CLIENTS.keys()) <= {True, False}


@pytest.mark.asyncio
async def test_close_all_clears_and_replaces():
    first = get_client(True)
    await close_all()
    after = get_client(True)
    assert after is not first, "close_all must clear the pool so the next get creates a fresh client"
    await close_all()
