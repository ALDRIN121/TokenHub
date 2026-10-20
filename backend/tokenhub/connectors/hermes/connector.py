"""Presence discovery and approval-gated Hermes session totals."""

import hashlib
import os
import stat
from pathlib import Path

from tokenhub.connectors.hermes import PARSER_VERSION
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

# Hermes uses a dotted config directory on macOS/Linux, while Windows keeps
# data under %LOCALAPPDATA%\hermes (with optional channel suffixes such as
# ``hermes-dev``), the legacy %USERPROFILE%\.hermes, and — for MSIX builds
# that ran with filesystem virtualisation — a package-local LocalCache. The
# patterns mirror Hermes' own ``hermes_home_globs()``.
#: ``.hermes*`` and ``hermes*`` already cover the dotted and undotted roots;
#: the two wildcards are what match channel suffixes such as ``hermes-dev``.
_LOCATION_PATTERNS = (
    ".hermes*",
    "hermes*",
    "Packages/NousResearch*_*/LocalCache/Local/hermes*",
    "Packages/NousResearch*_*/LocalCache/Local/.hermes*",
)


LAYOUT = AgentLayout(
    agent_id="hermes-local",
    display_name="Hermes Agent",
    env_keys=("HERMES_HOME",),
    names=_LOCATION_PATTERNS,
    process_names=("hermes",),
    markers=("state.db", "profiles"),
)


def _database_source(directory: Path, display_name: str) -> SourceDescriptor | None:
    """One direct ``state.db`` in ``directory``, never a nested copy.

    Discovery only stats the entry; the parser later opens it through the
    approved-root descriptor. A missing, linked, or unreadable directory is
    skipped rather than failing the whole provider.
    """
    try:
        with anchor_directory(directory) as anchored:
            try:
                metadata = stat_directory_entry("state.db", anchored.descriptor)
            except FileNotFoundError:
                return None
            if not stat.S_ISREG(metadata.st_mode):
                return None
            path = anchored.path / "state.db"
            fingerprint = hashlib.sha256(str(path).encode()).hexdigest()
            return SourceDescriptor(
                source_id=f"{LAYOUT.agent_id}:{fingerprint}",
                connector_id=LAYOUT.agent_id,
                provider=Provider.HERMES,
                display_name=display_name,
                canonical_path=path,
                approved_root=anchored.path,
                source_type="sqlite",
                path_fingerprint=fingerprint,
                evidence_codes=("state_database_found",),
                scan_supported=True,
                parser_version=PARSER_VERSION,
                approved_root_device=anchored.device,
                approved_root_inode=anchored.inode,
            )
    except (OSError, ValueError):
        return None


def _profiles(root: Path) -> list[tuple[Path, str]]:
    """Named profile directories under a Hermes root, by name only.

    Hermes stores a non-default profile at ``<root>/profiles/<name>/state.db``.
    The default profile keeps ``<root>/state.db``. Only ``profiles/*`` is
    walked: an arbitrary nested ``state.db`` is not a Hermes source.
    """
    profiles = root / "profiles"
    if not profiles.is_dir() or profiles.is_symlink():
        return []
    try:
        with anchor_directory(profiles) as anchored:
            discovered: list[tuple[Path, str]] = []
            for name in sorted(list_directory(anchored.descriptor)):
                try:
                    metadata = stat_directory_entry(name, anchored.descriptor)
                except FileNotFoundError:
                    continue
                profile = anchored.path / name
                if stat.S_ISDIR(metadata.st_mode) and not profile.is_symlink():
                    discovered.append((profile, name))
            return discovered
    except (OSError, ValueError):
        return []


class HermesConnector:
    connector_id = "hermes-local"
    display_name = "Hermes Agent"
    provider = Provider.HERMES
    layout = LAYOUT

    def detect(self, context: DiscoveryContext) -> DetectionResult:
        evidence: list[str] = []
        if context.which("hermes") is not None:
            evidence.append("executable_on_path")
        if locate_locations(context, LAYOUT):
            evidence.append("known_root_exists")
        sources = self.discover_sources(context)
        if sources:
            evidence.append("state_database_found")
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

    @memoized_sources
    def discover_sources(self, context: DiscoveryContext) -> list[SourceDescriptor]:
        roots = find_layout_roots(context, LAYOUT)
        sources: list[SourceDescriptor] = []
        seen: set[str] = set()
        for root in roots:
            candidates = [(root, "Hermes state database")]
            candidates.extend(
                (profile, f"Hermes state database ({name})")
                for profile, name in _profiles(root)
            )
            for directory, display_name in candidates:
                source = _database_source(directory, display_name)
                if source is None:
                    continue
                key = os.path.normcase(str(source.canonical_path))
                if key in seen:
                    continue
                seen.add(key)
                sources.append(source)
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
            return ScanResult(
                state=SourceState.UNSUPPORTED, reason_code="unsupported_source"
            )
        if source.state not in APPROVED_SOURCE_STATES:
            return ScanResult(state=source.state, reason_code="source_not_approved")
        if cursor is not None and cursor.source_id != source.source_id:
            raise ValueError("cursor belongs to a different source")
        from tokenhub.connectors.hermes.parser import parse_hermes_sqlite

        return parse_hermes_sqlite(source, cursor)

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            scan_supported=True, source_type="sqlite", parser_version=PARSER_VERSION
        )
