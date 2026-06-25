"""Query route returns enriched fields (elapsed/tokens/sources)."""
from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine
from kb_platform.db.repository import Repository
from kb_platform.query.engine import QueryResult, SourceRef
from fastapi.testclient import TestClient


class _Stub:
    async def search(self, method, query, kb_data_root):
        return QueryResult(
            answer="A", method=method,
            elapsed_ms=42.0, prompt_tokens=5, output_tokens=9, llm_calls=1,
            sources=[SourceRef("entity", "宁德时代", "电池厂商")],
        )


def _client():
    repo = Repository(create_engine("sqlite:///:memory:"))
    return TestClient(create_app(repo, data_root=".", query_engine=_Stub()))


def test_query_returns_sources_and_tokens():
    with _client() as c:
        r = c.post("/kbs/1/query", json={"method": "local", "query": "x"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "A"
    assert body["elapsed_ms"] == 42.0
    assert body["prompt_tokens"] == 5 and body["llm_calls"] == 1
    assert body["sources"][0]["name"] == "宁德时代"
