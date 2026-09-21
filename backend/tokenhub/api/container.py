"""Composition root for the local TokenHub application.

The container owns startup and shutdown so that constructing the app has **no
side effects**: no database file, no migration, and no provider discovery until
the ASGI lifespan starts. It also owns the two pieces of state that must survive
across HTTP requests:

* the process-local discovery handoff (``SourceRepository`` pending candidates),
* the injected ``DiscoveryContext``, which tests replace after ``create_app``.
"""

from __future__ import annotations

import os
import shutil
import threading
from dataclasses import dataclass

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from tokenhub.analytics.service import AnalyticsService
from tokenhub.connectors.protocol import DiscoveryContext
from tokenhub.connectors.registry import ConnectorRegistry
from tokenhub.database.migrations import run_migrations
from tokenhub.database.repositories import SourceRepository, UsageRepository
from tokenhub.database.session import create_engine_for
from tokenhub.discovery.service import DiscoveryService
from tokenhub.ingestion.service import IngestionService
from tokenhub.settings import TokenHubSettings


@dataclass(frozen=True, slots=True)
class Services:
    """Long-lived collaborators shared by the HTTP routes."""

    session: Session
    source_repository: SourceRepository
    usage_repository: UsageRepository
    discovery: DiscoveryService
    ingestion: IngestionService
    analytics: AnalyticsService


class Container:
    """Startup, shutdown, and request serialization for one app instance."""

    def __init__(self, settings: TokenHubSettings) -> None:
        self.settings = settings
        # One SQLite connection backs this single-user local server; the lock
        # keeps sequential routes from interleaving on it.
        self.lock = threading.RLock()
        self._engine: Engine | None = None
        self._services: Services | None = None
        self._discovery_context: DiscoveryContext | None = None

    @property
    def services(self) -> Services:
        if self._services is None:
            raise RuntimeError("container has not started")
        return self._services

    @property
    def discovery_context(self) -> DiscoveryContext:
        """Discovery inputs, defaulting to the real home and process environment."""
        if self._discovery_context is not None:
            return self._discovery_context
        return DiscoveryContext(
            home=self.settings.home_directory,
            environment=os.environ,
            which=shutil.which,
        )

    @discovery_context.setter
    def discovery_context(self, context: DiscoveryContext) -> None:
        self._discovery_context = context
        if self._services is not None:
            self._services.discovery.context = context

    def start(self) -> None:
        """Migrate the database, then build the services that routes depend on."""
        run_migrations(self.settings)
        engine = create_engine_for(self.settings)
        session = Session(engine)
        registry = ConnectorRegistry.default()
        source_repository = SourceRepository(session)
        usage_repository = UsageRepository(session)
        self._engine = engine
        self._services = Services(
            session=session,
            source_repository=source_repository,
            usage_repository=usage_repository,
            discovery=DiscoveryService(
                registry, source_repository, self.discovery_context
            ),
            ingestion=IngestionService(source_repository, usage_repository, registry),
            analytics=AnalyticsService(usage_repository),
        )

    def stop(self) -> None:
        """Release the session and engine; the database file stays behind."""
        if self._services is not None:
            self._services.session.close()
            self._services = None
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
