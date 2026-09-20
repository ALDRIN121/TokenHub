"""Repositories that keep source approval and scan persistence transactional."""

from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from tokenhub.database.models import (
    ImportRunRecord,
    SourceRecord,
    SyncCursorRecord,
    UsageEventRecord,
)
from tokenhub.domain.models import (
    ImportOutcome,
    SourceDescriptor,
    SourceState,
    SyncCursor,
    UsageEvent,
)
from tokenhub.security.paths import validate_source_path


@dataclass(frozen=True, slots=True)
class DashboardTotals:
    workload_tokens: int


class SourceRepository:
    """Persistence for discovery state; unapproved paths remain process-local."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._pending_candidates: dict[str, SourceDescriptor] = {}

    def upsert_discovery(self, candidate: SourceDescriptor) -> SourceRecord:
        """Record safe discovery metadata without writing an absolute path."""
        self._pending_candidates[candidate.source_id] = candidate
        values = {
            "source_id": candidate.source_id,
            "connector_id": candidate.connector_id,
            "provider": candidate.provider.value,
            "display_name": candidate.display_name,
            "source_type": candidate.source_type,
            "path_fingerprint": candidate.path_fingerprint,
            "state": candidate.state.value,
            "evidence_codes": ",".join(candidate.evidence_codes),
            "scan_supported": candidate.scan_supported,
            "parser_version": candidate.parser_version,
        }
        with self.session.begin():
            self.session.execute(
                insert(SourceRecord)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=["source_id"],
                    set_={key: value for key, value in values.items() if key != "source_id"},
                )
            )
            source = self._source(candidate.source_id)
            self.session.expunge(source)
        return source

    def approve(self, source_id: str) -> SourceRecord:
        """Persist the validated discovery path only for a discovered source."""
        candidate = self._pending_candidates.get(source_id)
        if candidate is None:
            raise ValueError("source must be discovered in this approval session")
        approved_path = validate_source_path(candidate.canonical_path, candidate.approved_root)
        with self.session.begin():
            source = self.session.get(SourceRecord, source_id)
            if source is None:
                raise LookupError(f"unknown source: {source_id}")
            if source.state != SourceState.DISCOVERED.value:
                raise ValueError("only discovered sources may be approved")
            source.canonical_path = str(approved_path)
            source.approved_root = str(candidate.approved_root.resolve(strict=True))
            source.state = SourceState.APPROVED.value
            self.session.flush()
            self.session.expunge(source)
        return source

    def _source(self, source_id: str) -> SourceRecord:
        source = self.session.get(SourceRecord, source_id)
        if source is None:
            raise LookupError(f"unknown source: {source_id}")
        return source


class UsageRepository:
    """Persistence for normalized events, scan cursors, and import audit records."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def persist_scan(self, events: list[UsageEvent], cursor: SyncCursor) -> ImportOutcome:
        inserted_events = 0
        with self.session.begin():
            for usage_event in events:
                result = self.session.execute(
                    insert(UsageEventRecord)
                    .values(
                        connector_id=usage_event.connector_id,
                        provider=usage_event.provider.value,
                        source_id=usage_event.source_id,
                        record_identity=usage_event.record_identity,
                        timestamp=usage_event.timestamp,
                        input_total_tokens=usage_event.input_total_tokens,
                        output_total_tokens=usage_event.output_total_tokens,
                        cache_read_tokens=usage_event.cache_read_tokens,
                        cache_write_tokens=usage_event.cache_write_tokens,
                        reasoning_tokens=usage_event.reasoning_tokens,
                        measurement_type=usage_event.measurement_type.value,
                        quality=usage_event.quality.value,
                        parser_version=usage_event.parser_version,
                    )
                    .on_conflict_do_nothing(index_elements=["source_id", "record_identity"])
                )
                inserted_events += cast(CursorResult[Any], result).rowcount
            self.session.execute(
                insert(SyncCursorRecord)
                .values(
                    source_id=cursor.source_id,
                    byte_offset=cursor.byte_offset,
                    source_mtime_ns=cursor.source_mtime_ns,
                    parser_version=cursor.parser_version,
                )
                .on_conflict_do_update(
                    index_elements=["source_id"],
                    set_={
                        "byte_offset": cursor.byte_offset,
                        "source_mtime_ns": cursor.source_mtime_ns,
                        "parser_version": cursor.parser_version,
                    },
                )
            )
            self.session.add(
                ImportRunRecord(
                    source_id=cursor.source_id,
                    inserted_events=inserted_events,
                    duplicate_events=len(events) - inserted_events,
                )
            )
        return ImportOutcome(
            inserted_events=inserted_events,
            duplicate_events=len(events) - inserted_events,
            cursor=cursor,
        )

    def current_cursor(self, source_id: str) -> SyncCursor | None:
        with self.session.begin():
            cursor = self.session.get(SyncCursorRecord, source_id)
            if cursor is None:
                return None
            return SyncCursor(
                source_id=cursor.source_id,
                byte_offset=cursor.byte_offset,
                source_mtime_ns=cursor.source_mtime_ns,
                parser_version=cursor.parser_version,
            )

    def dashboard_totals(self) -> DashboardTotals:
        with self.session.begin():
            workload = self.session.scalar(
                select(
                    func.coalesce(
                        func.sum(UsageEventRecord.input_total_tokens + UsageEventRecord.output_total_tokens), 0
                    )
                )
            )
            return DashboardTotals(workload_tokens=int(workload or 0))
