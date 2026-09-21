"""Repositories that keep source approval and scan persistence transactional."""

from datetime import UTC
from typing import Any, cast

from sqlalchemy import delete, func, select, update
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
    APPROVED_SOURCE_STATES,
    DashboardSummary,
    ImportOutcome,
    MeasurementType,
    Quality,
    SourceDescriptor,
    SourceFreshness,
    SourceState,
    SyncCursor,
    UsageEvent,
)
from tokenhub.security.paths import validate_source_path


class SourceRepository:
    """Persistence for discovery state; unapproved paths remain process-local."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._pending_candidates: dict[str, SourceDescriptor] = {}

    def upsert_discovery(self, candidate: SourceDescriptor) -> SourceRecord:
        """Record safe discovery metadata without writing an absolute path."""
        return self.upsert_discoveries([candidate])[0]

    def upsert_discoveries(
        self, candidates: list[SourceDescriptor]
    ) -> list[SourceRecord]:
        """Persist one connector's safe candidates atomically before caching them."""
        if not candidates:
            return []
        sources: list[SourceRecord] = []
        with self.session.begin():
            for candidate in candidates:
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
                self.session.execute(
                    insert(SourceRecord)
                    .values(**values)
                    .on_conflict_do_update(
                        index_elements=["source_id"],
                        set_={
                            key: value
                            for key, value in values.items()
                            if key not in {"source_id", "state"}
                        },
                    )
                )
                source = self._source(candidate.source_id)
                self.session.expunge(source)
                sources.append(source)
        self._pending_candidates.update(
            {candidate.source_id: candidate for candidate in candidates}
        )
        return sources

    def approve(self, source_id: str) -> SourceRecord:
        """Persist the validated discovery path only for a discovered source."""
        candidate = self._pending_candidates.get(source_id)
        if candidate is None:
            raise ValueError("source must be discovered in this approval session")
        approved_path = validate_source_path(
            candidate.canonical_path, candidate.approved_root
        )
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

    def get(self, source_id: str) -> SourceRecord:
        """Read a detached source without leaving an implicit transaction open."""
        with self.session.begin():
            source = self._source(source_id)
            self.session.expunge(source)
            return source

    def approved_sources(self) -> list[SourceRecord]:
        """Recover durable approvals without consulting discovery or provider roots."""
        with self.session.begin():
            sources = list(
                self.session.scalars(
                    select(SourceRecord)
                    .where(
                        SourceRecord.canonical_path.is_not(None),
                        SourceRecord.approved_root.is_not(None),
                        SourceRecord.state.in_(
                            [state.value for state in APPROVED_SOURCE_STATES]
                        ),
                    )
                    .order_by(SourceRecord.source_id)
                )
            )
            for source in sources:
                self.session.expunge(source)
            return sources

    def set_state(self, source_id: str, state: SourceState) -> None:
        with self.session.begin():
            self._source(source_id).state = state.value


class UsageRepository:
    """Persistence for normalized events, scan cursors, and import audit records."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def persist_scan(
        self,
        events: list[UsageEvent],
        cursor: SyncCursor,
        *,
        state: SourceState | None = None,
        partial_final_record: bool = False,
        unsupported_records: int = 0,
    ) -> ImportOutcome:
        if any(event.source_id != cursor.source_id for event in events):
            raise ValueError("scan events and cursor must belong to the same source")
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
                    .on_conflict_do_nothing(
                        index_elements=["source_id", "record_identity"]
                    )
                )
                inserted_events += cast(CursorResult[Any], result).rowcount
            self.session.execute(
                insert(SyncCursorRecord)
                .values(
                    source_id=cursor.source_id,
                    byte_offset=cursor.byte_offset,
                    source_mtime_ns=cursor.source_mtime_ns,
                    parser_version=cursor.parser_version,
                    prefix_fingerprint=cursor.prefix_fingerprint,
                )
                .on_conflict_do_update(
                    index_elements=["source_id"],
                    set_={
                        "byte_offset": cursor.byte_offset,
                        "source_mtime_ns": cursor.source_mtime_ns,
                        "parser_version": cursor.parser_version,
                        "prefix_fingerprint": cursor.prefix_fingerprint,
                    },
                )
            )
            self.session.add(
                ImportRunRecord(
                    source_id=cursor.source_id,
                    inserted_events=inserted_events,
                    duplicate_events=len(events) - inserted_events,
                    partial_final_record=partial_final_record,
                    unsupported_records=unsupported_records,
                )
            )
            if state is not None:
                state_result = self.session.execute(
                    update(SourceRecord)
                    .where(SourceRecord.source_id == cursor.source_id)
                    .values(state=state.value)
                )
                if cast(CursorResult[Any], state_result).rowcount != 1:
                    raise LookupError("source disappeared before scan persistence")
        return ImportOutcome(
            inserted_events=inserted_events,
            duplicate_events=len(events) - inserted_events,
            cursor=cursor,
            partial_final_record=partial_final_record,
            unsupported_records=unsupported_records,
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
                prefix_fingerprint=cursor.prefix_fingerprint,
            )

    def clear_normalized(self) -> None:
        """Clear only derived events and cursors; preserve approvals and audit history."""
        with self.session.begin():
            self.session.execute(delete(UsageEventRecord))
            self.session.execute(delete(SyncCursorRecord))

    def dashboard_totals(self) -> DashboardSummary:
        """Sum observed delta values, preserving SQL NULL for unknown/no data."""
        events = UsageEventRecord
        delta = events.measurement_type == MeasurementType.DELTA.value
        with self.session.begin():
            totals = self.session.execute(
                select(
                    func.sum(events.input_total_tokens + events.output_total_tokens),
                    func.sum(events.input_total_tokens),
                    func.sum(events.output_total_tokens),
                    func.sum(events.cache_read_tokens),
                    func.sum(events.cache_write_tokens),
                    func.sum(events.reasoning_tokens),
                    func.count(),
                ).where(delta)
            ).one()
            quality_counts = {quality: 0 for quality in Quality}
            for quality, count in self.session.execute(
                select(events.quality, func.count())
                .where(delta)
                .group_by(events.quality)
            ):
                quality_counts[Quality(quality)] = count
            latest_events = (
                select(
                    events.source_id,
                    func.max(events.timestamp).label("latest_event_at"),
                )
                .where(delta)
                .group_by(events.source_id)
                .subquery()
            )
            freshness = tuple(
                SourceFreshness(
                    source_id=source_id,
                    state=SourceState(state),
                    parser_version=parser_version,
                    latest_event_at=(
                        latest_event_at.replace(tzinfo=UTC)
                        if latest_event_at is not None
                        else None
                    ),
                    source_mtime_ns=mtime,
                )
                for source_id, state, parser_version, latest_event_at, mtime in self.session.execute(
                    select(
                        SourceRecord.source_id,
                        SourceRecord.state,
                        SourceRecord.parser_version,
                        latest_events.c.latest_event_at,
                        SyncCursorRecord.source_mtime_ns,
                    )
                    .outerjoin(
                        latest_events,
                        latest_events.c.source_id == SourceRecord.source_id,
                    )
                    .outerjoin(
                        SyncCursorRecord,
                        SyncCursorRecord.source_id == SourceRecord.source_id,
                    )
                    .order_by(SourceRecord.source_id)
                )
            )
            return DashboardSummary(
                workload_tokens=totals[0],
                input_total_tokens=totals[1],
                output_total_tokens=totals[2],
                cache_read_tokens=totals[3],
                cache_write_tokens=totals[4],
                reasoning_tokens=totals[5],
                event_count=totals[6],
                source_freshness=freshness,
                quality_counts=quality_counts,
            )
