import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tokenhub.app import create_app
from tokenhub.settings import TokenHubSettings

from tests.api.conftest import ORIGIN, codex_id
from tests.service_support import token_record


def test_discovery_to_dashboard_api_flow(client: TestClient, tmp_path: Path) -> None:
    assert client.get("/api/v1/status").json() == {"status": "ok"}
    source_id = codex_id(client)
    endpoint = f"/api/v1/sources/{source_id}"
    with sqlite3.connect(tmp_path / "data" / "tokenhub.sqlite3") as database:
        assert database.execute("SELECT canonical_path, approved_root FROM sources").fetchall() == [(None, None), (None, None)]
    assert client.post(f"{endpoint}/rescan", headers=ORIGIN).status_code == 409
    assert client.post(f"{endpoint}/approve").status_code == 403
    approved = client.post(f"{endpoint}/approve", headers=ORIGIN)
    assert approved.status_code == 200
    assert approved.json()["state"] == "approved"
    scanned = client.post(f"{endpoint}/rescan", headers=ORIGIN)
    assert scanned.status_code == 200
    assert scanned.json()["inserted_events"] == 1
    assert client.post(f"{endpoint}/rescan", headers=ORIGIN).json()["inserted_events"] == 0
    summary = client.get("/api/v1/dashboard")
    assert summary.json()["workload_tokens"] == 125
    assert summary.json()["cache_read_tokens"] is None
    source = tmp_path / "home" / ".codex" / "sessions" / "synthetic.jsonl"
    with source.open("ab") as stream:
        stream.write(token_record(2, input_tokens=10, output_tokens=5))
    assert client.post(f"{endpoint}/rescan", headers=ORIGIN).json()["inserted_events"] == 1
    before = client.get("/api/v1/dashboard").json()
    assert before["workload_tokens"] == 140
    assert client.post("/api/v1/rebuild", headers=ORIGIN).json()["inserted_events"] == 2
    assert client.get("/api/v1/dashboard").json() == before
    for response in (approved, scanned, summary, client.get("/api/v1/discovery"), client.get("/api/v1/data-quality")):
        for private in (str(tmp_path), "canonical_path", "approved_root", "payload", "sk-synthetic-secret", "auth.json"):
            assert private not in response.text


def test_nullable_metrics_and_quality_are_serialized(client: TestClient, tmp_path: Path) -> None:
    empty = client.get("/api/v1/dashboard").json()
    assert empty["workload_tokens"] is None
    assert empty["event_count"] == 0
    source = tmp_path / "home" / ".codex" / "sessions" / "synthetic.jsonl"
    source.write_bytes(token_record(1, output_tokens=0) + b'{"private":"sk-secret"}\n')
    source_id = codex_id(client)
    endpoint = f"/api/v1/sources/{source_id}"
    assert client.post(f"{endpoint}/approve", headers=ORIGIN).status_code == 200
    assert client.post(f"{endpoint}/rescan", headers=ORIGIN).status_code == 200
    summary = client.get("/api/v1/dashboard").json()
    assert summary["workload_tokens"] is None
    assert summary["input_total_tokens"] is None
    assert summary["output_total_tokens"] == 0
    quality = client.get("/api/v1/data-quality").json()
    assert quality["quality_counts"] == summary["quality_counts"]
    assert quality["source_freshness"] == summary["source_freshness"]
    source_quality = next(s for s in quality["source_freshness"] if s["source_id"] == source_id)
    assert source_quality["state"] == "partial"
    assert source_quality["unsupported_records"] == 1
    assert source_quality["latest_event_at"].startswith("2026-09-20T10:00:00")


def test_usage_endpoint_filters_a_local_day_before_aggregation(client: TestClient) -> None:
    assert client.post('/api/v1/collection/codex/enable', headers=ORIGIN).status_code == 200
    day = client.get('/api/v1/usage', params={
        'from': '2026-09-20T00:00:00Z', 'to': '2026-09-21T00:00:00Z',
    })
    assert day.status_code == 200
    assert day.json()['totals']['workload_tokens'] == 125
    assert len(day.json()['models']) == 1
    other = client.get('/api/v1/usage', params={
        'from': '2026-09-21T00:00:00Z', 'to': '2026-09-22T00:00:00Z',
    })
    assert other.json()['totals']['event_count'] == 0
    assert other.json()['totals']['workload_tokens'] is None
    assert client.get('/api/v1/usage').json()['totals']['workload_tokens'] == 125
    assert client.get('/api/v1/usage', params={'from': '2026-09-20T00:00:00'}).status_code == 422
    assert client.get('/api/v1/usage', params={
        'from': '2026-09-21T00:00:00Z', 'to': '2026-09-20T00:00:00Z',
    }).status_code == 422


def test_service_errors_are_safe(client: TestClient, tmp_path: Path) -> None:
    discovery = client.get("/api/v1/discovery").json()
    hermes = discovery["providers"][2]["sources"][0]["source_id"]
    for action, status in (("approve", 200), ("rescan", 400)):
        assert client.post(f"/api/v1/sources/{hermes}/{action}", headers=ORIGIN).status_code == status
        missing = client.post(f"/api/v1/sources/sk-secret/{action}", headers=ORIGIN)
        assert missing.status_code == 404
        assert "sk-secret" not in missing.text
    source_id = discovery["providers"][1]["sources"][0]["source_id"]
    source = tmp_path / "home" / ".codex" / "sessions" / "synthetic.jsonl"
    source.unlink()
    source.symlink_to(tmp_path / "outside-sk-secret.jsonl")
    invalid = client.post(f"/api/v1/sources/{source_id}/approve", headers=ORIGIN)
    assert invalid.status_code == 400
    assert str(tmp_path) not in invalid.text
    assert "sk-secret" not in invalid.text
    assert client.get("/api/v1/dashboard").json()["event_count"] == 0


def test_factory_has_no_database_or_discovery_side_effects(api_app: FastAPI, tmp_path: Path) -> None:
    assert api_app.routes
    assert not (tmp_path / "data").exists()


def test_lifespan_migrates_and_restart_recovers_approval(client: TestClient, api_app: FastAPI, tmp_path: Path) -> None:
    source_id = codex_id(client)
    assert client.post(f"/api/v1/sources/{source_id}/approve", headers=ORIGIN).status_code == 200
    with sqlite3.connect(tmp_path / "data" / "tokenhub.sqlite3") as database:
        assert database.execute("SELECT version_num FROM alembic_version").fetchone() == ("0005_usage_metadata",)
    restarted = create_app(api_app.state.container.settings)
    with TestClient(restarted, base_url="http://127.0.0.1:7432") as second:
        assert second.post(f"/api/v1/sources/{source_id}/rescan", headers=ORIGIN).json()["inserted_events"] == 0


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "attacker.example", "127.0.0.1@attacker"])
def test_settings_reject_non_loopback_bind(host: str) -> None:
    with pytest.raises(ValueError):
        TokenHubSettings(host=host)


def test_cli_starts_loopback_server(monkeypatch: pytest.MonkeyPatch) -> None:
    from tokenhub import cli

    calls = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: calls.append(kwargs))
    cli.main()
    assert calls == [{"host": "127.0.0.1", "port": 7432, "proxy_headers": False, "access_log": False}]


def test_static_mount_is_optional(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import tokenhub.app as app_module
    dist = tmp_path / "frontend" / "dist"
    monkeypatch.setattr(app_module, "FRONTEND_DIST", dist)
    settings = TokenHubSettings(home_directory=tmp_path / "home", data_directory=tmp_path / "data")
    with TestClient(create_app(settings), base_url="http://127.0.0.1:7432") as http:
        assert http.get("/").status_code == 404
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html>TokenHub synthetic UI</html>")
    with TestClient(create_app(settings), base_url="http://127.0.0.1:7432") as http:
        assert "TokenHub synthetic UI" in http.get("/").text
        assert http.get("/api/v1/status").status_code == 200
        assert http.get("/", headers={"host": "attacker.example"}).status_code == 400
