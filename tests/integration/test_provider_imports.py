"""Provider usage reaches the dashboard through real approval and collection."""

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tokenhub.app import create_app
from tokenhub.connectors.protocol import DiscoveryContext
from tokenhub.domain.models import SourceState
from tokenhub.settings import TokenHubSettings

from tests.api.conftest import ORIGIN


def claude_record(message_id: str, output: int = 25) -> bytes:
    return (
        json.dumps(
            {
                "type": "assistant",
                "uuid": "chunk-" + str(output),
                "timestamp": "2026-09-27T10:00:00Z",
                "message": {
                    "id": message_id,
                    "role": "assistant",
                    "content": "private prompt",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": output,
                        "cache_read_input_tokens": 70,
                        "cache_creation_input_tokens": 10,
                    },
                },
            }
        )
        + "\n"
    ).encode()


def create_hermes_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(path)
    database.execute("PRAGMA journal_mode=WAL")
    database.execute("""CREATE TABLE sessions (
        id TEXT PRIMARY KEY, started_at REAL, ended_at REAL,
        input_tokens INTEGER, output_tokens INTEGER,
        cache_read_tokens INTEGER, cache_write_tokens INTEGER,
        reasoning_tokens INTEGER, system_prompt TEXT)""")
    database.execute(
        "INSERT INTO sessions VALUES ('session-one', 1790503200, NULL, 100, 25, 70, 10, 5, 'private prompt')"
    )
    database.commit()
    return database


@pytest.fixture
def providers(tmp_path: Path):
    home = tmp_path / "home"
    claude = home / ".claude/projects/project/session.jsonl"
    claude.parent.mkdir(parents=True)
    claude.write_bytes(claude_record("message-one") * 2)
    database = create_hermes_database(home / ".hermes/state.db")
    app = create_app(
        TokenHubSettings(
            home_directory=home,
            data_directory=tmp_path / "data",
            scan_interval_seconds=3600,
        )
    )
    app.state.container.discovery_context = DiscoveryContext(home, {}, lambda _: None)
    with TestClient(app, base_url="http://127.0.0.1:7432") as client:
        yield client, claude, database
    database.close()


def source_id(client: TestClient, connector: str) -> str:
    provider = next(
        p
        for p in client.get("/api/v1/discovery").json()["providers"]
        if p["connector_id"] == connector
    )
    assert provider["sources"], "provider must expose its usage source"
    assert provider["sources"][0]["scan_supported"] is True
    return provider["sources"][0]["source_id"]


@pytest.mark.parametrize(
    "connector,total", [("claude-code-local", 205), ("hermes-local", 205)]
)
def test_provider_approval_import_and_rebuild(providers, connector, total):
    client, _, _ = providers
    source = source_id(client, connector)
    endpoint = f"/api/v1/sources/{source}"
    assert client.post(endpoint + "/rescan", headers=ORIGIN).status_code == 409
    assert client.post(endpoint + "/approve", headers=ORIGIN).status_code == 200
    imported = client.post(endpoint + "/rescan", headers=ORIGIN)
    assert imported.status_code == 200
    assert imported.json()["inserted_events"] == 1
    summary = client.get("/api/v1/dashboard").json()
    assert summary["workload_tokens"] == total
    assert summary["cache_read_tokens"] == 70
    assert summary["cache_write_tokens"] == 10
    assert summary["event_count"] == 1
    client.post(endpoint + "/rescan", headers=ORIGIN)
    assert client.get("/api/v1/dashboard").json()["workload_tokens"] == total
    assert client.post("/api/v1/rebuild", headers=ORIGIN).status_code == 200
    assert client.get("/api/v1/dashboard").json() == summary
    for route in ["/discovery", "/dashboard", "/data-quality"]:
        body = client.get("/api/v1" + route).text
        assert "private prompt" not in body
        assert str(client.app.state.container.settings.home_directory) not in body


def test_background_collection_refreshes_claude_streaming_usage(providers):
    client, claude, _ = providers
    source = source_id(client, "claude-code-local")
    assert (
        client.post(f"/api/v1/sources/{source}/approve", headers=ORIGIN).status_code
        == 200
    )
    client.app.state.container.collect()
    assert client.get("/api/v1/dashboard").json()["workload_tokens"] == 205
    with claude.open("ab") as stream:
        stream.write(
            claude_record("message-one", 50) + claude_record("message-two", 10)
        )
    client.app.state.container.collect()
    summary = client.get("/api/v1/dashboard").json()
    assert summary["workload_tokens"] == 420
    assert summary["event_count"] == 2
    client.app.state.container.collect()
    assert client.get("/api/v1/dashboard").json() == summary


def test_background_collection_sees_hermes_wal_and_replaces_session_totals(providers):
    client, _, database = providers
    source = source_id(client, "hermes-local")
    assert (
        client.post(f"/api/v1/sources/{source}/approve", headers=ORIGIN).status_code
        == 200
    )
    client.app.state.container.collect()
    assert client.get("/api/v1/dashboard").json()["workload_tokens"] == 205
    database.execute("UPDATE sessions SET input_tokens=140, output_tokens=40")
    database.commit()
    client.app.state.container.collect()
    assert client.get("/api/v1/dashboard").json()["workload_tokens"] == 260
    assert client.get("/api/v1/dashboard").json()["event_count"] == 1
    client.app.state.container.collect()
    assert client.get("/api/v1/dashboard").json()["workload_tokens"] == 260


def test_existing_hermes_detection_is_upgraded_to_importable(providers):
    client, _, _ = providers
    source = source_id(client, "hermes-local")
    services = client.app.state.container.services
    services.source_repository.set_state(source, SourceState.UNSUPPORTED)
    # An out-of-band database edit: signal it as the collector or a route would.
    services.collection.bump()
    source = source_id(client, "hermes-local")
    assert (
        client.post(f"/api/v1/sources/{source}/approve", headers=ORIGIN).status_code
        == 200
    )


@pytest.mark.parametrize(
    "provider,connector",
    [("claude_code", "claude-code-local"), ("hermes", "hermes-local")],
)
def test_provider_folder_consent_and_restart(providers, provider, connector):
    client, claude, _ = providers
    enabled = client.post(f"/api/v1/collection/{provider}/enable", headers=ORIGIN)
    assert enabled.status_code == 200
    assert connector in enabled.json()["auto_import_connectors"]
    assert client.get("/api/v1/dashboard").json()["workload_tokens"] == 205
    if provider == "claude_code":
        (claude.parent / "new.jsonl").write_bytes(claude_record("new-message", 10))
        client.app.state.container.collect()
        assert client.get("/api/v1/dashboard").json()["workload_tokens"] == 395
    restarted = create_app(client.app.state.container.settings)
    restarted.state.container.discovery_context = (
        client.app.state.container.discovery_context
    )
    with TestClient(restarted, base_url="http://127.0.0.1:7432") as second:
        assert (
            connector
            in second.get("/api/v1/collection").json()["auto_import_connectors"]
        )
        assert second.get("/api/v1/dashboard").json()["event_count"] == (
            2 if provider == "claude_code" else 1
        )
        disabled = second.post(f"/api/v1/collection/{provider}/disable", headers=ORIGIN)
        assert disabled.status_code == 200
        assert connector not in disabled.json()["auto_import_connectors"]


def test_provider_collection_enable_requires_origin(providers):
    client, _, _ = providers
    for provider in ["claude_code", "hermes"]:
        assert client.post(f"/api/v1/collection/{provider}/enable").status_code == 403
    assert client.get("/api/v1/dashboard").json()["event_count"] == 0


@pytest.mark.parametrize("connector", ["claude-code-local", "hermes-local"])
def test_replaced_provider_root_is_rejected_without_losing_imported_usage(
    providers, connector
):
    client, claude, database = providers
    source = source_id(client, connector)
    assert (
        client.post(f"/api/v1/sources/{source}/approve", headers=ORIGIN).status_code
        == 200
    )
    client.app.state.container.collect()
    root = (
        claude.parents[1]
        if connector == "claude-code-local"
        else claude.parents[3] / ".hermes"
    )
    if connector == "hermes-local":
        # Windows cannot rename a directory while this fixture holds its DB open.
        database.close()
    root.rename(root.with_name(root.name + "-original"))
    root.mkdir()
    client.app.state.container.collect()
    assert client.get("/api/v1/dashboard").json()["workload_tokens"] == 205
    assert client.get("/api/v1/collection").json()["failed_source_count"] == 1


def test_claude_partial_tail_is_imported_when_completed(providers):
    client, claude, _ = providers
    source = source_id(client, "claude-code-local")
    assert (
        client.post(f"/api/v1/sources/{source}/approve", headers=ORIGIN).status_code
        == 200
    )
    tail = claude_record("second-message", 10)
    with claude.open("ab") as stream:
        stream.write(tail[:-1])
    client.app.state.container.collect()
    assert client.get("/api/v1/dashboard").json()["workload_tokens"] == 205
    state = client.app.state.container.services.source_repository.get(source).state
    assert state == "partial"
    with claude.open("ab") as stream:
        stream.write(b"\n")
    client.app.state.container.collect()
    assert client.get("/api/v1/dashboard").json()["workload_tokens"] == 395


def test_hermes_invalid_usage_is_reported_without_losing_valid_sessions(providers):
    client, _, database = providers
    database.execute(
        "INSERT INTO sessions VALUES ('bad-session', 1790503200, NULL, -1, 25, 0, 0, 0, 'private prompt')"
    )
    database.commit()
    source = source_id(client, "hermes-local")
    assert (
        client.post(f"/api/v1/sources/{source}/approve", headers=ORIGIN).status_code
        == 200
    )
    client.app.state.container.collect()
    assert client.get("/api/v1/dashboard").json()["workload_tokens"] == 205
    freshness = next(
        s
        for s in client.get("/api/v1/data-quality").json()["source_freshness"]
        if s["source_id"] == source
    )
    assert freshness["state"] == "partial"
    assert freshness["unsupported_records"] == 1


def test_hermes_symlinked_journal_is_rejected(providers):
    client, claude, database = providers
    database.close()
    journal = claude.parents[3] / ".hermes/state.db-wal"
    journal.symlink_to(claude)
    source = source_id(client, "hermes-local")
    assert (
        client.post(f"/api/v1/sources/{source}/approve", headers=ORIGIN).status_code
        == 200
    )
    client.app.state.container.collect()
    assert client.get("/api/v1/dashboard").json()["event_count"] == 0
    assert client.get("/api/v1/collection").json()["failed_source_count"] == 1


def test_claude_forked_session_does_not_recount_shared_history(providers):
    client, claude, _ = providers
    (claude.parent / "fork.jsonl").write_bytes(
        claude_record("message-one", 50) + claude_record("fork-message", 10)
    )
    response = client.post("/api/v1/collection/claude_code/enable", headers=ORIGIN)
    assert response.status_code == 200
    summary = client.get("/api/v1/dashboard").json()
    assert summary["workload_tokens"] == 420
    assert summary["event_count"] == 2
    assert summary["cache_read_tokens"] == 140
    assert summary["quality_counts"]["exact"] == 2
    assert client.post("/api/v1/rebuild", headers=ORIGIN).status_code == 200
    assert client.get("/api/v1/dashboard").json() == summary


def test_hermes_database_replaced_during_snapshot_preserves_previous_totals(
    providers, monkeypatch
):
    import os

    from tokenhub.connectors.hermes import parser

    client, claude, database = providers
    source = source_id(client, "hermes-local")
    assert (
        client.post(f"/api/v1/sources/{source}/approve", headers=ORIGIN).status_code
        == 200
    )
    client.app.state.container.collect()
    database.close()
    root = claude.parents[3] / ".hermes"
    replacement = create_hermes_database(root / "replacement.db")
    replacement.execute("UPDATE sessions SET input_tokens=500, output_tokens=25")
    replacement.commit()
    original_copy = parser.shutil.copyfileobj
    swapped = False

    def replace_database_after_main_copy(stream, target, *args):
        nonlocal swapped
        original_copy(stream, target, *args)
        if not swapped:
            swapped = True
            os.replace(root / "replacement.db", root / "state.db")
            os.replace(root / "replacement.db-wal", root / "state.db-wal")

    monkeypatch.setattr(parser.shutil, "copyfileobj", replace_database_after_main_copy)
    try:
        client.app.state.container.collect()
        assert swapped
        assert client.get("/api/v1/dashboard").json()["workload_tokens"] == 205
        assert client.get("/api/v1/collection").json()["failed_source_count"] == 1
    finally:
        replacement.close()


def test_provider_models_and_sessions_reach_usage_explorer(providers):
    client, claude, database = providers
    record = json.loads(claude_record("message-one"))
    record["sessionId"] = "private-claude-session"
    record["message"]["model"] = "claude-model"
    claude.write_text(json.dumps(record) + "\n")
    database.execute("ALTER TABLE sessions ADD COLUMN model TEXT")
    database.execute("UPDATE sessions SET model = 'hermes-model'")
    database.commit()
    for connector in ["claude-code-local", "hermes-local"]:
        source = source_id(client, connector)
        client.post(f"/api/v1/sources/{source}/approve", headers=ORIGIN)
        client.post(f"/api/v1/sources/{source}/rescan", headers=ORIGIN)
    response = client.get("/api/v1/usage")
    assert response.status_code == 200
    result = response.json()
    assert result["totals"]["workload_tokens"] == 410
    assert result["totals"]["session_count"] == 2
    assert {row["model_name"]: row["attribution"] for row in result["models"]} == {
        "claude-model": "message", "hermes-model": "session",
    }
    assert "private" not in response.text
    assert str(claude.parent) not in response.text


def test_hermes_model_only_update_refreshes_attribution(providers):
    client, _, database = providers
    database.execute('ALTER TABLE sessions ADD COLUMN model TEXT')
    database.execute("UPDATE sessions SET model = 'model-before'")
    database.commit()
    source = source_id(client, 'hermes-local')
    client.post(f'/api/v1/sources/{source}/approve', headers=ORIGIN)
    client.post(f'/api/v1/sources/{source}/rescan', headers=ORIGIN)
    before = client.get('/api/v1/usage').json()
    assert before['models'][0]['model_name'] == 'model-before'
    database.execute("UPDATE sessions SET model = 'model-after'")
    database.commit()
    client.post(f'/api/v1/sources/{source}/rescan', headers=ORIGIN)
    after = client.get('/api/v1/usage').json()
    assert after['models'][0]['model_name'] == 'model-after'
    assert after['totals'] == before['totals']


def _spy_on_rescans(monkeypatch) -> list[str]:
    from tokenhub.ingestion.service import IngestionService

    scanned: list[str] = []
    original = IngestionService.rescan

    def rescan(self, source_id, **kwargs):
        scanned.append(source_id)
        return original(self, source_id, **kwargs)

    monkeypatch.setattr(IngestionService, "rescan", rescan)
    return scanned


def test_unchanged_sqlite_source_is_skipped_until_its_journal_changes(providers, monkeypatch):
    client, _, database = providers
    hermes = source_id(client, "hermes-local")
    client.post(f"/api/v1/sources/{hermes}/approve", headers=ORIGIN)
    container = client.app.state.container
    container.collect()
    scanned = _spy_on_rescans(monkeypatch)

    container.collect()
    assert hermes not in scanned

    # Active Hermes usage sits in the write-ahead log, not the main file.
    database.execute("UPDATE sessions SET input_tokens=140, output_tokens=40")
    database.commit()
    container.collect()
    assert hermes in scanned
    assert client.get("/api/v1/dashboard").json()["workload_tokens"] == 260


def test_restart_skips_unchanged_jsonl_sources_but_reads_appended_ones(
    providers, tmp_path, monkeypatch
):
    client, claude, _ = providers
    session = source_id(client, "claude-code-local")
    client.post(f"/api/v1/sources/{session}/approve", headers=ORIGIN)
    container = client.app.state.container
    container.collect()

    scanned = _spy_on_rescans(monkeypatch)
    home = tmp_path / "home"
    restarted = create_app(container.settings)
    restarted.state.container.discovery_context = DiscoveryContext(home, {}, lambda _: None)
    with TestClient(restarted, base_url="http://127.0.0.1:7432"):
        assert session not in scanned
        with claude.open("ab") as stream:
            stream.write(claude_record("message-two"))
        restarted.state.container.collect()
        assert session in scanned
