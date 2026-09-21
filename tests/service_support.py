"""Small test-only composition for service behavior, without an HTTP app."""

import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session
from tokenhub.analytics.service import AnalyticsService
from tokenhub.connectors.protocol import DiscoveryContext
from tokenhub.connectors.registry import ConnectorRegistry
from tokenhub.database.repositories import SourceRepository, UsageRepository
from tokenhub.discovery.service import DiscoveryService
from tokenhub.ingestion.service import IngestionService


@dataclass
class Services:
    session: Session
    source_repository: SourceRepository
    usage_repository: UsageRepository
    discovery: DiscoveryService
    ingestion: IngestionService
    analytics: AnalyticsService
    session_file: Path


def services_for(session: Session, home: Path, session_file: Path) -> Services:
    sources = SourceRepository(session)
    usage = UsageRepository(session)
    registry = ConnectorRegistry.default()
    return Services(
        session=session,
        source_repository=sources,
        usage_repository=usage,
        discovery=DiscoveryService(
            registry,
            sources,
            DiscoveryContext(home=home, environment={}, which=lambda _: None),
        ),
        ingestion=IngestionService(sources, usage, registry),
        analytics=AnalyticsService(usage),
        session_file=session_file,
    )


def discover_codex(services: Services) -> str:
    return next(
        result.sources[0].source_id
        for result in services.discovery.discover()
        if result.connector_id == "codex-local"
    )


def discover_and_approve_codex(services: Services) -> str:
    source_id = discover_codex(services)
    services.ingestion.approve(source_id)
    return source_id


def token_record(ordinal: int, **usage: int) -> bytes:
    return (
        json.dumps(
            {
                "ordinal": ordinal,
                "timestamp": "2026-09-20T10:00:00Z",
                "type": "token_usage_record",
                "payload": {"usage": usage},
            }
        )
        + "\n"
    ).encode()
