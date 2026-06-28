import json
import subprocess
import sys

from sqlalchemy import create_engine, inspect, text


def _alembic(db, *args):
    subprocess.run([sys.executable, "-m", "alembic", "-x", f"db={db}", *args], check=True)


def test_migration_backfills_legacy_kb(tmp_path):
    db = tmp_path / "kb.db"
    # 1. build schema at the prior revision (no provider_profile, no KB profile cols)
    _alembic(db, "upgrade", "0004")
    eng = create_engine(f"sqlite:///{db}")
    with eng.begin() as c:
        c.execute(text(
            "INSERT INTO knowledge_base(name, method, settings_json, data_root) "
            "VALUES(:name, :method, :settings, :data_root)"
        ), {"name": "legacy", "method": "standard", "data_root": str(tmp_path),
            "settings": json.dumps({
                "llm": {"model_provider": "deepseek", "model": "deepseek-chat",
                        "api_base": "https://api.deepseek.com", "api_key_env": "DEEPSEEK_API_KEY"},
                "embedding": {"model_provider": "ollama", "model": "nomic-embed-text",
                              "api_base": "http://localhost:11434", "enabled": True},
                "community_reports": {"structured_output": False, "max_length": 1500},
                "chunking": {"size": 900},
            })})
    # 2. run the new migration
    _alembic(db, "upgrade", "head")

    eng = create_engine(f"sqlite:///{db}")
    with eng.connect() as conn:
        kb = conn.execute(text("SELECT llm_profile_id, embedding_profile_id, settings_json FROM knowledge_base WHERE id=1")).one()
        profiles = conn.execute(text("SELECT id, kind, provider, model, structured_output FROM provider_profile")).all()

    assert kb.llm_profile_id is not None
    assert kb.embedding_profile_id is not None
    content = json.loads(kb.settings_json)
    assert "llm" not in content and "embedding" not in content          # stripped
    assert content["community_reports"]["max_length"] == 1500           # content stays
    assert "structured_output" not in content["community_reports"]      # moved to profile
    # one llm + one embedding profile, structured_output carried from the llm block
    llm_profiles = [p for p in profiles if p.kind == "llm"]
    assert len(llm_profiles) == 1 and not llm_profiles[0].structured_output
    assert any(p.kind == "embedding" for p in profiles)


def test_provider_profile_columns_exist(tmp_path):
    db = tmp_path / "kb.db"
    _alembic(db, "upgrade", "head")
    eng = create_engine(f"sqlite:///{db}")
    cols = {c["name"] for c in inspect(eng).get_columns("knowledge_base")}
    assert "llm_profile_id" in cols and "embedding_profile_id" in cols
    assert "provider_profile" in set(inspect(eng).get_table_names())
