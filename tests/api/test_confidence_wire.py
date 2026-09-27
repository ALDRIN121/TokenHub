"""Wire contract for the discovery ``confidence`` field.

Complements ``tests/api/test_discovery_confidence.py``: this file pins the
*shape* of the field on the real HTTP surface — a bare lowercase string that
round-trips through the ``Confidence`` enum — and proves it appears on the
discovery payload and nowhere else, in particular never in a 403/404 error body.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tokenhub.domain.models import Confidence

# `client` and `api_app` are the shared fixtures from the sibling
# ``tests/api/conftest.py``; pytest injects them by name (the repo idiom in
# ``test_routes.py`` / ``test_error_mapping.py``), so they are reused, not
# redeclared. Importing the fixture objects themselves is shadowed by the test
# signatures and trips ruff F811.
from tests.api.conftest import ORIGIN

_WIRE_LEVELS = frozenset({"high", "medium", "low"})


def _providers(http: TestClient) -> list[dict[str, object]]:
    response = http.get("/api/v1/discovery")
    assert response.status_code == 200
    return response.json()["providers"]


def test_confidence_is_a_bare_lowercase_json_string(client: TestClient) -> None:
    """Fails if the value is an enum repr, a capitalized label, or null."""
    response = client.get("/api/v1/discovery")
    assert response.status_code == 200

    providers = response.json()["providers"]
    assert providers
    observed: set[object] = set()
    for provider in providers:
        value = provider["confidence"]
        assert type(value) is str
        assert value in _WIRE_LEVELS
        observed.add(value)

    # The raw body carries the compact literal for every level it reports.
    assert observed
    for level in observed:
        assert f'"confidence":"{level}"' in response.text


def test_confidence_never_serializes_as_an_enum_repr(client: TestClient) -> None:
    """Fails if any enum-only spelling appears in the raw discovery body."""
    response = client.get("/api/v1/discovery")

    for forbidden in (
        "Confidence.HIGH",
        "Confidence.MEDIUM",
        "Confidence.LOW",
        "<Confidence",
        '"confidence":null',
        '"confidence":None',
        '"confidence":"HIGH"',
        '"confidence":"Medium"',
    ):
        assert forbidden not in response.text


def test_every_provider_carries_confidence(client: TestClient) -> None:
    """Fails if a provider entry is ever missing the field."""
    providers = _providers(client)
    assert providers
    for provider in providers:
        assert "confidence" in provider
        assert provider["confidence"] in _WIRE_LEVELS


def test_confidence_round_trips_through_the_enum(client: TestClient) -> None:
    """Fails if the wire string is not a value the domain enum accepts."""
    for provider in _providers(client):
        wire_value = provider["confidence"]
        assert Confidence(wire_value).value == wire_value
        assert json.loads(json.dumps(wire_value)) == wire_value


def test_confidence_absent_from_error_bodies(client: TestClient) -> None:
    """Fails if a 403 or 404 body leaks the field or its enum spelling."""
    forbidden = client.post(
        "/api/v1/sources/unknown-source-sentinel/approve", headers=ORIGIN
    )
    rejected = client.post("/api/v1/rebuild")

    assert forbidden.status_code == 404
    assert rejected.status_code == 403
    for response in (forbidden, rejected):
        assert set(response.json()) == {"detail"}
        assert "confidence" not in response.text
        assert "Confidence" not in response.text


def test_status_body_has_no_confidence(client: TestClient) -> None:
    """Fails if the field bleeds into unrelated read-only endpoints."""
    response = client.get("/api/v1/status")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "confidence" not in response.text


def test_discovery_body_has_no_path_credential_or_auth_marker(
    client: TestClient, tmp_path: Path
) -> None:
    """Fails if the added field arrives alongside a private filesystem detail."""
    response = client.get("/api/v1/discovery")
    assert response.status_code == 200

    for marker in (
        str(tmp_path),
        "canonical_path",
        "approved_root",
        "auth.json",
        "sk-",
        "state.db",
        "synthetic.jsonl",
    ):
        assert marker not in response.text, f"{marker!r} leaked into discovery"


def test_api_app_fixture_reports_all_three_providers(
    client: TestClient, api_app: FastAPI
) -> None:
    """Guards the fixture these wire assertions lean on."""
    results = api_app.state.container.services.discovery.discover()
    assert {result.connector_id for result in results} == {
        "claude-code-local",
        "codex-local",
        "hermes-local",
    }


def test_connector_failure_still_reports_a_wire_value(
    client: TestClient, api_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails if a crashed connector yields a missing or off-contract value."""
    registry = api_app.state.container.services.discovery.registry

    def explode(*_args: object, **_kwargs: object) -> object:
        raise OSError("synthetic connector failure")

    monkeypatch.setattr(registry.connectors[0], "detect", explode)

    failed = next(
        provider
        for provider in _providers(client)
        if provider["connector_id"] == "claude-code-local"
    )

    assert failed["state"] == "error"
    assert failed["confidence"] in _WIRE_LEVELS
    # The error path has no known signal, so the wire collapses it to ``low``.
    assert failed["confidence"] == "low"
