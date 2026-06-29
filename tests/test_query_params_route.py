from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.query.engine import FakeQueryEngine, QueryParams


def _client():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="kb1", method="standard", settings_json="{}", data_root=".", llm_profile_id=None))
    repo = Repository(engine)
    captured: list = []

    class Capturing(FakeQueryEngine):
        async def stream_search(self, method, query, kb_data_root, params=None):
            captured.append(params)
            async for ev in super().stream_search(method, query, kb_data_root, params):
                yield ev

    app = create_app(repo, data_root=".", query_engine=Capturing())
    from starlette.testclient import TestClient
    return TestClient(app), captured


def _read_sse(text):
    # collect event types from the raw SSE body
    return [ln.split(":", 1)[1].strip() for ln in text.splitlines() if ln.startswith("event:")]


def test_query_route_forwards_per_query_params():
    client, captured = _client()
    body = {"method": "local", "query": "hi", "params": {"community_level": 1, "top_k": 9}}
    r = client.post("/kbs/1/query", json=body)
    assert r.status_code == 200
    assert "delta" in _read_sse(r.text)
    assert captured and captured[0].community_level == 1 and captured[0].top_k == 9


def test_query_route_no_params_sends_none_object():
    client, captured = _client()
    client.post("/kbs/1/query", json={"method": "local", "query": "hi"})
    # FakeQueryEngine receives params=None (no per-query); resolve still yields a QueryParams
    assert captured and captured[0].community_level is None


def test_query_route_kb_defaults_applied():
    # The production branch layers KB settings_json.query_defaults <- per-query
    # via resolve_query_params; the injected-engine branch uses empty settings
    # (it never loads the KB). Verify the layering that the route delegates to.
    from kb_platform.query.params import resolve_query_params

    kb_settings = {"query_defaults": {"temperature": 0.2, "top_k": 5}}
    resolved = resolve_query_params(kb_settings, None)
    assert resolved.temperature == 0.2
    assert resolved.top_k == 5

    # per-query overrides KB defaults
    per_query = QueryParams(temperature=0.9)
    resolved = resolve_query_params(kb_settings, per_query)
    assert resolved.temperature == 0.9
    assert resolved.top_k == 5  # KB default still applies for unset field


def test_query_route_production_branch_forwards_kb_defaults(monkeypatch, tmp_path):
    """Drive the PRODUCTION branch (query_engine=None) end-to-end through the
    HTTP layer and assert KB ``query_defaults`` reach the engine's
    ``stream_search``. The injected-engine tests above never load the KB, so a
    regression that drops the ``kb.settings_json`` read would slip past them.
    """
    import json

    from sqlalchemy import create_engine as sa_create_engine
    from starlette.testclient import TestClient

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

    captured: list = []

    class _CapturingEngine:
        def __init__(self, *a, **kw):
            pass

        async def stream_search(self, method, query, kb_data_root, params=None):
            captured.append(params)
            yield StreamDone(method=method, answer="ok")

    # Stub the two production-branch dependencies so no real graphrag/LLM runs.
    monkeypatch.setattr(ga, "assemble_kb_settings", lambda kb, repo: {})
    monkeypatch.setattr(gre, "GraphRagQueryEngine", _CapturingEngine)

    app = create_app(repo, data_root=".", query_engine=None)
    client = TestClient(app)

    r = client.post("/kbs/1/query", json={"method": "local", "query": "hi"})
    assert r.status_code == 200, r.text
    assert captured, "stream_search was never called"
    assert captured[0].temperature == 0.2
