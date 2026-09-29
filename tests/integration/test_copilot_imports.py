"""Copilot Chat usage reaches TokenHub only through approved local sources."""

import json
from pathlib import Path

from fastapi.testclient import TestClient
from tokenhub.app import create_app
from tokenhub.connectors.protocol import DiscoveryContext
from tokenhub.settings import TokenHubSettings

from tests.api.conftest import ORIGIN

WORKSPACE = "a" * 32
SESSION_ONE = "11111111-2222-4333-8444-555555555555"
SESSION_TWO = "22222222-2222-4222-8222-222222222222"


def _session(path: Path, session_id: str, request_id: str, prompt: int, output: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "kind": 0,
        "v": {
            "version": 3,
            "sessionId": session_id,
            "requests": [{
                "requestId": request_id,
                "timestamp": 1_790_503_200_000,
                "modelId": "copilot/gpt-5",
                "message": {"text": "private Copilot prompt"},
                "result": {"metadata": {"promptTokens": prompt, "outputTokens": output}},
            }],
        },
    }) + "\n")


def test_approved_copilot_chat_usage_updates_without_double_counting(tmp_path: Path) -> None:
    home = tmp_path / "home"
    user = tmp_path / "vscode-data" / "User"
    (user / "globalStorage" / "github.copilot-chat").mkdir(parents=True)
    first = user / "workspaceStorage" / WORKSPACE / "chatSessions" / f"{SESSION_ONE}.jsonl"
    _session(first, SESSION_ONE, "request-one", 12, 3)
    app = create_app(TokenHubSettings(
        home_directory=home, data_directory=tmp_path / "data", scan_interval_seconds=3600,
    ))
    app.state.container.discovery_context = DiscoveryContext(
        home, {"VSCODE_USER_DATA_DIR": str(tmp_path / "vscode-data")}, lambda _: None,
    )

    with TestClient(app, base_url="http://127.0.0.1:7432") as client:
        provider = next(p for p in client.get("/api/v1/discovery").json()["providers"]
                        if p["provider"] == "vscode_copilot")
        assert len(provider["sources"]) == 1
        source_id = provider["sources"][0]["source_id"]
        endpoint = f"/api/v1/sources/{source_id}"
        assert client.post(endpoint + "/rescan", headers=ORIGIN).status_code == 409
        assert client.get("/api/v1/dashboard").json()["event_count"] == 0
        assert client.post(endpoint + "/approve", headers=ORIGIN).status_code == 200
        assert client.post(endpoint + "/rescan", headers=ORIGIN).json()["inserted_events"] == 1
        assert client.get("/api/v1/dashboard").json()["workload_tokens"] == 15
        usage = client.get("/api/v1/usage").json()
        assert next(row for row in usage["models"] if row["provider"] == "vscode_copilot")["model_name"] == "gpt-5"
        session = next(row for row in usage["sessions"] if row["provider"] == "vscode_copilot")
        assert len(session["session_key"]) == 24
        assert SESSION_ONE not in json.dumps(usage)
        assert "private Copilot prompt" not in json.dumps(usage)

        with first.open("a") as stream:
            stream.write(json.dumps({"kind": 1, "k": ["requests", 0, "result"],
                                     "v": {"metadata": {"promptTokens": 20, "outputTokens": 4}}}) + "\n")
        app.state.container.collect()
        assert client.get("/api/v1/dashboard").json()["workload_tokens"] == 24
        app.state.container.collect()
        assert client.get("/api/v1/dashboard").json()["event_count"] == 1

        enabled = client.post("/api/v1/collection/vscode_copilot/enable", headers=ORIGIN)
        assert enabled.status_code == 200
        assert "vscode-copilot-local" in enabled.json()["auto_import_connectors"]
        second = first.with_name(f"{SESSION_TWO}.jsonl")
        _session(second, SESSION_TWO, "request-two", 5, 1)
        app.state.container.collect()
        summary = client.get("/api/v1/dashboard").json()
        assert summary["workload_tokens"] == 30
        assert summary["event_count"] == 2
        assert "private Copilot prompt" not in client.get("/api/v1/data-quality").text
        assert str(tmp_path) not in client.get("/api/v1/discovery").text


def test_one_provider_approval_imports_pending_sessions_beside_approved_ones(tmp_path: Path) -> None:
    home = tmp_path / "home"
    user = tmp_path / "vscode-data" / "User"
    (user / "globalStorage" / "github.copilot-chat").mkdir(parents=True)
    sessions = user / "workspaceStorage" / WORKSPACE / "chatSessions"
    _session(sessions / f"{SESSION_ONE}.jsonl", SESSION_ONE, "request-one", 12, 3)
    _session(sessions / f"{SESSION_TWO}.jsonl", SESSION_TWO, "request-two", 5, 1)
    app = create_app(TokenHubSettings(
        home_directory=home, data_directory=tmp_path / "data", scan_interval_seconds=3600,
    ))
    app.state.container.discovery_context = DiscoveryContext(
        home, {"VSCODE_USER_DATA_DIR": str(tmp_path / "vscode-data")}, lambda _: None,
    )

    with TestClient(app, base_url="http://127.0.0.1:7432") as client:
        provider = next(p for p in client.get("/api/v1/discovery").json()["providers"]
                        if p["provider"] == "vscode_copilot")
        first_id = provider["sources"][0]["source_id"]
        assert client.post(f"/api/v1/sources/{first_id}/approve", headers=ORIGIN).status_code == 200
        assert client.post(f"/api/v1/sources/{first_id}/rescan", headers=ORIGIN).status_code == 200
        assert client.get("/api/v1/dashboard").json()["workload_tokens"] == 15

        enabled = client.post("/api/v1/collection/vscode_copilot/enable", headers=ORIGIN)
        assert enabled.status_code == 200
        assert "vscode-copilot-local" in enabled.json()["auto_import_connectors"]
        summary = client.get("/api/v1/dashboard").json()
        assert summary["event_count"] == 2
        assert summary["workload_tokens"] == 21
        states = [source["state"] for source in next(
            p for p in client.get("/api/v1/discovery").json()["providers"]
            if p["provider"] == "vscode_copilot"
        )["sources"]]
        assert states == ["healthy", "healthy"]
