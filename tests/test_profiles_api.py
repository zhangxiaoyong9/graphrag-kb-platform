from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from kb_platform.api.app import create_app
from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_SECRET_KEY", Fernet.generate_key().decode())
    engine = create_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    repo = Repository(engine)
    return TestClient(create_app(repo, data_root=str(tmp_path))), repo


def test_create_list_profile_masks_keys(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    r = client.post("/provider-profiles", json={
        "name": "DeepSeek", "kind": "llm", "provider": "deepseek",
        "model": "deepseek-chat", "api_keys": ["sk-aaa", "sk-bbb"],
        "structured_output": False,
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["api_keys_count"] == 2
    assert "api_keys" not in body      # never plaintext
    assert "api_keys_enc" not in body

    lst = client.get("/provider-profiles?kind=llm").json()
    assert len(lst) == 1 and lst[0]["name"] == "DeepSeek"


def test_delete_referenced_profile_is_409(tmp_path, monkeypatch):
    client, repo = _client(tmp_path, monkeypatch)
    pid = client.post("/provider-profiles", json={
        "name": "DeepSeek", "kind": "llm", "provider": "deepseek",
        "model": "deepseek-chat", "api_keys": ["sk-a"], "structured_output": False,
    }).json()["id"]
    with session_scope(repo.engine) as s:
        s.add(KnowledgeBase(name="k1", method="standard", settings_json="{}",
                            data_root=str(tmp_path), llm_profile_id=pid))
    r = client.delete(f"/provider-profiles/{pid}")
    assert r.status_code == 409
    assert 1 in r.json()["detail"]["referencing_kbs"]


def test_patch_replaces_keys_only_when_sent(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    pid = client.post("/provider-profiles", json={
        "name": "P", "kind": "llm", "provider": "openai", "model": "gpt-4o-mini",
        "api_keys": ["sk-1"], "structured_output": True,
    }).json()["id"]
    # patch without api_keys -> count unchanged
    assert client.patch(f"/provider-profiles/{pid}", json={"model": "gpt-4o"}).json()["api_keys_count"] == 1
    # patch with [] -> cleared
    assert client.patch(f"/provider-profiles/{pid}", json={"api_keys": []}).json()["api_keys_count"] == 0


def test_create_profile_persists_ssl_verify(tmp_path, monkeypatch):
    _, repo = _client(tmp_path, monkeypatch)
    p = repo.create_profile(name="SelfSigned", kind="embedding", provider="ollama",
                            model="nomic-embed-text", api_base="https://emb.internal",
                            api_keys=["ollama"], ssl_verify=False)
    assert p.ssl_verify is False
    p2 = repo.create_profile(name="Cloud", kind="llm", provider="openai",
                             model="gpt-4o-mini", api_keys=["sk-1"])
    assert p2.ssl_verify is True  # default


def test_profile_out_includes_ssl_verify_and_patch_persists(tmp_path, monkeypatch):
    client, repo = _client(tmp_path, monkeypatch)
    r = client.post("/provider-profiles", json={
        "name": "SelfSigned", "kind": "embedding", "provider": "ollama",
        "model": "nomic-embed-text", "api_keys": ["ollama"], "ssl_verify": False,
    })
    assert r.status_code == 201, r.text
    assert r.json()["ssl_verify"] is False
    # default True on omit
    assert client.post("/provider-profiles", json={
        "name": "Cloud", "kind": "llm", "provider": "openai",
        "model": "gpt-4o-mini", "api_keys": ["sk-1"],
    }).json()["ssl_verify"] is True
    # patch flips it
    pid = r.json()["id"]
    patched = client.patch(f"/provider-profiles/{pid}", json={"ssl_verify": True}).json()
    assert patched["ssl_verify"] is True
