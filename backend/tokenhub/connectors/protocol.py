"""Provider-neutral connector contracts and path-free discovery results."""

import functools
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, computed_field

from tokenhub.discovery.locations import (
    AgentLayout,
    Location,
    ProcessInfo,
    resolve_locations,
    validate_location,
)
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
    # Locations an operator added to TokenHub's own discovery file, by agent id.
    locations: Mapping[str, tuple[Path, ...]] = field(default_factory=dict)
    # Live agent processes reduced to their layout's environment keys.
    processes: tuple[ProcessInfo, ...] = ()
    # Installed applications, so layout metadata can point at custom directories.
    applications: tuple[str, ...] = ()


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
    #: Where this agent may keep its data; see ``discovery.locations``.
    layout: AgentLayout

    def detect(self, context: DiscoveryContext) -> DetectionResult: ...

    def discover_sources(self, context: DiscoveryContext) -> list[SourceDescriptor]: ...

    def scan(
        self, source: SourceDescriptor, cursor: SyncCursor | None
    ) -> ScanResult: ...

    def capabilities(self) -> ConnectorCapabilities: ...


def _resolve(context: DiscoveryContext, layout: AgentLayout) -> tuple[Location, ...]:
    return resolve_locations(
        layout,
        context.home,
        context.environment,
        search_roots=context.search_roots,
        user_locations=context.locations.get(layout.agent_id, ()),
        processes=context.processes,
        installed_applications=context.applications,
    )


def locate_locations(
    context: DiscoveryContext, layout: AgentLayout
) -> tuple[Location, ...]:
    """Every existing directory this agent may be reading, with its origin.

    These are *candidates*: existence is not validity, so presence detection
    uses this instead of :func:`find_layout_roots`. A freshly installed agent
    with only a settings file is present, yet holds nothing to import.
    """
    return _resolve(context, layout)


def find_layout_roots(context: DiscoveryContext, layout: AgentLayout) -> tuple[Path, ...]:
    """Every location this agent's own layout validates, most-specific first.

    An agent can legitimately keep data in a default home *and* an
    operator-declared folder
    (a second profile, another drive, a work volume), and each of those is a
    separate importable source rather than a fallback for the previous one.
    """
    return tuple(
        location.path
        for location in _resolve(context, layout)
        if validate_location(layout, location).usable
    )
