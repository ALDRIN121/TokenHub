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


class SourceNotFoundError(LookupError):
    """The source has not been discovered and stored."""


class SourceNotApprovedError(ValueError):
    """Source access has not been approved or has been disabled."""


class UnsupportedSourceError(ValueError):
    """This milestone cannot scan the selected source."""


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

    def approve(self, source_id: str) -> SourceRecord:
        source = self._source(source_id)
        self._supported_connector(source)
        # The repository validates containment and writes paths in its approval transaction.
        return self.source_repository.approve(source_id)

    def rescan(self, source_id: str, *, record_unchanged: bool = True) -> ImportOutcome:
        source = self._source(source_id)
        connector = self._supported_connector(source)
        if (
            source.state not in APPROVED_SOURCE_STATES
            or source.canonical_path is None
            or source.approved_root is None
        ):
            raise SourceNotApprovedError("source must be approved before scanning")
        cursor = self.usage_repository.current_cursor(source_id)
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
            result = connector.scan(descriptor, cursor)
        except (OSError, ValueError):
            self.source_repository.set_state(source_id, SourceState.ERROR)
            raise
        if result.cursor is None or result.state not in {
            SourceState.HEALTHY,
            SourceState.PARTIAL,
        }:
            raise UnsupportedSourceError("connector did not return a supported scan")
        return self.usage_repository.persist_scan(
            list(result.events),
            result.cursor,
            state=result.state,
            partial_final_record=result.partial_final_record,
            unsupported_records=result.unsupported_records,
            record_import=(
                record_unchanged or result.cursor != cursor
                or result.state.value != source.state
            ),
        )

    def rebuild(self) -> RebuildOutcome:
        sources = self.source_repository.approved_sources()
        self.usage_repository.clear_normalized()
        imports: list[ImportOutcome] = []
        failures: list[str] = []
        for source in sources:
            try:
                self._supported_connector(source)
            except UnsupportedSourceError:
                continue
            try:
                imports.append(self.rescan(source.source_id))
            except (OSError, ValueError, SourceNotFoundError):
                failures.append(source.source_id)
        return RebuildOutcome(imports=tuple(imports), failed_source_ids=tuple(failures))

    def _source(self, source_id: str) -> SourceRecord:
        try:
            return self.source_repository.get(source_id)
        except LookupError as error:
            raise SourceNotFoundError("source has not been discovered") from error

    def _supported_connector(self, source: SourceRecord) -> UsageConnector:
        # Other providers remain presence-only even if their metadata claims scan support.
        if (
            source.connector_id != "codex-local"
            or source.provider != Provider.CODEX.value
        ):
            raise UnsupportedSourceError("source is discovery-only")
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
            not source.scan_supported
            or not capabilities.scan_supported
            or source.source_type != capabilities.source_type
            or source.parser_version != capabilities.parser_version
        ):
            raise UnsupportedSourceError("source format is unsupported")
        return connector
