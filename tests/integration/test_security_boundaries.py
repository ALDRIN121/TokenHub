"""Security-boundary integration tests for presence-only discovery.

The point of these tests is to prove, at the real filesystem boundary, that a
discovery pass over a synthetic home never opens a discovery-only provider path
(Claude Code transcripts/config, the Hermes state database) and never reads
provider credentials or unpacks an archive.

Instrumentation is installed on the narrowest possible primitives
(``builtins.open``, ``io.open``, ``Path.open``/``read_bytes``/``read_text``,
``os.open``, ``zipfile.ZipFile``, ``tarfile.open``) and any access to a watched
path both records the event and fails immediately. Everything is synthetic and
lives under ``tmp_path``.
"""

from __future__ import annotations

import builtins
import io
import json
import os
import tarfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tokenhub.app import create_app
from tokenhub.connectors.protocol import DiscoveryContext
from tokenhub.settings import TokenHubSettings

from tests.service_support import token_record

BASE_URL = "http://127.0.0.1:7432"
HOST_HEADERS = {"host": "127.0.0.1:7432"}
ORIGIN_HEADERS = {"host": "127.0.0.1:7432", "origin": BASE_URL}

# Deliberately not an ``sk-`` value: discovery must never surface it verbatim,
# and a non-redactable marker means the assertion cannot be satisfied by the
# error-body redactor hiding a leak.
SYNTHETIC_CODEX_SECRET = "SYNTHETIC_CODEX_CREDENTIAL_0001"
HERMES_DATABASE_BYTES = b"SQLite format 3\x00 hermes synthetic private database"


@dataclass(frozen=True)
class SyntheticHome:
    """A fake provider home; every path is under ``tmp_path``."""

    root: Path
    claude_root: Path
    hermes_root: Path
    hermes_database: Path
    codex_session: Path
    codex_credentials: Path


def _build_home(tmp_path: Path) -> SyntheticHome:
    home = tmp_path / "home"

    codex_session = home / ".codex" / "sessions" / "synthetic.jsonl"
    codex_session.parent.mkdir(parents=True)
    codex_session.write_bytes(token_record(1, input_tokens=100, output_tokens=25))
    codex_credentials = home / ".codex" / "auth.json"
    codex_credentials.write_text(
        json.dumps({"openai_api_key": SYNTHETIC_CODEX_SECRET, "note": "synthetic"})
    )

    claude_root = home / ".claude"
    (claude_root / "projects" / "synthetic").mkdir(parents=True)
    (claude_root / "settings.json").write_text('{"synthetic": true}')
    (claude_root / "projects" / "synthetic" / "transcript.jsonl").write_bytes(
        b'{"type":"assistant","content":"synthetic claude transcript"}\n'
    )

    hermes_root = home / ".hermes"
    hermes_root.mkdir(parents=True)
    hermes_database = hermes_root / "state.db"
    hermes_database.write_bytes(HERMES_DATABASE_BYTES)

    return SyntheticHome(
        root=home,
        claude_root=claude_root,
        hermes_root=hermes_root,
        hermes_database=hermes_database,
        codex_session=codex_session,
        codex_credentials=codex_credentials,
    )


def _client(tmp_path: Path, home: SyntheticHome) -> TestClient:
    app: FastAPI = create_app(
        TokenHubSettings(home_directory=home.root, data_directory=tmp_path / "data")
    )
    app.state.container.discovery_context = DiscoveryContext(home.root, {}, lambda _: None)
    return TestClient(app, base_url=BASE_URL)


@contextmanager
def _read_guard(watched: dict[str, Path]) -> Iterator[list[str]]:
    """Record and fail any open/read of a watched path (or an archive parse).

    ``watched`` maps a label to an absolute path; both the path itself and any
    descendant of it are guarded. Relative descriptor-relative opens carry only
    a leaf name, so the leaf name is matched too.
    """
    reads: list[str] = []

    def _match(path: object) -> str | None:
        try:
            decoded = os.fsdecode(path)  # type: ignore[arg-type]
        except TypeError:
            return None
        candidate = Path(decoded)
        for label, anchor in watched.items():
            if candidate == anchor or anchor in candidate.parents:
                return f"{label}:{candidate}"
        for label, anchor in watched.items():
            if candidate.name == anchor.name:
                return f"{label}:{candidate.name}"
        return None

    def _record(path: object) -> None:
        matched = _match(path)
        if matched is not None:
            reads.append(matched)
            raise AssertionError(f"discovery opened a discovery-only path: {matched}")

    real_builtins_open = builtins.open
    real_io_open = io.open
    real_os_open = os.open
    real_path_open = Path.open
    real_read_bytes = Path.read_bytes
    real_read_text = Path.read_text
    real_zipfile = zipfile.ZipFile
    real_tarfile_open = tarfile.open

    def guarded_builtins(path: object, *args: object, **kwargs: object):
        _record(path)
        return real_builtins_open(path, *args, **kwargs)

    def guarded_io(path: object, *args: object, **kwargs: object):
        _record(path)
        return real_io_open(path, *args, **kwargs)

    def guarded_path_open(self: Path, *args: object, **kwargs: object):
        _record(self)
        return real_path_open(self, *args, **kwargs)

    def guarded_read_bytes(self: Path) -> bytes:
        _record(self)
        return real_read_bytes(self)

    def guarded_read_text(self: Path, *args: object, **kwargs: object) -> str:
        _record(self)
        return real_read_text(self, *args, **kwargs)

    def guarded_os_open(path: object, flags: int, *args: object, **kwargs: object):
        if os.name != "nt" and not flags & os.O_DIRECTORY:
            _record(path)
        return real_os_open(path, flags, *args, **kwargs)

    def guarded_zipfile(file: object, *args: object, **kwargs: object):
        _record(file)
        return real_zipfile(file, *args, **kwargs)

    def guarded_tarfile(name: object, *args: object, **kwargs: object):
        _record(name)
        return real_tarfile_open(name, *args, **kwargs)

    with (
        patch.object(builtins, "open", guarded_builtins),
        patch.object(io, "open", guarded_io),
        patch.object(Path, "open", guarded_path_open),
        patch.object(Path, "read_bytes", guarded_read_bytes),
        patch.object(Path, "read_text", guarded_read_text),
        patch.object(os, "open", guarded_os_open),
        patch.object(zipfile, "ZipFile", guarded_zipfile),
        patch.object(tarfile, "open", guarded_tarfile),
    ):
        yield reads


def _assert_guard_is_armed(watched: dict[str, Path], target: Path) -> None:
    """Prove the instrumentation is not vacuous: opening ``target`` must fail."""
    with _read_guard(watched) as probe, pytest.raises(AssertionError):
        target.read_bytes()
    assert probe, "read guard did not record a deliberate probe read"


@pytest.mark.integration
def test_claude_and_hermes_are_never_opened_during_discovery(tmp_path: Path) -> None:
    """(H) Discovery over the HTTP route opens neither provider path."""
    home = _build_home(tmp_path)
    watched = {
        "claude_root": home.claude_root,
        "hermes_state_db": home.hermes_database,
    }
    _assert_guard_is_armed(watched, home.hermes_database)

    with _client(tmp_path, home) as client:
        with _read_guard(watched) as reads:
            discovery = client.get("/api/v1/discovery", headers=HOST_HEADERS)
        assert discovery.status_code == 200
        assert reads == []

        providers = {
            provider["connector_id"]: provider
            for provider in discovery.json()["providers"]
        }
        assert providers["claude-code-local"]["state"] == "discovered"
        assert providers["hermes-local"]["state"] == "discovered"
        hermes_sources = providers["hermes-local"]["sources"]
        assert hermes_sources
        assert hermes_sources[0]["source_type"] == "sqlite"
        assert hermes_sources[0]["scan_supported"] is True
        # Presence is reported; contents, names, and paths are not.
        assert str(tmp_path) not in discovery.text
        assert "SQLite format 3" not in discovery.text
        assert "state.db" not in discovery.text
        assert "settings.json" not in discovery.text
        assert "transcript" not in discovery.text


@pytest.mark.integration
def test_discovery_and_ingestion_never_read_provider_credentials(tmp_path: Path) -> None:
    """(H) The synthetic ``auth.json`` is never opened and never surfaces."""
    home = _build_home(tmp_path)
    watched = {"codex_credentials": home.codex_credentials}
    _assert_guard_is_armed(watched, home.codex_credentials)

    with _client(tmp_path, home) as client:
        with _read_guard(watched) as reads:
            discovery = client.get("/api/v1/discovery", headers=HOST_HEADERS)
            source_id = next(
                provider["sources"][0]["source_id"]
                for provider in discovery.json()["providers"]
                if provider["connector_id"] == "codex-local"
            )
            endpoint = f"/api/v1/sources/{source_id}"
            responses = [
                discovery,
                client.post(f"{endpoint}/approve", headers=ORIGIN_HEADERS),
                client.post(f"{endpoint}/rescan", headers=ORIGIN_HEADERS),
                client.post("/api/v1/rebuild", headers=ORIGIN_HEADERS),
                client.get("/api/v1/dashboard", headers=HOST_HEADERS),
                client.get("/api/v1/data-quality", headers=HOST_HEADERS),
                # The credential must not leak through an error path either.
                client.post(
                    f"/api/v1/sources/{SYNTHETIC_CODEX_SECRET}/rescan",
                    headers=ORIGIN_HEADERS,
                ),
            ]
        assert reads == []
        assert responses[0].status_code == 200
        assert responses[1].status_code == 200
        assert responses[2].status_code == 200
        assert responses[3].status_code == 200
        assert responses[6].status_code == 404
        for response in responses:
            assert SYNTHETIC_CODEX_SECRET not in response.text
            assert "auth.json" not in response.text
            assert str(tmp_path) not in response.text
        # The credential file is not even offered as a scannable source.
        codex = next(
            provider
            for provider in discovery.json()["providers"]
            if provider["connector_id"] == "codex-local"
        )
        assert [source["source_type"] for source in codex["sources"]] == ["jsonl"]


@pytest.mark.integration
def test_hermes_state_database_is_never_unpacked_or_surfaced(tmp_path: Path) -> None:
    """(H) An archive-shaped state.db is still never opened or extracted."""
    home = _build_home(tmp_path)
    # Make the private database a real ZIP so any archive reader would be caught.
    with zipfile.ZipFile(home.hermes_database, "w") as archive:
        archive.writestr(
            "payload.json", json.dumps({"token": SYNTHETIC_CODEX_SECRET})
        )

    watched = {"hermes_state_db": home.hermes_database}
    with _client(tmp_path, home) as client:
        with _read_guard(watched) as reads:
            discovery = client.get("/api/v1/discovery", headers=HOST_HEADERS)
            quality = client.get("/api/v1/data-quality", headers=HOST_HEADERS)
        assert discovery.status_code == 200
        assert quality.status_code == 200
        assert reads == []
        for response in (discovery, quality):
            assert "payload.json" not in response.text
            assert SYNTHETIC_CODEX_SECRET not in response.text
            assert str(tmp_path) not in response.text

        # An unapproved database cannot be read, even when disguised as an archive.
        hermes_id = next(
            provider["sources"][0]["source_id"]
            for provider in discovery.json()["providers"]
            if provider["connector_id"] == "hermes-local"
        )
        with _read_guard(watched) as reads:
            response = client.post(f"/api/v1/sources/{hermes_id}/rescan", headers=ORIGIN_HEADERS)
        assert response.status_code == 409
        assert reads == []
        assert client.post(f"/api/v1/sources/{hermes_id}/approve", headers=ORIGIN_HEADERS).status_code == 200
        with (
            patch.object(zipfile, "ZipFile", side_effect=AssertionError("must not unpack archives")),
            patch.object(tarfile, "open", side_effect=AssertionError("must not unpack archives")),
        ):
            response = client.post(f"/api/v1/sources/{hermes_id}/rescan", headers=ORIGIN_HEADERS)
        assert response.status_code == 400
        assert SYNTHETIC_CODEX_SECRET not in response.text
        assert not list(tmp_path.rglob("payload.json"))
