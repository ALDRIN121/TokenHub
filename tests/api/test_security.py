import builtins
import io
import os
import socket
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import ORIGIN, codex_id


@pytest.mark.parametrize("host", ["localhost", "localhost:7432", "127.0.0.1", "127.0.0.1:7432", "[::1]", "[::1]:7432"])
def test_loopback_hosts_are_allowed(client: TestClient, host: str) -> None:
    assert client.get("/api/v1/status", headers={"host": host}).status_code == 200


@pytest.mark.parametrize("host", [
    "attacker.example", "localhost.attacker.example", "localhost.", "localhost@attacker.example",
    "attacker@localhost", "http://localhost", "127.1", "2130706433", "::1", "[::1]evil",
    "localhost:7432:99", "localhost:", "localhost:0007432", "localhost:0", "localhost:65536",
    "localhost:8000", " localhost", "localhost ", "localhost/path", "localhost?x", "localhost#x",
    "localhost,attacker", "localhost\r\nx-evil: yes", "", "[::1%lo0]", "LOCALHOST",
])
def test_rejects_confusing_host_forms(client: TestClient, host: str) -> None:
    response = client.get("/api/v1/status", headers={"host": host})
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid Host header"}


def test_duplicate_host_and_origin_are_rejected(client: TestClient) -> None:
    assert client.get("/api/v1/status", headers=[("host", "localhost"), ("host", "attacker")]).status_code == 400
    assert client.post("/api/v1/rebuild", headers=[("origin", "http://127.0.0.1:7432"), ("origin", "http://attacker")]).status_code == 403


@pytest.mark.parametrize("origin", [None, "null", "http://attacker.example", "http://localhost:7432", "https://127.0.0.1:7432", "http://127.0.0.1:8000", "http://127.0.0.1:7432/", "http://user@127.0.0.1:7432", "http://127.0.0.1:7432.attacker", "http://127.0.0.1:7432\r\nx: y"])
def test_origin_rejected_before_approval_or_import(client: TestClient, origin: str | None) -> None:
    source_id = codex_id(client)
    headers = {} if origin is None else {"origin": origin}
    for action in ("approve", "rescan"):
        assert client.post(f"/api/v1/sources/{source_id}/{action}", headers=headers).status_code == 403
    assert client.post("/api/v1/rebuild", headers=headers).status_code == 403
    assert client.post(f"/api/v1/sources/{source_id}/rescan", headers=ORIGIN).status_code == 409
    assert client.get("/api/v1/dashboard").json()["event_count"] == 0


def test_rejected_host_cannot_approve_and_rejected_rebuild_cannot_clear(client: TestClient) -> None:
    source_id = codex_id(client)
    endpoint = f"/api/v1/sources/{source_id}"
    assert client.post(f"{endpoint}/approve", headers={**ORIGIN, "host": "attacker"}).status_code == 400
    assert client.post(f"{endpoint}/rescan", headers=ORIGIN).status_code == 409
    assert client.post(f"{endpoint}/approve", headers=ORIGIN).status_code == 200
    assert client.post(f"{endpoint}/rescan", headers=ORIGIN).status_code == 200
    assert client.post("/api/v1/rebuild").status_code == 403
    assert client.post("/api/v1/rebuild", headers={**ORIGIN, "host": "attacker"}).status_code == 400
    assert client.get("/api/v1/dashboard").json()["workload_tokens"] == 125


def test_discovery_and_approval_never_read_telemetry_or_credentials(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_open = builtins.open
    original_io_open = io.open
    original_os_open = os.open
    provider_home = str(tmp_path / "home")

    def check_path(path: object) -> None:
        if isinstance(path, (str, bytes, os.PathLike)) and provider_home in os.fsdecode(path):
            pytest.fail("provider content opened before approved rescan")

    def checked_open(path, *args, **kwargs):
        check_path(path)
        return original_open(path, *args, **kwargs)

    def checked_io_open(path, *args, **kwargs):
        check_path(path)
        return original_io_open(path, *args, **kwargs)

    def directory_open(path, flags, *args, **kwargs):
        # Discovery may open only directories. Approval validates a leaf but never reads it.
        if (os.name != "nt" and not flags & os.O_DIRECTORY
                and not str(path).startswith(str(tmp_path / "data") + os.sep)):
            pytest.fail("discovery opened a telemetry leaf")
        return original_os_open(path, flags, *args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("provider executable or network invoked")

    monkeypatch.setattr(builtins, "open", checked_open)
    monkeypatch.setattr(io, "open", checked_io_open)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(os, "open", directory_open)
    source_id = codex_id(client)
    assert client.get("/api/v1/dashboard").json()["event_count"] == 0
    assert client.post(f"/api/v1/sources/{source_id}/rescan", headers=ORIGIN).status_code == 409
    monkeypatch.setattr(os, "open", original_os_open)
    assert client.post(f"/api/v1/sources/{source_id}/approve", headers=ORIGIN).status_code == 200
    assert client.get("/api/v1/dashboard").json()["event_count"] == 0


def test_no_permissive_cors_or_forwarded_origin(client: TestClient) -> None:
    response = client.get("/api/v1/status", headers={"origin": "http://attacker"})
    assert "access-control-allow-origin" not in response.headers
    assert client.post("/api/v1/rebuild", headers={"origin": "https://attacker", "x-forwarded-host": "attacker", "x-forwarded-proto": "https"}).status_code == 403
