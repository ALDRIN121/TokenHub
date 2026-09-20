"""Presence detection for allowlisted Codex session JSONL files."""

import hashlib
import os
from pathlib import Path

from tokenhub.connectors.protocol import (
    ConnectorCapabilities,
    DetectionResult,
    DiscoveryContext,
    SafeSourceView,
    ScanResult,
    select_root,
)
from tokenhub.domain.models import Provider, SourceDescriptor, SourceState, SyncCursor


class CodexConnector:
    connector_id = "codex-local"
    display_name = "OpenAI Codex"
    provider = Provider.CODEX

    def detect(self, context: DiscoveryContext) -> DetectionResult:
        evidence: list[str] = []
        if context.which("codex") is not None:
            evidence.append("executable_on_path")
        root = select_root(context, "CODEX_HOME", ".codex")
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
            sources=tuple(SafeSourceView.model_validate(source.safe_view()) for source in sources),
        )

    def discover_sources(self, context: DiscoveryContext) -> list[SourceDescriptor]:
        root = select_root(context, "CODEX_HOME", ".codex")
        sessions = root / "sessions"
        if root.is_symlink() or not sessions.is_dir() or sessions.is_symlink():
            return []
        approved_root = sessions.resolve(strict=True)
        candidates: list[SourceDescriptor] = []
        for directory, dirnames, filenames in os.walk(sessions, followlinks=False):
            dirnames[:] = sorted(
                name
                for name in dirnames
                if name not in {"auth", "config", "history", "logs", "log", "cache", "caches"}
                and not (Path(directory) / name).is_symlink()
            )
            for filename in sorted(filenames):
                if filename.casefold() in {
                    "auth.jsonl",
                    "cache.jsonl",
                    "config.jsonl",
                    "history.jsonl",
                    "log.jsonl",
                } or not filename.endswith(".jsonl"):
                    continue
                path = Path(directory) / filename
                if path.is_symlink() or not path.is_file():
                    continue
                canonical = path.resolve(strict=True)
                try:
                    canonical.relative_to(approved_root)
                except ValueError:
                    continue
                fingerprint = hashlib.sha256(str(canonical).encode("utf-8")).hexdigest()
                candidates.append(
                    SourceDescriptor(
                        source_id=f"{self.connector_id}:{fingerprint}",
                        connector_id=self.connector_id,
                        provider=self.provider,
                        display_name="Codex session",
                        canonical_path=canonical,
                        approved_root=approved_root,
                        source_type="jsonl",
                        path_fingerprint=fingerprint,
                        evidence_codes=("session_source_found",),
                        scan_supported=True,
                        parser_version="codex-jsonl-v1",
                    )
                )
        return candidates

    def scan(self, source: SourceDescriptor, cursor: SyncCursor | None) -> ScanResult:
        return ScanResult(state=SourceState.UNSUPPORTED, reason_code="parser_not_installed")

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            scan_supported=True, source_type="jsonl", parser_version="codex-jsonl-v1"
        )
