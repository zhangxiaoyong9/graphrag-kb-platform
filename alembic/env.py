"""Alembic environment."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from kb_platform.db.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Allow `-x db=<path>` to override the target database (used by tests).
    try:
        _x_args = context.get_x_argument(as_dictionary=True)
        _db_override = _x_args.get("db")
        if _db_override:
            config.set_main_option("sqlalchemy.url", f"sqlite:///{_db_override}")
    except Exception:
        pass

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
