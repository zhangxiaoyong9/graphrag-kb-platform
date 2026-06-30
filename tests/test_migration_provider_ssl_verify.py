import subprocess
import sys

from sqlalchemy import create_engine, inspect


def _alembic(db, *args):
    subprocess.run([sys.executable, "-m", "alembic", "-x", f"db={db}", *args], check=True)


def test_migration_0008_adds_ssl_verify(tmp_path):
    db = tmp_path / "kb.db"
    # 1. build the schema just before this migration
    _alembic(db, "upgrade", "0007")
    # 2. apply 0008
    _alembic(db, "upgrade", "head")

    eng = create_engine(f"sqlite:///{db}")
    cols = {c["name"]: c for c in inspect(eng).get_columns("provider_profile")}
    assert "ssl_verify" in cols
    # NOT NULL, server_default true → existing rows backfill securely
    assert cols["ssl_verify"]["nullable"] is False
    assert cols["ssl_verify"]["default"] is not None
