"""Settings redaction: key/token/secret values are masked before exposure."""
import json

from fastapi.testclient import TestClient

from kb_platform.api.app import create_app
from kb_platform.api.routes_kbs import _redact
from kb_platform.db.engine import create_engine
from kb_platform.db.models import Base
from kb_platform.db.repository import Repository


def test_redact_masks_keys():
    s = json.dumps(
        {"llm": {"model": "deepseek-chat", "api_key": "sk-secret"}, "token": "abc"}
    )
    out = _redact(s)
    assert out["llm"]["model"] == "deepseek-chat"
    assert out["llm"]["api_key"] == "***"
    assert out["token"] == "***"


def test_redact_invalid_json_returns_empty():
    assert _redact(None) == {}
    assert _redact("not json") == {}


def test_get_kb_returns_redacted_settings(tmp_path):
    repo = Repository(create_engine(f"sqlite:///{tmp_path}/t.db"))
    Base.metadata.create_all(repo.engine)
    with repo.engine.begin() as c:
        c.exec_driver_sql(
            "INSERT INTO knowledge_base(name, method, settings_json, data_root) "
            "VALUES('k','standard', ?,'.')",
            (json.dumps({"llm": {"api_key": "sk-x", "model": "m"}}),),
        )
    with TestClient(create_app(repo, data_root=".")) as c:
        body = c.get("/kbs/1").json()
    assert body["name"] == "k"
    assert body["settings"]["llm"]["model"] == "m"
    assert body["settings"]["llm"]["api_key"] == "***"
