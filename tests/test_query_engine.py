import pytest

from kb_platform.query.engine import FakeQueryEngine, QueryResult


@pytest.mark.asyncio
async def test_fake_query_engine():
    engine = FakeQueryEngine()
    result = await engine.search("local", "what is ACME?", "/tmp")
    assert isinstance(result, QueryResult)
    assert result.method == "local"
    assert "ACME" in result.answer or result.answer
