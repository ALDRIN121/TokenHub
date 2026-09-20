"""Provider-neutral models for normalized usage data."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class Provider(StrEnum):
    CLAUDE_CODE = "claude_code"
    CODEX = "codex"
    HERMES = "hermes"


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

    @property
    def workload_tokens(self) -> int | None:
        """Tokens directly attributable to prompt input and generated output."""
        if self.input_total_tokens is None or self.output_total_tokens is None:
            return None
        return self.input_total_tokens + self.output_total_tokens


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    """A discovered source whose paths remain outside persistent storage until approval."""

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


@dataclass(frozen=True, slots=True)
class SyncCursor:
    """The next safe position for an incremental source scan."""

    source_id: str
    byte_offset: int
    source_mtime_ns: int | None
    parser_version: str


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    """The durable outcome of persisting one scan."""

    inserted_events: int
    duplicate_events: int
    cursor: SyncCursor
    partial_final_record: bool = False
    unsupported_records: int = 0
