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
