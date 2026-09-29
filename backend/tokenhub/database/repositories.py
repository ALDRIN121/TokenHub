"""Repositories that keep source approval and scan persistence transactional."""

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, aliased

from tokenhub.connectors.claude import PARSER_VERSION as CLAUDE_PARSER_VERSION
from tokenhub.connectors.codex import PARSER_VERSION
from tokenhub.connectors.hermes import PARSER_VERSION as HERMES_PARSER_VERSION
from tokenhub.database.models import (
    AutoImportRootRecord,
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
    Provider,
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
                        } | {"state": case(
                            (SourceRecord.state == SourceState.UNSUPPORTED.value,
                             candidate.state.value),
                            else_=SourceRecord.state,
                        )},
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
        if (
            candidate.approved_root_device is None
            or candidate.approved_root_inode is None
        ):
            raise ValueError("discovered source has no approved root identity")
        expected_root_identity = (
            candidate.approved_root_device,
            candidate.approved_root_inode,
        )
        approved_path = validate_source_path(
            candidate.canonical_path,
            candidate.approved_root,
            expected_root_identity,
        )
        with self.session.begin():
            source = self.session.get(SourceRecord, source_id)
            if source is None:
                raise LookupError(f"unknown source: {source_id}")
            if source.state != SourceState.DISCOVERED.value:
                raise ValueError("only discovered sources may be approved")
            source.canonical_path = str(approved_path)
            source.approved_root = str(candidate.approved_root)
            source.approved_root_device = candidate.approved_root_device
            source.approved_root_inode = candidate.approved_root_inode
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

    def upgrade_codex_parser_versions(self) -> None:
        """Upgrade known parsers without altering approvals, counts, or cursors.

        Old cursors force a reparse on collection; existing Codex rows receive
        metadata only, while the snapshot parsers reconcile their source rows.
        """
        upgrades = [
            ("codex-local", "codex", "jsonl", ["codex-jsonl-v1", "codex-jsonl-v2", "codex-jsonl-v3"], PARSER_VERSION),
            ("claude-code-local", "claude_code", "jsonl", ["claude-jsonl-v1"], CLAUDE_PARSER_VERSION),
            ("hermes-local", "hermes", "sqlite", ["hermes-sqlite-v1"], HERMES_PARSER_VERSION),
        ]
        with self.session.begin():
            for connector, provider, source_type, previous, current in upgrades:
                self.session.execute(
                    update(SourceRecord).where(
                        SourceRecord.connector_id == connector,
                        SourceRecord.provider == provider,
                        SourceRecord.source_type == source_type,
                        SourceRecord.scan_supported.is_(True),
                        SourceRecord.parser_version.in_(previous),
                    ).values(parser_version=current)
                )

    def enable_auto_import(self, candidate: SourceDescriptor) -> None:
        """Grant a supported provider root, pinned to its filesystem identity."""
        if (
            (candidate.connector_id, candidate.provider.value) not in {
                ("codex-local", "codex"),
                ("claude-code-local", "claude_code"),
                ("hermes-local", "hermes"),
                ("vscode-copilot-local", "vscode_copilot"),
                ("antigravity-local", "antigravity"),
            }
            or not candidate.scan_supported
            or candidate.approved_root_device is None
            or candidate.approved_root_inode is None
        ):
            raise ValueError("automatic imports require a supported provider source")
        validate_source_path(
            candidate.canonical_path,
            candidate.approved_root,
            (candidate.approved_root_device, candidate.approved_root_inode),
        )
        values = {
            "connector_id": candidate.connector_id,
            "approved_root": str(candidate.approved_root),
            "approved_root_device": candidate.approved_root_device,
            "approved_root_inode": candidate.approved_root_inode,
        }
        with self.session.begin():
            self.session.execute(
                insert(AutoImportRootRecord).values(**values).on_conflict_do_update(
                    index_elements=["connector_id"], set_=values
                )
            )

    def auto_import_roots(self) -> list[AutoImportRootRecord]:
        with self.session.begin():
            roots = list(self.session.scalars(select(AutoImportRootRecord)))
            for root in roots:
                self.session.expunge(root)
            return roots

    def disable_auto_import(self, connector_id: str) -> None:
        with self.session.begin():
            self.session.execute(
                delete(AutoImportRootRecord).where(
                    AutoImportRootRecord.connector_id == connector_id
                )
            )


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
        record_import: bool = True,
        replace_events: bool = False,
    ) -> ImportOutcome:
        if any(event.source_id != cursor.source_id for event in events):
            raise ValueError("scan events and cursor must belong to the same source")
        inserted_events = 0
        with self.session.begin():
            if replace_events:
                self.session.execute(
                    delete(UsageEventRecord).where(
                        UsageEventRecord.source_id == cursor.source_id
                    )
                )
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
                        model_name=usage_event.model_name,
                        session_id=usage_event.session_id,
                        model_attribution=usage_event.model_attribution,
                    )
                    .on_conflict_do_nothing(
                        index_elements=["source_id", "record_identity"]
                    )
                )
                inserted = cast(CursorResult[Any], result).rowcount
                inserted_events += inserted
                if not inserted:
                    # Reparse enriches historical rows without revising their
                    # token counts or reporting a duplicate as a new event.
                    self.session.execute(
                        update(UsageEventRecord).where(
                            UsageEventRecord.source_id == usage_event.source_id,
                            UsageEventRecord.record_identity == usage_event.record_identity,
                        ).values(
                            model_name=usage_event.model_name,
                            session_id=usage_event.session_id,
                            model_attribution=usage_event.model_attribution,
                            parser_version=usage_event.parser_version,
                        )
                    )
            self.session.execute(
                insert(SyncCursorRecord)
                .values(
                    source_id=cursor.source_id,
                    byte_offset=cursor.byte_offset,
                    source_mtime_ns=cursor.source_mtime_ns,
                    parser_version=cursor.parser_version,
                    prefix_fingerprint=cursor.prefix_fingerprint,
                    source_unsupported_records=cursor.source_unsupported_records,
                )
                .on_conflict_do_update(
                    index_elements=["source_id"],
                    set_={
                        "byte_offset": cursor.byte_offset,
                        "source_mtime_ns": cursor.source_mtime_ns,
                        "parser_version": cursor.parser_version,
                        "prefix_fingerprint": cursor.prefix_fingerprint,
                        "source_unsupported_records": cursor.source_unsupported_records,
                    },
                )
            )
            if record_import or inserted_events:
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
                source_unsupported_records=cursor.source_unsupported_records,
            )

    def clear_normalized(self) -> None:
        """Clear only derived events and cursors; preserve approvals and audit history."""
        with self.session.begin():
            self.session.execute(delete(UsageEventRecord))
            self.session.execute(delete(SyncCursorRecord))

    @staticmethod
    def _canonical_deltas() -> tuple[Any, Any]:
        records = UsageEventRecord
        ranked = select(
            records,
            func.row_number().over(
                partition_by=(records.connector_id, records.record_identity),
                order_by=(
                    case((
                        records.input_total_tokens.is_not(None)
                        & records.output_total_tokens.is_not(None), 1,
                    ), else_=0).desc(),
                    (func.coalesce(records.input_total_tokens, 0)
                     + func.coalesce(records.output_total_tokens, 0)).desc(),
                    case((records.model_name.is_not(None), 1), else_=0).desc(),
                    records.timestamp.desc(), records.source_id,
                ),
            ).label("message_rank"),
        ).where(records.measurement_type == MeasurementType.DELTA.value).subquery()
        events = aliased(UsageEventRecord, ranked)
        delta = or_(
            events.provider != Provider.CLAUDE_CODE.value,
            ranked.c.message_rank == 1,
        )
        return events, delta

    def observed_events(
        self, start: datetime | None = None, end: datetime | None = None
    ) -> list[UsageEventRecord]:
        """Detached canonical observations shared with the overview totals."""
        events, delta = self._canonical_deltas()
        query = select(events).where(delta)
        if start is not None:
            query = query.where(events.timestamp >= start.astimezone(UTC).replace(tzinfo=None))
        if end is not None:
            query = query.where(events.timestamp < end.astimezone(UTC).replace(tzinfo=None))
        with self.session.begin():
            rows = list(self.session.scalars(query))
            for row in rows:
                self.session.expunge(row)
            return rows

    def dashboard_totals(self) -> DashboardSummary:
        """Sum observed delta values, preserving SQL NULL for unknown/no data."""
        records = UsageEventRecord
        # Claude can copy conversation history into another session file. Keep
        # each source's provenance but count one complete observation per API
        # message, preferring the largest usage over earlier streaming chunks.
        events, delta = self._canonical_deltas()
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
                    records.source_id,
                    func.max(records.timestamp).label("latest_event_at"),
                )
                .where(records.measurement_type == MeasurementType.DELTA.value)
                .group_by(records.source_id)
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
                    unsupported_records=unsupported_records,
                )
                for source_id, state, parser_version, latest_event_at, mtime, unsupported_records in self.session.execute(
                    select(
                        SourceRecord.source_id,
                        SourceRecord.state,
                        SourceRecord.parser_version,
                        latest_events.c.latest_event_at,
                        SyncCursorRecord.source_mtime_ns,
                        SyncCursorRecord.source_unsupported_records,
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
