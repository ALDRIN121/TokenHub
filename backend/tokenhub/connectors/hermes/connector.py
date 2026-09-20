"""Presence-only Hermes Agent detection."""

import hashlib

from tokenhub.connectors.protocol import (
    ConnectorCapabilities,
    DetectionResult,
    DiscoveryContext,
    SafeSourceView,
    ScanResult,
    select_root,
)
from tokenhub.domain.models import Provider, SourceDescriptor, SourceState, SyncCursor


class HermesConnector:
    connector_id = "hermes-local"
    display_name = "Hermes Agent"
    provider = Provider.HERMES

    def detect(self, context: DiscoveryContext) -> DetectionResult:
        evidence: list[str] = []
        if context.which("hermes") is not None:
            evidence.append("executable_on_path")
        root = select_root(context, "HERMES_HOME", ".hermes")
        if root.is_dir() and not root.is_symlink():
            evidence.append("known_root_exists")
        sources = self.discover_sources(context)
        if sources:
            evidence.append("state_database_found")
        return DetectionResult(
            connector_id=self.connector_id,
            display_name=self.display_name,
            provider=self.provider,
            state=SourceState.UNSUPPORTED if sources else (
                SourceState.DISCOVERED if evidence else SourceState.SOURCE_MISSING
            ),
            evidence_codes=tuple(evidence),
            sources=tuple(SafeSourceView.model_validate(source.safe_view()) for source in sources),
        )

    def discover_sources(self, context: DiscoveryContext) -> list[SourceDescriptor]:
        root = select_root(context, "HERMES_HOME", ".hermes")
        candidate = root / "state.db"
        if not root.is_dir() or root.is_symlink() or candidate.is_symlink() or not candidate.is_file():
            return []
        canonical = candidate.resolve(strict=True)
        fingerprint = hashlib.sha256(str(canonical).encode("utf-8")).hexdigest()
        return [
            SourceDescriptor(
                source_id=f"{self.connector_id}:{fingerprint}",
                connector_id=self.connector_id,
                provider=self.provider,
                display_name="Hermes state database",
                canonical_path=canonical,
                approved_root=root.resolve(strict=True),
                source_type="sqlite",
                path_fingerprint=fingerprint,
                state=SourceState.UNSUPPORTED,
                evidence_codes=("state_database_found",),
                scan_supported=False,
            )
        ]

    def scan(self, source: SourceDescriptor, cursor: SyncCursor | None) -> ScanResult:
        return ScanResult(state=SourceState.UNSUPPORTED, reason_code="presence_only")

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(scan_supported=False, source_type="sqlite")
