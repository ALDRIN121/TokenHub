"""Read usage metadata and consolidate Claude's repeated streaming chunks."""

import hashlib
import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from tokenhub.connectors.claude import PARSER_VERSION
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
from tokenhub.security.paths import open_source_path

_MAX_TOKEN_VALUE = 2**63 - 1


def parse_claude_jsonl(
    source: SourceDescriptor, cursor: SyncCursor | None
) -> ScanResult:
    # Reconcile a changed session as a whole: a later streaming chunk can revise
    # an earlier message's usage. A byte-only append would preserve stale counts.
    events: dict[str, UsageEvent] = {}
    hasher = hashlib.sha256()
    offset = unsupported = 0
    partial = False
    if source.approved_root_device is None or source.approved_root_inode is None:
        raise ValueError("approved source has no trusted root identity")
    descriptor = open_source_path(
        source.canonical_path,
        source.approved_root,
        (source.approved_root_device, source.approved_root_inode),
    )
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        while line := stream.readline():
            if not line.endswith(b"\n"):
                partial = True
                break
            hasher.update(line)
            offset += len(line)
            try:
                record = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                unsupported += 1
                continue
            if (
                isinstance(record, dict)
                and isinstance(record.get("type"), str)
                and record["type"] != "assistant"
            ):
                continue
            event = _usage_event(record, source)
            if event is None:
                unsupported += 1
                continue
            previous = events.get(event.record_identity)
            if previous is not None:
                event = replace(
                    event,
                    timestamp=min(previous.timestamp, event.timestamp),
                    input_total_tokens=_maximum(
                        previous.input_total_tokens, event.input_total_tokens
                    ),
                    output_total_tokens=_maximum(
                        previous.output_total_tokens, event.output_total_tokens
                    ),
                    cache_read_tokens=_maximum(
                        previous.cache_read_tokens, event.cache_read_tokens
                    ),
                    cache_write_tokens=_maximum(
                        previous.cache_write_tokens, event.cache_write_tokens
                    ),
                    reasoning_tokens=_maximum(
                        previous.reasoning_tokens, event.reasoning_tokens
                    ),
                )
                if (event.input_total_tokens or 0) + (
                    event.output_total_tokens or 0
                ) > _MAX_TOKEN_VALUE:
                    unsupported += 1
                    continue
            events[event.record_identity] = event
    fingerprint = hasher.hexdigest()
    changed = (
        cursor is None
        or cursor.parser_version != PARSER_VERSION
        or cursor.prefix_fingerprint != fingerprint
    )
    return ScanResult(
        state=SourceState.PARTIAL if partial or unsupported else SourceState.HEALTHY,
        events=tuple(events.values()) if changed else (),
        cursor=SyncCursor(
            source_id=source.source_id,
            byte_offset=offset,
            source_mtime_ns=metadata.st_mtime_ns,
            parser_version=PARSER_VERSION,
            prefix_fingerprint=fingerprint,
            source_unsupported_records=unsupported,
        ),
        partial_final_record=partial,
        unsupported_records=unsupported,
        replace_events=changed,
    )


def _usage_event(record: Any, source: SourceDescriptor) -> UsageEvent | None:
    if not isinstance(record, dict) or record.get("type") != "assistant":
        return None
    message = record.get("message")
    if (
        not isinstance(message, dict)
        or not isinstance(message.get("id"), str)
        or not message["id"].strip()
    ):
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    try:
        timestamp = datetime.fromisoformat(record["timestamp"])
        if timestamp.tzinfo is None:
            return None
        prompt = _token(usage, "input_tokens")
        read = _token(usage, "cache_read_input_tokens")
        write = _token(usage, "cache_creation_input_tokens")
        output = _token(usage, "output_tokens")
        # Anthropic reports uncached input separately from both cache counters.
        total_input = (
            prompt + (read or 0) + (write or 0) if prompt is not None else None
        )
        details = usage.get("output_tokens_details")
        reasoning = (
            _token(details, "thinking_tokens") if isinstance(details, dict) else None
        )
        if total_input is None and output is None:
            return None
        if (total_input or 0) + (output or 0) > _MAX_TOKEN_VALUE:
            return None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return UsageEvent(
        connector_id=source.connector_id,
        provider=source.provider,
        source_id=source.source_id,
        record_identity=message["id"],
        timestamp=timestamp.astimezone(UTC),
        input_total_tokens=total_input,
        output_total_tokens=output,
        cache_read_tokens=read,
        cache_write_tokens=write,
        reasoning_tokens=reasoning,
        measurement_type=MeasurementType.DELTA,
        quality=Quality.EXACT,
        parser_version=PARSER_VERSION,
        model_name=usage_identifier(message.get("model")),
        session_id=usage_identifier(record.get("sessionId")),
        model_attribution="message" if usage_identifier(message.get("model")) else "unknown",
    )


def _token(usage: dict[str, Any], field: str) -> int | None:
    if field not in usage:
        return None
    value = usage[field]
    if type(value) is not int or not 0 <= value <= _MAX_TOKEN_VALUE:
        raise ValueError("invalid token count")
    return value


def _maximum(left: int | None, right: int | None) -> int | None:
    if left is None:
        return right
    return left if right is None else max(left, right)
