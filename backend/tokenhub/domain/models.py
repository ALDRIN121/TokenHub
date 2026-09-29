"""Provider-neutral models for normalized usage data."""

import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class Provider(StrEnum):
    ANTIGRAVITY = "antigravity"
    CLAUDE_CODE = "claude_code"
    CODEX = "codex"
    HERMES = "hermes"
    VSCODE_COPILOT = "vscode_copilot"


class SourceState(StrEnum):
    DISCOVERED = "discovered"
    APPROVED = "approved"
    IMPORTING = "importing"
    HEALTHY = "healthy"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    PERMISSION_DENIED = "permission_denied"
    SOURCE_MISSING = "source_missing"
    ERROR = "error"
    DISABLED = "disabled"


class MeasurementType(StrEnum):
    DELTA = "delta"
    CUMULATIVE = "cumulative"
    SNAPSHOT = "snapshot"
    GAUGE = "gauge"


class Quality(StrEnum):
    EXACT = "exact"
    HIGH = "high"
    ESTIMATED = "estimated"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class Confidence(StrEnum):
    """How sure discovery is that a provider is really present.

    Always derived from recorded evidence, never guessed: see
    :func:`confidence_from_evidence`.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


#: Signals a connector records when it observes something real about a provider.
#: A code that is not listed here is ignored rather than counted, so adding a
#: new evidence code can never silently raise a provider's confidence.
EVIDENCE_SIGNAL_CODES = frozenset(
    {
        "executable_on_path",
        "known_root_exists",
        "configuration_found",
        "session_source_found",
        "state_database_found",
    }
)

#: Independent signals needed before discovery claims high confidence.
_HIGH_CONFIDENCE_SIGNALS = 2


def confidence_from_evidence(codes: Iterable[str]) -> Confidence:
    """Summarize independent discovery signals as a confidence level.

    Two or more distinct signals → ``HIGH``, exactly one → ``MEDIUM``, none →
    ``LOW``. Repeated codes count once: the same signal observed twice is not
    two independent reasons to believe a provider is installed.
    """
    signals = {code for code in codes if code in EVIDENCE_SIGNAL_CODES}
    if len(signals) >= _HIGH_CONFIDENCE_SIGNALS:
        return Confidence.HIGH
    return Confidence.MEDIUM if signals else Confidence.LOW


@dataclass(frozen=True, slots=True)
class UsageEvent:
    """A normalized usage record emitted by any supported provider."""

    connector_id: str
    provider: Provider
    source_id: str
    record_identity: str
    timestamp: datetime
    input_total_tokens: int | None
    output_total_tokens: int | None
    measurement_type: MeasurementType
    quality: Quality
    parser_version: str
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    model_name: str | None = None
    session_id: str | None = None
    model_attribution: str = "unknown"

    @property
    def workload_tokens(self) -> int | None:
        """Tokens directly attributable to prompt input and generated output."""
        if self.input_total_tokens is None or self.output_total_tokens is None:
            return None
        return self.input_total_tokens + self.output_total_tokens


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    """A discovered source whose paths remain outside persistent storage until approval.

    Pass untrusted path spellings as strings so lexical validation precedes Path
    normalization. Existing Path inputs carry only their already-normalized
    spelling; a dot removed before this boundary cannot be recovered.
    """

    source_id: str
    connector_id: str
    provider: Provider
    display_name: str
    canonical_path: Path
    approved_root: Path
    source_type: str
    path_fingerprint: str
    state: SourceState = SourceState.DISCOVERED
    evidence_codes: tuple[str, ...] = ()
    scan_supported: bool = False
    parser_version: str | None = None
    approved_root_device: int | None = None
    approved_root_inode: int | None = None

    def __init__(
        self,
        source_id: str,
        connector_id: str,
        provider: Provider,
        display_name: str,
        canonical_path: Path | str,
        approved_root: Path | str,
        source_type: str,
        path_fingerprint: str,
        state: SourceState = SourceState.DISCOVERED,
        evidence_codes: tuple[str, ...] = (),
        scan_supported: bool = False,
        parser_version: str | None = None,
        approved_root_device: int | None = None,
        approved_root_inode: int | None = None,
    ) -> None:
        # Check the original spelling before Path can discard literal dots.
        for path in (canonical_path, approved_root):
            spelling = os.fspath(path)
            if os.altsep is not None:
                spelling = spelling.replace(os.altsep, os.sep)
            if (
                any(part in {".", ".."} for part in spelling.split(os.sep))
                or not Path(spelling).is_absolute()
            ):
                raise ValueError("source paths must be absolute without dot components")

        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "connector_id", connector_id)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(self, "canonical_path", Path(canonical_path))
        object.__setattr__(self, "approved_root", Path(approved_root))
        object.__setattr__(self, "source_type", source_type)
        object.__setattr__(self, "path_fingerprint", path_fingerprint)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "evidence_codes", evidence_codes)
        object.__setattr__(self, "scan_supported", scan_supported)
        object.__setattr__(self, "parser_version", parser_version)
        object.__setattr__(self, "approved_root_device", approved_root_device)
        object.__setattr__(self, "approved_root_inode", approved_root_inode)

    def safe_view(self) -> dict[str, str | bool | tuple[str, ...] | None]:
        """Project only public discovery metadata, never filesystem paths."""
        return {
            "source_id": self.source_id,
            "connector_id": self.connector_id,
            "provider": self.provider.value,
            "display_name": self.display_name,
            "source_type": self.source_type,
            "path_fingerprint": self.path_fingerprint,
            "state": self.state.value,
            "evidence_codes": self.evidence_codes,
            "scan_supported": self.scan_supported,
            "parser_version": self.parser_version,
        }


@dataclass(frozen=True, slots=True)
class SyncCursor:
    """The next safe position for an incremental source scan."""

    source_id: str
    byte_offset: int
    source_mtime_ns: int | None
    parser_version: str
    prefix_fingerprint: str | None = None
    source_unsupported_records: int = 0


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    """The durable outcome of persisting one scan."""

    inserted_events: int
    duplicate_events: int
    cursor: SyncCursor
    partial_final_record: bool = False
    unsupported_records: int = 0


# These states retain explicit approval through successful scans and retryable failures.
APPROVED_SOURCE_STATES = frozenset(
    {
        SourceState.APPROVED,
        SourceState.HEALTHY,
        SourceState.PARTIAL,
        SourceState.ERROR,
        SourceState.PERMISSION_DENIED,
        SourceState.SOURCE_MISSING,
    }
)


@dataclass(frozen=True, slots=True)
class SourceFreshness:
    """Stable observed freshness, without exposing a provider path."""

    source_id: str
    state: SourceState
    parser_version: str | None
    latest_event_at: datetime | None
    source_mtime_ns: int | None
    unsupported_records: int | None


@dataclass(frozen=True, slots=True)
class DashboardSummary:
    """Observed delta totals; unknown metrics remain nullable."""

    workload_tokens: int | None
    input_total_tokens: int | None
    output_total_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    reasoning_tokens: int | None
    event_count: int
    source_freshness: tuple[SourceFreshness, ...]
    quality_counts: dict[Quality, int]


@dataclass(frozen=True, slots=True)
class RebuildOutcome:
    """Successful imports and safe identifiers of sources needing attention."""

    imports: tuple[ImportOutcome, ...]
    failed_source_ids: tuple[str, ...] = ()

    @property
    def inserted_events(self) -> int:
        return sum(outcome.inserted_events for outcome in self.imports)
