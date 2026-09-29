"""Programmatic Alembic upgrade for the application-owned database.

The migration environment derives its URL from ``TOKENHUB_DATA_DIRECTORY``
(``database/migrations/env.py``) rather than accepting a full database URL, so
startup passes the container's data directory through that variable and restores
the previous value afterwards.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config

from tokenhub.settings import TokenHubSettings

SCRIPT_LOCATION = Path(__file__).resolve().parent / "migrations"
DATA_DIRECTORY_ENV = "TOKENHUB_DATA_DIRECTORY"


@contextmanager
def _data_directory(path: Path) -> Iterator[None]:
    previous = os.environ.get(DATA_DIRECTORY_ENV)
    os.environ[DATA_DIRECTORY_ENV] = str(path)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(DATA_DIRECTORY_ENV, None)
        else:
            os.environ[DATA_DIRECTORY_ENV] = previous


def migration_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(SCRIPT_LOCATION))
    return config


def run_migrations(settings: TokenHubSettings) -> None:
    """Bring the TokenHub database up to the head revision."""
    with _data_directory(settings.data_directory):
        command.upgrade(migration_config(), "head")
