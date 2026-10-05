import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
import tokenhub.connectors.codex.parser as codex_parser
from tokenhub.connectors.codex.parser import parse_codex_jsonl
from tokenhub.domain.models import (
    MeasurementType,
    Provider,
    Quality,
    SourceDescriptor,
)

FIXTURES = Path(__file__).parents[3] / "fixtures" / "codex"


def test_non_usage_session_records_are_ignored_without_marking_usage_partial(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_bytes(
        b'{"type":"session_meta","payload":{"id":"synthetic"}}\n'
        + b'{"type":"response_item","payload":{"content":"synthetic private text"}}\n'
        + b'{"type":"event_msg","payload":{"type":"task_complete"}}\n'
        + (FIXTURES / "normal.jsonl").read_bytes()
    )
    source = SourceDescriptor(
        source_id="codex-local:synthetic", connector_id="codex-local", provider=Provider.CODEX,
        display_name="Codex session", canonical_path=path, approved_root=tmp_path,
        source_type="jsonl", path_fingerprint="synthetic",
    )
    result = parse_codex_jsonl(source, 0)
    assert len(result.events) == 1
    assert result.events[0].workload_tokens == 125
    assert result.unsupported_records == 0


@pytest.mark.parametrize("payload", [
    {"type": "unknown_usage", "usage": {"input_tokens": 9999}},
    None,
])
def test_usage_bearing_event_messages_remain_visible_as_unsupported(
    tmp_path: Path, payload: object
) -> None:
    path = tmp_path / "session.jsonl"
    path.write_bytes(
        (FIXTURES / "normal.jsonl").read_bytes()
        + json.dumps({"type": "event_msg", "payload": payload}).encode() + b"\n"
    )
    source = replace(synthetic_source("normal.jsonl"), canonical_path=path, approved_root=tmp_path)
    result = parse_codex_jsonl(source, 0)
    assert len(result.events) == 1
    assert result.events[0].workload_tokens == 125
    assert result.unsupported_records == 1


@pytest.mark.parametrize("info", [{"total_token_usage": {"input_tokens": 9999}}, None])
def test_snapshots_beside_per_response_usage_are_skipped_not_unsupported(
    tmp_path: Path, info: object
) -> None:
    path = tmp_path / "session.jsonl"
    path.write_bytes(
        (FIXTURES / "normal.jsonl").read_bytes()
        + json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": info}}).encode() + b"\n"
    )
    source = replace(synthetic_source("normal.jsonl"), canonical_path=path, approved_root=tmp_path)
    result = parse_codex_jsonl(source, 0)
    assert [event.workload_tokens for event in result.events] == [125]
    assert result.unsupported_records == 0


def test_snapshots_without_per_response_usage_remain_unsupported(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    snapshot = {"type": "event_msg", "payload": {"type": "token_count", "info": None}}
    path.write_bytes((json.dumps(snapshot) + "\n").encode() * 2)
    source = replace(synthetic_source("normal.jsonl"), canonical_path=path, approved_root=tmp_path)
    result = parse_codex_jsonl(source, 0)
    assert result.events == []
    assert result.unsupported_records == 2


def test_token_values_outside_storage_range_are_unsupported(tmp_path: Path) -> None:
    from tests.service_support import token_record

    path = tmp_path / "session.jsonl"
    path.write_bytes(token_record(1, input_tokens=2**63, output_tokens=0))
    source = replace(synthetic_source("normal.jsonl"), canonical_path=path, approved_root=tmp_path)
    result = parse_codex_jsonl(source, 0)
    assert result.events == []
    assert result.unsupported_records == 1


def synthetic_source(name: str) -> SourceDescriptor:
    path = FIXTURES / name
    return SourceDescriptor(
        source_id="codex-local:fixture-fingerprint",
        connector_id="codex-local",
        provider=Provider.CODEX,
        display_name="Codex session",
        canonical_path=path,
        approved_root=FIXTURES,
        source_type="jsonl",
        path_fingerprint="fixture-fingerprint",
    )


@pytest.mark.parametrize("path_field", ["canonical_path", "approved_root"])
@pytest.mark.parametrize("component", [".", "..", "relative"])
def test_descriptor_rejects_raw_unsafe_paths_before_any_open(
    path_field: str, component: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails if raw spelling is normalized before the descriptor validates it."""
    source_path = str(FIXTURES / "normal.jsonl")
    root_path = str(FIXTURES)
    if path_field == "canonical_path":
        source_path = (
            "normal.jsonl" if component == "relative" else f"{FIXTURES}/{component}/normal.jsonl"
        )
    else:
        root_path = "fixtures/codex" if component == "relative" else f"{FIXTURES}/{component}"

    def forbidden_open(*args: object, **kwargs: object) -> int:
        pytest.fail("unsafe lexical paths must be rejected before any descriptor is opened")

    monkeypatch.setattr(codex_parser.os, "open", forbidden_open)

    with pytest.raises(ValueError, match="source paths must be absolute without dot components"):
        SourceDescriptor(
            source_id="codex-local:fixture-fingerprint",
            connector_id="codex-local",
            provider=Provider.CODEX,
            display_name="Codex session",
            canonical_path=source_path,
            approved_root=root_path,
            source_type="jsonl",
            path_fingerprint="fixture-fingerprint",
        )


def test_parser_accepts_clean_raw_string_paths() -> None:
    """Clean raw input keeps Path-valued fields and remains usable for scanning."""
    source = SourceDescriptor(
        source_id="codex-local:fixture-fingerprint",
        connector_id="codex-local",
        provider=Provider.CODEX,
        display_name="Codex session",
        canonical_path=str(FIXTURES / "normal.jsonl"),
        approved_root=str(FIXTURES),
        source_type="jsonl",
        path_fingerprint="fixture-fingerprint",
    )

    result = parse_codex_jsonl(source, start_offset=0)

    assert [event.record_identity for event in result.events] == ["ordinal:1"]
    assert isinstance(source.canonical_path, Path)
    assert isinstance(source.approved_root, Path)


def test_parser_uses_delta_usage_and_excludes_cumulative_breakdowns() -> None:
    """Fails if parser reads cumulative totals or counts cache/reasoning as workload."""
    result = parse_codex_jsonl(synthetic_source("normal.jsonl"), start_offset=0)

    assert len(result.events) == 1
    event = result.events[0]
    assert event.input_total_tokens == 100
    assert event.cache_read_tokens == 70
    assert event.cache_write_tokens == 10
    assert event.output_total_tokens == 25
    assert event.reasoning_tokens == 20
    assert event.workload_tokens == 125
    assert event.measurement_type is MeasurementType.DELTA


def test_parser_stops_before_partial_final_line() -> None:
    """Fails if an incomplete append advances the cursor beyond recoverable bytes."""
    source = synthetic_source("partial.jsonl")

    result = parse_codex_jsonl(source, start_offset=0)

    assert [event.record_identity for event in result.events] == ["ordinal:1"]
    assert result.partial_final_record is True
    assert result.safe_byte_offset < source.canonical_path.stat().st_size


def test_parser_marks_unknown_shape_without_guessing_tokens() -> None:
    """Fails if an unsupported usage structure becomes a zero-token event."""
    result = parse_codex_jsonl(synthetic_source("unsupported.jsonl"), start_offset=0)

    assert result.events == []
    assert result.unsupported_records == 1


def test_parser_keeps_duplicate_ordinals_for_persistence_deduplication() -> None:
    """Fails if parsing drops records before the source-and-ordinal uniqueness boundary."""
    result = parse_codex_jsonl(synthetic_source("duplicate.jsonl"), start_offset=0)

    assert [event.record_identity for event in result.events] == ["ordinal:1", "ordinal:1"]
    assert result.events[1].source_id == "codex-local:fixture-fingerprint"


def test_parser_leaves_missing_breakdowns_unknown() -> None:
    """Fails if missing optional usage values are fabricated as zero."""
    result = parse_codex_jsonl(synthetic_source("duplicate.jsonl"), start_offset=0)

    event = result.events[1]
    assert event.cache_read_tokens is None
    assert event.cache_write_tokens is None
    assert event.reasoning_tokens is None
    assert event.quality is Quality.EXACT
    assert event.parser_version == "codex-jsonl-v6"


def test_parser_starts_at_the_provided_completed_line_offset() -> None:
    """Fails if an incremental scan rereads bytes before its cursor."""
    source = synthetic_source("duplicate.jsonl")
    first_line_size = source.canonical_path.read_bytes().index(b"\n") + 1

    result = parse_codex_jsonl(source, start_offset=first_line_size)

    assert [event.record_identity for event in result.events] == ["ordinal:1"]
    assert result.events[0].input_total_tokens == 10
    assert result.safe_byte_offset == source.canonical_path.stat().st_size


def test_parser_keeps_valid_records_when_later_token_value_is_invalid(tmp_path: Path) -> None:
    """Fails if one malformed token value discards a prior valid completed record."""
    path = tmp_path / "synthetic.jsonl"
    path.write_bytes(
        (FIXTURES / "normal.jsonl").read_bytes()
        + b'{"ordinal":2,"timestamp":"2026-09-20T10:01:00Z",'
        b'"type":"token_usage_record","payload":{"usage":{"input_tokens":-1}}}\n'
    )
    source = SourceDescriptor(
        source_id="codex-local:synthetic-fingerprint",
        connector_id="codex-local",
        provider=Provider.CODEX,
        display_name="Codex session",
        canonical_path=path,
        approved_root=tmp_path,
        source_type="jsonl",
        path_fingerprint="synthetic-fingerprint",
    )

    result = parse_codex_jsonl(source, start_offset=0)

    assert [event.record_identity for event in result.events] == ["ordinal:1"]
    assert result.unsupported_records == 1
    assert result.safe_byte_offset == path.stat().st_size


def test_parser_rejects_explicit_null_token_values(tmp_path: Path) -> None:
    """Fails if a malformed token field is mistaken for an absent optional field."""
    path = tmp_path / "synthetic.jsonl"
    path.write_bytes(
        (FIXTURES / "normal.jsonl").read_bytes()
        + b'{"ordinal":2,"timestamp":"2026-09-20T10:01:00Z",'
        b'"type":"token_usage_record","payload":{"usage":{"input_tokens":null}}}\n'
    )
    source = SourceDescriptor(
        source_id="codex-local:synthetic-fingerprint",
        connector_id="codex-local",
        provider=Provider.CODEX,
        display_name="Codex session",
        canonical_path=path,
        approved_root=tmp_path,
        source_type="jsonl",
        path_fingerprint="synthetic-fingerprint",
    )

    result = parse_codex_jsonl(source, start_offset=0)

    assert [event.record_identity for event in result.events] == ["ordinal:1"]
    assert result.unsupported_records == 1


def test_parser_rejects_source_swapped_for_outside_symlink(tmp_path: Path) -> None:
    """Fails if scan-time opening follows a replacement symlink outside approval."""
    approved_root = tmp_path / "approved"
    approved_root.mkdir()
    source_path = approved_root / "session.jsonl"
    source_path.write_bytes((FIXTURES / "normal.jsonl").read_bytes())
    outside_path = tmp_path / "outside.jsonl"
    outside_path.write_bytes(
        (FIXTURES / "normal.jsonl").read_bytes().replace(b'"ordinal":1', b'"ordinal":99')
    )
    source = SourceDescriptor(
        source_id="codex-local:synthetic-fingerprint",
        connector_id="codex-local",
        provider=Provider.CODEX,
        display_name="Codex session",
        canonical_path=source_path,
        approved_root=approved_root,
        source_type="jsonl",
        path_fingerprint="synthetic-fingerprint",
    )
    source_path.unlink()
    source_path.symlink_to(outside_path)

    with pytest.raises(ValueError, match="approved source cannot be opened safely"):
        parse_codex_jsonl(source, start_offset=0)


def test_parser_normalizes_timestamp_offsets_to_utc(tmp_path: Path) -> None:
    """Fails if accepted timestamps retain a source timezone offset."""
    path = tmp_path / "synthetic.jsonl"
    path.write_bytes(
        b'{"ordinal":1,"timestamp":"2026-09-20T10:00:00+05:30",'
        b'"type":"token_usage_record","payload":{"usage":{"input_tokens":100,'
        b'"output_tokens":25}}}\n'
    )
    source = SourceDescriptor(
        source_id="codex-local:synthetic-fingerprint",
        connector_id="codex-local",
        provider=Provider.CODEX,
        display_name="Codex session",
        canonical_path=path,
        approved_root=tmp_path,
        source_type="jsonl",
        path_fingerprint="synthetic-fingerprint",
    )

    result = parse_codex_jsonl(source, start_offset=0)

    assert result.events[0].timestamp == datetime(2026, 9, 20, 4, 30, tzinfo=UTC)
    assert result.events[0].timestamp.tzinfo is UTC


@pytest.mark.skipif(os.name == "nt", reason="POSIX openat race injection")
def test_parser_rejects_ancestor_swapped_after_approved_root_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails if a child path can escape after the approved root descriptor opens."""
    approved_root = tmp_path / "approved"
    nested = approved_root / "nested"
    nested.mkdir(parents=True)
    source_path = nested / "session.jsonl"
    source_path.write_bytes((FIXTURES / "normal.jsonl").read_bytes())
    outside_nested = tmp_path / "outside" / "nested"
    outside_nested.mkdir(parents=True)
    (outside_nested / "session.jsonl").write_bytes(
        (FIXTURES / "normal.jsonl").read_bytes().replace(b'"ordinal":1', b'"ordinal":99')
    )
    source = SourceDescriptor(
        source_id="codex-local:synthetic-fingerprint",
        connector_id="codex-local",
        provider=Provider.CODEX,
        display_name="Codex session",
        canonical_path=source_path,
        approved_root=approved_root,
        source_type="jsonl",
        path_fingerprint="synthetic-fingerprint",
    )
    original_open = os.open
    swapped = False

    def swap_after_root_open(
        path: str | bytes | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        lexical_path = Path(os.fsdecode(path)) if isinstance(path, bytes) else Path(path)
        if lexical_path == Path(approved_root.name) and dir_fd is not None and not swapped:
            nested.rename(tmp_path / "original-nested")
            nested.symlink_to(outside_nested, target_is_directory=True)
            swapped = True
        return descriptor

    monkeypatch.setattr(codex_parser.os, "open", swap_after_root_open)

    with pytest.raises(ValueError, match="approved source cannot be opened safely"):
        parse_codex_jsonl(source, start_offset=0)
    assert swapped is True


def test_parser_skips_timestamp_that_overflows_during_utc_conversion(tmp_path: Path) -> None:
    """Fails if an unrepresentable UTC conversion aborts the completed-record scan."""
    path = tmp_path / "synthetic.jsonl"
    path.write_bytes(
        (FIXTURES / "normal.jsonl").read_bytes()
        + b'{"ordinal":2,"timestamp":"0001-01-01T00:00:00+14:00",'
        b'"type":"token_usage_record","payload":{"usage":{"input_tokens":10,'
        b'"output_tokens":5}}}\n'
    )
    source = SourceDescriptor(
        source_id="codex-local:synthetic-fingerprint",
        connector_id="codex-local",
        provider=Provider.CODEX,
        display_name="Codex session",
        canonical_path=path,
        approved_root=tmp_path,
        source_type="jsonl",
        path_fingerprint="synthetic-fingerprint",
    )

    result = parse_codex_jsonl(source, start_offset=0)

    assert [event.record_identity for event in result.events] == ["ordinal:1"]
    assert result.unsupported_records == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX openat race injection")
def test_parser_rejects_approved_root_ancestor_swapped_during_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails if an ancestor swap redirects an absolute approved-root open."""
    mutable_parent = tmp_path / "mutable-parent"
    approved_root = mutable_parent / "approved"
    nested = approved_root / "nested"
    nested.mkdir(parents=True)
    source_path = nested / "session.jsonl"
    source_path.write_bytes((FIXTURES / "normal.jsonl").read_bytes())
    outside_parent = tmp_path / "outside-parent"
    outside_nested = outside_parent / "approved" / "nested"
    outside_nested.mkdir(parents=True)
    (outside_nested / "session.jsonl").write_bytes(
        (FIXTURES / "normal.jsonl").read_bytes().replace(b'"ordinal":1', b'"ordinal":99')
    )
    source = SourceDescriptor(
        source_id="codex-local:synthetic-fingerprint",
        connector_id="codex-local",
        provider=Provider.CODEX,
        display_name="Codex session",
        canonical_path=source_path,
        approved_root=approved_root,
        source_type="jsonl",
        path_fingerprint="synthetic-fingerprint",
    )
    original_open = os.open
    swapped = False

    def swap_before_ancestor_open(
        path: str | bytes | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        lexical_path = Path(os.fsdecode(path)) if isinstance(path, bytes) else Path(path)
        old_open_point = lexical_path == approved_root and dir_fd is None
        new_open_point = lexical_path == Path(mutable_parent.name) and dir_fd is not None
        if (old_open_point or new_open_point) and not swapped:
            mutable_parent.rename(tmp_path / "original-mutable-parent")
            mutable_parent.symlink_to(outside_parent, target_is_directory=True)
            swapped = True
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(codex_parser.os, "open", swap_before_ancestor_open)

    with pytest.raises(ValueError, match="approved source cannot be opened safely"):
        parse_codex_jsonl(source, start_offset=0)
    assert swapped is True


def test_model_context_survives_incremental_scans_and_model_switches(tmp_path: Path) -> None:
    from tests.service_support import token_record

    path = tmp_path / "models.jsonl"
    context = lambda model, turn: json.dumps({"type": "turn_context", "payload": {"model": model, "turn_id": turn}}).encode() + b"\n"
    usage = json.loads(token_record(1, input_tokens=100, output_tokens=25))
    usage["payload"].update(session_id="synthetic-session", turn_id="turn-one")
    path.write_bytes(context("model-one", "turn-one") + json.dumps(usage).encode() + b"\n")
    source = replace(synthetic_source("normal.jsonl"), canonical_path=path, approved_root=tmp_path)
    first = parse_codex_jsonl(source, 0)
    assert first.events[0].model_name == "model-one"
    assert first.events[0].session_id == "synthetic-session"
    usage["ordinal"] = 2
    with path.open("ab") as stream:
        stream.write(context("model-two", "turn-two"))
        # A late record still belongs to the first turn.
        stream.write(json.dumps(usage).encode() + b"\n")
        usage["ordinal"] = 3
        usage["payload"]["turn_id"] = "turn-two"
        stream.write(json.dumps(usage).encode() + b"\n")
    second = parse_codex_jsonl(source, first.safe_byte_offset, first.safe_prefix_fingerprint)
    assert [event.model_name for event in second.events] == ["model-one", "model-two"]
    assert all(event.model_attribution == "turn" for event in second.events)


def test_codex_tasks_are_separate_even_with_a_shared_app_session(tmp_path: Path) -> None:
    from tests.service_support import token_record

    path = tmp_path / 'tasks.jsonl'
    rows = []
    for ordinal, thread in [(1, 'task-one'), (2, 'task-two')]:
        row = json.loads(token_record(ordinal, input_tokens=100, output_tokens=25))
        row['payload'].update(session_id='shared-app-session', thread_id=thread)
        rows.append(json.dumps(row))
    path.write_text('\n'.join(rows) + '\n')
    source = replace(synthetic_source('normal.jsonl'), canonical_path=path, approved_root=tmp_path)
    result = parse_codex_jsonl(source, 0)
    assert {event.session_id for event in result.events} == {'task-one', 'task-two'}
