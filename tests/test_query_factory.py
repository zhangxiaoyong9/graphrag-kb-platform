"""build_query_engine dispatch + injected-client construction."""

import pytest

from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository


def _repo_with_kb(tmp_path, *, neo4j_profile_id=None):
    """Build an in-memory-style SQLite repo + a KB linked to an LLM profile.

    The brief's helper assumed ``engine_from_url`` / ``repo.create_kb``, which
    don't exist; this mirrors the pattern used across the rest of the test
    suite (``create_engine`` + ``session_scope`` + ``KnowledgeBase(...)``).
    """
    eng = create_engine(f"sqlite:///{tmp_path}/f.db")
    Base.metadata.create_all(eng)
    repo = Repository(eng)
    llm = repo.create_profile(
        name="llm", kind="llm", provider="openai", model="gpt-4o-mini",
        api_keys=["sk-x"], ssl_verify=True,
    )
    with session_scope(eng) as s:
        kb_row = KnowledgeBase(
            name="kb", method="standard", settings_json="{}",
            data_root=".", llm_profile_id=llm.id,
            neo4j_profile_id=neo4j_profile_id,
        )
        s.add(kb_row)
        s.flush()
        kb_id = kb_row.id
    kb = repo.get_kb(kb_id)
    return repo, kb


def _app_state(data_root="."):
    return type("S", (), {"data_root": data_root})()


def test_graphrag_methods_dispatch_to_graphrag_engine(tmp_path, monkeypatch):
    from kb_platform.query import factory as F
    from kb_platform.query.graphrag_engine import GraphRagQueryEngine

    # short-circuit the graphrag config build (we only test dispatch here)
    monkeypatch.setattr(
        F, "_assemble_kb_settings",
        lambda kb, repo: {"llm": {"model": "m", "kb_profiles": []}},
    )
    repo, kb = _repo_with_kb(tmp_path)
    eng = F.build_query_engine("local", kb, repo, app_state=_app_state())
    assert isinstance(eng, GraphRagQueryEngine)


def test_neo4j_method_without_profile_raises(tmp_path):
    from kb_platform.query import factory as F

    repo, kb = _repo_with_kb(tmp_path)  # no neo4j profile linked
    with pytest.raises(RuntimeError, match="Neo4j profile"):
        F.build_query_engine("cypher", kb, repo, app_state=_app_state())


def test_neo4j_method_with_profile_builds_neo4j_engine(tmp_path, monkeypatch):
    from kb_platform.query import factory as F
    from kb_platform.query.neo4j_engine import Neo4jQueryEngine

    repo, kb = _repo_with_kb(tmp_path)
    neo = repo.create_profile(
        name="neo", kind="neo4j", provider="neo4j", model="",
        api_base="bolt://localhost:7687", username="neo4j",
        api_keys=["pw"],
    )
    kb.neo4j_profile_id = neo.id
    # bypass the [neo4j] extra check + the LLM config build (we test dispatch/clients)
    monkeypatch.setattr(F, "_ensure_neo4j_available", lambda: None)
    monkeypatch.setattr(F, "_assemble_kb_settings", lambda kb, repo: {
        "llm": {
            "model": "gpt-4o-mini",
            "kb_profiles": [{
                "provider": "openai", "model": "gpt-4o-mini",
                "keys": ["k"], "ssl_verify": True,
            }],
        },
    })
    eng = F.build_query_engine("hybrid", kb, repo, app_state=_app_state())
    assert isinstance(eng, Neo4jQueryEngine)
    assert eng._username == "neo4j"
    assert eng._password == "pw"
    assert eng._embed is None  # no embedding profile configured


def test_neo4j_extra_missing_raises_clear_error(tmp_path, monkeypatch):
    from kb_platform.query import factory as F

    repo, kb = _repo_with_kb(tmp_path)
    neo = repo.create_profile(
        name="neo", kind="neo4j", provider="neo4j", model="",
        api_base="bolt://localhost:7687", username="neo4j", api_keys=["pw"],
    )
    kb.neo4j_profile_id = neo.id

    def _missing():
        raise ModuleNotFoundError("neo4j")

    monkeypatch.setattr(F, "_ensure_neo4j_available", _missing)
    with pytest.raises(RuntimeError, match="uv sync --extra neo4j"):
        F.build_query_engine("cypher", kb, repo, app_state=_app_state())
