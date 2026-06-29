"""Conversation HTTP routes: real async round-trip via ASGITransport, no LLM."""
import httpx
import pytest

from kb_platform.api.app import create_app
from kb_platform.api.sse import parse_sse
from kb_platform.conversation.rewriter import FakeRewriter
from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.query.engine import FakeQueryEngine


async def _post_sse(client, path, body):
    """POST and parse the SSE response into a list of (event, data)."""
    r = await client.post(path, json=body)
    assert r.status_code == 200, r.text
    return parse_sse(r.text)


def _make_app(tmp_path, *, inject=True):
    engine = create_engine(f"sqlite:///{tmp_path}/a.db")
    Base.metadata.create_all(engine)
    repo = Repository(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=str(tmp_path)))
    kwargs = {"query_engine": FakeQueryEngine(), "rewriter": FakeRewriter()} if inject else {}
    return create_app(repo, data_root=str(tmp_path), **kwargs)


@pytest.fixture()
def client(tmp_path):
    app = _make_app(tmp_path)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


async def test_create_list_get_rename_delete(client):
    await client.__aenter__()
    try:
        r = await client.post("/kbs/1/conversations", json={})
        assert r.status_code == 201
        cid = r.json()["id"]
        # list
        lst = await client.get("/kbs/1/conversations")
        assert lst.status_code == 200 and lst.json()[0]["id"] == cid
        # rename
        assert (await client.patch(f"/conversations/{cid}", json={"title": "T"})).json()["title"] == "T"
        # 404s
        assert (await client.get("/conversations/999")).status_code == 404
        assert (await client.post("/kbs/999/conversations", json={})).status_code == 404
        # delete
        assert (await client.delete(f"/conversations/{cid}")).status_code == 204
        assert (await client.get(f"/conversations/{cid}")).status_code == 404
    finally:
        await client.__aexit__(None, None, None)


async def test_send_first_turn_then_followup(client):
    await client.__aenter__()
    try:
        cid = (await client.post("/kbs/1/conversations", json={})).json()["id"]
        ev1 = await _post_sse(client, f"/conversations/{cid}/messages", {"content": "hi", "method": "local"})
        types1 = [e for e, _ in ev1]
        assert types1[0] == "meta" and types1[-1] == "done"
        assert "delta" in types1
        done1 = next(d for e, d in ev1 if e == "done")
        msg1 = done1["message"]
        assert msg1["role"] == "assistant" and "hi" in msg1["content"]
        assert msg1["rewritten_query"] is None and msg1["rewrite_fell_back"] is False
        # second turn rewrites
        ev2 = await _post_sse(client, f"/conversations/{cid}/messages", {"content": "more"})
        meta2 = next(d for e, d in ev2 if e == "meta")
        assert meta2["rewritten_query"] is not None  # follow-up was rewritten
        assert meta2["method"] == "local"  # defaulted from prior assistant
        # detail has 4 rows
        det = await client.get(f"/conversations/{cid}")
        assert len(det.json()["messages"]) == 4
    finally:
        await client.__aexit__(None, None, None)


async def test_send_missing_conversation_404(client):
    await client.__aenter__()
    try:
        r = await client.post("/conversations/999/messages", json={"content": "x"})
        assert r.status_code == 404
    finally:
        await client.__aexit__(None, None, None)


async def test_production_settings_error_is_graceful(tmp_path):
    app = _make_app(tmp_path, inject=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/kbs/1/conversations", json={})).json()["id"]
        r = await ac.post(f"/conversations/{cid}/messages", json={"content": "hi"})
        assert r.status_code == 200
        events = parse_sse(r.text)
        err = next(d for e, d in events if e == "error")
        assert err["message"].startswith("settings resolution failed")
