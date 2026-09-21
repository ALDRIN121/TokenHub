"""Provider-neutral connector contracts and path-free discovery results."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from tokenhub.domain.models import (
    Provider,
    SourceDescriptor,
    SourceState,
    SyncCursor,
    UsageEvent,
)


@dataclass(frozen=True, slots=True)
class DiscoveryContext:
    home: Path
    environment: Mapping[str, str]
    which: Callable[[str], str | None]


class SafeSourceView(BaseModel):
    """A source representation with no private filesystem fields."""

    source_id: str
    connector_id: str
    provider: Provider
    display_name: str
    source_type: str
    path_fingerprint: str
    state: SourceState
    evidence_codes: tuple[str, ...] = ()
    scan_supported: bool = False
    parser_version: str | None = None


class DetectionResult(BaseModel):
    connector_id: str
    display_name: str
    provider: Provider | None
    state: SourceState
    evidence_codes: tuple[str, ...] = ()
    sources: tuple[SafeSourceView, ...] = ()


@dataclass(frozen=True, slots=True)
class ConnectorCapabilities:
    scan_supported: bool
    source_type: str | None = None
    parser_version: str | None = None


@dataclass(frozen=True, slots=True)
class ScanResult:
    state: SourceState
    events: tuple[UsageEvent, ...] = ()
    cursor: SyncCursor | None = None
    reason_code: str | None = None
    partial_final_record: bool = False
    unsupported_records: int = 0


class UsageConnector(Protocol):
    connector_id: str
    display_name: str

    def detect(self, context: DiscoveryContext) -> DetectionResult: ...

    def discover_sources(self, context: DiscoveryContext) -> list[SourceDescriptor]: ...

    def scan(
        self, source: SourceDescriptor, cursor: SyncCursor | None
    ) -> ScanResult: ...

    def capabilities(self) -> ConnectorCapabilities: ...


def select_root(
    context: DiscoveryContext, override_key: str, default_name: str
) -> Path:
    """Use an existing injected override directory or the injected home root."""
    override = context.environment.get(override_key)
    if override:
        if override == "~":
            candidate = context.home
        elif override.startswith("~/"):
            candidate = context.home / override[2:]
        else:
            candidate = Path(override)
        if candidate.is_dir() and not candidate.is_symlink():
            return candidate.resolve(strict=True)
    return context.home / default_name
