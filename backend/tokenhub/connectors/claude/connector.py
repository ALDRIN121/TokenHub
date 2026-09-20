"""Presence-only Claude Code detection."""

from tokenhub.connectors.protocol import (
    ConnectorCapabilities,
    DetectionResult,
    DiscoveryContext,
    ScanResult,
    select_root,
)
from tokenhub.domain.models import Provider, SourceDescriptor, SourceState, SyncCursor


class ClaudeConnector:
    connector_id = "claude-code-local"
    display_name = "Claude Code"
    provider = Provider.CLAUDE_CODE

    def detect(self, context: DiscoveryContext) -> DetectionResult:
        evidence: list[str] = []
        if context.which("claude") is not None:
            evidence.append("executable_on_path")
        root = select_root(context, "CLAUDE_CONFIG_DIR", ".claude")
        if root.is_dir() and not root.is_symlink():
            evidence.append("known_root_exists")
            settings = root / "settings.json"
            if not settings.is_symlink() and settings.is_file():
                evidence.append("configuration_found")
        return DetectionResult(
            connector_id=self.connector_id,
            display_name=self.display_name,
            provider=self.provider,
            state=SourceState.DISCOVERED if evidence else SourceState.SOURCE_MISSING,
            evidence_codes=tuple(evidence),
        )

    def discover_sources(self, context: DiscoveryContext) -> list[SourceDescriptor]:
        return []

    def scan(self, source: SourceDescriptor, cursor: SyncCursor | None) -> ScanResult:
        return ScanResult(state=SourceState.UNSUPPORTED, reason_code="presence_only")

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(scan_supported=False)
