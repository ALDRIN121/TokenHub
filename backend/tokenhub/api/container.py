"""One collection writer, independent request sessions, and cheap progress reads."""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from tokenhub.analytics.service import AnalyticsService
from tokenhub.connectors.protocol import DetectionResult, DiscoveryContext
from tokenhub.connectors.registry import ConnectorRegistry
from tokenhub.connectors.roots import candidate_roots
from tokenhub.database.migrations import run_migrations
from tokenhub.database.repositories import SourceRepository, UsageRepository
from tokenhub.database.session import create_engine_for, optimize_database
from tokenhub.discovery.engine import DiscoveryEngine
from tokenhub.discovery.host import (
    installed_applications,
    read_user_location_config,
    running_agents,
)
from tokenhub.discovery.service import DiscoveryService
from tokenhub.domain.models import ImportOutcome, RebuildOutcome
from tokenhub.ingestion.collection import CollectionService
from tokenhub.ingestion.jobs import CollectionJob, load_history, now, save_history
from tokenhub.ingestion.progress import ScanInterrupted, reporting
from tokenhub.ingestion.service import (
    IngestionService,
    SourceNotApprovedError,
    SourceNotFoundError,
    UnsupportedSourceError,
)
from tokenhub.pricing import PricingTable, ensure_pricing_file
from tokenhub.settings import TokenHubSettings


@dataclass(frozen=True, slots=True)
class Services:
    session: Session
    source_repository: SourceRepository
    usage_repository: UsageRepository
    discovery: DiscoveryService
    ingestion: IngestionService
    analytics: AnalyticsService
    collection: CollectionService


class CollectionBusyError(RuntimeError):
    """The bounded manual work queue is full."""


class Container:
    def __init__(self, settings: TokenHubSettings) -> None:
        self.settings = settings
        self.lock = threading.RLock()
        self._condition = threading.Condition(self.lock)
        self._engine: Engine | None = None
        self._services: Services | None = None
        self._discovery_context: DiscoveryContext | None = None
        self.discovery_version = -1
        self.collection_thread: threading.Thread | None = None
        self._collection_stop = threading.Event()
        self._ready = threading.Event()
        self._discovery_ready = threading.Event()
        self._startup_error: Exception | None = None
        self._queue: deque[str] = deque()
        self._jobs: dict[str, CollectionJob] = {}
        self._history: list[dict[str, Any]] = []
        self._active_job: str | None = None
        self._latest_job: str | None = None
        self._progress_revision = 0
        self._discovery_results: list[DetectionResult] | None = None
        self._pricing: PricingTable | None = None
        self._pricing_mtime: int | None = None
        self._status: dict[str, Any] = {
            "scan_interval_seconds": settings.scan_interval_seconds, "codex_auto_import": False,
            "auto_import_connectors": [], "last_scan_at": None, "failed_source_count": 0,
            "requires_reapproval_connectors": [],
            # A new process cannot accidentally reuse a browser's old version.
            "data_version": time.time_ns() // 1000,
        }
        self._version_base = self._status["data_version"]
        self._cache: OrderedDict[tuple[Any, ...], tuple[dict[str, Any], int]] = OrderedDict()
        self._cache_bytes = 0
        # One build per identical cache key. Waiters block instead of issuing a
        # duplicate aggregate (PowerSync's LRU fetchMethod does the same for
        # checksum requests).
        self._inflight: dict[tuple[Any, ...], threading.Event] = {}

    @property
    def services(self) -> Services:
        """Writer-owned services; HTTP reads use read_services instead."""
        if self._services is None:
            raise RuntimeError("container has not started")
        return self._services

    @property
    def discovery_context(self) -> DiscoveryContext:
        if self._discovery_context is not None:
            return self._discovery_context
        return self.host_discovery_context()

    @discovery_context.setter
    def discovery_context(self, context: DiscoveryContext) -> None:
        self._discovery_context = context
        if self._services is not None:
            self._services.discovery.context = context

    def host_discovery_context(self) -> DiscoveryContext:
        """A fresh host snapshot: locations, live agents, installed apps.

        Cheap enough to rebuild every discovery pass, which is what lets
        TokenHub notice an agent installed or started while it is already
        running. A test-injected context still wins, so nothing here can leak
        into a synthetic home.
        """
        if self._discovery_context is not None:
            # An injected context (tests, an embedder) is authoritative: host
            # probes must never widen it to the real machine.
            return self._discovery_context
        home = self.settings.home_directory
        environment = os.environ
        layouts = ConnectorRegistry.default().layouts()
        process_names = tuple(name for layout in layouts for name in layout.process_names)
        environment_keys = tuple(key for layout in layouts for key in layout.env_keys)
        application_names = tuple(
            hint.name for layout in layouts for hint in layout.applications
        )
        return DiscoveryContext(
            home,
            environment,
            shutil.which,
            candidate_roots(environment, home),
            locations=read_user_location_config(self.settings.discovery_file, home=home),
            processes=running_agents(process_names, environment_keys),
            applications=installed_applications(
                application_names, home=home, environment=environment
            ),
        )

    def discovery_engine(self) -> DiscoveryEngine:
        """The three-question view of this host, built from the live context."""
        context = self.discovery_context
        return DiscoveryEngine.for_layouts(
            ConnectorRegistry.default().layouts(),
            home=context.home,
            environment=context.environment,
            search_roots=context.search_roots,
            user_locations=context.locations,
            processes=context.processes,
            installed_applications=context.applications,
        )

    def pricing_table(self) -> PricingTable:
        """Reload the editable pricing file when its mtime changes."""
        path = self.settings.pricing_file
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            mtime = 0
        with self.lock:
            if self._pricing is None or self._pricing_mtime != mtime:
                self._pricing = PricingTable.load(path)
                self._pricing_mtime = mtime
            return self._pricing

    def _build_services(self, session: Session) -> Services:
        registry = ConnectorRegistry.default()
        sources = SourceRepository(session)
        usage = UsageRepository(session)
        discovery = DiscoveryService(
            registry, sources, self.discovery_context, context_provider=self.host_discovery_context
        )
        ingestion = IngestionService(sources, usage, registry)
        collection = CollectionService(sources, usage, discovery, ingestion)
        return Services(session, sources, usage, discovery, ingestion,
                        AnalyticsService(usage, self.pricing_table()), collection)

    @contextmanager
    def read_services(self) -> Iterator[Services]:
        if self._engine is None:
            raise RuntimeError("container has not started")
        with Session(self._engine) as session:
            yield self._build_services(session)

    def start(self) -> None:
        run_migrations(self.settings)
        try:
            ensure_pricing_file(self.settings.pricing_file)
        except OSError as failure:
            logging.getLogger(__name__).warning("could not create editable pricing file: %s", failure)
        self._engine = create_engine_for(self.settings)
        self._history = load_history(self.settings.data_directory)
        self._latest_job = self._history[-1]["job_id"] if self._history else None
        self._collection_stop.clear()
        self.collection_thread = threading.Thread(target=self._work, name="tokenhub-collection", daemon=True)
        self.collection_thread.start()
        self._ready.wait()
        if self._startup_error is not None:
            error = self._startup_error
            self.stop()
            raise error
        self.submit("refresh", automatic=True)

    def _sync_status(self) -> None:
        status = self.services.collection.status(self.settings.scan_interval_seconds)
        status["data_version"] = self._version_base + self.services.collection.data_version
        with self.lock:
            self._status = status

    def _publish_discovery(self) -> None:
        results = self.services.discovery.last_results
        if results is not None:
            with self.lock:
                self._discovery_results = [result.model_copy(deep=True) for result in results]
            self._discovery_ready.set()

    def _on_discovery_change(self) -> None:
        self.services.collection.bump()
        self._publish_discovery()

    def discovery_results(self) -> list[DetectionResult]:
        # Initial presence discovery may still be running. It cannot hold the
        # progress endpoint or block application startup/readiness.
        self._discovery_ready.wait()
        with self.lock:
            return list(self._discovery_results or [])

    def collection_status(self) -> dict[str, Any]:
        with self.lock:
            return {**self._status, "active_job": self._snapshot(self._active_job),
                    "latest_job": self._snapshot(self._latest_job),
                    "progress_revision": self._progress_revision,
                    "queued_jobs": len(self._queue)}

    def cached_view(self, key: tuple[Any, ...], build: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        with self.lock:
            version = self._status["data_version"]
            cache_key = (version, *key)
            if cache_key in self._cache:
                self._cache.move_to_end(cache_key)
                return self._cache[cache_key][0]
            waiter = self._inflight.get(cache_key)
            if waiter is None:
                waiter = threading.Event()
                self._inflight[cache_key] = waiter
                owner = True
            else:
                owner = False
        if not owner:
            waiter.wait(timeout=60)
            with self.lock:
                if cache_key in self._cache:
                    self._cache.move_to_end(cache_key)
                    return self._cache[cache_key][0]
        try:
            result = {**build(), "data_version": version}
        except Exception:
            if owner:
                with self.lock:
                    self._inflight.pop(cache_key, None)
                waiter.set()
            raise
        size = len(json.dumps(result).encode())
        with self.lock:
            if version == self._status["data_version"] and size <= 16 * 1024 * 1024:
                previous = self._cache.pop(cache_key, None)
                if previous:
                    self._cache_bytes -= previous[1]
                self._cache[cache_key] = (result, size)
                self._cache_bytes += size
                while len(self._cache) > 32 or self._cache_bytes > 16 * 1024 * 1024:
                    self._cache_bytes -= self._cache.popitem(last=False)[1][1]
            if owner:
                self._inflight.pop(cache_key, None)
                waiter.set()
        return result

    def _snapshot(self, job_id: str | None) -> dict[str, Any] | None:
        if job_id is None:
            return None
        job = self._jobs.get(job_id)
        if job is not None:
            return job.snapshot()
        return next((dict(item) for item in reversed(self._history) if item["job_id"] == job_id), None)

    def job_status(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            result = self._snapshot(job_id)
            if result is None:
                raise SourceNotFoundError("unknown job")
            return result

    def _save_jobs(self) -> None:
        snapshots = [item for item in self._history if item["job_id"] not in self._jobs]
        snapshots.extend(job.snapshot() for job in self._jobs.values())
        try:
            save_history(self.settings.data_directory, snapshots)
        except OSError:
            # A full disk must not strand waiters or kill the collection thread.
            logging.getLogger(__name__).error("Collection history could not be saved")

    def submit(self, kind: str, argument: str | None = None, *, automatic: bool = False) -> CollectionJob:
        with self._condition:
            if self._collection_stop.is_set():
                raise CollectionBusyError("collection is stopping")
            for job in self._jobs.values():
                if job.state in {"queued", "running"} and job.kind == kind and job.argument == argument:
                    return job
            if automatic and (self._active_job is not None or self._queue):
                # An existing collection will discover active changes; don't
                # accumulate timer jobs while the laptop is busy.
                return self._jobs[self._active_job or self._queue[-1]]
            if len(self._queue) >= 16:
                raise CollectionBusyError("collection queue is full")
            job = CollectionJob(kind, argument)
            if kind == "enable" and argument:
                job.provider = argument.removesuffix("-local").replace("claude-code", "claude_code").replace("vscode-copilot", "vscode_copilot")
            self._jobs[job.job_id] = job
            self._queue.append(job.job_id)
            self._latest_job = job.job_id
            self._progress_revision += 1
            self._save_jobs()
            self._condition.notify()
            return job

    def execute(self, kind: str, argument: str | None = None) -> Any:
        job = self.submit(kind, argument)
        job.done.wait()
        if job.exception is not None:
            raise job.exception
        return job.value

    def collect(self) -> None:
        self.execute("refresh")

    def _report(self, job: CollectionJob, *, stage: str | None = None, **values: Any) -> None:
        if self._collection_stop.is_set():
            raise ScanInterrupted("collection was stopped")
        with self.lock:
            if job.update(stage, **values):
                self._progress_revision += 1

    def _run(self, job: CollectionJob) -> Any:
        services = self.services
        value: Any
        if job.kind == "refresh":
            services.collection.run_once(self._collection_stop.is_set)
        elif job.kind == "enable":
            services.collection.enable(str(job.argument), self._collection_stop.is_set)
        elif job.kind == "disable":
            services.source_repository.disable_auto_import(str(job.argument))
            services.collection.bump()
        elif job.kind == "approve":
            value = services.ingestion.approve(str(job.argument))
            services.collection.bump()
            return value
        elif job.kind == "rescan":
            self._report(job, files_total=1, files_completed=0)
            value = services.ingestion.rescan(str(job.argument))
            self._report(job, files_completed=1)
            services.collection.bump()
            return value
        elif job.kind == "rebuild":
            value = services.ingestion.rebuild()
            services.collection.bump()
            return value
        elif job.kind == "discover":
            value = services.discovery.discover()
            self._publish_discovery()
            return value
        else:
            raise ValueError("unknown collection operation")
        return None

    @staticmethod
    def _safe_error(error: Exception) -> str:
        if isinstance(error, SourceNotFoundError):
            return "Unknown source"
        if isinstance(error, SourceNotApprovedError):
            return "Source is not approved"
        if isinstance(error, UnsupportedSourceError):
            return "Source is not supported"
        if isinstance(error, (ValueError, OSError)):
            return "Source is unavailable"
        return "The import could not be completed. Refresh data to retry."

    def _work(self) -> None:
        assert self._engine is not None
        try:
            self._services = self._build_services(Session(self._engine))
            self.services.source_repository.upgrade_codex_parser_versions()
            # A database created before the projection existed has raw events
            # but no canonical rows. Backfill once, before the app reports
            # ready, so the first dashboard request is already fast.
            if self.services.usage_repository.ensure_canonical():
                optimize_database(self._engine)
            self.services.collection.on_change = self._sync_status
            self.services.discovery.on_change = self._on_discovery_change
            self._sync_status()
        except Exception as error:  # noqa: BLE001 — isolate collection failures
            self._startup_error = error
            self._ready.set()
            return
        self._ready.set()
        try:
            while not self._collection_stop.is_set():
                with self._condition:
                    if not self._queue:
                        self._condition.wait(self.settings.scan_interval_seconds)
                    if self._collection_stop.is_set():
                        break
                    if not self._queue:
                        self.submit("refresh", automatic=True)
                    job = self._jobs[self._queue.popleft()]
                    self._active_job = job.job_id
                    job.state = "running"
                    job.started_at = now()
                    self._save_jobs()
                try:
                    with reporting(lambda current=job, **values: self._report(current, **values)):
                        job.value = self._run(job)
                    if isinstance(job.value, ImportOutcome):
                        job.result = {"inserted_events": job.value.inserted_events,
                                      "duplicate_events": job.value.duplicate_events,
                                      "partial_final_record": job.value.partial_final_record,
                                      "unsupported_records": job.value.unsupported_records}
                    elif isinstance(job.value, RebuildOutcome):
                        job.result = {"inserted_events": job.value.inserted_events,
                                      "failed_source_ids": list(job.value.failed_source_ids)}
                    failed = (len(job.value.failed_source_ids) if isinstance(job.value, RebuildOutcome)
                              else self.services.collection.failed_source_count if job.kind in {"enable", "refresh"} else 0)
                    job.state = "completed_with_errors" if failed else "completed"
                    job.stage = "complete"
                    if self._engine is not None and (
                        isinstance(job.value, RebuildOutcome)
                        or (isinstance(job.value, ImportOutcome) and job.value.inserted_events)
                    ):
                        optimize_database(self._engine)
                except ScanInterrupted as error:
                    self.services.session.rollback()
                    job.state = job.stage = "interrupted"
                    job.error = "Collection was interrupted; refresh data to continue."
                    job.exception = error
                except Exception as error:  # noqa: BLE001 — isolate collection failures
                    self.services.session.rollback()
                    job.state = job.stage = "failed"
                    self.services.collection.data_version += 1
                    job.error = self._safe_error(error)
                    job.exception = error.with_traceback(None)
                    job.exception.__cause__ = job.exception.__context__ = None
                    logging.getLogger(__name__).error("Collection operation failed; refresh data to retry")
                finally:
                    try:
                        self.services.discovery.refresh_states()
                        self._publish_discovery()
                        self._sync_status()
                    except Exception:  # noqa: BLE001 — always release job waiters
                        self.services.session.rollback()
                        job.state = job.stage = "failed"
                        job.error = "The import status could not be updated. Refresh data to retry."
                        job.exception = RuntimeError(job.error)
                    with self._condition:
                        job.finished_at = now()
                        self._active_job = None
                        self._progress_revision += 1
                        self._save_jobs()
                        job.done.set()
                        while len(self._jobs) > 50:
                            oldest = next(iter(self._jobs))
                            if not self._jobs[oldest].done.is_set():
                                break
                            del self._jobs[oldest]
        finally:
            with self.lock:
                for job_id in self._queue:
                    job = self._jobs[job_id]
                    job.state = job.stage = "interrupted"
                    job.finished_at = now()
                    job.exception = ScanInterrupted("collection was stopped")
                    job.done.set()
                self._save_jobs()
            self._discovery_ready.set()
            self.services.session.close()

    def stop(self) -> None:
        self._collection_stop.set()
        with self._condition:
            self._condition.notify_all()
        if self.collection_thread is not None:
            self.collection_thread.join()
            self.collection_thread = None
        self._services = None
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
