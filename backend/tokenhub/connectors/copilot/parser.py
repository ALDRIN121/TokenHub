"""Read recorded Copilot Chat counters from VS Code's snapshot/patch JSONL.

Only allowlisted request metadata is retained while parsing. Chat messages,
responses, tool output, and extension authentication fields are never stored.
"""

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from tokenhub.connectors.copilot import PARSER_VERSION
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
from tokenhub.ingestion.progress import progress_stream
from tokenhub.security.paths import open_source_path

_MAX_TOKEN_VALUE = 2**63 - 1


def _counter(metadata: dict[str, Any], field: str) -> tuple[int | None, bool]:
    if field not in metadata:
        return None, False
    value = metadata[field]
    if type(value) is not int or not 0 <= value <= _MAX_TOKEN_VALUE:
        return None, True
    return value, False


@dataclass(slots=True)
class _RequestUsage:
    request_id: str | None
    timestamp_ms: int | None
    model_id: str | None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    resolved_model: str | None = None
    bad_prompt: bool = False
    bad_output: bool = False

    @property
    def invalid_counter(self) -> bool:
        return self.bad_prompt or self.bad_output

    @classmethod
    def from_record(cls, value: Any) -> "_RequestUsage":
        if not isinstance(value, dict):
            return cls(None, None, None)
        timestamp = value.get("timestamp")
        request = cls(
            request_id=usage_identifier(value.get("requestId")),
            timestamp_ms=timestamp if type(timestamp) is int else None,
            model_id=usage_identifier(value.get("modelId")),
        )
        request.update_result(value.get("result"))
        return request

    def update_result(self, result: Any) -> None:
        metadata = result.get("metadata") if isinstance(result, dict) else None
        self.update_metadata(metadata)

    def update_metadata(self, metadata: Any) -> None:
        fields = metadata if isinstance(metadata, dict) else {}
        self.prompt_tokens, self.bad_prompt = _counter(fields, "promptTokens")
        self.output_tokens, self.bad_output = _counter(fields, "outputTokens")
        self.resolved_model = usage_identifier(fields.get("resolvedModel"))

    def update_metadata_field(self, field: str, value: Any) -> None:
        if field == "resolvedModel":
            self.resolved_model = usage_identifier(value)
        elif field in {"promptTokens", "outputTokens"}:
            count, bad = _counter({field: value}, field)
            if field == "promptTokens":
                self.prompt_tokens = count
                self.bad_prompt = bad
            else:
                self.output_tokens = count
                self.bad_output = bad


def _apply_record(
    record: Any, requests: list[_RequestUsage], session_id: str,
) -> tuple[list[_RequestUsage], str, int]:
    if not isinstance(record, dict):
        return requests, session_id, 1
    kind = record.get("kind")
    key = record.get("k")
    value = record.get("v")
    if kind == 0:
        if not isinstance(value, dict) or value.get("version") != 3:
            return [], session_id, 1
        session_id = usage_identifier(value.get("sessionId")) or session_id
        raw = value.get("requests")
        if not isinstance(raw, list):
            return [], session_id, 1
        return [_RequestUsage.from_record(item) for item in raw], session_id, 0
    if kind == 2 and key == ["requests"] and isinstance(value, list):
        requests.extend(_RequestUsage.from_record(item) for item in value)
        return requests, session_id, 0
    if kind == 1 and key == ["requests"] and isinstance(value, list):
        return [_RequestUsage.from_record(item) for item in value], session_id, 0
    if kind == 1 and isinstance(key, list) and len(key) >= 2 and key[0] == "requests":
        index = key[1]
        if type(index) is not int or not 0 <= index < len(requests):
            return requests, session_id, 1
        request = requests[index]
        if len(key) == 2:
            requests[index] = _RequestUsage.from_record(value)
        elif key[2:] == ["result"]:
            request.update_result(value)
        elif key[2:] == ["result", "metadata"]:
            request.update_metadata(value)
        elif len(key) == 5 and key[2:4] == ["result", "metadata"] and isinstance(key[4], str):
            request.update_metadata_field(key[4], value)
        elif key[2:] == ["modelId"]:
            request.model_id = usage_identifier(value)
        return requests, session_id, 0
    # VS Code also patches response text, follow-ups, and editor UI state.
    # Those fields are intentionally ignored.
    return requests, session_id, 0


def _event(request: _RequestUsage, source: SourceDescriptor, session_id: str) -> UsageEvent | None:
    model_id = request.model_id
    if model_id is None or not model_id.startswith("copilot/"):
        return None
    if request.request_id is None or request.timestamp_ms is None or request.invalid_counter:
        raise ValueError("Copilot request has invalid usage metadata")
    prompt, output = request.prompt_tokens, request.output_tokens
    if prompt is None and output is None:
        raise ValueError("Copilot request has no token counters")
    if (prompt or 0) + (output or 0) > _MAX_TOKEN_VALUE:
        raise ValueError("Copilot request token total overflows")
    try:
        timestamp = datetime.fromtimestamp(request.timestamp_ms / 1000, UTC)
    except (OverflowError, OSError, ValueError) as error:
        raise ValueError("Copilot request has invalid timestamp") from error
    model_name = request.resolved_model or usage_identifier(model_id.removeprefix("copilot/"))
    if model_name == "auto":
        model_name = None
    return UsageEvent(
        connector_id=source.connector_id,
        provider=source.provider,
        source_id=source.source_id,
        record_identity=request.request_id,
        timestamp=timestamp,
        input_total_tokens=prompt,
        output_total_tokens=output,
        measurement_type=MeasurementType.DELTA,
        quality=Quality.EXACT if prompt is not None and output is not None else Quality.PARTIAL,
        parser_version=PARSER_VERSION,
        model_name=model_name,
        session_id=session_id,
        model_attribution="request" if model_name else "unknown",
    )


def parse_copilot_chat_jsonl(source: SourceDescriptor, cursor: SyncCursor | None) -> ScanResult:
    """Reconcile complete saved requests; later patches may revise earlier counts."""
    if source.approved_root_device is None or source.approved_root_inode is None:
        raise ValueError("approved source has no trusted root identity")
    descriptor = open_source_path(
        source.canonical_path, source.approved_root,
        (source.approved_root_device, source.approved_root_inode),
    )
    requests: list[_RequestUsage] = []
    session_id = source.canonical_path.stem
    unsupported = offset = 0
    partial = False
    hasher = hashlib.sha256()
    with progress_stream(os.fdopen(descriptor, "rb")) as stream:
        metadata = os.fstat(stream.fileno())
        while line := stream.readline():
            if not line.endswith(b"\n"):
                partial = True
                break
            offset += len(line)
            hasher.update(line)
            try:
                record = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                unsupported += 1
                continue
            requests, session_id, invalid = _apply_record(record, requests, session_id)
            unsupported += invalid
    events: dict[str, UsageEvent] = {}
    for request in requests:
        try:
            event = _event(request, source, session_id)
        except ValueError:
            unsupported += 1
            continue
        if event is not None:
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
