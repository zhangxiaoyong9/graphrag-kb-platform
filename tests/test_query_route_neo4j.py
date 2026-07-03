"""Route wiring: cypher/hybrid build via build_query_engine; StreamMeta -> meta;
truncated lands on the done payload. Uses the injected-engine seam (FakeQueryEngine)
for the streaming shape, and asserts the no-profile error path."""

from fastapi.testclient import TestClient

from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.query.engine import FakeQueryEngine


def _make_kb(repo, *, neo4j_profile_id=None):
    """Insert a KB row inline (Repository has no create_kb method)."""
    llm = repo.create_profile(
        name="llm", kind="llm", provider="openai", model="m", api_keys=["k"]
    )
    with session_scope(repo.engine) as s:
        kb = KnowledgeBase(
            name="kb",
            method="standard",
            settings_json="{}",
            data_root=".",
            llm_profile_id=llm.id,
            neo4j_profile_id=neo4j_profile_id,
        )
        s.add(kb)
        s.flush()
        return kb.id


def test_cypher_streams_meta_then_done(tmp_path):
    """FakeQueryEngine yields deltas + done; the route must still parse SSE for
    any method string (cypher included) when an engine is injected. The leading
    `meta{method}` is emitted by the route even without a StreamMeta event."""
    eng = create_engine(f"sqlite:///{tmp_path}/r.db")
    Base.metadata.create_all(eng)
    repo = Repository(eng)
    kb_id = _make_kb(repo)

    client = TestClient(create_app(repo, data_root=".", query_engine=FakeQueryEngine()))
    with client.stream("POST", f"/kbs/{kb_id}/query", json={"method": "cypher", "query": "q"}) as r:
        body = b"".join(r.iter_bytes()).decode()
    assert "event: meta" in body
    assert "event: done" in body
    assert '"truncated"' in body  # QueryResultOut now carries truncated


def test_neo4j_method_no_profile_yields_sse_error(tmp_path):
    """Production path (no injected engine) + KB without neo4j_profile_id:
    build_query_engine raises RuntimeError, which the route surfaces as SSE error."""
    eng = create_engine(f"sqlite:///{tmp_path}/r2.db")
    Base.metadata.create_all(eng)
    repo = Repository(eng)
    kb_id = _make_kb(repo)

    client = TestClient(create_app(repo, data_root=".", query_engine=None))
    with client.stream("POST", f"/kbs/{kb_id}/query", json={"method": "cypher", "query": "q"}) as r:
        body = b"".join(r.iter_bytes()).decode()
    assert "event: error" in body
    assert "Neo4j profile" in body or "neo4j" in body.lower()
