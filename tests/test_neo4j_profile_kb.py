"""neo4j provider-profile kind + KB.neo4j_profile_id round-trip."""

from kb_platform.db.crypto import decrypt_values
from kb_platform.db.engine import create_engine, session_scope
from kb_platform.db.models import Base, KnowledgeBase
from kb_platform.db.repository import Repository


def _repo(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(eng)
    return Repository(eng)


def test_create_neo4j_profile_round_trips_uri_username_password(tmp_path):
    repo = _repo(tmp_path)
    p = repo.create_profile(
        name="neo-main", kind="neo4j", provider="neo4j", model="",
        api_base="bolt://localhost:7687", username="neo4j",
        api_keys=["s3cret"],
    )
    assert p.kind == "neo4j"
    assert p.api_base == "bolt://localhost:7687"
    assert p.username == "neo4j"
    assert decrypt_values(p.api_keys_enc) == ["s3cret"]


def test_update_profile_username_and_password(tmp_path):
    repo = _repo(tmp_path)
    p = repo.create_profile(
        name="neo-main", kind="neo4j", provider="neo4j", model="",
        api_base="bolt://localhost:7687", username="neo4j", api_keys=["s3cret"],
    )
    updated = repo.update_profile(p.id, username="graphuser", api_keys=["new-pw"])
    assert updated.username == "graphuser"
    assert decrypt_values(updated.api_keys_enc) == ["new-pw"]


def test_kb_carries_neo4j_profile_id(tmp_path):
    repo = _repo(tmp_path)
    llm = repo.create_profile(
        name="llm", kind="llm", provider="openai", model="gpt-4o-mini", api_keys=["k"],
    )
    neo = repo.create_profile(
        name="neo-main", kind="neo4j", provider="neo4j", model="",
        api_base="bolt://localhost:7687", username="neo4j", api_keys=["s3cret"],
    )
    # Repository has no create_kb method; build the KB inline (mirrors routes_kbs.py).
    with session_scope(repo.engine) as s:
        kb = KnowledgeBase(
            name="kb1", method="standard", settings_json="{}", data_root=".",
            llm_profile_id=llm.id, neo4j_profile_id=neo.id,
        )
        s.add(kb)
        s.flush()
        assert kb.neo4j_profile_id == neo.id

    # update_kb can clear it (None)
    cleared = repo.update_kb(
        kb.id, name="kb1", method="standard", settings_json="{}",
        llm_profile_id=llm.id, neo4j_profile_id=None,
    )
    assert cleared.neo4j_profile_id is None
