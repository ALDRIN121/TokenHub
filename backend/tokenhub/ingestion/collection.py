"""Periodic collection through the existing approval and incremental scan gates."""

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from tokenhub.database.models import SourceRecord
from tokenhub.database.repositories import SourceRepository, UsageRepository
from tokenhub.discovery.service import DiscoveryService
from tokenhub.domain.models import SourceState, SyncCursor
from tokenhub.ingestion.service import IngestionService, UnsupportedSourceError
from tokenhub.security.paths import open_source_path


class CollectionService:
    def __init__(
        self,
        sources: SourceRepository,
        usage: UsageRepository,
        discovery: DiscoveryService,
        ingestion: IngestionService,
    ) -> None:
        self.sources = sources
        self.usage = usage
        self.discovery = discovery
        self.ingestion = ingestion
        self.last_scan_at: datetime | None = None
        self.failed_source_count = 0
        self._seen_files: dict[str, tuple[int, ...]] = {}

    def enable_codex(self) -> None:
        self.enable("codex-local")

    def enable(self, connector_id: str) -> None:
        results = self.discovery.discover()
        candidate = next(
            (
                self.discovery.candidate(source.source_id)
                for result in results if result.connector_id == connector_id
                for source in result.sources if source.scan_supported
            ),
            None,
        )
        if candidate is None:
            raise UnsupportedSourceError("no supported source was discovered")
        self.sources.enable_auto_import(candidate)
        self.run_once()

    def run_once(self, should_stop: Callable[[], bool] = lambda: False) -> None:
        """Scan once; ``should_stop`` is checked between sources so shutdown stays prompt."""
        failures: set[str] = set()
        roots = {root.connector_id: root for root in self.sources.auto_import_roots()}
        for result in self.discovery.discover():
            root = roots.get(result.connector_id)
            if root is None:
                continue
            for view in result.sources:
                if should_stop():
                    return
                if not view.scan_supported or view.state is not SourceState.DISCOVERED:
                    continue
                candidate = self.discovery.candidate(view.source_id)
                if (
                    str(candidate.approved_root) != root.approved_root
                    or candidate.approved_root_device != root.approved_root_device
                    or candidate.approved_root_inode != root.approved_root_inode
                ):
                    continue
                try:
                    self.ingestion.approve(view.source_id)
                except Exception:  # noqa: BLE001 - isolate one source's approval failure
                    self.sources.session.rollback()
                    failures.add(view.source_id)

        for source in self.sources.approved_sources():
            if should_stop():
                return
            if not source.scan_supported:
                continue
            if (
                source.state == SourceState.SOURCE_MISSING.value
                and source.canonical_path is not None
                and not os.path.lexists(source.canonical_path)
            ):
                # The provider removed this file (for example Claude Code's own
                # session cleanup). Its imported usage stays; do not retry it
                # until the path exists again.
                continue
            try:
                if source.canonical_path is None or source.approved_root is None:
                    continue
                if source.approved_root_device is None or source.approved_root_inode is None:
                    raise ValueError("approved source has no trusted root identity")
                descriptor = open_source_path(
                    Path(source.canonical_path), Path(source.approved_root),
                    (source.approved_root_device, source.approved_root_inode),
                )
                try:
                    metadata = os.fstat(descriptor)
                finally:
                    os.close(descriptor)
                identity = self._identity(source, metadata)
                cursor = self.usage.current_cursor(source.source_id)
                if self._unchanged(source, metadata, identity, cursor):
                    continue
                self.ingestion.rescan(source.source_id, record_unchanged=False)
                self._seen_files[source.source_id] = identity
            except FileNotFoundError:
                # A removed source is not a failure: the provider deleted it.
                self._seen_files.pop(source.source_id, None)
                self.sources.session.rollback()
                self.sources.set_state(source.source_id, SourceState.SOURCE_MISSING)
            except Exception:  # noqa: BLE001 - one bad source must not starve the others
                self._seen_files.pop(source.source_id, None)
                self.sources.session.rollback()
                self.sources.set_state(source.source_id, SourceState.ERROR)
                failures.add(source.source_id)
        self.failed_source_count = len(failures)
        self.last_scan_at = datetime.now(UTC)

    @staticmethod
    def _identity(source: SourceRecord, metadata: os.stat_result) -> tuple[int, ...]:
        """Cheap change signature. SQLite sources include the write-ahead log,
        where active sessions keep usage that the main file does not show yet."""
        identity = (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
        if source.source_type != "sqlite" or source.canonical_path is None:
            return identity
        try:
            journal = os.lstat(source.canonical_path + "-wal")
        except OSError:
            return (*identity, -1)
        return (*identity, journal.st_dev, journal.st_ino, journal.st_size, journal.st_mtime_ns)

    def _unchanged(
        self,
        source: SourceRecord,
        metadata: os.stat_result,
        identity: tuple[int, ...],
        cursor: SyncCursor | None,
    ) -> bool:
        """Whether a healthy source can be skipped without reading it.

        After a restart the in-memory signature is empty, so JSONL sources also
        match the stored cursor: it records the mtime and the byte offset it
        finished at, which equals the size when nothing has been appended.
        """
        if (
            cursor is None
            or source.state not in {SourceState.HEALTHY.value, SourceState.PARTIAL.value}
            or cursor.parser_version != source.parser_version
        ):
            return False
        if self._seen_files.get(source.source_id) == identity and cursor.byte_offset <= metadata.st_size:
            return True
        return (
            source.source_type == "jsonl"
            and cursor.source_mtime_ns == metadata.st_mtime_ns
            and cursor.byte_offset == metadata.st_size
        )

    def status(self, interval: float) -> dict[str, object]:
        connectors = sorted(root.connector_id for root in self.sources.auto_import_roots())
        return {
            "scan_interval_seconds": interval,
            "codex_auto_import": "codex-local" in connectors,
            "auto_import_connectors": connectors,
            "last_scan_at": self.last_scan_at.isoformat() if self.last_scan_at else None,
            "failed_source_count": self.failed_source_count,
        }
