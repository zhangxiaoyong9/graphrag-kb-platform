"""Tests for the MCP query server: KbApiClient + tool logic + server wiring.

The client is exercised against the real FastAPI app (with ``FakeQueryEngine``)
via ``httpx.ASGITransport`` — a true async HTTP round-trip through the actual
routes, no socket, no LLM.

NB: ``POST /kbs`` requires a provider profile, and an injected
``FakeQueryEngine`` makes the query route skip KB lookup entirely (kb_id is
ignored). So KBs are seeded directly via the Repository, and the tool's
error-passthrough/trimming is checked with a stub client instead.
"""

import httpx
import pytest

from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.query.engine import FakeQueryEngine


def _make_app(tmp_path, *, names=("alpha", "beta")):
    """Build an app with KB rows seeded directly (no profile needed to list)."""
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    repo = Repository(engine)
    with session_scope(engine) as s:
        for n in names:
            s.add(
                KnowledgeBase(
                    name=n, method="standard", settings_json="{}", data_root=str(tmp_path)
                )
            )
    return create_app(repo, data_root=str(tmp_path), query_engine=FakeQueryEngine())


@pytest.fixture()
def app(tmp_path):
    return _make_app(tmp_path)


async def _client_for(app, *, base_url="http://testserver"):
    """A KbApiClient backed by an in-process ASGI transport over ``app``."""
    from kb_platform.mcp.server import KbApiClient

    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url)
    return KbApiClient(base_url, http=http), http


# --- KbApiClient --------------------------------------------------------


async def test_client_list_kbs_returns_kbs(app):
    client, http = await _client_for(app)
    try:
        kbs = await client.list_kbs()
        assert {k["name"] for k in kbs} == {"alpha", "beta"}
        assert all({"id", "name", "method"} <= k.keys() for k in kbs)
    finally:
        await http.aclose()


async def test_client_query_round_trips_through_api(app):
    client, http = await _client_for(app)
    try:
        # FakeQueryEngine ignores kb_id; this still exercises the full proxy path.
        res = await client.query(kb_id=1, method="local", query="what is ACME?")
        assert res["method"] == "local"
        assert "ACME" in res["answer"]
    finally:
        await http.aclose()


async def test_client_raises_on_unreachable_api():
    from kb_platform.mcp.server import KbApiError, KbApiClient

    # Real socket to a refused port (no ASGI transport): should fail fast.
    client = KbApiClient("http://127.0.0.1:1", timeout=2.0)
    with pytest.raises(KbApiError):
        await client.list_kbs()


# --- tool logic ---------------------------------------------------------


async def test_tool_list_knowledge_bases(app):
    from kb_platform.mcp.server import list_knowledge_bases

    client, http = await _client_for(app)
    try:
        kbs = await list_knowledge_bases(client)
        assert {k["name"] for k in kbs} == {"alpha", "beta"}
    finally:
        await http.aclose()


async def test_tool_list_knowledge_bases_empty_when_no_kbs(tmp_path):
    from kb_platform.mcp.server import list_knowledge_bases

    app = _make_app(tmp_path, names=())
    client, http = await _client_for(app)
    try:
        assert await list_knowledge_bases(client) == []
    finally:
        await http.aclose()


async def test_tool_list_knowledge_bases_error_shape_when_unreachable():
    from kb_platform.mcp.server import KbApiClient, list_knowledge_bases

    client = KbApiClient("http://127.0.0.1:1", timeout=2.0)
    out = await list_knowledge_bases(client)
    assert out == [{"error": out[0]["error"]}]


async def test_tool_query_default_method_is_local(app):
    from kb_platform.mcp.server import query_knowledge_base

    client, http = await _client_for(app)
    try:
        out = await query_knowledge_base(client, kb_id=1, query="hi")
        assert out["method"] == "local"
        assert "answer" in out
    finally:
        await http.aclose()


async def test_tool_query_explicit_method(app):
    from kb_platform.mcp.server import query_knowledge_base

    client, http = await _client_for(app)
    try:
        out = await query_knowledge_base(client, kb_id=1, query="hi", method="basic")
        assert out["method"] == "basic"
    finally:
        await http.aclose()


async def test_tool_query_error_does_not_raise_when_api_unreachable():
    """A connection failure must surface as a structured error, not an exception."""
    from kb_platform.mcp.server import KbApiClient, query_knowledge_base

    client = KbApiClient("http://127.0.0.1:1", timeout=2.0)
    out = await query_knowledge_base(client, kb_id=1, query="hi")
    assert "error" in out
    assert "answer" not in out


class _StubClient:
    """Duck-typed KbApiClient returning a canned response (no network)."""

    def __init__(self, response: dict):
        self._response = response

    async def query(self, kb_id, method, query):  # noqa: ANN001 - duck typed
        return self._response


async def test_tool_query_passes_through_api_error_and_trims():
    """A 200 carrying an `error` is surfaced; sources normalized; usage fields dropped."""
    from kb_platform.mcp.server import query_knowledge_base

    stub = _StubClient(
        {"answer": "", "method": "local", "error": "kb not found",
         "elapsed_ms": 9.0, "prompt_tokens": 5, "sources": None}
    )
    out = await query_knowledge_base(stub, kb_id=1, query="hi")
    assert out["error"] == "kb not found"
    assert out["sources"] == []  # None -> []
    assert "elapsed_ms" not in out and "prompt_tokens" not in out  # trimmed for agents


async def test_tool_query_trims_sources_on_success():
    from kb_platform.mcp.server import query_knowledge_base

    stub = _StubClient(
        {"answer": "A", "method": "global", "error": None,
         "sources": [{"kind": "entity", "name": "ACME", "text": "...", "extra": 1}]}
    )
    out = await query_knowledge_base(stub, kb_id=1, query="hi", method="global")
    assert out["answer"] == "A"
    assert "error" not in out  # None error omitted
    assert out["sources"] == [{"kind": "entity", "name": "ACME", "text": "..."}]  # extra dropped


# --- server wiring ------------------------------------------------------


async def test_build_mcp_server_registers_both_tools(app):
    from kb_platform.mcp.server import build_mcp_server

    client, http = await _client_for(app)
    try:
        server = build_mcp_server(client)
        names = {t.name for t in await server.list_tools()}
        assert {"list_knowledge_bases", "query_knowledge_base"} <= names
    finally:
        await http.aclose()
