"""Conversation route: KB-default QueryParams forwarding through the
production branch (query_engine=None). Mirrors test_query_params_route.py's
production-branch coverage for the chat path."""

import json


def test_conversation_route_production_branch_forwards_kb_defaults(monkeypatch, tmp_path):
    """Drive the conversation route's PRODUCTION branch end-to-end and assert
    KB ``query_defaults`` reach the engine's ``stream_search``. The injected-
    engine tests never load the KB, so a regression that drops the
    ``kb.settings_json`` read would slip past them."""
    from sqlalchemy import create_engine as sa_create_engine
    from starlette.testclient import TestClient

    from kb_platform.api.app import create_app
    from kb_platform.api.sse import parse_sse
    from kb_platform.db.engine import session_scope
    from kb_platform.db.models import Base, KnowledgeBase
    from kb_platform.db.repository import Repository
    from kb_platform.graph import graphrag_adapter as ga
    from kb_platform.query import graphrag_engine as gre
    from kb_platform.query.engine import StreamDone

    # File-backed SQLite so TestClient's request thread sees the seeded KB.
    db_path = tmp_path / "kb.db"
    sa_engine = sa_create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(sa_engine)
    with session_scope(sa_engine) as s:
        s.add(
            KnowledgeBase(
                name="kb1",
                method="standard",
                settings_json=json.dumps({"query_defaults": {"temperature": 0.2}}),
                data_root=".",
                llm_profile_id=None,
            )
        )
    repo = Repository(sa_engine)
    cid = repo.create_conversation(1).id

    captured: list = []

    class _CapturingEngine:
        def __init__(self, *a, **kw):
            pass

        async def stream_search(self, method, query, kb_data_root, params=None):
            captured.append(params)
            yield StreamDone(method=method, answer="ok")

    # Stub the production-branch dependencies so no real graphrag/LLM runs.
    monkeypatch.setattr(ga, "assemble_kb_settings", lambda kb, repo: {})
    monkeypatch.setattr(ga, "build_chat_complete", lambda settings: None)
    monkeypatch.setattr(gre, "GraphRagQueryEngine", _CapturingEngine)

    app = create_app(repo, data_root=".", query_engine=None, rewriter=None)
    client = TestClient(app)

    r = client.post(f"/conversations/{cid}/messages", json={"content": "hi", "method": "local"})
    assert r.status_code == 200, r.text
    assert captured, "stream_search was never called"
    assert captured[0].temperature == 0.2
    # Sanity: the response is well-formed SSE.
    events = parse_sse(r.text)
    assert any(e == "done" for e, _ in events)
