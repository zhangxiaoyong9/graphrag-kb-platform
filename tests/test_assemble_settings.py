from cryptography.fernet import Fernet

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository
from kb_platform.graph.graphrag_adapter import assemble_kb_settings


def _seed_kb(tmp_path, monkeypatch, *, with_embedding=True, llm_keys=None):
    monkeypatch.setenv("KB_SECRET_KEY", Fernet.generate_key().decode())
    engine = create_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    repo = Repository(engine)
    llm = repo.create_profile(name="DS", kind="llm", provider="deepseek",
                              model="deepseek-chat", api_keys=llm_keys if llm_keys is not None else ["sk-a"],
                              structured_output=False)
    emb_pid = None
    if with_embedding:
        emb = repo.create_profile(name="Ollama", kind="embedding", provider="ollama",
                                  model="nomic-embed-text", api_base="http://localhost:11434",
                                  api_keys=["ollama"])
        emb_pid = emb.id
    settings = '{"chunking":{"size":800},"community_reports":{"max_length":1500}}'
    with session_scope(engine) as s:
        s.add(KnowledgeBase(name="k", method="standard", settings_json=settings,
                            data_root=str(tmp_path), llm_profile_id=llm.id, embedding_profile_id=emb_pid))
        return engine, repo


def test_assemble_merges_profile_and_content(tmp_path, monkeypatch):
    engine, repo = _seed_kb(tmp_path, monkeypatch)
    with session_scope(engine) as s:
        assembled = assemble_kb_settings(s.get(KnowledgeBase, 1), repo)
    assert assembled["llm"]["model"] == "deepseek-chat"
    assert assembled["llm"]["api_keys"] == ["sk-a"]
    assert assembled["embedding"]["model"] == "nomic-embed-text"
    assert assembled["community_reports"]["structured_output"] is False  # from llm profile
    assert assembled["community_reports"]["max_length"] == 1500  # from KB
    assert assembled["chunking"]["size"] == 800


def test_assemble_omits_embedding_when_null(tmp_path, monkeypatch):
    engine, repo = _seed_kb(tmp_path, monkeypatch, with_embedding=False)
    with session_scope(engine) as s:
        assembled = assemble_kb_settings(s.get(KnowledgeBase, 1), repo)
    assert "embedding" not in assembled


def test_assemble_raises_without_key(tmp_path, monkeypatch):
    import pytest
    engine, repo = _seed_kb(tmp_path, monkeypatch, llm_keys=[])
    with pytest.raises(ValueError):
        with session_scope(engine) as s:
            assemble_kb_settings(s.get(KnowledgeBase, 1), repo)


def test_assemble_propagates_ssl_verify(tmp_path, monkeypatch):
    engine, repo = _seed_kb(tmp_path, monkeypatch)  # profiles id=1 (llm), id=2 (embedding)
    # flip both profiles to insecure via repo update path
    repo.update_profile(1, ssl_verify=False)
    repo.update_profile(2, ssl_verify=False)
    with session_scope(engine) as s:
        assembled = assemble_kb_settings(s.get(KnowledgeBase, 1), repo)
    assert assembled["llm"]["ssl_verify"] is False
    assert assembled["embedding"]["ssl_verify"] is False
