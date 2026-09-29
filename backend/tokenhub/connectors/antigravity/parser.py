"""Read verified Antigravity generation counters from a trusted SQLite snapshot.

Antigravity's protobuf schema is private. Only the observed usage, model and
timestamp fields are decoded; output parts must reconcile to the stored total.
Unknown layouts remain partial instead of becoming invented usage.
"""

import hashlib
import json
import os
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from tokenhub.connectors.antigravity import PARSER_VERSION
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

_MAX_COUNT = 2**63 - 1
_INPUT_COUNTERS = frozenset({1, 2, 5})
_OUTPUT_COUNTERS = frozenset({3, 9, 10})


def _fields(data: bytes) -> list[tuple[int, int | bytes]]:
    """Decode only protobuf varints and length-delimited fields, with bounds."""
    result: list[tuple[int, int | bytes]] = []
    position = 0

    def read_varint() -> int:
        nonlocal position
        value = 0
        for shift in range(0, 70, 7):
            if position >= len(data):
                raise ValueError("truncated Antigravity metadata")
            byte = data[position]
            position += 1
            value |= (byte & 0x7f) << shift
            if byte < 0x80:
                return value
        raise ValueError("invalid Antigravity metadata")

    while position < len(data):
        tag = read_varint()
        number, wire = tag >> 3, tag & 7
        if not number:
            raise ValueError("invalid Antigravity metadata")
        if wire == 0:
            value: int | bytes = read_varint()
        elif wire == 2:
            length = read_varint()
            if length > len(data) - position:
                raise ValueError("truncated Antigravity metadata")
            value = data[position:position + length]
            position += length
        elif wire in (1, 5):
            length = 8 if wire == 1 else 4
            if length > len(data) - position:
                raise ValueError("truncated Antigravity metadata")
            position += length
            continue
        else:
            raise ValueError("unsupported Antigravity metadata wire type")
        result.append((number, value))
    return result


def _field(data: bytes, number: int, kind: type[int] | type[bytes]) -> int | bytes | None:
    return next((value for field, value in _fields(data) if field == number and isinstance(value, kind)), None)


def _counter(fields: list[tuple[int, int | bytes]], number: int) -> int | None:
    """A varint counter, ``0`` when the writer omitted it, ``None`` if not an integer."""
    values = [value for field, value in fields if field == number]
    if not values:
        return 0
    return values[0] if isinstance(values[0], int) else None


def _timestamp(data: bytes) -> datetime | None:
    try:
        seconds = _field(data, 1, int)
        nanos = _field(data, 2, int) or 0
        if not isinstance(seconds, int) or not isinstance(nanos, int) or not 0 <= nanos < 1_000_000_000:
            return None
        return datetime.fromtimestamp(seconds + nanos / 1_000_000_000, UTC)
    except (ValueError, OverflowError, OSError):
        return None


def _step_times(database: sqlite3.Connection) -> tuple[dict[str, datetime], dict[int, datetime]]:
    by_response: dict[str, datetime] = {}
    by_index: dict[int, datetime] = {}
    for (blob,) in database.execute("SELECT metadata FROM steps WHERE step_type = 15 AND metadata IS NOT NULL"):
        if not isinstance(blob, bytes):
            continue
        try:
            stamp = _field(blob, 1, bytes)
            when = _timestamp(stamp) if isinstance(stamp, bytes) else None
            if when is None:
                continue
            usage = _field(blob, 9, bytes)
            response = _field(usage, 11, bytes) if isinstance(usage, bytes) else None
            index_message = _field(blob, 20, bytes)
            index = _field(index_message, 3, int) if isinstance(index_message, bytes) else None
            if isinstance(response, bytes):
                by_response[response.decode("utf-8")] = when
            if isinstance(index, int):
                by_index[index] = when
        except (ValueError, UnicodeDecodeError):
            continue
    return by_response, by_index


def _generation(
    blob: bytes, index: int, by_response: dict[str, datetime],
    by_index: dict[int, datetime], source: SourceDescriptor,
) -> UsageEvent | None:
    try:
        chat = _field(blob, 1, bytes)
        usage = _field(chat, 4, bytes) if isinstance(chat, bytes) else None
        if not isinstance(chat, bytes) or not isinstance(usage, bytes):
            return None
        fields = _fields(usage)
        present = {number for number, _ in fields}
        # A protobuf writer omits a counter whose value is zero, so a first message
        # with no cached input has no cache-read field. Still require a recognisable
        # usage message: at least one input counter and one output counter.
        if not present & _INPUT_COUNTERS or not present & _OUTPUT_COUNTERS:
            return None
        parts = [_counter(fields, number) for number in (1, 2, 3, 5, 9, 10)]
        if any(value is None or not 0 <= value <= _MAX_COUNT for value in parts):
            return None
        numbers = [value for value in parts if value is not None]
        fixed, new_input, total_output, cache_read, text_output, thinking = numbers
        if total_output != text_output + thinking:
            return None
        input_total = fixed + new_input + cache_read
        if input_total > _MAX_COUNT or input_total + total_output > _MAX_COUNT:
            return None
        response = _field(usage, 11, bytes)
        response_id = response.decode("utf-8") if isinstance(response, bytes) else None
        when = by_response.get(response_id or "") or by_index.get(index)
        if when is None:
            return None
        model = _field(chat, 19, bytes) or _field(chat, 21, bytes)
        model_name = usage_identifier(model.decode("utf-8") if isinstance(model, bytes) else None)
        return UsageEvent(
            connector_id=source.connector_id,
            provider=source.provider,
            source_id=source.source_id,
            record_identity=response_id or f"generation:{index}",
            timestamp=when,
            input_total_tokens=input_total,
            output_total_tokens=total_output,
            cache_read_tokens=cache_read,
            reasoning_tokens=thinking,
            measurement_type=MeasurementType.DELTA,
            quality=Quality.HIGH,
            parser_version=PARSER_VERSION,
            model_name=model_name,
            session_id=source.path_fingerprint,
            model_attribution="turn" if model_name else "unknown",
        )
    except (ValueError, UnicodeDecodeError, TypeError):
        return None


def _signature(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns


def _capture(path: Path, source: SourceDescriptor, target: Path) -> os.stat_result | None:
    if source.approved_root_device is None or source.approved_root_inode is None:
        raise ValueError("approved source has no trusted root identity")
    identity = (source.approved_root_device, source.approved_root_inode)
    try:
        descriptor = open_source_path(path, source.approved_root, identity)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        with target.open("wb") as output:
            shutil.copyfileobj(stream, output)
        if _signature(before) != _signature(os.fstat(stream.fileno())):
            raise OSError("Antigravity database changed during snapshot")
    try:
        current = open_source_path(path, source.approved_root, identity)
    except FileNotFoundError:
        raise OSError("Antigravity database changed during snapshot") from None
    try:
        if _signature(before) != _signature(os.fstat(current)):
            raise OSError("Antigravity database changed during snapshot")
    finally:
        os.close(current)
    return before


def parse_antigravity_sqlite(source: SourceDescriptor, cursor: SyncCursor | None) -> ScanResult:
    with TemporaryDirectory(prefix="tokenhub-antigravity-") as directory:
        snapshot = Path(directory) / "conversation.db"
        metadata = _capture(source.canonical_path, source, snapshot)
        if metadata is None:
            raise FileNotFoundError("Antigravity database is unavailable")
        wal = source.canonical_path.with_name(source.canonical_path.name + "-wal")
        _capture(wal, source, snapshot.with_name("conversation.db-wal"))
        try:
            database = sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True)
            try:
                database.execute("PRAGMA query_only=ON")
                database.execute("PRAGMA trusted_schema=OFF")
                by_response, by_index = _step_times(database)
                rows = database.execute("SELECT idx, data FROM gen_metadata ORDER BY idx").fetchall()
            finally:
                database.close()
        except sqlite3.Error as error:
            raise ValueError("Antigravity usage database is unavailable") from error
    events: list[UsageEvent] = []
    unsupported = 0
    seen: set[str] = set()
    for index, blob in rows:
        event = _generation(blob, index, by_response, by_index, source) if type(index) is int and isinstance(blob, bytes) else None
        if event is None:
            unsupported += 1
        elif event.record_identity not in seen:
            events.append(event)
            seen.add(event.record_identity)
    fingerprint = hashlib.sha256(json.dumps([
        [(event.record_identity, event.timestamp.isoformat(), event.input_total_tokens,
          event.output_total_tokens, event.cache_read_tokens, event.reasoning_tokens,
          event.model_name) for event in events], unsupported,
    ], separators=(",", ":")).encode()).hexdigest()
    changed = cursor is None or cursor.parser_version != PARSER_VERSION or cursor.prefix_fingerprint != fingerprint
    return ScanResult(
        state=SourceState.PARTIAL if unsupported else SourceState.HEALTHY,
        events=tuple(events) if changed else (),
        cursor=SyncCursor(
            source_id=source.source_id, byte_offset=0,
            source_mtime_ns=metadata.st_mtime_ns,
            parser_version=PARSER_VERSION, prefix_fingerprint=fingerprint,
            source_unsupported_records=unsupported,
        ),
        unsupported_records=unsupported,
        replace_events=changed,
    )
