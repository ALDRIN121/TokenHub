"""A background launcher can identify and stop only its own local server."""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from tokenhub.app import create_app
from tokenhub.settings import TokenHubSettings

TOKEN = "a" * 64
CHALLENGE = "b" * 64
ORIGIN = "http://127.0.0.1:7432"


def _expected_proof(message: str) -> str:
    return hmac.new(bytes.fromhex(TOKEN), message.encode(), hashlib.sha256).hexdigest()


def _settings(tmp_path: Path) -> TokenHubSettings:
    return TokenHubSettings(home_directory=tmp_path / "home", data_directory=tmp_path / "data")


def test_runtime_ready_proves_instance_identity_without_revealing_token(tmp_path: Path) -> None:
    stops: list[str] = []
    control = SimpleNamespace(token=TOKEN, shutdown=lambda: stops.append("stop"))
    with TestClient(create_app(_settings(tmp_path), runtime_control=control), base_url=ORIGIN) as http:
        ready = http.get(
            "/api/v1/_runtime/ready", headers={"x-tokenhub-challenge": CHALLENGE}
        )
        assert ready.status_code == 200
        assert ready.json() == {"proof": _expected_proof(f"ready:{CHALLENGE}")}
        assert TOKEN not in ready.text
        assert stops == []


def test_runtime_ready_rejects_missing_and_duplicate_challenges(tmp_path: Path) -> None:
    control = SimpleNamespace(token=TOKEN, shutdown=lambda: None)
    with TestClient(create_app(_settings(tmp_path), runtime_control=control), base_url=ORIGIN) as http:
        assert http.get("/api/v1/_runtime/ready").status_code == 404
        duplicate = http.get(
            "/api/v1/_runtime/ready",
            headers=[("x-tokenhub-challenge", CHALLENGE), ("x-tokenhub-challenge", "c" * 64)],
        )
        assert duplicate.status_code == 404


def test_runtime_stop_requires_same_origin_and_separate_proof(tmp_path: Path) -> None:
    stops: list[str] = []
    control = SimpleNamespace(token=TOKEN, shutdown=lambda: stops.append("stop"))
    headers = {
        "origin": ORIGIN,
        "x-tokenhub-nonce": CHALLENGE,
        "x-tokenhub-proof": _expected_proof(f"stop:{CHALLENGE}"),
    }
    with TestClient(create_app(_settings(tmp_path), runtime_control=control), base_url=ORIGIN) as http:
        assert http.post(
            "/api/v1/_runtime/stop", headers={**headers, "origin": "http://evil.example"}
        ).status_code == 403
        assert http.post(
            "/api/v1/_runtime/stop",
            headers={**headers, "x-tokenhub-proof": _expected_proof(f"ready:{CHALLENGE}")},
        ).status_code == 404
        assert http.post("/api/v1/_runtime/stop", headers=headers).status_code == 202
        assert stops == ["stop"]


def test_normal_app_has_no_runtime_control_routes(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path)), base_url=ORIGIN) as http:
        assert http.get(
            "/api/v1/_runtime/ready", headers={"x-tokenhub-challenge": CHALLENGE}
        ).status_code == 404
