"""Presence discovery uses synthetic paths and never reads provider telemetry."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tokenhub.connectors.claude.connector import ClaudeConnector
from tokenhub.connectors.codex.connector import CodexConnector
from tokenhub.connectors.hermes.connector import HermesConnector
from tokenhub.connectors.protocol import DetectionResult, DiscoveryContext
from tokenhub.connectors.registry import ConnectorRegistry
from tokenhub.database.models import SourceRecord
from tokenhub.database.repositories import SourceRepository
from tokenhub.database.session import create_engine_for, initialize_database
from tokenhub.discovery.service import DiscoveryService
from tokenhub.domain.models import Confidence, Provider, SourceDescriptor, SourceState
from tokenhub.settings import TokenHubSettings


def test_codex_detection_uses_override_and_reports_safe_evidence(tmp_path: Path) -> None:
    home = tmp_path / "home"
    codex_root = tmp_path / "custom-codex"
    session_dir = codex_root / "sessions" / "2026" / "09" / "20"
    session_dir.mkdir(parents=True)
    (session_dir / "rollout.jsonl").write_text("{}\n")

    result = CodexConnector().detect(
        DiscoveryContext(home=home, environment={"CODEX_HOME": str(codex_root)}, which=lambda _: None)
    )

    assert result.provider is Provider.CODEX
    assert result.state is SourceState.DISCOVERED
    assert "known_root_exists" in result.evidence_codes
    assert "session_source_found" in result.evidence_codes
    assert str(codex_root) not in result.model_dump_json()
    assert str(home) not in result.model_dump_json()


def test_detection_reports_confidence_from_its_own_evidence(tmp_path: Path) -> None:
    """Fails if the wire contract drops confidence or disagrees with evidence."""
    home = tmp_path / "home"
    codex_root = tmp_path / "codex"
    session_dir = codex_root / "sessions" / "2026" / "09" / "20"
    session_dir.mkdir(parents=True)
    (session_dir / "rollout.jsonl").write_text("{}\n")
    (codex_root / "config.toml").write_text("synthetic\n")

    detected = CodexConnector().detect(
        DiscoveryContext(home=home, environment={"CODEX_HOME": str(codex_root)}, which=lambda _: None)
    )
    missing = CodexConnector().detect(
        DiscoveryContext(home=home, environment={}, which=lambda _: None)
    )

    # Two independent signals (root + session source) → high; nothing → low.
    assert detected.confidence is Confidence.HIGH
    assert missing.confidence is Confidence.LOW
    assert '"confidence":"high"' in detected.model_dump_json()
    assert str(codex_root) not in detected.model_dump_json()


def test_codex_candidates_only_include_regular_session_jsonl(tmp_path: Path) -> None:
    root = tmp_path / "codex"
    sessions = root / "sessions"
    nested = sessions / "2026" / "09" / "20"
    nested.mkdir(parents=True)
    valid = nested / "rollout.jsonl"
    valid.write_text("private telemetry\n")
    (root / "history.jsonl").write_text("history\n")
    (root / "auth.json").write_text("secret")
    (root / "config.toml").write_text("secret")
    (sessions / "history.jsonl").write_text("history\n")
    for excluded_name in ("auth.jsonl", "config.jsonl", "log.jsonl", "cache.jsonl"):
        (sessions / excluded_name).write_text("private\n")
        (nested / excluded_name).write_text("private\n")
    (nested / "history.jsonl").write_text("history\n")
    (nested / "other.json").write_text("{}")
    (nested / "linked.jsonl").symlink_to(valid)
    (nested / "folder.jsonl").mkdir()
    logs = sessions / "logs"
    logs.mkdir()
    (logs / "log.jsonl").write_text("log\n")
    cache = sessions / "cache"
    cache.mkdir()
    (cache / "cache.jsonl").write_text("cache\n")
    for excluded in ("auth", "config", "history"):
        private_dir = sessions / excluded
        private_dir.mkdir()
        (private_dir / "private.jsonl").write_text("private\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "escape.jsonl").write_text("outside\n")
    (sessions / "escape").symlink_to(outside, target_is_directory=True)

    candidates = CodexConnector().discover_sources(
        DiscoveryContext(home=tmp_path, environment={"CODEX_HOME": str(root)}, which=lambda _: None)
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.canonical_path == valid.resolve()
    assert candidate.approved_root == sessions.resolve()
    assert candidate.display_name == "Codex session"
    safe = candidate.safe_view()
    assert len(str(safe["path_fingerprint"])) == 64
    assert str(valid.resolve()) not in str(safe)
    assert str(root.resolve()) not in str(safe)
    assert "canonical_path" not in safe
    assert "approved_root" not in safe


def test_invalid_root_override_falls_back_to_injected_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    session = home / ".codex" / "sessions" / "valid.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text("{}\n")

    sources = CodexConnector().discover_sources(
        DiscoveryContext(
            home=home,
            environment={"CODEX_HOME": str(tmp_path / "missing")},
            which=lambda _: None,
        )
    )

    assert [source.canonical_path for source in sources] == [session.resolve()]


def test_tilde_override_expands_with_injected_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    session = home / "custom-codex" / "sessions" / "valid.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text("{}\n")

    sources = CodexConnector().discover_sources(
        DiscoveryContext(
            home=home,
            environment={"CODEX_HOME": "~/custom-codex"},
            which=lambda _: None,
        )
    )

    assert [source.canonical_path for source in sources] == [session.resolve()]


def test_default_root_symlink_is_not_followed(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    session = outside / "sessions" / "a.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text("{}\n")
    (home / ".codex").symlink_to(outside, target_is_directory=True)

    sources = CodexConnector().discover_sources(
        DiscoveryContext(home=home, environment={}, which=lambda _: None)
    )

    assert sources == []


def test_presence_discovery_does_not_open_provider_files(tmp_path: Path) -> None:
    root = tmp_path / "codex"
    session = root / "sessions" / "a.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text("private telemetry\n")
    hermes = tmp_path / "hermes"
    hermes.mkdir()
    (hermes / "state.db").write_text("private database")
    context = DiscoveryContext(
        home=tmp_path,
        environment={"CODEX_HOME": str(root), "HERMES_HOME": str(hermes)},
        which=lambda _: None,
    )

    with (
        patch("builtins.open", side_effect=AssertionError("provider file opened")),
        patch("io.open", side_effect=AssertionError("provider file opened")),
    ):
        results = ConnectorRegistry.default().discover_all(context)

    assert results[1].sources[0].source_type == "jsonl"
    assert results[2].sources[0].source_type == "sqlite"


def test_claude_configuration_alone_has_no_importable_sources(tmp_path: Path) -> None:
    root = tmp_path / "claude"
    root.mkdir()
    (root / "settings.json").write_text("private configuration")
    context = DiscoveryContext(
        home=tmp_path, environment={"CLAUDE_CONFIG_DIR": str(root)}, which=lambda _: None
    )

    result = ClaudeConnector().detect(context)

    assert result.provider is Provider.CLAUDE_CODE
    assert "known_root_exists" in result.evidence_codes
    assert "configuration_found" in result.evidence_codes
    assert result.sources == ()
    assert ClaudeConnector().discover_sources(context) == []
    assert str(root) not in result.model_dump_json()


def test_hermes_exposes_only_direct_database_candidate_without_reading_it(tmp_path: Path) -> None:
    root = tmp_path / "hermes"
    root.mkdir()
    direct = root / "state.db"
    direct.write_text("not a real database")
    nested = root / "cache"
    nested.mkdir()
    (nested / "state.db").write_text("other")
    context = DiscoveryContext(
        home=tmp_path, environment={"HERMES_HOME": str(root)}, which=lambda _: None
    )

    sources = HermesConnector().discover_sources(context)
    result = HermesConnector().scan(sources[0], None)

    assert len(sources) == 1
    assert sources[0].canonical_path == direct.resolve()
    assert sources[0].state is SourceState.DISCOVERED
    assert sources[0].scan_supported is True
    assert result.state is SourceState.DISCOVERED
    assert result.events == ()
    assert str(direct) not in str(sources[0].safe_view())


def test_discovery_service_keeps_private_descriptor_in_process(tmp_path: Path) -> None:
    root = tmp_path / "codex"
    session = root / "sessions" / "a.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text("{}\n")
    settings = TokenHubSettings(home_directory=tmp_path, data_directory=tmp_path / "data")
    engine = create_engine_for(settings)
    initialize_database(engine)
    context = DiscoveryContext(
        home=tmp_path, environment={"CODEX_HOME": str(root)}, which=lambda _: None
    )
    with Session(engine) as db_session:
        repository = SourceRepository(db_session)
        service = DiscoveryService(ConnectorRegistry([CodexConnector()]), repository, context)

        result = service.discover()[0]
        source_id = result.sources[0].source_id
        stored = repository._source(source_id)

        assert result.sources[0].display_name == "Codex session"
        assert str(root) not in result.model_dump_json()
        assert stored.canonical_path is None
        assert stored.approved_root is None
        assert service.candidate(source_id).canonical_path == session.resolve()
        db_session.rollback()
        approved = repository.approve(source_id)
        assert approved.canonical_path == str(session.resolve())

        refreshed = service.discover()[0]
        stored_after_refresh = repository._source(source_id)
        assert stored_after_refresh.state == SourceState.APPROVED.value
        assert stored_after_refresh.canonical_path == str(session.resolve())
        assert stored_after_refresh.approved_root == str((root / "sessions").resolve())
        assert stored_after_refresh.path_fingerprint == refreshed.sources[0].path_fingerprint
        assert refreshed.sources[0].state is SourceState.APPROVED
        db_session.rollback()
        repository.upsert_discovery(
            replace(service.candidate(source_id), evidence_codes=("refreshed_marker",))
        )
        assert repository._source(source_id).evidence_codes == "refreshed_marker"


def test_failed_connector_batch_leaves_no_approvable_or_persisted_source(tmp_path: Path) -> None:
    root = tmp_path / "codex"
    sessions = root / "sessions"
    sessions.mkdir(parents=True)
    first_file = sessions / "first.jsonl"
    second_file = sessions / "second.jsonl"
    first_file.write_text("{}\n")
    second_file.write_text("{}\n")

    class TwoCandidates:
        connector_id = "codex-local"
        display_name = "OpenAI Codex"
        provider = Provider.CODEX

        def detect(self, _context: DiscoveryContext) -> DetectionResult:
            return DetectionResult(
                connector_id=self.connector_id,
                display_name=self.display_name,
                provider=self.provider,
                state=SourceState.DISCOVERED,
            )

        def discover_sources(self, _context: DiscoveryContext) -> list[SourceDescriptor]:
            return [
                SourceDescriptor(
                    source_id=source_id,
                    connector_id=self.connector_id,
                    provider=self.provider,
                    display_name="Codex session",
                    canonical_path=path,
                    approved_root=sessions,
                    source_type="jsonl",
                    path_fingerprint=f"fingerprint-{source_id}",
                )
                for source_id, path in (("first", first_file), ("second", second_file))
            ]

    engine = create_engine_for(TokenHubSettings(data_directory=tmp_path / "data"))
    initialize_database(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER fail_second_source BEFORE INSERT ON sources "
            "WHEN NEW.source_id = 'second' "
            "BEGIN SELECT RAISE(FAIL, 'synthetic second-source failure'); END"
        )

    with Session(engine) as db_session:
        repository = SourceRepository(db_session)
        service = DiscoveryService(
            ConnectorRegistry([TwoCandidates()]),
            repository,
            DiscoveryContext(home=tmp_path, environment={}, which=lambda _: None),
        )

        result = service.discover()[0]

        assert result.state is SourceState.ERROR
        assert result.sources == ()
        for source_id in ("first", "second"):
            with pytest.raises(KeyError):
                service.candidate(source_id)
            with pytest.raises(ValueError, match="source must be discovered"):
                repository.approve(source_id)
        assert db_session.scalars(select(SourceRecord)).all() == []
