"""Presence detection for allowlisted Codex session JSONL files."""

import hashlib
import os
import stat
from pathlib import Path

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


class CodexConnector:
    connector_id = "codex-local"
    display_name = "OpenAI Codex"
    provider = Provider.CODEX

    def detect(self, context: DiscoveryContext) -> DetectionResult:
        evidence: list[str] = []
        if context.which("codex") is not None:
            evidence.append("executable_on_path")
        root = select_root(context, "CODEX_HOME", ".codex", canonicalize=False)
        if root.is_dir() and not root.is_symlink():
            evidence.append("known_root_exists")
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
        root = select_root(context, "CODEX_HOME", ".codex", canonicalize=False)
        sessions = root / "sessions"
        # These checks cheaply classify stable missing/symlink roots. The
        # descriptor anchor below remains the authority if the path changes.
        if root.is_symlink() or not sessions.is_dir() or sessions.is_symlink():
            return []
        with anchor_directory(sessions) as approved_root:
            return self._discover_anchored_sources(
                approved_root.path,
                approved_root.descriptor,
                approved_root.device,
                approved_root.inode,
            )

    def _discover_anchored_sources(
        self,
        approved_root: Path,
        root_descriptor: int,
        approved_root_device: int,
        approved_root_inode: int,
    ) -> list[SourceDescriptor]:
        candidates: list[SourceDescriptor] = []

        def visit(directory: Path, descriptor: int) -> None:
            for name in sorted(list_directory(descriptor)):
                try:
                    entry_stat = stat_directory_entry(name, descriptor)
                except FileNotFoundError:
                    continue
                if stat.S_ISDIR(entry_stat.st_mode):
                    if name in {
                        "auth",
                        "config",
                        "history",
                        "logs",
                        "log",
                        "cache",
                        "caches",
                    }:
                        continue
                    try:
                        child_descriptor = open_directory_entry(name, descriptor)
                    except FileNotFoundError:
                        continue
                    try:
                        visit(directory / name, child_descriptor)
                    finally:
                        os.close(child_descriptor)
                    continue
                if not stat.S_ISREG(entry_stat.st_mode) or not name.endswith(".jsonl"):
                    continue
                if name.casefold() in {
                    "auth.jsonl",
                    "cache.jsonl",
                    "config.jsonl",
                    "history.jsonl",
                    "log.jsonl",
                }:
                    continue
                path = directory / name
                fingerprint = hashlib.sha256(str(path).encode("utf-8")).hexdigest()
                candidates.append(
                    SourceDescriptor(
                        source_id=f"{self.connector_id}:{fingerprint}",
                        connector_id=self.connector_id,
                        provider=self.provider,
                        display_name="Codex session",
                        canonical_path=path,
                        approved_root=approved_root,
                        source_type="jsonl",
                        path_fingerprint=fingerprint,
                        evidence_codes=("session_source_found",),
                        scan_supported=True,
                        parser_version="codex-jsonl-v1",
                        approved_root_device=approved_root_device,
                        approved_root_inode=approved_root_inode,
                    )
                )

        visit(approved_root, root_descriptor)
        return candidates

    def scan(self, source: SourceDescriptor, cursor: SyncCursor | None) -> ScanResult:
        capabilities = self.capabilities()
        if (
            source.connector_id != self.connector_id
            or source.provider is not self.provider
            or not source.scan_supported
            or source.source_type != capabilities.source_type
            or source.parser_version != capabilities.parser_version
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

        # Import and file access occur only after the approved-source checks.
        from tokenhub.connectors.codex.parser import parse_codex_jsonl

        start_offset = (
            cursor.byte_offset
            if cursor is not None
            and cursor.parser_version == capabilities.parser_version
            and cursor.prefix_fingerprint is not None
            else 0
        )
        parsed = parse_codex_jsonl(
            source,
            start_offset,
            (
                cursor.prefix_fingerprint
                if cursor is not None
                and cursor.parser_version == capabilities.parser_version
                else None
            ),
        )
        source_unsupported_records = parsed.unsupported_records
        if (
            not parsed.full_reparse
            and cursor is not None
            and cursor.parser_version == capabilities.parser_version
        ):
            source_unsupported_records += cursor.source_unsupported_records
        return ScanResult(
            state=(
                SourceState.PARTIAL
                if parsed.partial_final_record or source_unsupported_records
                else SourceState.HEALTHY
            ),
            events=tuple(parsed.events),
            cursor=SyncCursor(
                source_id=source.source_id,
                byte_offset=parsed.safe_byte_offset,
                source_mtime_ns=parsed.source_mtime_ns,
                parser_version=capabilities.parser_version or "codex-jsonl-v1",
                prefix_fingerprint=parsed.safe_prefix_fingerprint,
                source_unsupported_records=source_unsupported_records,
            ),
            partial_final_record=parsed.partial_final_record,
            unsupported_records=parsed.unsupported_records,
        )

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            scan_supported=True, source_type="jsonl", parser_version="codex-jsonl-v1"
        )
