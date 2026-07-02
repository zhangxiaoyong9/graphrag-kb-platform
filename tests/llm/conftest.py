"""Shared fixtures for the LLM test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_breaker_registry() -> None:
    """Clear the process-wide breaker registry before each test.

    The registry is a singleton that accumulates endpoints across tests; without
    this reset a test's breaker state would leak into the next. The registry
    itself (breaker_for / snapshot) is module-level so this fixture touches only
    test isolation, not production behavior.
    """
    from kb_platform.llm import breaker_registry

    breaker_registry._reset_for_test()
