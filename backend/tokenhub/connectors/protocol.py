"""Provider-neutral connector contracts and path-free discovery results."""

import functools
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, computed_field

from tokenhub.domain.models import (
    Confidence,
    Provider,
    SourceDescriptor,
    SourceState,
    SyncCursor,
    UsageEvent,
    confidence_from_evidence,
)


@dataclass(frozen=True, slots=True)
class DiscoveryContext:
    home: Path
    environment: Mapping[str, str]
    which: Callable[[str], str | None]
    # Extra bases (other drives, redirected profiles) searched after ``home``.
    search_roots: tuple[Path, ...] = ()
    # Set by one discovery pass so ``detect`` and ``discover_sources`` share a
    # single directory walk. ``None`` (the default) disables sharing.
    source_memo: dict[str, list[SourceDescriptor]] | None = field(
        default=None, compare=False, repr=False
    )


def memoized_sources(
    method: Callable[[Any, DiscoveryContext], list[SourceDescriptor]],
) -> Callable[[Any, DiscoveryContext], list[SourceDescriptor]]:
    """Reuse a connector's source walk within one discovery pass."""

    @functools.wraps(method)
    def wrapper(self: Any, context: DiscoveryContext) -> list[SourceDescriptor]:
        memo = context.source_memo
        if memo is None:
            return method(self, context)
        if self.connector_id not in memo:
            memo[self.connector_id] = method(self, context)
        return list(memo[self.connector_id])

    return wrapper


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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def confidence(self) -> Confidence:
        """How sure discovery is that this provider is present.

        Derived from the evidence this detection recorded, so the API contract
        and the connector can never disagree about it.
        """
        return confidence_from_evidence(self.evidence_codes)


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
    replace_events: bool = False
    record_identity_aliases: tuple[tuple[str, str], ...] = ()


class UsageConnector(Protocol):
    connector_id: str
    display_name: str

    def detect(self, context: DiscoveryContext) -> DetectionResult: ...

    def discover_sources(self, context: DiscoveryContext) -> list[SourceDescriptor]: ...

    def scan(
        self, source: SourceDescriptor, cursor: SyncCursor | None
    ) -> ScanResult: ...

    def capabilities(self) -> ConnectorCapabilities: ...


def _override_directory(
    context: DiscoveryContext, override_key: str, canonicalize: bool
) -> Path | None:
    """The env override when it names an existing, non-symlink directory."""
    override = context.environment.get(override_key)
    if not override:
        return None
    if override == "~":
        candidate = context.home
    elif override.startswith(("~/", "~\\")):
        candidate = context.home / override[2:]
    else:
        candidate = Path(os.path.expandvars(override))
    if candidate.is_dir() and not candidate.is_symlink():
        if canonicalize:
            return candidate.resolve(strict=True)
        if candidate.is_absolute():
            return candidate
    return None


def find_root(
    context: DiscoveryContext,
    override_key: str,
    default_names: str | Sequence[str],
    *,
    marker: str | None = None,
    canonicalize: bool = True,
) -> Path:
    """Locate an agent's data root under an injected override, home or search roots.

    The env override wins. Otherwise each ``default_names`` entry is tried
    under ``home`` and then every search root, preferring a directory that
    contains ``marker`` (so an empty stub at home does not hide real data on
    another drive), then any existing directory. With no match the
    ``home``-relative first name is returned, as before.
    """
    override = _override_directory(context, override_key, canonicalize)
    if override is not None:
        return override
    names = (default_names,) if isinstance(default_names, str) else tuple(default_names)
    candidates = [
        base / name for base in (context.home, *context.search_roots) for name in names
    ]
    usable = [path for path in candidates if path.is_dir() and not path.is_symlink()]
    if marker is not None:
        for path in usable:
            if (path / marker).exists():
                return path
    if usable:
        return usable[0]
    return context.home / names[0]


def select_root(
    context: DiscoveryContext,
    override_key: str,
    default_name: str,
    *,
    canonicalize: bool = True,
) -> Path:
    """Use an existing injected override directory or the injected home root."""
    override = _override_directory(context, override_key, canonicalize)
    return override if override is not None else context.home / default_name
