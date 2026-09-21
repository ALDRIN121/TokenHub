"""Static-mount behavior: optional, host-guarded, and never a write surface."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tokenhub.app import create_app
from tokenhub.settings import TokenHubSettings

from tests.api.conftest import ORIGIN


def _settings(tmp_path: Path) -> TokenHubSettings:
    return TokenHubSettings(
        home_directory=tmp_path / "home", data_directory=tmp_path / "data"
    )


def test_root_is_404_and_api_is_live_when_dist_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tokenhub.app as app_module

    monkeypatch.setattr(app_module, "FRONTEND_DIST", tmp_path / "frontend" / "dist")
    with TestClient(create_app(_settings(tmp_path)), base_url="http://127.0.0.1:7432") as http:
        assert http.get("/").status_code == 404
        assert http.get("/api/v1/status").status_code == 200


def test_root_serves_the_built_index_and_keeps_the_api_reachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tokenhub.app as app_module

    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html>TokenHub synthetic UI</html>")
    (dist / "asset.js").write_text("console.log('synthetic');\n")
    monkeypatch.setattr(app_module, "FRONTEND_DIST", dist)

    with TestClient(create_app(_settings(tmp_path)), base_url="http://127.0.0.1:7432") as http:
        root = http.get("/")
        assert root.status_code == 200
        assert "TokenHub synthetic UI" in root.text
        assert http.get("/asset.js").status_code == 200
        # A loopback host with the configured port still reaches the API surface.
        assert http.get("/api/v1/status", headers={"host": "localhost:7432"}).status_code == 200
        assert http.get("/api/v1/status", headers={"host": "[::1]:7432"}).status_code == 200
        # An unknown API path is a plain 404, not the index page.
        assert http.get("/api/v1/nope").status_code == 404


def test_hostile_host_is_blocked_on_the_static_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tokenhub.app as app_module

    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html>TokenHub synthetic UI</html>")
    monkeypatch.setattr(app_module, "FRONTEND_DIST", dist)

    with TestClient(create_app(_settings(tmp_path)), base_url="http://127.0.0.1:7432") as http:
        hostile = http.get("/", headers={"host": "attacker.example"})
        assert hostile.status_code == 400
        assert hostile.json() == {"detail": "Invalid Host header"}
        assert "TokenHub synthetic UI" not in hostile.text


def test_static_mount_does_not_accept_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tokenhub.app as app_module

    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html>TokenHub synthetic UI</html>")
    monkeypatch.setattr(app_module, "FRONTEND_DIST", dist)

    with TestClient(create_app(_settings(tmp_path)), base_url="http://127.0.0.1:7432") as http:
        # A non-read method still needs the same origin, and the mount rejects it.
        assert http.post("/", headers=ORIGIN).status_code == 405
        assert http.post("/", headers={"origin": "http://attacker"}).status_code == 403
