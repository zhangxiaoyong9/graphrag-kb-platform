"""LLM request failures must propagate (mark unit FAILED), not degrade to empty.

graphrag's extractors swallow exceptions via their on_error hook (default no-op)
and return empty. We pass a re-raising on_error so the platform's run_unit
catches the failure and fails the unit.
"""
import asyncio

import pytest


def test_raise_on_error_raises_when_error_present():
    from kb_platform.graph.graphrag_adapter import _raise_on_error

    with pytest.raises(RuntimeError):
        _raise_on_error(RuntimeError("boom"), "trace", {"k": 1})


def test_raise_on_error_noop_when_no_error():
    from kb_platform.graph.graphrag_adapter import _raise_on_error

    # err is None -> must not raise (graphrag calls on_error unconditionally;
    # a None error would mean nothing went wrong)
    _raise_on_error(None, None, None)


class _RaisingCompletion:
    """Stand-in LLMCompletion whose completion_async always fails."""

    async def completion_async(self, **kwargs):  # noqa: ANN003
        raise RuntimeError("auth failed")


def test_graph_extractor_propagates_llm_failure():
    from graphrag.index.operations.extract_graph.graph_extractor import GraphExtractor

    from kb_platform.graph.graphrag_adapter import _raise_on_error

    extractor = GraphExtractor(
        model=_RaisingCompletion(),
        prompt="",
        max_gleanings=0,
        on_error=_raise_on_error,
    )
    # An LLM failure must propagate (not return empty dataframes).
    with pytest.raises(RuntimeError, match="auth failed"):
        asyncio.run(extractor(text="some text", entity_types=[], source_id="c1"))
