"""End-to-end discovery -> approval -> import -> rebuild over the real app stack.

Everything here runs through a live ``create_app`` + ``TestClient``: real
Alembic migrations, real SQLite database, real HTTP guards, and real services.
Only the provider home is synthetic. No network, subprocess, or real provider
path is ever touched, and every path lives under ``tmp_path``.
"""

from __future__ import annotations

import builtins
import io
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
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

# Values that must never appear in an HTTP response body.
_PRIVATE_MARKERS = (
    "canonical_path",
    "approved_root",
    "payload",
    "auth.json",
    "sk-",
)


def _synthetic_home(tmp_path: Path) -> tuple[Path, Path]:
    """A fake home with one Codex session and presence-only siblings."""
    home = tmp_path / "home"
    session_file = home / ".codex" / "sessions" / "synthetic.jsonl"
    session_file.parent.mkdir(parents=True)
    session_file.write_bytes(token_record(1, input_tokens=100, output_tokens=25))
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text('{"synthetic": true}')
    (home / ".hermes").mkdir(parents=True)
    (home / ".hermes" / "state.db").write_bytes(
        b"SQLite format 3\x00 synthetic integration database"
    )
    (home / ".codex" / "auth.json").write_text(
        json.dumps({"openai_api_key": "sk-integration-synthetic-secret"})
    )
    return home, session_file


def _app_for(tmp_path: Path, home: Path) -> FastAPI:
    app = create_app(
        TokenHubSettings(home_directory=home, data_directory=tmp_path / "data")
    )
    app.state.container.discovery_context = DiscoveryContext(home, {}, lambda _: None)
    return app


def _codex_source_id(discovery: dict[str, object]) -> str:
    providers = discovery["providers"]
    assert isinstance(providers, list)
    codex = next(
        provider
        for provider in providers
        if provider["connector_id"] == "codex-local"
    )
    return codex["sources"][0]["source_id"]


def _source_view(discovery: dict[str, object], source_id: str) -> dict[str, object]:
    providers = discovery["providers"]
    assert isinstance(providers, list)
    for provider in providers:
        for source in provider["sources"]:
            if source["source_id"] == source_id:
                return source
    raise AssertionError(f"source {source_id} missing from discovery response")


@contextmanager
def _no_source_reads(session_file: Path) -> Iterator[list[str]]:
    """Fail if the watched source file is opened while this block runs.

    Guards the whole content-read boundary (``builtins.open``, ``io.open``,
    ``Path.open``, and ``os.open`` for non-directory leaves) so an import that
    happened before approval cannot go unnoticed.
    """
    reads: list[str] = []
    target_name = session_file.name
    real_builtins_open = builtins.open
    real_io_open = io.open
    real_os_open = os.open
    real_path_open = Path.open

    def _note(path: object) -> None:
        try:
            decoded = os.fsdecode(path)  # type: ignore[arg-type]
        except TypeError:
            return
        if decoded == str(session_file) or Path(decoded).name == target_name:
            reads.append(decoded)
            raise AssertionError(f"source file was read: {decoded}")

    def guarded_builtins(path: object, *args: object, **kwargs: object):
        _note(path)
        return real_builtins_open(path, *args, **kwargs)

    def guarded_io(path: object, *args: object, **kwargs: object):
        _note(path)
        return real_io_open(path, *args, **kwargs)

    def guarded_path_open(self: Path, *args: object, **kwargs: object):
        _note(self)
        return real_path_open(self, *args, **kwargs)

    def guarded_os_open(path: object, flags: int, *args: object, **kwargs: object):
        if not flags & os.O_DIRECTORY:
            _note(path)
        return real_os_open(path, flags, *args, **kwargs)

    with (
        patch.object(builtins, "open", guarded_builtins),
        patch.object(io, "open", guarded_io),
        patch.object(Path, "open", guarded_path_open),
        patch.object(os, "open", guarded_os_open),
    ):
        yield reads


def _assert_no_private_material(response_text: str, tmp_path: Path) -> None:
    assert str(tmp_path) not in response_text
    for marker in _PRIVATE_MARKERS:
        assert marker not in response_text


@pytest.mark.integration
def test_discovery_approval_import_append_and_rebuild(tmp_path: Path) -> None:
    """The product's core flow under a real app stack (assertions A-G)."""
    home, session_file = _synthetic_home(tmp_path)
    app = _app_for(tmp_path, home)

    with TestClient(app, base_url=BASE_URL) as client:
        # (B) Pre-approval discovery: no path, no raw record, no read.
        with _no_source_reads(session_file) as reads:
            discovery_response = client.get("/api/v1/discovery", headers=HOST_HEADERS)
        assert discovery_response.status_code == 200
        assert reads == []
        _assert_no_private_material(discovery_response.text, tmp_path)
        discovery = discovery_response.json()
        source_id = _codex_source_id(discovery)
        endpoint = f"/api/v1/sources/{source_id}"
        assert _source_view(discovery, source_id)["state"] == "discovered"
        pre_approval = client.get("/api/v1/dashboard", headers=HOST_HEADERS).json()
        assert pre_approval["event_count"] == 0
        assert pre_approval["workload_tokens"] is None

        # (C) A mutation without Origin is refused before it can change state.
        rejected = client.post(f"{endpoint}/approve", headers=HOST_HEADERS)
        assert rejected.status_code == 403
        assert rejected.json() == {"detail": "Invalid Origin header"}
        unchanged = client.get("/api/v1/discovery", headers=HOST_HEADERS).json()
        assert _source_view(unchanged, source_id)["state"] == "discovered"
        assert client.get("/api/v1/dashboard", headers=HOST_HEADERS).json()["event_count"] == 0

        # (C) Same-origin approval, then the first import totals 125 workload.
        approved = client.post(f"{endpoint}/approve", headers=ORIGIN_HEADERS)
        assert approved.status_code == 200
        assert approved.json()["state"] == "approved"
        first_scan = client.post(f"{endpoint}/rescan", headers=ORIGIN_HEADERS)
        assert first_scan.status_code == 200
        assert first_scan.json()["inserted_events"] == 1
        first_summary = client.get("/api/v1/dashboard", headers=HOST_HEADERS).json()
        assert first_summary["workload_tokens"] == 125

        # (E) Repeat rescan with no new bytes inserts nothing.
        repeated = client.post(f"{endpoint}/rescan", headers=ORIGIN_HEADERS)
        assert repeated.status_code == 200
        assert repeated.json()["inserted_events"] == 0
        assert client.get("/api/v1/dashboard", headers=HOST_HEADERS).json()["workload_tokens"] == 125

        # (D) Append 10 + 5 = 15 workload tokens; the total becomes 140.
        with session_file.open("ab") as stream:
            stream.write(token_record(2, input_tokens=10, output_tokens=5))
        appended = client.post(f"{endpoint}/rescan", headers=ORIGIN_HEADERS)
        assert appended.status_code == 200
        assert appended.json()["inserted_events"] == 1
        grown = client.get("/api/v1/dashboard", headers=HOST_HEADERS).json()
        assert grown["workload_tokens"] == 140

        # (G) Rebuild is idempotent for totals.
        rebuilt = client.post("/api/v1/rebuild", headers=ORIGIN_HEADERS)
        assert rebuilt.status_code == 200
        assert rebuilt.json()["inserted_events"] == 2
        assert rebuilt.json()["failed_source_ids"] == []
        final = client.get("/api/v1/dashboard", headers=HOST_HEADERS).json()
        assert final["workload_tokens"] == 140
        assert final["event_count"] == 2
        _assert_no_private_material(rebuilt.text, tmp_path)


@pytest.mark.integration
def test_partial_final_line_is_deferred_until_completed(tmp_path: Path) -> None:
    """(F) A truncated final line imports nothing until it is completed."""
    home, session_file = _synthetic_home(tmp_path)
    app = _app_for(tmp_path, home)

    with TestClient(app, base_url=BASE_URL) as client:
        discovery = client.get("/api/v1/discovery", headers=HOST_HEADERS).json()
        source_id = _codex_source_id(discovery)
        endpoint = f"/api/v1/sources/{source_id}"
        assert client.post(f"{endpoint}/approve", headers=ORIGIN_HEADERS).status_code == 200
        assert client.post(f"{endpoint}/rescan", headers=ORIGIN_HEADERS).json()["inserted_events"] == 1
        assert client.get("/api/v1/dashboard", headers=HOST_HEADERS).json()["workload_tokens"] == 125

        complete_record = token_record(3, input_tokens=7, output_tokens=3)
        split = len(complete_record) // 2

        # Write a truncated JSON line with no trailing newline.
        with session_file.open("ab") as stream:
            stream.write(complete_record[:split])

        partial = client.post(f"{endpoint}/rescan", headers=ORIGIN_HEADERS)
        assert partial.status_code == 200
        assert partial.json()["inserted_events"] == 0
        assert partial.json()["partial_final_record"] is True
        quality = client.get("/api/v1/data-quality", headers=HOST_HEADERS).json()
        freshness = next(
            item for item in quality["source_freshness"] if item["source_id"] == source_id
        )
        assert freshness["state"] == "partial"
        assert client.get("/api/v1/dashboard", headers=HOST_HEADERS).json()["workload_tokens"] == 125

        # Complete the line; the next rescan imports it and the state clears.
        with session_file.open("ab") as stream:
            stream.write(complete_record[split:])

        completed = client.post(f"{endpoint}/rescan", headers=ORIGIN_HEADERS)
        assert completed.status_code == 200
        assert completed.json()["inserted_events"] == 1
        assert completed.json()["partial_final_record"] is False
        settled = client.get("/api/v1/dashboard", headers=HOST_HEADERS).json()
        assert settled["workload_tokens"] == 135
