"""PATCH /kbs/{id} updates name/method/settings/profiles (full replace)."""
import json

from fastapi.testclient import TestClient

from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository

from conftest import seed_profile


def _client(tmp_path):
    repo = Repository(create_engine(f"sqlite:///{tmp_path}/u.db"))
    Base.metadata.create_all(repo.engine)
    c = TestClient(create_app(repo, data_root="."))
    pid = seed_profile(c, name="P1")
    with session_scope(repo.engine) as s:
        s.add(KnowledgeBase(name="old", method="standard",
                            settings_json=json.dumps({"chunking": {"size": 100}}),
                            data_root=".", llm_profile_id=pid))
    return repo, c, pid


def test_patch_updates_name_method_settings(tmp_path):
    repo, c, pid = _client(tmp_path)
    r = c.patch("/kbs/1", json={"name": "new", "method": "fast", "llm_profile_id": pid,
                                "settings_yaml": json.dumps({"chunking": {"size": 500}})})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "new" and body["method"] == "fast"
    assert body["settings"]["chunking"]["size"] == 500
    assert body["llm_profile"]["model"] == "gpt-4o-mini"
    with repo.engine.connect() as conn:
        row = conn.exec_driver_sql("SELECT name, method, settings_json FROM knowledge_base WHERE id=1").one()
    assert row.name == "new" and row.method == "fast"
    assert json.loads(row.settings_json)["chunking"]["size"] == 500


def test_patch_404_missing(tmp_path):
    _, c, pid = _client(tmp_path)
    assert c.patch("/kbs/999", json={"name": "x", "method": "standard", "llm_profile_id": pid}).status_code == 404


def test_patch_swaps_embedding_profile(tmp_path):
    _, c, pid = _client(tmp_path)
    emb = seed_profile(c, name="Emb", kind="embedding", provider="ollama", model="nomic-embed-text")
    r = c.patch("/kbs/1", json={"name": "n", "method": "standard", "llm_profile_id": pid,
                                "embedding_profile_id": emb})
    assert r.status_code == 200
    assert r.json()["embedding_profile"]["model"] == "nomic-embed-text"
