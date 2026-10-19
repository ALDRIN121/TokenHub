"""The canonical projection must equal a full rebuild after every write path."""

from datetime import datetime

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from tokenhub.compat import UTC
from tokenhub.database.models import CanonicalUsageEventRecord, UsageEventRecord
from tokenhub.database.repositories import UsageRepository
from tokenhub.database.session import create_engine_for, initialize_database
from tokenhub.domain.models import (
    MeasurementType,
    Provider,
    Quality,
    SyncCursor,
    UsageEvent,
)
from tokenhub.settings import TokenHubSettings


@pytest.fixture
def session(tmp_path):
    engine = create_engine_for(TokenHubSettings(data_directory=tmp_path / "data"))
    initialize_database(engine)
    with Session(engine) as database_session:
        yield database_session
    engine.dispose()


def event(identity: str, *, source: str = "source-a", provider: Provider = Provider.CLAUDE_CODE,
          tokens: int = 100) -> UsageEvent:
    return UsageEvent(
        connector_id="claude-code-local" if provider is Provider.CLAUDE_CODE else "codex-local",
        provider=provider,
        source_id=source,
        record_identity=identity,
        timestamp=datetime(2026, 9, 20, tzinfo=UTC),
        input_total_tokens=tokens,
        output_total_tokens=25,
        cache_read_tokens=0,
        cache_write_tokens=0,
        reasoning_tokens=0,
        measurement_type=MeasurementType.DELTA,
        quality=Quality.EXACT,
        parser_version="test",
        model_name="claude-sonnet-5",
        session_id="session",
    )


def cursor(source: str) -> SyncCursor:
    return SyncCursor(source, 10, None, "test")


def rows(session: Session) -> list[tuple]:
    result = list(session.execute(
        select(
            CanonicalUsageEventRecord.connector_id,
            CanonicalUsageEventRecord.record_identity,
            CanonicalUsageEventRecord.source_id,
            CanonicalUsageEventRecord.input_total_tokens,
        ).order_by(CanonicalUsageEventRecord.record_identity, CanonicalUsageEventRecord.source_id)
    ).tuples())
    # A read autobegins a transaction; the next persist_scan owns its own.
    session.rollback()
    return result


def test_incremental_refresh_equals_full_rebuild(session: Session) -> None:
    repo = UsageRepository(session)
    # Two sources record the same Claude message; the larger copy must win.
    repo.persist_scan([event("same", source="source-a", tokens=100)], cursor("source-a"))
    repo.persist_scan([event("same", source="source-b", tokens=300),
                       event("other", source="source-b", tokens=50)], cursor("source-b"))
    incremental = rows(session)

    repo.rebuild_canonical()
    assert rows(session) == incremental
    assert ("claude-code-local", "same", "source-b", 300) in incremental


def test_replace_events_prunes_canonical_rows(session: Session) -> None:
    repo = UsageRepository(session)
    repo.persist_scan([event("a"), event("b")], cursor("source-a"))
    assert {identity for _, identity, _, _ in rows(session)} == {"a", "b"}

    repo.persist_scan([event("a", tokens=999)], cursor("source-a"), replace_events=True)
    assert rows(session) == [("claude-code-local", "a", "source-a", 999)]
    assert list(session.scalars(select(UsageEventRecord.record_identity))) == ["a"]


def test_ensure_canonical_backfills_an_empty_projection(session: Session) -> None:
    repo = UsageRepository(session)
    repo.persist_scan([event("a")], cursor("source-a"))
    session.execute(delete(CanonicalUsageEventRecord))
    session.commit()

    assert repo.ensure_canonical() is True
    assert rows(session) == [("claude-code-local", "a", "source-a", 100)]
    assert repo.ensure_canonical() is False
