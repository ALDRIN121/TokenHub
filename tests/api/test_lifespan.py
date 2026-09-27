"""Lifespan, migration, and restart behavior of the application factory."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tokenhub.app import create_app
from tokenhub.database.migrations import DATA_DIRECTORY_ENV
from tokenhub.settings import TokenHubSettings

from tests.api.conftest import ORIGIN, codex_id

HEAD_REVISION = "0004_auto_import_roots"
EXPECTED_TABLES = frozenset(
    {"alembic_version", "sources", "usage_events", "sync_cursors", "import_runs", "auto_import_roots"}
)


def test_create_app_has_no_side_effects_before_lifespan(api_app: FastAPI, tmp_path: Path) -> None:
    assert api_app.routes
    assert not (tmp_path / "data").exists()
    assert list(tmp_path.glob("**/tokenhub.sqlite3")) == []
    # The container exists but has not started: no engine, session, or services.
    with pytest.raises(RuntimeError, match="not started"):
        _ = api_app.state.container.services


def test_lifespan_migrates_to_head_and_creates_the_schema(client: TestClient, tmp_path: Path) -> None:
    database_path = tmp_path / "data" / "tokenhub.sqlite3"
    assert database_path.exists()
    with sqlite3.connect(database_path) as database:
        assert database.execute("SELECT version_num FROM alembic_version").fetchone() == (
            HEAD_REVISION,
        )
        tables = {
            row[0]
            for row in database.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert EXPECTED_TABLES <= tables


def test_restart_recovers_a_durable_approval_through_rediscovery(
    client: TestClient, api_app: FastAPI
) -> None:
    source_id = codex_id(client)
    approved = client.post(f"/api/v1/sources/{source_id}/approve", headers=ORIGIN)
    assert approved.status_code == 200
    assert approved.json()["state"] == "approved"

    restarted = create_app(api_app.state.container.settings)
    with TestClient(restarted, base_url="http://127.0.0.1:7432") as second:
        # Rediscovery must not reset the approval persisted in the first run.
        assert second.get("/api/v1/discovery").status_code == 200
        freshness = _freshness_by_source(second)
        assert freshness[source_id]["state"] == "healthy"
        assert second.post(f"/api/v1/sources/{source_id}/rescan", headers=ORIGIN).json()[
            "inserted_events"
        ] == 0
        assert _freshness_by_source(second)[source_id]["state"] == "healthy"


def _freshness_by_source(client: TestClient) -> dict[str, dict[str, object]]:
    quality = client.get("/api/v1/data-quality")
    assert quality.status_code == 200
    return {item["source_id"]: item for item in quality.json()["source_freshness"]}


def test_status_is_ok_through_the_static_mount_absent_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tokenhub.app as app_module

    absent_dist = tmp_path / "frontend" / "dist"
    monkeypatch.setattr(app_module, "FRONTEND_DIST", absent_dist)
    settings = TokenHubSettings(
        home_directory=tmp_path / "home", data_directory=tmp_path / "data"
    )
    with TestClient(create_app(settings), base_url="http://127.0.0.1:7432") as http:
        response = http.get("/api/v1/status")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert http.get("/").status_code == 404
    assert not absent_dist.exists()


def test_lifespan_restores_the_migration_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(DATA_DIRECTORY_ENV, raising=False)
    settings = TokenHubSettings(
        home_directory=tmp_path / "home", data_directory=tmp_path / "data"
    )
    with TestClient(create_app(settings), base_url="http://127.0.0.1:7432") as http:
        assert http.get("/api/v1/status").status_code == 200
        assert DATA_DIRECTORY_ENV not in os.environ
    assert DATA_DIRECTORY_ENV not in os.environ


def test_migration_refuses_an_explicit_database_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TOKENHUB_DATABASE_URL", "sqlite:///synthetic.sqlite3")
    settings = TokenHubSettings(
        home_directory=tmp_path / "home", data_directory=tmp_path / "data"
    )
    with (
        pytest.raises(RuntimeError, match="TOKENHUB_DATABASE_URL is not supported"),
        TestClient(create_app(settings), base_url="http://127.0.0.1:7432"),
    ):
        pass
    assert not (tmp_path / "data" / "tokenhub.sqlite3").exists()
