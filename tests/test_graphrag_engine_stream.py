"""GraphRagQueryEngine.stream_search wiring (no real LLM, no parquet).

We monkey-patch ``_build_engine`` to return a fake graphrag engine whose
``stream_search`` yields known chunks and fires ``on_context`` — exercising the
delta→StreamDelta, done→StreamDone, and sources-via-callback wiring without
graphrag's index/LLM machinery.
"""

import types
from unittest.mock import patch

import pandas as pd
import pytest

from kb_platform.query.engine import StreamDelta, StreamDone  # noqa: F401
from kb_platform.query.graphrag_engine import GraphRagQueryEngine, _SourceCapturingCallback


def test_source_capturing_callback_records_context():
    cb = _SourceCapturingCallback()
    assert cb.context_data is None
    cb.on_context({"entities": "x"})
    assert cb.context_data == {"entities": "x"}
    # any other callback hook is a no-op (must not raise)
    cb.on_llm_new_token("t")
    cb.on_map_response_end([])
    cb.on_reduce_response_start("ctx")


class _FakeGraphragEngine:
    """Stand-in for a graphrag search engine: yields chunks, fires on_context."""

    def __init__(self, chunks, context_data):
        self._chunks = chunks
        self._context_data = context_data
        self.callbacks: list = []
        self.model = types.SimpleNamespace()  # present so attribute access works

    async def stream_search(self, query):  # noqa: ARG002 (query unused)
        for cb in self.callbacks:
            cb.on_context(self._context_data)
        for c in self._chunks:
            yield c


@pytest.mark.asyncio
async def test_stream_search_yields_deltas_then_done_with_sources():
    ents = pd.DataFrame([{"name": "ACME", "description": "a company"}])
    engine = GraphRagQueryEngine(data_root=".", model_config=None)
    fake = _FakeGraphragEngine(["Hello ", "world"], {"entities": ents})
    with patch.object(engine, "_build_engine", return_value=fake):
        out = [e async for e in engine.stream_search("local", "q", ".")]
    assert [type(e).__name__ for e in out] == ["StreamDelta", "StreamDelta", "StreamDone"]
    assert out[0].text == "Hello " and out[1].text == "world"
    done = out[2]
    assert done.answer == "Hello world"
    assert done.method == "local"
    assert done.elapsed_ms is not None and done.elapsed_ms >= 0
    assert done.error is None
    assert done.sources and done.sources[0].kind == "entity" and done.sources[0].name == "ACME"


@pytest.mark.asyncio
async def test_stream_search_reports_missing_reports_guard():
    engine = GraphRagQueryEngine(data_root=".", model_config=None)
    # global needs community_reports.parquet; with no data_root files, guard fires
    out = [e async for e in engine.stream_search("global", "q", "/nonexistent-root")]
    assert len(out) == 1 and isinstance(out[0], StreamDone)
    assert out[0].error and "community reports" in out[0].error


@pytest.mark.asyncio
async def test_stream_search_wraps_build_engine_failure():
    engine = GraphRagQueryEngine(data_root=".", model_config=None)
    with patch.object(engine, "_build_engine", side_effect=FileNotFoundError("missing parquet")):
        out = [e async for e in engine.stream_search("local", "q", ".")]
    assert len(out) == 1 and isinstance(out[0], StreamDone)
    assert "missing parquet" in (out[0].error or "")
