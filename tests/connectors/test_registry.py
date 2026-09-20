"""Connector registry isolation and executable presence checks."""

from pathlib import Path

from sqlalchemy.orm import Session
from tokenhub.connectors.codex.connector import CodexConnector
from tokenhub.connectors.protocol import DetectionResult, DiscoveryContext
from tokenhub.connectors.registry import ConnectorRegistry
from tokenhub.database.repositories import SourceRepository
from tokenhub.database.session import create_engine_for, initialize_database
from tokenhub.discovery.service import DiscoveryService
from tokenhub.domain.models import Provider, SourceState
from tokenhub.settings import TokenHubSettings


def test_discovery_never_runs_provider_executables(tmp_path: Path) -> None:
    calls: list[str] = []
    context = DiscoveryContext(
        home=tmp_path,
        environment={},
        which=lambda executable: calls.append(executable) or "/bin/fake",
    )

    results = ConnectorRegistry.default().discover_all(context)

    assert calls == ["claude", "codex", "hermes"]
    assert all("executable_on_path" in result.evidence_codes for result in results)
    assert all("/bin/fake" not in result.model_dump_json() for result in results)


def test_one_connector_failure_does_not_hide_other_results(tmp_path: Path) -> None:
    class FailingConnector:
        connector_id = "failing"
        display_name = "Failing"

        def detect(self, _context: DiscoveryContext) -> None:
            raise RuntimeError("private /secret/path")

    registry = ConnectorRegistry([FailingConnector(), CodexConnector()])

    results = registry.discover_all(
        DiscoveryContext(home=tmp_path, environment={}, which=lambda _: None)
    )

    assert [result.connector_id for result in results] == ["failing", "codex-local"]
    assert results[0].state is SourceState.ERROR
    assert "/secret/path" not in results[0].model_dump_json()
    assert results[1].state is SourceState.SOURCE_MISSING


def test_source_discovery_failure_stays_local_and_path_free(tmp_path: Path) -> None:
    class FailingSources:
        connector_id = "failing"
        display_name = "Failing"
        provider = Provider.CLAUDE_CODE

        def detect(self, _context: DiscoveryContext) -> DetectionResult:
            return DetectionResult(
                connector_id=self.connector_id,
                display_name=self.display_name,
                provider=self.provider,
                state=SourceState.DISCOVERED,
            )

        def discover_sources(self, _context: DiscoveryContext) -> None:
            raise RuntimeError("private /secret/provider/path")

    engine = create_engine_for(TokenHubSettings(data_directory=tmp_path / "data"))
    initialize_database(engine)
    context = DiscoveryContext(home=tmp_path, environment={}, which=lambda _: None)
    with Session(engine) as db_session:
        service = DiscoveryService(
            ConnectorRegistry([FailingSources(), CodexConnector()]),
            SourceRepository(db_session),
            context,
        )

        results = service.discover()

    assert [result.state for result in results] == [
        SourceState.ERROR,
        SourceState.SOURCE_MISSING,
    ]
    assert "/secret/provider/path" not in results[0].model_dump_json()
