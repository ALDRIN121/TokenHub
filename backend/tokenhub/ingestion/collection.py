"""Periodic collection through the existing approval and incremental scan gates."""

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from tokenhub.database.models import SourceRecord
from tokenhub.database.repositories import SourceRepository, UsageRepository
from tokenhub.discovery.service import DiscoveryService
from tokenhub.domain.models import APPROVED_SOURCE_STATES, SourceState, SyncCursor
from tokenhub.ingestion.progress import ScanInterrupted, report
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
        #: Increases whenever data the UI shows may have changed; an idle scan
        #: leaves it alone so clients can poll it instead of refetching everything.
        self.data_version = 0
        self.on_change: Callable[[], None] | None = None
        self._seen_files: dict[str, tuple[int, ...]] = {}

    def bump(self) -> None:
        self.data_version += 1
        if self.on_change is not None:
            self.on_change()

    def enable(self, connector_id: str, should_stop: Callable[[], bool] = lambda: False) -> None:
        # Renewal is an explicit action against freshly discovered, safely
        # anchored directories, never a silent response to an expired grant.
        results = self.discovery.discover()
        candidates = [
                self.discovery.candidate(source.source_id)
                for result in results if result.connector_id == connector_id
                for source in result.sources if source.scan_supported and source.state is not SourceState.DISABLED
        ]
        if not candidates:
            raise UnsupportedSourceError("no supported source was discovered")
        roots = {str(candidate.approved_root): candidate for candidate in candidates}
        for candidate in roots.values():
            self.sources.enable_auto_import(candidate)
        for candidate in candidates:
            if should_stop():
                return
            saved = self.sources.get(candidate.source_id)
            if saved.state == SourceState.DISCOVERED.value or (
                saved.state in {state.value for state in APPROVED_SOURCE_STATES}
                and (saved.approved_root, saved.approved_root_device, saved.approved_root_inode)
                != (str(candidate.approved_root), candidate.approved_root_device, candidate.approved_root_inode)
            ):
                self.ingestion.approve(candidate.source_id, renew=True)
                self._seen_files.pop(candidate.source_id, None)
        self.bump()
        self.run_once(should_stop, connector_id=connector_id)

    def run_once(self, should_stop: Callable[[], bool] = lambda: False, *, connector_id: str | None = None) -> None:
        """Scan once; ``should_stop`` is checked between sources so shutdown stays prompt."""
        report("discovering")
        failures: set[str] = set()
        changed = False
        roots = {(root.connector_id, root.approved_root): root for root in self.sources.auto_import_roots()}
        for result in self.discovery.discover():
            if connector_id is not None and result.connector_id != connector_id:
                continue
            for view in result.sources:
                if should_stop():
                    return
                if not view.scan_supported or view.state is not SourceState.DISCOVERED:
                    continue
                candidate = self.discovery.candidate(view.source_id)
                root = roots.get((result.connector_id, str(candidate.approved_root)))
                if root is None:
                    continue
                if (
                    str(candidate.approved_root) != root.approved_root
                    or candidate.approved_root_device != root.approved_root_device
                    or candidate.approved_root_inode != root.approved_root_inode
                ):
                    continue
                try:
                    self.ingestion.approve(view.source_id)
                    changed = True
                except Exception:  # noqa: BLE001 - isolate one source's approval failure
                    self.sources.session.rollback()
                    failures.add(view.source_id)
                    changed = True

        cursors = self.usage.current_cursors()
        approved = [source for source in self.sources.approved_sources()
                    if source.scan_supported and (connector_id is None or source.connector_id == connector_id)]
        report(files_total=len(approved), files_completed=0, skipped_files=0)
        skipped = 0
        for index, source in enumerate(approved):
            report("reading", files_completed=index, provider=source.provider, bytes_read=0,
                   bytes_total=None, records_saved=0, records_total=None)
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
                cursor = cursors.get(source.source_id)
                if self._unchanged(source, metadata, identity, cursor):
                    skipped += 1
                    report(skipped_files=skipped)
                    continue
                before_state = source.state
                outcome = self.ingestion.rescan(source.source_id, record_unchanged=False)
                self._seen_files[source.source_id] = identity
                if (outcome.visible_change
                    or cursor is None
                    or outcome.cursor.source_unsupported_records != cursor.source_unsupported_records
                    or self.sources.get(source.source_id).state != before_state):
                    changed = True
                    self.bump()
            except ScanInterrupted:
                raise
            except FileNotFoundError:
                changed = True
                # A removed source is not a failure: the provider deleted it.
                self._seen_files.pop(source.source_id, None)
                self.sources.session.rollback()
                self.sources.set_state(source.source_id, SourceState.SOURCE_MISSING)
            except Exception:  # noqa: BLE001 - one bad source must not starve the others
                changed = True
                self._seen_files.pop(source.source_id, None)
                self.sources.session.rollback()
                self.sources.set_state(source.source_id, SourceState.ERROR)
                failures.add(source.source_id)
        report(files_completed=len(approved))
        if changed or len(failures) != self.failed_source_count:
            self.bump()
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
        roots = self.sources.auto_import_roots()
        connectors = sorted({root.connector_id for root in roots})
        grants = {(root.connector_id, root.approved_root): (root.approved_root_device, root.approved_root_inode)
                  for root in roots}
        needs_approval: set[str] = set()
        candidates = [self.discovery.candidate(view.source_id)
                      for result in self.discovery.last_results or []
                      for view in result.sources if view.scan_supported and view.state is not SourceState.DISABLED]
        # Status uses the collector's cached discovery metadata: polling does
        # not walk or open provider files.
        with self.sources.session.begin():
            saved = self.sources._existing_sources([candidate.source_id for candidate in candidates])
            for candidate in candidates:
                identity = (candidate.approved_root_device, candidate.approved_root_inode)
                grant = grants.get((candidate.connector_id, str(candidate.approved_root)))
                previous = saved[candidate.source_id]
                if (candidate.connector_id in connectors and grant != identity) or (
                    previous.canonical_path is not None
                    and (previous.approved_root, previous.approved_root_device, previous.approved_root_inode)
                    != (str(candidate.approved_root), *identity)
                ):
                    needs_approval.add(candidate.connector_id)
        return {
            "scan_interval_seconds": interval,
            "codex_auto_import": "codex-local" in connectors,
            "auto_import_connectors": connectors,
            "requires_reapproval_connectors": sorted(needs_approval),
            "last_scan_at": self.last_scan_at.isoformat() if self.last_scan_at else None,
            "failed_source_count": self.failed_source_count,
            "data_version": self.data_version,
        }
