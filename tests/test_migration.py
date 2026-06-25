import subprocess
import sys

from kb_platform.db.engine import create_engine
from kb_platform.db.models import Base


def test_migration_matches_models(tmp_path):
    # Build tables via metadata
    e1 = create_engine(f"sqlite:///{tmp_path}/models.db")
    Base.metadata.create_all(e1)
    # Build tables via alembic
    db = tmp_path / "alembic.db"
    subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"db={db}", "upgrade", "head"],
        check=True,
    )
    # Coarse check: table name sets agree (alembic adds alembic_version)
    from sqlalchemy import inspect

    insp_models = set(inspect(e1).get_table_names())
    insp_alembic = set(inspect(create_engine(f"sqlite:///{db}")).get_table_names())
    assert insp_models <= insp_alembic | {"alembic_version"}


def test_migration_adds_unit_tracking_columns(tmp_path):
    import subprocess
    import sys

    from sqlalchemy import inspect as sa_inspect

    from kb_platform.db.engine import create_engine

    db = tmp_path / "cols.db"
    subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"db={db}", "upgrade", "head"],
        check=True,
    )
    cols = {c["name"] for c in sa_inspect(create_engine(f"sqlite:///{db}")).get_columns("unit")}
    for expected in ("input_hash", "cost_json", "llm_raw_output", "needs_reconsolidation"):
        assert expected in cols, f"missing column {expected}"


def test_migration_adds_worker_heartbeat_columns(tmp_path):
    import subprocess
    import sys

    from sqlalchemy import inspect as sa_inspect

    from kb_platform.db.engine import create_engine

    db = tmp_path / "wh.db"
    subprocess.run([sys.executable, "-m", "alembic", "-x", f"db={db}", "upgrade", "head"], check=True)
    cols = {c["name"] for c in sa_inspect(create_engine(f"sqlite:///{db}")).get_columns("unit")}
    assert "worker_id" in cols and "heartbeat_at" in cols
