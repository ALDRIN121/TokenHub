"""Read Hermes session counters from an isolated, no-follow SQLite snapshot."""

import hashlib
import json
import os
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from tokenhub.connectors.hermes import PARSER_VERSION
from tokenhub.connectors.metadata import usage_identifier
from tokenhub.connectors.protocol import ScanResult
from tokenhub.domain.models import (
    MeasurementType,
    Quality,
    SourceDescriptor,
    SourceState,
    SyncCursor,
    UsageEvent,
)
from tokenhub.ingestion.progress import progress_stream, report
from tokenhub.security.paths import open_source_path

_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)
_MAX_TOKEN_VALUE = 2**63 - 1


def parse_hermes_sqlite(
    source: SourceDescriptor, cursor: SyncCursor | None
) -> ScanResult:
    if source.approved_root_device is None or source.approved_root_inode is None:
        raise ValueError("approved source has no trusted root identity")
    root_identity = (source.approved_root_device, source.approved_root_inode)
    # SQLite normally reopens paths and sidecars itself. Copy through the trusted
    # descriptor boundary first so it cannot follow a replaced path or write to
    # the provider database. Include WAL: active sessions may exist only there.
    events: list[UsageEvent] = []
    unsupported = 0
    with TemporaryDirectory(prefix="tokenhub-hermes-") as directory:
        snapshot = Path(directory) / "state.db"
        descriptor = open_source_path(
            source.canonical_path,
            source.approved_root,
            root_identity,
        )
        with progress_stream(os.fdopen(descriptor, "rb")) as stream:
            metadata = os.fstat(stream.fileno())
            with snapshot.open("wb") as target:
                shutil.copyfileobj(stream, target)
            try:
                wal_path = source.canonical_path.with_name(
                    source.canonical_path.name + "-wal"
                )
                wal_descriptor = open_source_path(
                    wal_path,
                    source.approved_root,
                    root_identity,
                )
            except FileNotFoundError:
                journal_metadata = None
            else:
                with progress_stream(os.fdopen(wal_descriptor, "rb")) as journal:
                    journal_metadata = os.fstat(journal.fileno())
                    with snapshot.with_name("state.db-wal").open("wb") as target:
                        shutil.copyfileobj(journal, target)
                    if _file_signature(journal_metadata) != _file_signature(
                        os.fstat(journal.fileno())
                    ):
                        raise OSError(
                            "Hermes usage changed during snapshot; retry later"
                        )
            if _file_signature(metadata) != _file_signature(os.fstat(stream.fileno())):
                raise OSError("Hermes usage changed during snapshot; retry later")
            # A path may have been replaced while its original descriptor
            # remained unchanged. Verify the captured main/WAL pair still
            # names those files before SQLite interprets their copied pages.
            _verify_captured_path(source.canonical_path, source, metadata)
            _verify_captured_path(wal_path, source, journal_metadata)
        try:
            database = sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True)
            try:
                database.execute("PRAGMA query_only=ON")
                database.execute("PRAGMA trusted_schema=OFF")
                columns = {
                    row[1] for row in database.execute("PRAGMA table_info(sessions)")
                }
                if not {"id", "started_at", "input_tokens", "output_tokens"} <= columns:
                    raise ValueError("unsupported Hermes session schema")
                fields = ("id", "started_at", "ended_at", "model", *_TOKEN_FIELDS)
                # Only these fixed metadata fields are selected; no message,
                # prompt, configuration, or credential column enters a record.
                expressions = [
                    field if field in columns else f"NULL AS {field}"
                    for field in fields
                ]
                for index, row in enumerate(database.execute(
                    "SELECT " + ", ".join(expressions) + " FROM sessions ORDER BY id"
                )):
                    report("reading", records_read=index + 1)
                    event = _session_event(dict(zip(fields, row, strict=True)), source)
                    if event is None:
                        unsupported += 1
                    elif any(
                        (getattr(event, field) or 0)
                        for field in (
                            "input_total_tokens",
                            "output_total_tokens",
                            "cache_read_tokens",
                            "cache_write_tokens",
                            "reasoning_tokens",
                        )
                    ):
                        events.append(event)
            finally:
                database.close()
        except sqlite3.Error as error:
            raise ValueError("Hermes usage database is unavailable") from error
    signature = [
        (
            event.record_identity,
            event.timestamp.isoformat(),
            event.input_total_tokens,
            event.output_total_tokens,
            event.cache_read_tokens,
            event.cache_write_tokens,
            event.reasoning_tokens,
            event.model_name,
            event.model_attribution,
        )
        for event in events
    ]
    fingerprint = hashlib.sha256(
        json.dumps([signature, unsupported], separators=(",", ":")).encode()
    ).hexdigest()
    changed = (
        cursor is None
        or cursor.parser_version != PARSER_VERSION
        or cursor.prefix_fingerprint != fingerprint
    )
    return ScanResult(
        state=SourceState.PARTIAL if unsupported else SourceState.HEALTHY,
        events=tuple(events) if changed else (),
        cursor=SyncCursor(
            source_id=source.source_id,
            byte_offset=0,
            source_mtime_ns=metadata.st_mtime_ns,
            parser_version=PARSER_VERSION,
            prefix_fingerprint=fingerprint,
            source_unsupported_records=unsupported,
        ),
        unsupported_records=unsupported,
        replace_events=changed,
    )


def _session_event(row: dict[str, Any], source: SourceDescriptor) -> UsageEvent | None:
    if not isinstance(row["id"], str) or not row["id"].strip():
        return None
    try:
        values = {field: _token(row[field]) for field in _TOKEN_FIELDS}
        # Hermes normalizes API usage into separate uncached/cache buckets.
        prompt = values["input_tokens"]
        total_input = (
            prompt
            + (values["cache_read_tokens"] or 0)
            + (values["cache_write_tokens"] or 0)
            if prompt is not None
            else None
        )
        if values["input_tokens"] is None and values["output_tokens"] is None:
            return None
        if (total_input or 0) + (values["output_tokens"] or 0) > _MAX_TOKEN_VALUE:
            return None
        time_value = (
            row["ended_at"] if row["ended_at"] is not None else row["started_at"]
        )
        if type(time_value) not in {int, float}:
            return None
        timestamp = datetime.fromtimestamp(time_value, UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    # Each session contributes one non-overlapping interval of usage. Replace
    # the source's normalized rows atomically as its running totals grow.
    return UsageEvent(
        connector_id=source.connector_id,
        provider=source.provider,
        source_id=source.source_id,
        record_identity=row["id"],
        timestamp=timestamp,
        input_total_tokens=total_input,
        output_total_tokens=values["output_tokens"],
        cache_read_tokens=values["cache_read_tokens"],
        cache_write_tokens=values["cache_write_tokens"],
        reasoning_tokens=values["reasoning_tokens"],
        measurement_type=MeasurementType.DELTA,
        quality=Quality.HIGH,
        parser_version=PARSER_VERSION,
        model_name=usage_identifier(row.get("model")),
        session_id=row["id"],
        model_attribution="session" if usage_identifier(row.get("model")) else "unknown",
    )


def _token(value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= _MAX_TOKEN_VALUE:
        raise ValueError("invalid token count")
    return value


def _file_signature(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns


def _verify_captured_path(
    path: Path,
    source: SourceDescriptor,
    metadata: os.stat_result | None,
) -> None:
    if source.approved_root_device is None or source.approved_root_inode is None:
        raise ValueError("approved source has no trusted root identity")
    try:
        descriptor = open_source_path(
            path,
            source.approved_root,
            (source.approved_root_device, source.approved_root_inode),
        )
    except FileNotFoundError:
        if metadata is None:
            return
        raise OSError("Hermes usage changed during snapshot; retry later") from None
    try:
        current = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if metadata is None or _file_signature(current) != _file_signature(metadata):
        raise OSError("Hermes usage changed during snapshot; retry later")
