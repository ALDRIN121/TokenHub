"""Approval-gated discovery of Antigravity conversation databases."""

import hashlib
import stat
from pathlib import Path

from tokenhub.connectors.antigravity import PARSER_VERSION
from tokenhub.connectors.protocol import (
    ConnectorCapabilities,
    DetectionResult,
    DiscoveryContext,
    SafeSourceView,
    ScanResult,
    find_layout_roots,
    locate_locations,
    memoized_sources,
)
from tokenhub.discovery.locations import AgentLayout
from tokenhub.domain.models import (
    APPROVED_SOURCE_STATES,
    Provider,
    SourceDescriptor,
    SourceState,
    SyncCursor,
)
from tokenhub.security.paths import (
    anchor_directory,
    list_directory,
    stat_directory_entry,
)

#: Antigravity's CLI and desktop app both keep conversations under its Gemini
#: home: ``conversations/`` holds the encrypted ``.pb`` files plus the ``.db``
#: databases TokenHub reads, and ``conversation_summaries.db`` is the index.
LAYOUT = AgentLayout(
    agent_id="antigravity-local",
    display_name="Antigravity",
    env_keys=("ANTIGRAVITY_HOME",),
    names=(".gemini/antigravity",),
    process_names=("antigravity",),
    markers=("conversations", "conversation_summaries.db"),
)


def _has_unreadable_conversations(root: Path) -> bool:
    """Whether ``conversations`` holds saved chats in a format this connector cannot read.

    The desktop app saves ``.pb`` files that look encrypted (near-maximum entropy);
    only the ``.db`` databases carry the counters TokenHub reads. Only names are
    listed, never file contents.
    """
    conversations = root / "conversations"
    if not conversations.is_dir() or conversations.is_symlink():
        return False
    try:
        with anchor_directory(conversations) as anchored:
            return any(name.endswith(".pb") for name in list_directory(anchored.descriptor))
    except (OSError, ValueError):
        return False


class AntigravityConnector:
    connector_id = "antigravity-local"
    display_name = "Antigravity"
    provider = Provider.ANTIGRAVITY
    layout = LAYOUT

    def detect(self, context: DiscoveryContext) -> DetectionResult:
        roots = locate_locations(context, LAYOUT)
        evidence = []
        if roots:
            evidence.append("known_root_exists")
        sources = self.discover_sources(context)
        if sources:
            evidence.append("session_source_found")
        elif any(_has_unreadable_conversations(root.path) for root in roots):
            evidence.append("unsupported_session_format")
        return DetectionResult(
            connector_id=self.connector_id,
            display_name=self.display_name,
            provider=self.provider,
            state=SourceState.DISCOVERED if evidence else SourceState.SOURCE_MISSING,
            evidence_codes=tuple(evidence),
            sources=tuple(SafeSourceView.model_validate(source.safe_view()) for source in sources),
        )

    @memoized_sources
    def discover_sources(self, context: DiscoveryContext) -> list[SourceDescriptor]:
        sources = []
        for data_root in find_layout_roots(context, LAYOUT):
            conversations = data_root / "conversations"
            if not conversations.is_dir() or conversations.is_symlink():
                continue
            sources.extend(self._scan_conversations(conversations))
        return sources

    def _scan_conversations(self, root: Path) -> list[SourceDescriptor]:
        sources = []
        with anchor_directory(root) as anchored:
            for name in sorted(list_directory(anchored.descriptor)):
                if not name.endswith(".db"):
                    continue
                try:
                    metadata = stat_directory_entry(name, anchored.descriptor)
                except FileNotFoundError:
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    continue
                path = anchored.path / name
                fingerprint = hashlib.sha256(str(path).encode()).hexdigest()
                sources.append(SourceDescriptor(
                    source_id=f"{self.connector_id}:{fingerprint}",
                    connector_id=self.connector_id,
                    provider=self.provider,
                    display_name="Antigravity conversation",
                    canonical_path=path,
                    approved_root=anchored.path,
                    source_type="sqlite",
                    path_fingerprint=fingerprint,
                    evidence_codes=("session_source_found",),
                    scan_supported=True,
                    parser_version=PARSER_VERSION,
                    approved_root_device=anchored.device,
                    approved_root_inode=anchored.inode,
                ))
        return sources

    def scan(self, source: SourceDescriptor, cursor: SyncCursor | None) -> ScanResult:
        if (
            source.connector_id != self.connector_id
            or source.provider is not self.provider
            or not source.scan_supported
            or source.source_type != "sqlite"
            or source.parser_version != PARSER_VERSION
            or source.approved_root_device is None
            or source.approved_root_inode is None
        ):
            return ScanResult(state=SourceState.UNSUPPORTED, reason_code="unsupported_source")
        if source.state not in APPROVED_SOURCE_STATES:
            return ScanResult(state=source.state, reason_code="source_not_approved")
        if cursor is not None and cursor.source_id != source.source_id:
            raise ValueError("cursor belongs to a different source")
        from tokenhub.connectors.antigravity.parser import parse_antigravity_sqlite

        return parse_antigravity_sqlite(source, cursor)

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            scan_supported=True, source_type="sqlite", parser_version=PARSER_VERSION
        )
