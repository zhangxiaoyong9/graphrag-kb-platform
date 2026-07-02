"""Shared fixtures for the LLM test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_process_singletons() -> None:
    """Clear the process-wide singletons before each test.

    - breaker_registry: accumulates endpoints across tests; a test's breaker
      state would leak into the next.
    - http_client: pools shared httpx clients; clear so tests don't accumulate
      clients. (Tests inject their own MockTransport clients for any live path,
      so the pooled clients are never used for real network here.)
    Both modules are module-level singletons; this touches only test isolation.
    """
    from kb_platform.llm import breaker_registry, http_client

    breaker_registry._reset_for_test()
    http_client._reset_for_test()
