"""Presence discovery and approval-gated Claude Code session usage."""

import hashlib
import os
import stat
from pathlib import Path

from tokenhub.connectors.claude import PARSER_VERSION
from tokenhub.connectors.protocol import (
    ConnectorCapabilities,
    DetectionResult,
    DiscoveryContext,
    SafeSourceView,
    ScanResult,
    select_root,
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


class ClaudeConnector:
    connector_id = "claude-code-local"
    display_name = "Claude Code"
    provider = Provider.CLAUDE_CODE

    def detect(self, context: DiscoveryContext) -> DetectionResult:
        evidence: list[str] = []
        if context.which("claude") is not None:
            evidence.append("executable_on_path")
        root = select_root(context, "CLAUDE_CONFIG_DIR", ".claude", canonicalize=False)
        if root.is_dir() and not root.is_symlink():
            evidence.append("known_root_exists")
            settings = root / "settings.json"
            if not settings.is_symlink() and settings.is_file():
                evidence.append("configuration_found")
        sources = self.discover_sources(context)
        if sources:
            evidence.append("session_source_found")
        return DetectionResult(
            connector_id=self.connector_id,
            display_name=self.display_name,
            provider=self.provider,
            state=SourceState.DISCOVERED if evidence else SourceState.SOURCE_MISSING,
            evidence_codes=tuple(evidence),
            sources=tuple(
                SafeSourceView.model_validate(source.safe_view()) for source in sources
            ),
        )

    def discover_sources(self, context: DiscoveryContext) -> list[SourceDescriptor]:
        root = select_root(context, "CLAUDE_CONFIG_DIR", ".claude", canonicalize=False)
        projects = root / "projects"
        if root.is_symlink() or not projects.is_dir() or projects.is_symlink():
            return []
        candidates: list[SourceDescriptor] = []
        with anchor_directory(projects) as anchored:

            def visit(directory: Path, descriptor: int) -> None:
                for name in sorted(list_directory(descriptor)):
                    try:
                        metadata = stat_directory_entry(name, descriptor)
                    except FileNotFoundError:
                        continue
                    if stat.S_ISDIR(metadata.st_mode):
                        if name.casefold() in {
                            "auth",
                            "cache",
                            "config",
                            "history",
                            "logs",
                            "log",
                        }:
                            continue
                        try:
                            child = open_directory_entry(name, descriptor)
                        except FileNotFoundError:
                            continue
                        try:
                            visit(directory / name, child)
                        finally:
                            os.close(child)
                    elif (
                        stat.S_ISREG(metadata.st_mode)
                        and name.endswith(".jsonl")
                        and name.casefold()
                        not in {
                            "auth.jsonl",
                            "history.jsonl",
                            "config.jsonl",
                            "cache.jsonl",
                            "log.jsonl",
                        }
                    ):
                        path = directory / name
                        fingerprint = hashlib.sha256(str(path).encode()).hexdigest()
                        candidates.append(
                            SourceDescriptor(
                                source_id=f"{self.connector_id}:{fingerprint}",
                                connector_id=self.connector_id,
                                provider=self.provider,
                                display_name="Claude Code session",
                                canonical_path=path,
                                approved_root=anchored.path,
                                source_type="jsonl",
                                path_fingerprint=fingerprint,
                                evidence_codes=("session_source_found",),
                                scan_supported=True,
                                parser_version=PARSER_VERSION,
                                approved_root_device=anchored.device,
                                approved_root_inode=anchored.inode,
                            )
                        )

            visit(anchored.path, anchored.descriptor)
        return candidates

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
            return ScanResult(
                state=SourceState.UNSUPPORTED, reason_code="unsupported_source"
            )
        if source.state not in APPROVED_SOURCE_STATES:
            return ScanResult(state=source.state, reason_code="source_not_approved")
        if cursor is not None and cursor.source_id != source.source_id:
            raise ValueError("cursor belongs to a different source")
        from tokenhub.connectors.claude.parser import parse_claude_jsonl

        return parse_claude_jsonl(source, cursor)

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            scan_supported=True, source_type="jsonl", parser_version=PARSER_VERSION
        )
