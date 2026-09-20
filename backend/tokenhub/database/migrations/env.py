"""Alembic environment for TokenHub's application-owned schema."""

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool
from tokenhub.database.models import Base
from tokenhub.settings import TokenHubSettings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

if os.environ.get("TOKENHUB_DATABASE_URL") is not None:
    raise RuntimeError("TOKENHUB_DATABASE_URL is not supported; configure TOKENHUB_DATA_DIRECTORY instead")

configured_data_directory = os.environ.get("TOKENHUB_DATA_DIRECTORY")
settings = TokenHubSettings(
    data_directory=None if configured_data_directory is None else Path(configured_data_directory).resolve()
)
database_path = settings.data_directory / "tokenhub.sqlite3"
database_path.parent.mkdir(parents=True, exist_ok=True)
config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without a database connection."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the configured TokenHub database."""
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
