"""Discovery orchestration with private, process-local source handoff."""

from collections.abc import Callable
from dataclasses import replace

from tokenhub.connectors.protocol import (
    DetectionResult,
    DiscoveryContext,
    SafeSourceView,
)
from tokenhub.connectors.registry import ConnectorRegistry
from tokenhub.database.repositories import SourceRepository
from tokenhub.domain.models import SourceDescriptor, SourceState
from tokenhub.ingestion.progress import ScanInterrupted, report


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
        self._last_results: list[DetectionResult] | None = None
        self._signature: tuple[object, ...] | None = None
        #: Called when providers, sources, or their states differ from the last pass.
        self.on_change: Callable[[], None] | None = None

    @property
    def last_results(self) -> list[DetectionResult] | None:
        """The most recent pass, or ``None`` before the first one."""
        return self._last_results

    def discover(self) -> list[DetectionResult]:
        # One shared memo per pass: ``detect`` and ``discover_sources`` then walk
        # each provider tree once instead of twice.
        context = replace(self.context, source_memo={})
        results = self.registry.discover_all(context)
        self._candidates.clear()
        for connector, result in zip(self.registry.connectors, results, strict=True):
            report()
            if result.state is SourceState.ERROR:
                continue
            try:
                candidates = connector.discover_sources(context)
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
            except ScanInterrupted:
                raise
            except Exception:  # noqa: BLE001 - keep other providers visible
                result.state = SourceState.ERROR
                result.evidence_codes = ("discovery_error",)
                result.sources = ()
        self._last_results = results
        signature = self._result_signature(results)
        changed = signature != self._signature
        self._signature = signature
        if changed and self.on_change is not None:
            self.on_change()
        return results

    @staticmethod
    def _result_signature(results: list[DetectionResult]) -> tuple[object, ...]:
        return tuple(
            (
                result.connector_id,
                result.state,
                result.evidence_codes,
                tuple((source.source_id, source.state) for source in result.sources),
            )
            for result in results
        )

    def refresh_states(self) -> None:
        """Remember states already published by a scan, without walking again."""
        if self._last_results is None:
            return
        with self.source_repository.session.begin():
            stored = self.source_repository._existing_sources([
                source.source_id for result in self._last_results for source in result.sources
            ])
            for result in self._last_results:
                result.sources = tuple(source.model_copy(update={"state": SourceState(stored[source.source_id].state)})
                    if source.source_id in stored else source for source in result.sources)
        self._signature = self._result_signature(self._last_results)

    def candidate(self, source_id: str) -> SourceDescriptor:
        return self._candidates[source_id]
