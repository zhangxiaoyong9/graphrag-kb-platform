"""PATCH /kbs/{id} updates name/method/settings (full replace)."""
import json

from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine
from kb_platform.db.models import Base
from kb_platform.db.repository import Repository
from fastapi.testclient import TestClient


def _client(tmp_path):
    repo = Repository(create_engine(f"sqlite:///{tmp_path}/u.db"))
    Base.metadata.create_all(repo.engine)
    # seed a KB with some settings
    with repo.engine.begin() as c:
        c.exec_driver_sql(
            "INSERT INTO knowledge_base(name, method, settings_json, data_root) VALUES(?, ?, ?, ?)",
            ("old", "standard", json.dumps({"llm": {"api_key": "sk-secret", "model": "m1"}}), "."),
        )
    return repo, TestClient(create_app(repo, data_root="."))


def test_patch_updates_name_method_settings(tmp_path):
    repo, c = _client(tmp_path)
    r = c.patch("/kbs/1", json={"name": "new", "method": "fast",
                                "settings_yaml": json.dumps({"llm": {"model": "m2", "api_key_env": "X"}})})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "new" and body["method"] == "fast"
    assert body["settings"]["llm"]["model"] == "m2"
    assert body["settings"]["llm"]["api_key_env"] == "X"
    # persisted
    with repo.engine.connect() as conn:
        row = conn.exec_driver_sql("SELECT name, method, settings_json FROM knowledge_base WHERE id=1").one()
    assert row.name == "new" and row.method == "fast"
    assert json.loads(row.settings_json)["llm"]["model"] == "m2"


def test_patch_404_missing(tmp_path):
    _, c = _client(tmp_path)
    assert c.patch("/kbs/999", json={"name": "x", "method": "standard"}).status_code == 404


def test_patch_redacts_api_key(tmp_path):
    _, c = _client(tmp_path)
    r = c.patch("/kbs/1", json={"name": "n", "method": "standard",
                                "settings_yaml": json.dumps({"llm": {"api_key": "sk-new", "model": "m"}})})
    assert r.json()["settings"]["llm"]["api_key"] == "***"
    assert r.json()["settings"]["llm"]["model"] == "m"
