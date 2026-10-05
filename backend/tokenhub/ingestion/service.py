"""Approval-gated ingestion through the normalized connector boundary."""

from tokenhub.connectors.protocol import UsageConnector
from tokenhub.connectors.registry import ConnectorRegistry
from tokenhub.database.models import SourceRecord
from tokenhub.database.repositories import SourceRepository, UsageRepository
from tokenhub.domain.models import (
    APPROVED_SOURCE_STATES,
    ImportOutcome,
    Provider,
    RebuildOutcome,
    SourceDescriptor,
    SourceState,
)
from tokenhub.ingestion.progress import report


class SourceNotFoundError(LookupError):
    """The source has not been discovered and stored."""


class SourceNotApprovedError(ValueError):
    """Source access has not been approved or has been disabled."""


class UnsupportedSourceError(ValueError):
    """The selected connector cannot scan this source format."""


class IngestionService:
    def __init__(
        self,
        source_repository: SourceRepository,
        usage_repository: UsageRepository,
        registry: ConnectorRegistry,
    ) -> None:
        self.source_repository = source_repository
        self.usage_repository = usage_repository
        self.registry = registry

    def approve(self, source_id: str, *, renew: bool = False) -> SourceRecord:
        source = self._source(source_id)
        self._supported_connector(source)
        # The repository validates containment and writes paths in its approval transaction.
        return self.source_repository.approve(source_id, renew=renew)

    def rescan(self, source_id: str, *, record_unchanged: bool = True, rebuild: bool = False) -> ImportOutcome:
        source = self._source(source_id)
        connector = self._supported_connector(source)
        if (
            source.state not in APPROVED_SOURCE_STATES
            or source.canonical_path is None
            or source.approved_root is None
        ):
            raise SourceNotApprovedError("source must be approved before scanning")
        cursor = None if rebuild else self.usage_repository.current_cursor(source_id)
        try:
            # Pass database strings directly: SourceDescriptor checks their original spelling.
            descriptor = SourceDescriptor(
                source_id=source.source_id,
                connector_id=source.connector_id,
                provider=Provider(source.provider),
                display_name=source.display_name,
                canonical_path=source.canonical_path,
                approved_root=source.approved_root,
                source_type=source.source_type,
                path_fingerprint=source.path_fingerprint,
                state=SourceState(source.state),
                scan_supported=source.scan_supported,
                parser_version=source.parser_version,
                approved_root_device=source.approved_root_device,
                approved_root_inode=source.approved_root_inode,
            )
            report("reading", provider=source.provider, bytes_read=0, bytes_total=None,
                   records_saved=0, records_total=None)
            result = connector.scan(descriptor, cursor)
        except (OSError, ValueError):
            self.source_repository.set_state(source_id, SourceState.ERROR)
            raise
        if result.cursor is None or result.state not in {
            SourceState.HEALTHY,
            SourceState.PARTIAL,
        }:
            raise UnsupportedSourceError("connector did not return a supported scan")
        outcome = self.usage_repository.persist_scan(
            list(result.events),
            result.cursor,
            state=result.state,
            partial_final_record=result.partial_final_record,
            unsupported_records=result.unsupported_records,
            replace_events=rebuild or result.replace_events,
            record_identity_aliases=result.record_identity_aliases,
            record_import=(
                record_unchanged or result.cursor != cursor
                or result.state.value != source.state
            ),
        )

        report(inserted_delta=outcome.inserted_events, duplicate_delta=outcome.duplicate_events,
               unsupported_delta=outcome.unsupported_records)
        return outcome

    def rebuild(self) -> RebuildOutcome:
        sources = self.source_repository.approved_sources()
        self.usage_repository.remove_disabled_usage()
        imports: list[ImportOutcome] = []
        failures: list[str] = []
        report("discovering", files_total=len(sources), files_completed=0)
        for index, source in enumerate(sources):
            report("reading", files_completed=index, provider=source.provider)
            try:
                self._supported_connector(source)
            except UnsupportedSourceError:
                continue
            try:
                imports.append(self.rescan(source.source_id, rebuild=True))
            except (OSError, ValueError, SourceNotFoundError):
                failures.append(source.source_id)
        report(files_completed=len(sources))
        return RebuildOutcome(imports=tuple(imports), failed_source_ids=tuple(failures))

    def _source(self, source_id: str) -> SourceRecord:
        try:
            return self.source_repository.get(source_id)
        except LookupError as error:
            raise SourceNotFoundError("source has not been discovered") from error

    def _supported_connector(self, source: SourceRecord) -> UsageConnector:
        connector = next(
            (
                item
                for item in self.registry.connectors
                if item.connector_id == source.connector_id
            ),
            None,
        )
        if connector is None:
            raise UnsupportedSourceError("source connector is unavailable")
        capabilities = connector.capabilities()
        if (
            source.provider != getattr(connector, "provider", None)
            or not source.scan_supported
            or not capabilities.scan_supported
            or source.source_type != capabilities.source_type
            or source.parser_version != capabilities.parser_version
        ):
            raise UnsupportedSourceError("source format is unsupported")
        return connector
