"""Streaming parser for approved Codex session usage records."""

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from tokenhub.domain.models import (
    MeasurementType,
    Quality,
    SourceDescriptor,
    UsageEvent,
)
from tokenhub.security.paths import open_source_path

_MISSING = object()


@dataclass(frozen=True, slots=True)
class ParsedCodexScan:
    """Normalized results from one safe, incremental Codex JSONL scan."""

    events: list[UsageEvent]
    safe_byte_offset: int
    partial_final_record: bool
    unsupported_records: int
    source_mtime_ns: int
    safe_prefix_fingerprint: str
    full_reparse: bool


def parse_codex_jsonl(
    source: SourceDescriptor,
    start_offset: int,
    expected_prefix_fingerprint: str | None = None,
) -> ParsedCodexScan:
    """Parse completed token-usage lines without retaining their raw content."""
    if start_offset < 0:
        raise ValueError("start offset must be nonnegative")
    events: list[UsageEvent] = []
    safe_byte_offset = start_offset
    unsupported_records = 0
    partial_final_record = False
    full_reparse = start_offset == 0

    with os.fdopen(_open_approved_source(source), "rb") as session_file:
        source_stat = os.fstat(session_file.fileno())
        prefix_hasher = hashlib.sha256()
        if start_offset > source_stat.st_size:
            start_offset = 0
            safe_byte_offset = 0
            full_reparse = True
        elif start_offset > 0:
            remaining = start_offset
            while remaining:
                chunk = session_file.read(min(remaining, 1024 * 1024))
                if not chunk:
                    break
                prefix_hasher.update(chunk)
                remaining -= len(chunk)
            if remaining or (
                expected_prefix_fingerprint is not None
                and prefix_hasher.hexdigest() != expected_prefix_fingerprint
            ):
                start_offset = 0
                safe_byte_offset = 0
                prefix_hasher = hashlib.sha256()
                full_reparse = True
        session_file.seek(start_offset)
        while raw_line := session_file.readline():
            if not raw_line.endswith(b"\n"):
                partial_final_record = True
                break

            safe_byte_offset += len(raw_line)
            prefix_hasher.update(raw_line)
            event = _parse_completed_line(raw_line, source)
            if event is None:
                unsupported_records += 1
            else:
                events.append(event)

    return ParsedCodexScan(
        events=events,
        safe_byte_offset=safe_byte_offset,
        partial_final_record=partial_final_record,
        unsupported_records=unsupported_records,
        source_mtime_ns=source_stat.st_mtime_ns,
        safe_prefix_fingerprint=prefix_hasher.hexdigest(),
        full_reparse=full_reparse,
    )


def _parse_completed_line(
    raw_line: bytes, source: SourceDescriptor
) -> UsageEvent | None:
    try:
        record = json.loads(raw_line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict) or record.get("type") != "token_usage_record":
        return None

    ordinal = record.get("ordinal")
    if not _is_nonempty_ordinal(ordinal):
        return None

    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None

    timestamp = _parse_timestamp(record.get("timestamp"))
    if timestamp is None:
        return None

    try:
        input_total_tokens = _optional_token(usage, "input_tokens")
        cache_read_tokens = _optional_token(usage, "cached_input_tokens")
        cache_write_tokens = _optional_token(usage, "cache_write_input_tokens")
        output_total_tokens = _optional_token(usage, "output_tokens")
        reasoning_tokens = _optional_token(usage, "reasoning_output_tokens")
    except ValueError:
        return None

    return UsageEvent(
        connector_id=source.connector_id,
        provider=source.provider,
        source_id=source.source_id,
        record_identity=str(ordinal),
        timestamp=timestamp,
        input_total_tokens=input_total_tokens,
        output_total_tokens=output_total_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        reasoning_tokens=reasoning_tokens,
        measurement_type=MeasurementType.DELTA,
        quality=Quality.EXACT,
        parser_version="codex-jsonl-v1",
    )


def _is_nonempty_ordinal(value: Any) -> bool:
    return (
        isinstance(value, (int, str))
        and not isinstance(value, bool)
        and bool(str(value).strip())
    )


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        timestamp = datetime.fromisoformat(value)
        return timestamp.astimezone(UTC) if timestamp.tzinfo is not None else None
    except (OverflowError, ValueError):
        return None


def _optional_token(usage: dict[str, Any], field: str) -> int | None:
    value = usage.get(field, _MISSING)
    if value is _MISSING:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return value


def _open_approved_source(source: SourceDescriptor) -> int:
    """Open an approved regular file by descending from its root descriptor."""
    try:
        expected_identity = (
            (source.approved_root_device, source.approved_root_inode)
            if source.approved_root_device is not None
            and source.approved_root_inode is not None
            else None
        )
        return open_source_path(
            source.canonical_path,
            source.approved_root,
            expected_identity,
        )
    except (OSError, ValueError) as error:
        raise ValueError("approved source cannot be opened safely") from error
