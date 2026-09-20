"""Discovery orchestration with private, process-local source handoff."""

from tokenhub.connectors.protocol import (
    DetectionResult,
    DiscoveryContext,
    SafeSourceView,
)
from tokenhub.connectors.registry import ConnectorRegistry
from tokenhub.database.repositories import SourceRepository
from tokenhub.domain.models import SourceDescriptor, SourceState


class DiscoveryService:
    def __init__(
        self,
        registry: ConnectorRegistry,
        source_repository: SourceRepository,
        context: DiscoveryContext,
    ) -> None:
        self.registry = registry
        self.source_repository = source_repository
        self.context = context
        self._candidates: dict[str, SourceDescriptor] = {}

    def discover(self) -> list[DetectionResult]:
        results = self.registry.discover_all(self.context)
        self._candidates.clear()
        for connector, result in zip(self.registry.connectors, results, strict=True):
            if result.state is SourceState.ERROR:
                continue
            try:
                candidates = connector.discover_sources(self.context)
                saved_sources = self.source_repository.upsert_discoveries(candidates)
                views: list[SafeSourceView] = [
                    SafeSourceView.model_validate(
                        {**candidate.safe_view(), "state": saved.state}
                    )
                    for candidate, saved in zip(candidates, saved_sources, strict=True)
                ]
                self._candidates.update(
                    {candidate.source_id: candidate for candidate in candidates}
                )
                result.sources = tuple(views)
            except Exception:  # noqa: BLE001 - keep other providers visible
                result.state = SourceState.ERROR
                result.evidence_codes = ("discovery_error",)
                result.sources = ()
        return results

    def candidate(self, source_id: str) -> SourceDescriptor:
        return self._candidates[source_id]
