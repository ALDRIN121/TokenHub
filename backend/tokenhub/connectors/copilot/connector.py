"""Approval-gated discovery of VS Code Copilot Chat sessions."""

import hashlib
import os
import re
import stat
from pathlib import Path

from tokenhub.connectors.copilot import PARSER_VERSION
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
from tokenhub.discovery.locations import AgentLayout, ApplicationHint
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
    open_directory_entry,
    stat_directory_entry,
)

_WORKSPACE = re.compile(r"[0-9a-f]{32}", re.IGNORECASE)
_SESSION_FILE = re.compile(
    r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\.jsonl", re.IGNORECASE
)


#: VS Code keeps user data per edition. Copilot chat sessions live under
#: ``User/workspaceStorage/<hash>/chatSessions`` in whichever edition wrote them,
#: and ``VSCODE_USER_DATA_DIR`` re-roots the stable edition.
LAYOUT = AgentLayout(
    agent_id="vscode-copilot-local",
    display_name="VS Code Copilot",
    env_keys=("VSCODE_USER_DATA_DIR",),
    names=(
        "Library/Application Support/Code",
        ".config/Code",
        "AppData/Roaming/Code",
    ),
    platform_names={
        "darwin": ("~/Library/Application Support/Code",),
        "win32": ("%APPDATA%/Code", "~/AppData/Roaming/Code"),
        "linux": ("$XDG_CONFIG_HOME/Code", "~/.config/Code"),
    },
    # An Insiders or portable install keeps its own user-data directory; the
    # installed bundle is what tells discovery to look there.
    applications=(
        ApplicationHint(
            "Visual Studio Code - Insiders",
            (
                "~/Library/Application Support/Code - Insiders",
                "%APPDATA%/Code - Insiders",
                "$XDG_CONFIG_HOME/Code - Insiders",
            ),
        ),
    ),
    process_names=("code",),
    markers=("User/workspaceStorage",),
)


class CopilotConnector:
    connector_id = "vscode-copilot-local"
    display_name = "VS Code Copilot"
    provider = Provider.VSCODE_COPILOT
    layout = LAYOUT

    def detect(self, context: DiscoveryContext) -> DetectionResult:
        roots = locate_locations(context, LAYOUT)
        configured = any(
            (root.path / "User" / "globalStorage" / "github.copilot-chat").is_dir()
            and not (root.path / "User" / "globalStorage" / "github.copilot-chat").is_symlink()
            for root in roots
        )
        sources = self.discover_sources(context)
        evidence: list[str] = []
        if roots and (configured or sources):
            evidence.append("known_root_exists")
        if configured:
            evidence.append("configuration_found")
        if sources:
            evidence.append("session_source_found")
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
        sources: list[SourceDescriptor] = []
        for data_root in find_layout_roots(context, LAYOUT):
            root = data_root / "User" / "workspaceStorage"
            if not root.is_dir() or root.is_symlink():
                continue
            sources.extend(self._scan_workspace_storage(root))
        return sources

    def _scan_workspace_storage(self, root: Path) -> list[SourceDescriptor]:
        sources: list[SourceDescriptor] = []
        with anchor_directory(root) as anchored:
            for workspace in sorted(list_directory(anchored.descriptor)):
                if not _WORKSPACE.fullmatch(workspace):
                    continue
                try:
                    metadata = stat_directory_entry(workspace, anchored.descriptor)
                except FileNotFoundError:
                    continue
                if not stat.S_ISDIR(metadata.st_mode):
                    continue
                workspace_fd = open_directory_entry(workspace, anchored.descriptor)
                try:
                    try:
                        chats = stat_directory_entry("chatSessions", workspace_fd)
                    except FileNotFoundError:
                        continue
                    if not stat.S_ISDIR(chats.st_mode):
                        continue
                    chats_fd = open_directory_entry("chatSessions", workspace_fd)
                    try:
                        for filename in sorted(list_directory(chats_fd)):
                            if not _SESSION_FILE.fullmatch(filename):
                                continue
                            try:
                                file_stat = stat_directory_entry(filename, chats_fd)
                            except FileNotFoundError:
                                continue
                            if not stat.S_ISREG(file_stat.st_mode):
                                continue
                            path = anchored.path / workspace / "chatSessions" / filename
                            fingerprint = hashlib.sha256(str(path).encode()).hexdigest()
                            sources.append(SourceDescriptor(
                                source_id=f"{self.connector_id}:{fingerprint}",
                                connector_id=self.connector_id,
                                provider=self.provider,
                                display_name="VS Code Copilot chat session",
                                canonical_path=path,
                                approved_root=anchored.path,
                                source_type="jsonl",
                                path_fingerprint=fingerprint,
                                evidence_codes=("session_source_found",),
                                scan_supported=True,
                                parser_version=PARSER_VERSION,
                                approved_root_device=anchored.device,
                                approved_root_inode=anchored.inode,
                            ))
                    finally:
                        os.close(chats_fd)
                finally:
                    os.close(workspace_fd)
        return sources

    def scan(self, source: SourceDescriptor, cursor: SyncCursor | None) -> ScanResult:
        if (
            source.connector_id != self.connector_id
            or source.provider is not self.provider
            or not source.scan_supported
            or source.source_type != "jsonl"
            or source.parser_version != PARSER_VERSION
            or source.approved_root_device is None
            or source.approved_root_inode is None
        ):
            return ScanResult(state=SourceState.UNSUPPORTED, reason_code="unsupported_source")
        if source.state not in APPROVED_SOURCE_STATES:
            return ScanResult(state=source.state, reason_code="source_not_approved")
        if cursor is not None and cursor.source_id != source.source_id:
            raise ValueError("cursor belongs to a different source")
        from tokenhub.connectors.copilot.parser import parse_copilot_chat_jsonl

        return parse_copilot_chat_jsonl(source, cursor)

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            scan_supported=True, source_type="jsonl", parser_version=PARSER_VERSION
        )
