"""Route error mapping and body-redaction tests.

Every service exception class must reach a defined status code, and no error or
success body may echo a filesystem path, schema column, raw record, credential,
or a client-supplied forwarding header.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from tokenhub.api.routes import _service_failure
from tokenhub.ingestion.service import (
    SourceNotApprovedError,
    SourceNotFoundError,
    UnsupportedSourceError,
)

from tests.api.conftest import ORIGIN, codex_id

_MAPPINGS: tuple[tuple[Exception, int, str], ...] = (
    (SourceNotFoundError("source has not been discovered"), 404, "Unknown source"),
    (SourceNotApprovedError("source must be approved"), 409, "Source is not approved"),
    (UnsupportedSourceError("source is discovery-only"), 422, "Source is not supported"),
    (ValueError("bad path spelling"), 400, "Invalid source path"),
    (OSError("source file vanished"), 400, "Source is unavailable"),
    (RuntimeError("unexpected"), 500, "Internal error"),
)


@pytest.mark.parametrize(("error", "status", "detail"), _MAPPINGS)
def test_service_exception_status_mapping(error: Exception, status: int, detail: str) -> None:
    mapped = _service_failure(error)
    assert isinstance(mapped, HTTPException)
    assert mapped.status_code == status
    assert mapped.detail == detail
    # The mapped message is a fixed, path-free string, never the exception text.
    assert str(error) not in detail


def discovery_source_id(client: TestClient, connector_id: str) -> str:
    response = client.get("/api/v1/discovery")
    assert response.status_code == 200
    for provider in response.json()["providers"]:
        if provider["connector_id"] == connector_id:
            assert provider["sources"], f"{connector_id} reported no sources"
            return provider["sources"][0]["source_id"]
    raise AssertionError(f"{connector_id} was not discovered")


@pytest.mark.parametrize("action", ["approve", "rescan"])
def test_unknown_source_id_maps_to_404_without_echoing_the_id(
    client: TestClient, action: str
) -> None:
    sentinel = "unknown-source-sentinel-9f3a7c"
    response = client.post(f"/api/v1/sources/{sentinel}/{action}", headers=ORIGIN)
    assert response.status_code == 404
    assert response.json() == {"detail": "Unknown source"}
    assert sentinel not in response.text


@pytest.mark.parametrize("action", ["approve", "rescan"])
def test_discovery_only_provider_maps_to_422(client: TestClient, action: str) -> None:
    source_id = discovery_source_id(client, "hermes-local")
    response = client.post(f"/api/v1/sources/{source_id}/{action}", headers=ORIGIN)
    assert response.status_code == 422
    assert response.json() == {"detail": "Source is not supported"}


def test_unapproved_rescan_maps_to_409(client: TestClient) -> None:
    source_id = codex_id(client)
    response = client.post(f"/api/v1/sources/{source_id}/rescan", headers=ORIGIN)
    assert response.status_code == 409
    assert response.json() == {"detail": "Source is not approved"}


def test_symlink_escaped_approve_maps_to_400_without_leaking_a_path(
    client: TestClient, tmp_path: Path
) -> None:
    source_id = codex_id(client)
    source = tmp_path / "home" / ".codex" / "sessions" / "synthetic.jsonl"
    source.unlink()
    source.symlink_to(tmp_path / "outside-secret-value.jsonl")
    response = client.post(f"/api/v1/sources/{source_id}/approve", headers=ORIGIN)
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid source path"}
    assert str(tmp_path) not in response.text
    assert "outside-secret-value" not in response.text


def test_approved_file_that_disappears_before_rescan_maps_to_400(
    client: TestClient, tmp_path: Path
) -> None:
    source_id = codex_id(client)
    approved = client.post(f"/api/v1/sources/{source_id}/approve", headers=ORIGIN)
    assert approved.status_code == 200
    (tmp_path / "home" / ".codex" / "sessions" / "synthetic.jsonl").unlink()
    response = client.post(f"/api/v1/sources/{source_id}/rescan", headers=ORIGIN)
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid source path"}
    assert str(tmp_path) not in response.text


def test_no_body_echoes_paths_columns_records_or_forwarded_host(
    client: TestClient, tmp_path: Path
) -> None:
    forwarded = "evil-forwarded-host.invalid-sentinel"
    headers = {**ORIGIN, "x-forwarded-host": forwarded, "x-forwarded-proto": "https"}
    source_id = codex_id(client)
    hermes_id = discovery_source_id(client, "hermes-local")

    responses = [
        client.post("/api/v1/sources/unknown-source-sentinel/approve", headers=headers),
        client.post("/api/v1/sources/unknown-source-sentinel/rescan", headers=headers),
        client.post(f"/api/v1/sources/{hermes_id}/approve", headers=headers),
        client.post(f"/api/v1/sources/{hermes_id}/rescan", headers=headers),
        client.post(f"/api/v1/sources/{source_id}/rescan", headers=headers),
        client.post(f"/api/v1/sources/{source_id}/approve", headers=headers),
        client.post(f"/api/v1/sources/{source_id}/rescan", headers=headers),
        client.get("/api/v1/discovery"),
        client.get("/api/v1/dashboard"),
        client.get("/api/v1/data-quality"),
    ]
    assert [response.status_code for response in responses] == [
        404, 404, 422, 422, 409, 200, 200, 200, 200, 200,
    ]

    forbidden = (
        str(tmp_path),
        "canonical_path",
        "approved_root",
        "payload",
        "auth.json",
        "sk-syn...cret",
        forwarded,
    )
    for response in responses:
        for value in forbidden:
            assert value not in response.text, f"{value!r} leaked in {response.request.url}"


def test_unknown_service_failure_becomes_a_generic_500(
    client: TestClient, api_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unexpected exception is mapped at the route, not leaked to the client."""
    secret = "RuntimeError: /home/synthetic-provider/.codex/sessions sk-live-secret"
    source_id = codex_id(client)

    def explode(_source_id: str) -> object:
        raise RuntimeError(secret)

    monkeypatch.setattr(api_app.state.container.services.ingestion, "approve", explode)
    response = client.post(f"/api/v1/sources/{source_id}/approve", headers=ORIGIN)

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal error"}
    for value in (secret, "/home/synthetic-provider", "sk-live-secret", "RuntimeError"):
        assert value not in response.text
