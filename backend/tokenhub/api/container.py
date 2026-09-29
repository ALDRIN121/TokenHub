"""Composition root for the local TokenHub application.

The container owns startup and shutdown so that constructing the app has **no
side effects**: no database file, no migration, and no provider discovery until
the ASGI lifespan starts. It also owns the two pieces of state that must survive
across HTTP requests:

* the process-local discovery handoff (``SourceRepository`` pending candidates),
* the injected ``DiscoveryContext``, which tests replace after ``create_app``.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
from dataclasses import dataclass

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from tokenhub.analytics.service import AnalyticsService
from tokenhub.connectors.protocol import DiscoveryContext
from tokenhub.connectors.registry import ConnectorRegistry
from tokenhub.connectors.roots import candidate_roots
from tokenhub.database.migrations import run_migrations
from tokenhub.database.repositories import SourceRepository, UsageRepository
from tokenhub.database.session import create_engine_for
from tokenhub.discovery.service import DiscoveryService
from tokenhub.ingestion.collection import CollectionService
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
    collection: CollectionService


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
        self.collection_thread: threading.Thread | None = None
        self._collection_stop = threading.Event()

    @property
    def services(self) -> Services:
        """Collaborators for the running app; raises before :meth:`start`."""
        if self._services is None:
            raise RuntimeError("container has not started")
        return self._services

    @property
    def discovery_context(self) -> DiscoveryContext:
        """Discovery inputs, defaulting to the real home and process environment."""
        if self._discovery_context is not None:
            return self._discovery_context
        home = self.settings.home_directory
        return DiscoveryContext(
            home=home,
            environment=os.environ,
            which=shutil.which,
            search_roots=candidate_roots(os.environ, home),
        )

    @discovery_context.setter
    def discovery_context(self, context: DiscoveryContext) -> None:
        """Replace discovery inputs, including after startup."""
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
        source_repository.upgrade_codex_parser_versions()
        usage_repository = UsageRepository(session)
        self._engine = engine
        discovery = DiscoveryService(registry, source_repository, self.discovery_context)
        ingestion = IngestionService(source_repository, usage_repository, registry)
        self._services = Services(
            session=session,
            source_repository=source_repository,
            usage_repository=usage_repository,
            discovery=discovery,
            ingestion=ingestion,
            analytics=AnalyticsService(usage_repository),
            collection=CollectionService(source_repository, usage_repository, discovery, ingestion),
        )
        self._collect_safely()
        self._collection_stop.clear()
        self.collection_thread = threading.Thread(
            target=self._collect_periodically, name="tokenhub-collection", daemon=True
        )
        self.collection_thread.start()

    def collect(self) -> None:
        with self.lock:
            self.services.collection.run_once()

    def _collect_periodically(self) -> None:
        while not self._collection_stop.wait(self.settings.scan_interval_seconds):
            self._collect_safely()

    def _collect_safely(self) -> None:
        try:
            self.collect()
        except Exception:  # noqa: BLE001 - startup and later collection must remain available
            # Do not log exception text, which may contain a provider path.
            logging.getLogger(__name__).error("Automatic collection failed; retrying on the next scan")
            with self.lock:
                self.services.session.rollback()
                self.services.collection.failed_source_count = max(
                    1, self.services.collection.failed_source_count
                )

    def stop(self) -> None:
        """Release the session and engine; the database file stays behind."""
        self._collection_stop.set()
        if self.collection_thread is not None:
            self.collection_thread.join()
            self.collection_thread = None
        if self._services is not None:
            self._services.session.close()
            self._services = None
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
