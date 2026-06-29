"""Query route streams enriched fields (elapsed/tokens/sources) over SSE."""
from fastapi.testclient import TestClient

from kb_platform.api.app import create_app
from kb_platform.api.sse import parse_sse
from kb_platform.db.engine import create_engine
from kb_platform.db.repository import Repository
from kb_platform.query.engine import QueryResult, SourceRef, StreamDelta, StreamDone


class _Stub:
    async def search(self, method, query, kb_data_root):
        return QueryResult(
            answer="A", method=method,
            elapsed_ms=42.0, prompt_tokens=5, output_tokens=9, llm_calls=1,
            sources=[SourceRef("entity", "宁德时代", "电池厂商")],
        )

    async def stream_search(self, method, query, kb_data_root):
        yield StreamDelta(text="A")
        yield StreamDone(
            answer="A", method=method,
            elapsed_ms=42.0, prompt_tokens=5, output_tokens=9,
            sources=[SourceRef("entity", "宁德时代", "电池厂商")],
        )


def _client():
    repo = Repository(create_engine("sqlite:///:memory:"))
    return TestClient(create_app(repo, data_root=".", query_engine=_Stub()))


def test_query_returns_sources_and_tokens():
    with _client() as c:
        r = c.post("/kbs/1/query", json={"method": "local", "query": "x"})
    assert r.status_code == 200
    events = parse_sse(r.text)
    done = next(d for e, d in events if e == "done")["result"]
    assert done["answer"] == "A"
    assert done["elapsed_ms"] == 42.0
    assert done["prompt_tokens"] == 5
    assert done["sources"][0]["name"] == "宁德时代"
