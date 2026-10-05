"""Existing folder grants survive adding independently approved archive roots."""

import sqlite3
from pathlib import Path

from alembic import command
from tokenhub.database.migrations import _data_directory, migration_config


def test_existing_consent_survives_upgrade_and_allows_an_archive_grant(tmp_path: Path) -> None:
    with _data_directory(tmp_path):
        command.upgrade(migration_config(), '0006_performance_jobs')
    path = tmp_path / 'tokenhub.sqlite3'
    with sqlite3.connect(path) as database:
        database.execute("INSERT INTO auto_import_roots VALUES ('codex-local', '/original/sessions', 12, 34)")
    with _data_directory(tmp_path):
        command.upgrade(migration_config(), 'head')
    with sqlite3.connect(path) as database:
        assert database.execute('SELECT * FROM auto_import_roots').fetchall() == [
            ('codex-local', '/original/sessions', 12, 34)
        ]
        database.execute("INSERT INTO auto_import_roots VALUES ('codex-local', '/original/archived_sessions', 56, 78)")
        assert database.execute('SELECT COUNT(*) FROM auto_import_roots').fetchone()[0] == 2
