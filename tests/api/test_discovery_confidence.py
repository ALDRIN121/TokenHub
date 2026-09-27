"""The discovery payload must carry the confidence the design spec requires.

`docs/superpowers/specs/2026-09-20-tokenhub-foundation-design.md` (API contract)
lists confidence alongside display name, connection state, safe evidence, and
the source identifier. These tests pin the field, its values, and the fact that
adding it changed nothing else about the payload.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from tokenhub.domain.models import Confidence, confidence_from_evidence

_PROVIDER_KEYS = {
    "connector_id",
    "display_name",
    "provider",
    "state",
    "evidence_codes",
    "sources",
    "confidence",
}


def test_every_provider_reports_confidence_consistent_with_its_evidence(
    client: TestClient,
) -> None:
    """Fails if confidence is missing, unknown, or disagrees with the evidence."""
    providers = client.get("/api/v1/discovery").json()["providers"]
    assert providers

    for provider in providers:
        confidence = provider["confidence"]
        assert confidence in {level.value for level in Confidence}
        # Recomputed independently, from the evidence in the same payload.
        assert confidence == confidence_from_evidence(provider["evidence_codes"]).value


def test_confidence_reflects_how_much_evidence_was_found(client: TestClient) -> None:
    """A provider found only by absence reports low; a located one reports high."""
    providers = {entry["connector_id"]: entry for entry in client.get("/api/v1/discovery").json()["providers"]}

    # The fixture home has no Claude Code root and no executable on PATH.
    assert providers["claude-code-local"]["state"] == "source_missing"
    assert providers["claude-code-local"]["confidence"] == Confidence.LOW.value

    # The fixture home has a Codex session file and a Hermes state database.
    assert providers["codex-local"]["confidence"] == Confidence.HIGH.value
    assert providers["hermes-local"]["confidence"] == Confidence.HIGH.value


def test_confidence_is_additive_and_leaks_nothing(client: TestClient, tmp_path: Path) -> None:
    """The field is the only payload change, and it exposes no provider detail."""
    response = client.get("/api/v1/discovery")
    providers = response.json()["providers"]

    for provider in providers:
        assert set(provider) == _PROVIDER_KEYS

    for private in (str(tmp_path), "canonical_path", "approved_root", "auth.json", "sk-"):
        assert private not in response.text
