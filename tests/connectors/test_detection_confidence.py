"""Per-connector confidence against synthetic homes.

Every home is built under ``tmp_path``: no real user path, no network, no
subprocess. Each test also recomputes the level from ``result.evidence_codes``
with the pure rule, so the connector contract and the domain rule can never
disagree silently.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tokenhub.connectors.claude.connector import ClaudeConnector
from tokenhub.connectors.codex.connector import CodexConnector
from tokenhub.connectors.hermes.connector import HermesConnector
from tokenhub.connectors.protocol import (
    ConnectorCapabilities,
    DetectionResult,
    DiscoveryContext,
    ScanResult,
)
from tokenhub.connectors.registry import ConnectorRegistry
from tokenhub.domain.models import (
    Confidence,
    Provider,
    SourceDescriptor,
    SourceState,
    SyncCursor,
    confidence_from_evidence,
)


def _context(home: Path, environment: dict[str, str] | None = None) -> DiscoveryContext:
    """A discovery context with no PATH hits and no overrides by default."""
    return DiscoveryContext(home=home, environment=environment or {}, which=lambda _: None)


def test_claude_root_and_configuration_reach_high(tmp_path: Path) -> None:
    """Fails if a real ``.claude/settings.json`` is not two independent signals."""
    home = tmp_path / "home"
    root = home / ".claude"
    root.mkdir(parents=True)
    (root / "settings.json").write_text("{}")

    result = ClaudeConnector().detect(_context(home))

    assert set(result.evidence_codes) == {"known_root_exists", "configuration_found"}
    assert result.confidence is Confidence.HIGH


def test_claude_root_without_configuration_is_medium(tmp_path: Path) -> None:
    """Fails if a bare ``.claude`` directory is over-reported as high."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)

    result = ClaudeConnector().detect(_context(home))

    assert result.evidence_codes == ("known_root_exists",)
    assert result.confidence is Confidence.MEDIUM


def test_claude_with_nothing_found_is_low(tmp_path: Path) -> None:
    """Fails if an absent provider is reported as found."""
    home = tmp_path / "home"
    home.mkdir()

    result = ClaudeConnector().detect(_context(home))

    assert result.evidence_codes == ()
    assert result.confidence is Confidence.LOW


def test_claude_settings_directory_is_not_a_configuration(tmp_path: Path) -> None:
    """Fails if ``settings.json`` as a directory is counted as a found config."""
    home = tmp_path / "home"
    root = home / ".claude"
    root.mkdir(parents=True)
    (root / "settings.json").mkdir()

    result = ClaudeConnector().detect(_context(home))

    assert result.evidence_codes == ("known_root_exists",)
    assert result.confidence is Confidence.MEDIUM


def test_codex_session_file_reaches_high(tmp_path: Path) -> None:
    """Fails if a located session file plus its root is not high."""
    home = tmp_path / "home"
    session = home / ".codex" / "sessions" / "synthetic.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text("{}\n")

    result = CodexConnector().detect(_context(home))

    assert set(result.evidence_codes) == {"known_root_exists", "session_source_found"}
    assert result.confidence is Confidence.HIGH


def test_codex_root_without_sessions_is_medium(tmp_path: Path) -> None:
    """Fails if an empty ``.codex`` root is treated as two signals."""
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)

    result = CodexConnector().detect(_context(home))

    assert result.evidence_codes == ("known_root_exists",)
    assert result.confidence is Confidence.MEDIUM


def test_hermes_state_database_reaches_high(tmp_path: Path) -> None:
    """Fails if a state database alone (with its root) is not high."""
    home = tmp_path / "home"
    root = home / ".hermes"
    root.mkdir(parents=True)
    (root / "state.db").write_text("synthetic private database")

    result = HermesConnector().detect(_context(home))

    assert set(result.evidence_codes) == {"known_root_exists", "state_database_found"}
    assert result.confidence is Confidence.HIGH


def test_hermes_nested_state_database_is_not_a_signal(tmp_path: Path) -> None:
    """Fails if a nested ``state.db`` is counted when only the direct one is allowed."""
    home = tmp_path / "home"
    nested = home / ".hermes" / "cache"
    nested.mkdir(parents=True)
    (nested / "state.db").write_text("synthetic private database")

    result = HermesConnector().detect(_context(home))

    assert result.evidence_codes == ("known_root_exists",)
    assert result.confidence is Confidence.MEDIUM


def _build_full_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    claude = home / ".claude"
    claude.mkdir(parents=True)
    (claude / "settings.json").write_text("{}")
    sessions = home / ".codex" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "synthetic.jsonl").write_text("{}\n")
    hermes = home / ".hermes"
    hermes.mkdir()
    (hermes / "state.db").write_text("synthetic private database")
    return home


@pytest.mark.parametrize("connector_id", ["claude-code-local", "codex-local", "hermes-local"])
def test_detection_confidence_agrees_with_recomputed_evidence(
    tmp_path: Path, connector_id: str
) -> None:
    """Fails if a connector's own level diverges from the pure rule."""
    home = _build_full_home(tmp_path)
    context = _context(home)
    detected: tuple[tuple[str, DetectionResult], ...] = (
        ("claude-code-local", ClaudeConnector().detect(context)),
        ("codex-local", CodexConnector().detect(context)),
        ("hermes-local", HermesConnector().detect(context)),
    )
    result = dict(detected)[connector_id]

    assert result.confidence is confidence_from_evidence(result.evidence_codes)
    assert result.confidence is Confidence.HIGH


def test_every_connector_matches_the_rule_across_a_home_matrix(tmp_path: Path) -> None:
    """Fails if any connector disagrees with the rule on any synthetic home."""
    (tmp_path / "empty").mkdir()
    (tmp_path / "partial" / ".claude").mkdir(parents=True)
    _build_full_home(tmp_path)

    for name in ("empty", "partial", "home"):
        context = _context(tmp_path / name)
        for connector in (ClaudeConnector(), CodexConnector(), HermesConnector()):
            result = connector.detect(context)
            assert result.confidence is confidence_from_evidence(result.evidence_codes), (
                f"{connector.connector_id} disagreed on the {name!r} home"
            )


def test_state_and_confidence_cannot_contradict(tmp_path: Path) -> None:
    """Fails if a provider is 'discovered' with no signal, or 'missing' with one."""
    (tmp_path / "empty").mkdir()
    (tmp_path / "partial" / ".claude").mkdir(parents=True)
    _build_full_home(tmp_path)

    for name in ("empty", "partial", "home"):
        context = _context(tmp_path / name)
        for connector in (ClaudeConnector(), CodexConnector(), HermesConnector()):
            result = connector.detect(context)
            if result.state is SourceState.DISCOVERED:
                assert result.confidence is not Confidence.LOW
            if result.state is SourceState.SOURCE_MISSING:
                assert result.confidence is Confidence.LOW


def test_connector_confidence_serializes_as_its_value(tmp_path: Path) -> None:
    """Fails if a connector's ``model_dump_json`` leaks an enum repr or a path."""
    home = _build_full_home(tmp_path)

    result = CodexConnector().detect(_context(home))
    dumped = result.model_dump_json()

    assert '"confidence":"high"' in dumped
    assert "Confidence.HIGH" not in dumped
    assert str(home) not in dumped


class _ExplodingConnector:
    """A connector whose detection always raises, as the registry tolerates."""

    connector_id = "claude-code-local"
    display_name = "Claude Code"
    provider = Provider.CLAUDE_CODE

    def detect(self, context: DiscoveryContext) -> DetectionResult:
        raise OSError("synthetic connector failure")

    def discover_sources(self, context: DiscoveryContext) -> list[SourceDescriptor]:
        raise OSError("synthetic connector failure")

    def scan(self, source: SourceDescriptor, cursor: SyncCursor | None) -> ScanResult:
        raise OSError("synthetic connector failure")

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(scan_supported=False)


def test_failed_detection_is_reported_as_low_confidence(tmp_path: Path) -> None:
    """Pins the rule table's blind spot: ``ERROR`` collapses to ``low``.

    A connector whose detection crashed is summarised exactly like a provider
    that was looked for and found absent, because ``discovery_error`` is not a
    known signal code. This is documented behaviour of the current rule, not a
    failure of the function — see ``reviews/confidence-test-gap.md``.
    """
    registry = ConnectorRegistry([_ExplodingConnector()])

    result = registry.discover_all(_context(tmp_path))[0]

    assert result.state is SourceState.ERROR
    assert result.evidence_codes == ("discovery_error",)
    assert result.confidence is Confidence.LOW
    # Even on the failure path, the rule and the contract agree.
    assert result.confidence is confidence_from_evidence(result.evidence_codes)


def test_failed_detection_serializes_the_error_state_beside_low(tmp_path: Path) -> None:
    """Fails if the failure path drops ``confidence`` from the payload."""
    registry = ConnectorRegistry([_ExplodingConnector()])

    dumped = registry.discover_all(_context(tmp_path))[0].model_dump_json()

    assert '"state":"error"' in dumped
    assert '"confidence":"low"' in dumped
