"""VS Code Copilot Chat imports only recorded, approved usage metadata."""

import json
from dataclasses import replace
from pathlib import Path

from tokenhub.connectors.protocol import DiscoveryContext, UsageConnector
from tokenhub.connectors.registry import ConnectorRegistry
from tokenhub.domain.models import Provider, SourceState

WORKSPACE = "a" * 32
SESSION = "11111111-2222-4333-8444-555555555555"


def _source(tmp_path: Path, records: list[dict]) -> tuple[UsageConnector, Path]:
    root = tmp_path / "vscode-data"
    session = root / "User" / "workspaceStorage" / WORKSPACE / "chatSessions" / f"{SESSION}.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text("".join(json.dumps(record) + "\n" for record in records))
    (root / "User" / "globalStorage" / "github.copilot-chat").mkdir(parents=True)
    connector = next(
        (item for item in ConnectorRegistry.default().connectors if item.connector_id == "vscode-copilot-local"),
        None,
    )
    assert connector is not None, "VS Code Copilot must be registered as an importable source"
    return connector, session


def _context(tmp_path: Path) -> DiscoveryContext:
    return DiscoveryContext(
        home=tmp_path / "home",
        environment={"VSCODE_USER_DATA_DIR": str(tmp_path / "vscode-data")},
        which=lambda _: None,
    )


def _request(
    request_id: str, model: str, prompt: object | None, output: object | None,
    *, resolved: str | None = None,
) -> dict:
    metadata: dict[str, object] = {}
    if prompt is not None:
        metadata["promptTokens"] = prompt
    if output is not None:
        metadata["outputTokens"] = output
    if resolved is not None:
        metadata["resolvedModel"] = resolved
    return {
        "requestId": request_id,
        "timestamp": 1_790_503_200_000,
        "modelId": model,
        "message": {"text": "private prompt"},
        "result": {"metadata": metadata},
    }


def test_discovers_only_chat_session_files_with_path_free_evidence(tmp_path: Path) -> None:
    connector, session = _source(tmp_path, [])
    (session.parent / "auth.jsonl").write_text("secret")
    (session.parent / "not-a-session.jsonl").write_text("secret")
    (session.parent / "22222222-2222-4222-8222-222222222222.jsonl").symlink_to(session)
    outside = tmp_path / "outside.jsonl"
    outside.write_text("secret")
    (session.parent.parent / "outside").symlink_to(tmp_path, target_is_directory=True)

    result = connector.detect(_context(tmp_path))

    assert result.provider is Provider.VSCODE_COPILOT
    assert result.state is SourceState.DISCOVERED
    assert set(result.evidence_codes) == {"known_root_exists", "configuration_found", "session_source_found"}
    assert len(result.sources) == 1
    assert result.sources[0].scan_supported is True
    assert str(tmp_path) not in result.model_dump_json()
    assert connector.discover_sources(_context(tmp_path))[0].canonical_path == session


def test_patch_updates_replace_request_counts_without_retaining_chat_content(tmp_path: Path) -> None:
    records = [
        {"kind": 0, "v": {"version": 3, "sessionId": SESSION, "requests": [
            _request("request-one", "copilot/gpt-5", 10, 3),
            _request("request-two", "copilot/claude-haiku-4.5", None, None),
            _request("byok", "openrouter/model", 100, 50),
        ]}},
        {"kind": 2, "k": ["requests"], "v": [_request("request-three", "copilot/auto", 7, 2, resolved="gpt-5-mini")]},
        {"kind": 1, "k": ["requests", 0, "result"], "v": {"metadata": {"promptTokens": 20, "outputTokens": 4}}},
    ]
    connector, session = _source(tmp_path, records)
    discovered = connector.discover_sources(_context(tmp_path))[0]
    assert connector.scan(discovered, None).reason_code == "source_not_approved"
    approved = replace(discovered, state=SourceState.APPROVED)

    first = connector.scan(approved, None)

    assert first.state is SourceState.PARTIAL
    assert first.replace_events is True
    assert first.unsupported_records == 1
    assert {event.record_identity: (event.model_name, event.input_total_tokens, event.output_total_tokens)
            for event in first.events} == {
        "request-one": ("gpt-5", 20, 4),
        "request-three": ("gpt-5-mini", 7, 2),
    }
    assert all(event.session_id == SESSION for event in first.events)
    assert "private prompt" not in repr(first)

    unchanged = connector.scan(approved, first.cursor)
    assert unchanged.events == ()
    assert unchanged.replace_events is False

    with session.open("a") as stream:
        stream.write(json.dumps({"kind": 1, "k": ["requests", 1, "result"],
                                 "v": {"metadata": {"promptTokens": 0, "outputTokens": 6}}}) + "\n")
    updated = connector.scan(approved, first.cursor)
    assert updated.replace_events is True
    assert updated.unsupported_records == 0
    assert {event.record_identity: event.workload_tokens for event in updated.events} == {
        "request-one": 24, "request-two": 6, "request-three": 9,
    }


def test_invalid_counters_are_not_guessed_or_counted(tmp_path: Path) -> None:
    records = [{"kind": 0, "v": {"version": 3, "sessionId": SESSION, "requests": [
        _request("negative", "copilot/gpt-5", -1, 8),
        _request("boolean", "copilot/gpt-5", True, 8),
        _request("too-large", "copilot/gpt-5", 2**63, 8),
        _request("zero", "copilot/gpt-5", 0, 0),
    ]}}]
    connector, session = _source(tmp_path, records)
    source = replace(connector.discover_sources(_context(tmp_path))[0], state=SourceState.APPROVED)

    result = connector.scan(source, None)

    assert result.state is SourceState.PARTIAL
    assert result.unsupported_records == 3
    assert [(event.record_identity, event.workload_tokens) for event in result.events] == [("zero", 0)]

    with session.open("a") as stream:
        stream.write(json.dumps({"kind": 1, "k": ["requests", 0, "result", "metadata", "promptTokens"], "v": 5}) + "\n")
    corrected = connector.scan(source, result.cursor)
    assert corrected.unsupported_records == 2
    assert {event.record_identity: event.workload_tokens for event in corrected.events} == {
        "negative": 13, "zero": 0,
    }
