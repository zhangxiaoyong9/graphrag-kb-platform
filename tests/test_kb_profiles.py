from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine
from kb_platform.db.models import Base
from kb_platform.db.repository import Repository


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_SECRET_KEY", Fernet.generate_key().decode())
    engine = create_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    repo = Repository(engine)
    c = TestClient(create_app(repo, data_root=str(tmp_path)))
    pid = c.post("/provider-profiles", json={
        "name": "DS", "kind": "llm", "provider": "deepseek", "model": "deepseek-chat",
        "api_keys": ["sk"], "structured_output": False,
    }).json()["id"]
    return c, pid


def test_create_kb_with_profile_and_detail(tmp_path, monkeypatch):
    c, pid = _client(tmp_path, monkeypatch)
    r = c.post("/kbs", json={"name": "k1", "method": "standard", "llm_profile_id": pid,
                             "settings_yaml": '{"chunking":{"size":500}}'})
    assert r.status_code == 201, r.text
    kb_id = r.json()["id"]
    d = c.get(f"/kbs/{kb_id}").json()
    assert d["llm_profile"]["model"] == "deepseek-chat"
    assert d["embedding_profile"] is None
    assert d["settings"]["chunking"]["size"] == 500


def test_create_kb_rejects_unknown_profile(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    r = c.post("/kbs", json={"name": "x", "method": "standard",
                             "llm_profile_id": 999, "settings_yaml": "{}"})
    assert r.status_code == 400


def test_update_kb_changes_profile(tmp_path, monkeypatch):
    c, pid = _client(tmp_path, monkeypatch)
    eid = c.post("/kbs", json={"name": "k1", "method": "standard", "llm_profile_id": pid}).json()["id"]
    emb = c.post("/provider-profiles", json={
        "name": "Ollama", "kind": "embedding", "provider": "ollama",
        "model": "nomic-embed-text", "api_keys": ["ollama"], "structured_output": True,
    }).json()["id"]
    r = c.patch(f"/kbs/{eid}", json={"name": "k1", "method": "standard", "llm_profile_id": pid,
                                     "embedding_profile_id": emb})
    assert r.status_code == 200
    assert r.json()["embedding_profile"]["model"] == "nomic-embed-text"
