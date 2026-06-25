"""Tests for POST /kbs/{id}/query endpoint."""

import pytest
from fastapi.testclient import TestClient

from kb_platform.api.app import create_app
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


def test_query_returns_answer(client):
    client.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}"})
    r = client.post("/kbs/1/query", json={"method": "local", "query": "what is ACME?"})
    assert r.status_code == 200
    body = r.json()
    assert body["method"] == "local"
    assert "ACME" in body["answer"]


def test_query_default_engine_when_not_injected(tmp_path):
    """create_app without query_engine should default to FakeQueryEngine."""
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    client = TestClient(create_app(Repository(engine), data_root=str(tmp_path)))
    client.post("/kbs", json={"name": "kb1", "method": "standard", "settings_yaml": "{}"})
    r = client.post("/kbs/1/query", json={"method": "global", "query": "hello"})
    assert r.status_code == 200
    assert r.json()["answer"] == "[global] You asked: hello"


def test_query_positional_args_still_work(tmp_path):
    """Existing callers using positional args must still work (query_engine default)."""
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    app = create_app(Repository(engine), str(tmp_path))
    assert app.state.query_engine is not None
