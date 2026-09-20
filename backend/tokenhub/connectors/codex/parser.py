"""Streaming parser for approved Codex session usage records."""

import json
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tokenhub.domain.models import (
    MeasurementType,
    Quality,
    SourceDescriptor,
    UsageEvent,
)

_MISSING = object()


@dataclass(frozen=True, slots=True)
class ParsedCodexScan:
    """Normalized results from one safe, incremental Codex JSONL scan."""

    events: list[UsageEvent]
    safe_byte_offset: int
    partial_final_record: bool
    unsupported_records: int


def parse_codex_jsonl(source: SourceDescriptor, start_offset: int) -> ParsedCodexScan:
    """Parse completed token-usage lines without retaining their raw content."""
    events: list[UsageEvent] = []
    safe_byte_offset = start_offset
    unsupported_records = 0
    partial_final_record = False

    with os.fdopen(_open_approved_source(source), "rb") as session_file:
        session_file.seek(start_offset)
        while raw_line := session_file.readline():
            if not raw_line.endswith(b"\n"):
                partial_final_record = True
                break

            safe_byte_offset += len(raw_line)
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
    )


def _parse_completed_line(raw_line: bytes, source: SourceDescriptor) -> UsageEvent | None:
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
    return isinstance(value, (int, str)) and not isinstance(value, bool) and bool(str(value).strip())


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
    relative_path = _lexical_relative_path(source)
    try:
        root_descriptor = _open_directory(source.approved_root)
    except (OSError, ValueError) as error:
        raise ValueError("approved source cannot be opened safely") from error

    try:
        directory_descriptors = [root_descriptor]
        for component in relative_path.parts[:-1]:
            directory_descriptors.append(_open_directory(component, directory_descriptors[-1]))
        return _open_regular_file(relative_path.name, directory_descriptors[-1])
    except (OSError, ValueError) as error:
        raise ValueError("approved source cannot be opened safely") from error
    finally:
        for descriptor in reversed(directory_descriptors):
            os.close(descriptor)


def _lexical_relative_path(source: SourceDescriptor) -> Path:
    """Return a source path relative to its root without resolving any symlinks."""
    try:
        relative_path = source.canonical_path.relative_to(source.approved_root)
    except ValueError as error:
        raise ValueError("approved source cannot be opened safely") from error
    if not relative_path.parts or ".." in source.canonical_path.parts:
        raise ValueError("approved source cannot be opened safely")
    return relative_path


def _open_directory(path: Path | str, parent_descriptor: int | None = None) -> int:
    """Open one non-symlink directory and verify its descriptor type."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, dir_fd=parent_descriptor)
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ValueError("source is not a directory")
    except (OSError, ValueError):
        os.close(descriptor)
        raise
    return descriptor


def _open_regular_file(name: str, parent_descriptor: int) -> int:
    """Open one nonblocking, non-symlink file and verify its descriptor type."""
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("source is not a regular file")
    except (OSError, ValueError):
        os.close(descriptor)
        raise
    return descriptor
