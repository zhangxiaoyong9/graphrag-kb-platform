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
