import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tokenhub.database.models import SourceRecord
from tokenhub.database.repositories import SourceRepository, UsageRepository
from tokenhub.database.session import create_engine_for, initialize_database
from tokenhub.domain.models import (
    ImportOutcome,
    MeasurementType,
    Provider,
    Quality,
    SourceDescriptor,
    SyncCursor,
    UsageEvent,
)
from tokenhub.security.paths import directory_identity
from tokenhub.settings import TokenHubSettings


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = create_engine_for(TokenHubSettings(data_directory=tmp_path / "tokenhub-data"))
    initialize_database(engine)
    with Session(engine) as database_session:
        yield database_session
    engine.dispose()


def synthetic_codex_candidate(path: Path) -> SourceDescriptor:
    return SourceDescriptor(
        source_id="source-a",
        connector_id="codex-local",
        provider=Provider.CODEX,
        display_name="Codex session",
        canonical_path=path,
        approved_root=path.parent.parent,
        source_type="jsonl",
        path_fingerprint="fingerprint-a",
    )


def synthetic_event(record_identity: str) -> UsageEvent:
    return UsageEvent(
        connector_id="codex-local",
        provider=Provider.CODEX,
        source_id="source-a",
        record_identity=record_identity,
        timestamp=datetime(2026, 9, 20, tzinfo=UTC),
        input_total_tokens=100,
        output_total_tokens=25,
        cache_read_tokens=70,
        cache_write_tokens=10,
        reasoning_tokens=20,
        measurement_type=MeasurementType.DELTA,
        quality=Quality.EXACT,
        parser_version="codex-jsonl-v1",
    )


def cursor_for(byte_offset: int) -> SyncCursor:
    return SyncCursor(
        source_id="source-a",
        byte_offset=byte_offset,
        source_mtime_ns=123,
        parser_version="codex-jsonl-v1",
    )


def test_discovery_does_not_persist_path_until_approval(session: Session, tmp_path: Path) -> None:
    """Fails if discovery writes a pre-approval absolute path to SQLite."""
    candidate = synthetic_codex_candidate(tmp_path / "fake" / "sessions" / "a.jsonl")

    source = SourceRepository(session).upsert_discovery(candidate)

    assert source.canonical_path is None
    assert source.path_fingerprint == "fingerprint-a"
    stored = session.scalar(select(SourceRecord).where(SourceRecord.source_id == "source-a"))
    assert stored is not None
    assert stored.canonical_path is None


def test_approval_persists_path_only_after_discovery(session: Session, tmp_path: Path) -> None:
    """Fails if returning a discovered source leaves the session transaction open."""
    approved_root = tmp_path / "approved"
    session_file = approved_root / "sessions" / "a.jsonl"
    session_file.parent.mkdir(parents=True)
    session_file.write_text("{}\n")
    root_device, root_inode = directory_identity(approved_root)
    candidate = SourceDescriptor(
        source_id="source-a",
        connector_id="codex-local",
        provider=Provider.CODEX,
        display_name="Codex session",
        canonical_path=session_file,
        approved_root=approved_root,
        source_type="jsonl",
        path_fingerprint="fingerprint-a",
        approved_root_device=root_device,
        approved_root_inode=root_inode,
    )
    repository = SourceRepository(session)
    repository.upsert_discovery(candidate)

    approved = repository.approve("source-a")

    assert approved.state == "approved"
    assert approved.canonical_path == str(session_file.resolve())


def test_duplicate_event_and_cursor_are_atomic(session: Session) -> None:
    """Fails if duplicate scans inflate totals or cursor persistence is separate."""
    repo = UsageRepository(session)

    first = repo.persist_scan([synthetic_event(record_identity="1")], cursor_for(12))
    second = repo.persist_scan([synthetic_event(record_identity="1")], cursor_for(12))

    assert first == ImportOutcome(inserted_events=1, duplicate_events=0, cursor=cursor_for(12))
    assert second == ImportOutcome(inserted_events=0, duplicate_events=1, cursor=cursor_for(12))
    assert repo.current_cursor("source-a") == cursor_for(12)
    assert repo.dashboard_totals().workload_tokens == 125


def test_persist_scan_after_read_helpers_uses_a_new_atomic_transaction(session: Session) -> None:
    """Fails if read helpers leave an implicit transaction open for the next scan."""
    repo = UsageRepository(session)
    repo.persist_scan([synthetic_event(record_identity="1")], cursor_for(12))

    assert repo.current_cursor("source-a") == cursor_for(12)
    assert repo.dashboard_totals().workload_tokens == 125
    outcome = repo.persist_scan([synthetic_event(record_identity="2")], cursor_for(24))

    assert outcome.inserted_events == 1
    assert repo.current_cursor("source-a") == cursor_for(24)


def test_engine_rejects_database_outside_tokenhub_data_directory(tmp_path: Path) -> None:
    """Fails if a caller could point TokenHub's engine at a provider database."""
    settings = TokenHubSettings(data_directory=tmp_path / "tokenhub-data")
    engine = create_engine_for(settings)

    assert engine.url.database == str(settings.data_directory / "tokenhub.sqlite3")
    engine.dispose()


def test_alembic_rejects_an_arbitrary_database_url(tmp_path: Path) -> None:
    """Fails if migrations can be directed to a provider or arbitrary SQLite database."""
    foreign_database = tmp_path / "provider.sqlite3"
    environment = {
        **os.environ,
        "TOKENHUB_DATA_DIRECTORY": str(tmp_path / "tokenhub-data"),
        "TOKENHUB_DATABASE_URL": f"sqlite:///{foreign_database}",
    }

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "backend/alembic.ini",
            "upgrade",
            "head",
        ],
        cwd=Path(__file__).parents[2],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "TOKENHUB_DATABASE_URL is not supported" in result.stderr
    assert not foreign_database.exists()


def test_large_scan_batches_database_calls_and_preserves_duplicates(session: Session) -> None:
    """A thousand observations should not require a thousand SQL round trips."""
    from dataclasses import replace

    from sqlalchemy import event

    statements: list[str] = []
    engine = session.get_bind()
    def observe(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(engine, "before_cursor_execute", observe)
    try:
        repo = UsageRepository(session)
        rows = [synthetic_event(str(index)) for index in range(1000)]
        outcome = repo.persist_scan(rows, cursor_for(1000))
        assert outcome.inserted_events == 1000
        assert len(statements) < 20
        statements.clear()
        revised = [replace(row, model_name="recorded-model", input_total_tokens=999) for row in rows]
        repeated = repo.persist_scan(revised + [revised[0]], cursor_for(1000))
        assert repeated.inserted_events == 0
        assert repeated.duplicate_events == 1001
        assert len(statements) < 20
        assert repo.dashboard_totals().workload_tokens == 125000
        assert repo.observed_events()[0].model_name == "recorded-model"
    finally:
        event.remove(engine, "before_cursor_execute", observe)


def test_interruption_between_batches_rolls_back_events_and_cursor(session):
    from tokenhub.ingestion.progress import ScanInterrupted, reporting
    repo = UsageRepository(session)
    repo.persist_scan([synthetic_event('original')], cursor_for(12))
    def stop(**values):
        if values.get('records_saved') == 500:
            raise ScanInterrupted('synthetic stop')
    with reporting(stop), pytest.raises(ScanInterrupted):
        repo.persist_scan([synthetic_event(str(index)) for index in range(1000)], cursor_for(1000))
    assert repo.current_cursor('source-a') == cursor_for(12)
    assert repo.dashboard_totals().workload_tokens == 125
