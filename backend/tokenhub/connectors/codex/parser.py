"""Streaming parser for approved Codex session usage records."""

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from tokenhub.connectors.codex import PARSER_VERSION
from tokenhub.connectors.metadata import usage_identifier
from tokenhub.domain.models import (
    MeasurementType,
    Quality,
    SourceDescriptor,
    UsageEvent,
)
from tokenhub.ingestion.progress import progress_stream
from tokenhub.security.paths import open_source_path

_MISSING = object()
_MAX_TOKEN_VALUE = 2**63 - 1
_NON_USAGE_RECORD_TYPES = frozenset({
    "session_meta", "response_item", "turn_context", "compacted",
    "world_state", "inter_agent_communication_metadata", "realtime_item",
})
_NON_USAGE_EVENT_TYPES = frozenset({
    "task_started", "task_complete", "item_completed", "thread_settings_applied",
    "turn_aborted", "thread_goal_updated", "user_message", "agent_message",
    "agent_reasoning", "agent_reasoning_raw_content", "context_compacted",
})


@dataclass(slots=True)
class _ModelContext:
    session_id: str | None = None
    current_model: str | None = None
    turns: dict[str, str | None] = field(default_factory=dict)
    saw_usage_record: bool = False

    def observe(self, record: Any) -> None:
        if not isinstance(record, dict) or not isinstance(record.get("payload"), dict):
            return
        if record.get("type") == "token_usage_record":
            self.saw_usage_record = True
        payload = record["payload"]
        if record.get("type") == "session_meta":
            self.session_id = usage_identifier(payload.get("id") or payload.get("session_id"))
        elif record.get("type") == "turn_context":
            self.current_model = usage_identifier(payload.get("model"))
            turn = usage_identifier(payload.get("turn_id"))
            if turn:
                self.turns[turn] = self.current_model

    def model_for(self, payload: dict[str, Any]) -> str | None:
        explicit = usage_identifier(payload.get("model"))
        if explicit:
            return explicit
        turn = usage_identifier(payload.get("turn_id"))
        if turn and self.turns:
            return self.turns.get(turn)
        return self.current_model


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
    record_identity_aliases: tuple[tuple[str, str], ...]


def parse_codex_jsonl(
    source: SourceDescriptor,
    start_offset: int,
    expected_prefix_fingerprint: str | None = None,
) -> ParsedCodexScan:
    """Parse completed token-usage lines without retaining their raw content."""
    if start_offset < 0:
        raise ValueError("start offset must be nonnegative")
    events: list[UsageEvent] = []
    record_identity_aliases: list[tuple[str, str]] = []
    context = _ModelContext()
    safe_byte_offset = start_offset
    unsupported_records = 0
    snapshot_records = 0
    partial_final_record = False
    full_reparse = start_offset == 0

    with progress_stream(os.fdopen(_open_approved_source(source), "rb")) as session_file:
        source_stat = os.fstat(session_file.fileno())
        prefix_hasher = hashlib.sha256()
        if start_offset > source_stat.st_size:
            start_offset = 0
            safe_byte_offset = 0
            full_reparse = True
        elif start_offset > 0:
            remaining = start_offset
            while remaining:
                chunk = session_file.readline(remaining)
                if not chunk:
                    break
                prefix_hasher.update(chunk)
                remaining -= len(chunk)
                try:
                    context.observe(json.loads(chunk))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    pass
            if remaining or (
                expected_prefix_fingerprint is not None
                and prefix_hasher.hexdigest() != expected_prefix_fingerprint
            ):
                start_offset = 0
                safe_byte_offset = 0
                prefix_hasher = hashlib.sha256()
                context = _ModelContext()
                full_reparse = True
        session_file.seek(start_offset)
        while raw_line := session_file.readline():
            if not raw_line.endswith(b"\n"):
                partial_final_record = True
                break

            safe_byte_offset += len(raw_line)
            prefix_hasher.update(raw_line)
            try:
                record = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                unsupported_records += 1
                continue
            context.observe(record)
            if _is_non_usage_record(record):
                continue
            if _is_usage_snapshot(record):
                snapshot_records += 1
                continue
            event = _parse_usage_record(record, source, context)
            if event is None:
                unsupported_records += 1
            else:
                events.append(event)
                record_identity_aliases.append((str(record["ordinal"]), event.record_identity))

    if snapshot_records and not (events or context.saw_usage_record):
        # Snapshots normally repeat what per-response records already carry. A
        # session with snapshots but no per-response usage would otherwise look
        # healthy while contributing nothing, so keep that case visible.
        unsupported_records += snapshot_records

    return ParsedCodexScan(
        events=events,
        safe_byte_offset=safe_byte_offset,
        partial_final_record=partial_final_record,
        unsupported_records=unsupported_records,
        source_mtime_ns=source_stat.st_mtime_ns,
        safe_prefix_fingerprint=prefix_hasher.hexdigest(),
        full_reparse=full_reparse,
        record_identity_aliases=tuple(record_identity_aliases),
    )


def _parse_usage_record(
    record: Any, source: SourceDescriptor, context: _ModelContext
) -> UsageEvent | None:
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
    if (input_total_tokens or 0) + (output_total_tokens or 0) > _MAX_TOKEN_VALUE:
        return None

    return UsageEvent(
        connector_id=source.connector_id,
        provider=source.provider,
        source_id=source.source_id,
        record_identity=(f"response:{response_id}" if (response_id := usage_identifier(payload.get("response_id"))) else f"ordinal:{ordinal}"),
        timestamp=timestamp,
        input_total_tokens=input_total_tokens,
        output_total_tokens=output_total_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        reasoning_tokens=reasoning_tokens,
        measurement_type=MeasurementType.DELTA,
        quality=Quality.EXACT,
        parser_version=PARSER_VERSION,
        model_name=context.model_for(payload),
        session_id=(usage_identifier(payload.get("thread_id")) or context.session_id
                    or usage_identifier(payload.get("session_id"))),
        model_attribution=("turn" if context.model_for(payload) else "unknown"),
    )


def _is_non_usage_record(record: Any) -> bool:
    if not isinstance(record, dict) or not isinstance(record.get("type"), str):
        return False
    if record["type"] in _NON_USAGE_RECORD_TYPES:
        return True
    payload = record.get("payload")
    # Cumulative token_count snapshots cannot safely be added to per-response
    # deltas. Keep those and unknown event schemas visible as unsupported.
    return (
        record["type"] == "event_msg"
        and isinstance(payload, dict)
        and isinstance(payload.get("type"), str)
        and payload["type"] in _NON_USAGE_EVENT_TYPES
    )


def _is_usage_snapshot(record: Any) -> bool:
    """A cumulative ``token_count`` event; never added to per-response totals."""
    payload = record.get("payload") if isinstance(record, dict) else None
    return (
        isinstance(record, dict)
        and record.get("type") == "event_msg"
        and isinstance(payload, dict)
        and payload.get("type") == "token_count"
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
    if type(value) is not int or not 0 <= value <= _MAX_TOKEN_VALUE:
        raise ValueError(f"{field} must fit a nonnegative storage integer")
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
