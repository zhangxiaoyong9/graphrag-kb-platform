import pytest

from kb_platform.query.engine import FakeQueryEngine, QueryResult, StreamDelta, StreamDone


@pytest.mark.asyncio
async def test_fake_query_engine():
    engine = FakeQueryEngine()
    result = await engine.search("local", "what is ACME?", "/tmp")
    assert isinstance(result, QueryResult)
    assert result.method == "local"
    assert "ACME" in result.answer or result.answer


@pytest.mark.asyncio
async def test_fake_stream_search_yields_deltas_then_done():
    engine = FakeQueryEngine()
    out = [e async for e in engine.stream_search("local", "what is ACME?", "/tmp")]
    # contract: 0+ deltas then exactly one StreamDone
    assert isinstance(out[-1], StreamDone)
    deltas = out[:-1]
    assert deltas and all(isinstance(d, StreamDelta) for d in deltas)
    # the concatenated delta text equals the same answer search() returns
    blocking = await engine.search("local", "what is ACME?", "/tmp")
    assert "".join(d.text for d in deltas) == blocking.answer
    done = out[-1]
    assert done.method == "local"
    assert done.answer == blocking.answer
    assert done.error is None
