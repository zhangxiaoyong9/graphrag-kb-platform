"""Tests for POST /kbs/{id}/query endpoint."""

import pytest
from fastapi.testclient import TestClient

from kb_platform.api.app import create_app
from kb_platform.api.sse import parse_sse
from kb_platform.db.engine import create_engine
from kb_platform.db.models import Base
from kb_platform.db.repository import Repository
from kb_platform.query.engine import FakeQueryEngine


@pytest.fixture()
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    return TestClient(
        create_app(
            Repository(engine),
            data_root=str(tmp_path),
            query_engine=FakeQueryEngine(),
        )
    )


def _post_sse(client, path, body):
    r = client.post(path, json=body)
    assert r.status_code == 200, r.text
    return parse_sse(r.text)


def test_query_returns_answer(client):
    client.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}"})
    events = _post_sse(client, "/kbs/1/query", {"method": "local", "query": "what is ACME?"})
    types = [e for e, _ in events]
    assert types[0] == "meta" and types[-1] == "done" and "delta" in types
    done = next(d for e, d in events if e == "done")["result"]
    assert done["method"] == "local"
    assert "ACME" in done["answer"]


def test_query_builds_real_engine_per_kb_when_not_injected(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    client = TestClient(create_app(Repository(engine), data_root=str(tmp_path)))
    client.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}"})
    r = client.post("/kbs/1/query", json={"method": "global", "query": "hello"})
    assert r.status_code == 200  # graceful: SSE error event, not 500
    events = parse_sse(r.text)
    err = next((d for e, d in events if e == "error"), None)
    assert err is not None  # no community reports / no LLM → error event


def test_query_positional_args_still_work(tmp_path):
    """Existing callers using positional args must still work (query_engine defaults to None)."""
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    app = create_app(Repository(engine), str(tmp_path))
    assert app.state.query_engine is None  # None = build real per-KB in production
