"""Approval-gated discovery of VS Code Copilot Chat sessions."""

import hashlib
import os
import re
import stat
import sys
from pathlib import Path

from tokenhub.connectors.copilot import PARSER_VERSION
from tokenhub.connectors.protocol import (
    ConnectorCapabilities,
    DetectionResult,
    DiscoveryContext,
    SafeSourceView,
    ScanResult,
    find_root,
    memoized_sources,
)
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


def _user_data_dir(context: DiscoveryContext) -> Path:
    if sys.platform == "darwin":
        names = ("Library/Application Support/Code",)
    elif os.name == "nt":
        roaming = context.environment.get("APPDATA", str(context.home / "AppData/Roaming"))
        names = (str(Path(roaming) / "Code"), "AppData/Roaming/Code")
    else:
        config = context.environment.get("XDG_CONFIG_HOME", str(context.home / ".config"))
        names = (str(Path(config) / "Code"), ".config/Code")
    return find_root(
        context, "VSCODE_USER_DATA_DIR", names, marker="User/workspaceStorage", canonicalize=False
    )


class CopilotConnector:
    connector_id = "vscode-copilot-local"
    display_name = "VS Code Copilot"
    provider = Provider.VSCODE_COPILOT

    def detect(self, context: DiscoveryContext) -> DetectionResult:
        user = _user_data_dir(context) / "User"
        sessions = user / "workspaceStorage"
        extension = user / "globalStorage" / "github.copilot-chat"
        configured = extension.is_dir() and not extension.is_symlink()
        sources = self.discover_sources(context)
        evidence: list[str] = []
        if sessions.is_dir() and not sessions.is_symlink() and (configured or sources):
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
        root = _user_data_dir(context) / "User" / "workspaceStorage"
        if not root.is_dir() or root.is_symlink():
            return []
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
