"""Question three: has TokenHub already imported this location?

The ledger compares a candidate directory with the roots TokenHub has stored
for approved sources. Two spellings of the same directory (a symlinked home, a
moved profile, a case-insensitive volume) resolve to the same stored identity,
so a location that was imported yesterday is not silently imported again under
a new name.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ImportedSource:
    """One stored source and the events it already contributed."""

    source_id: str
    connector_id: str
    approved_root: str | None
    device: int | None
    inode: int | None
    events: int


@dataclass(frozen=True, slots=True)
class ImportMatch:
    """How much of a stored source's history already exists."""

    events: int

    @property
    def imported(self) -> bool:
        """A stored source with at least one observed event is an import."""
        return self.events > 0


@dataclass(frozen=True, slots=True)
class ImportLedger:
    """The stored sources, indexed for questions about one directory."""

    sources: tuple[ImportedSource, ...] = ()

    def for_location(self, connector_id: str, path: Path) -> tuple[ImportMatch, ...]:
        """Stored sources of ``connector_id`` inside ``path``, by identity or root."""
        identity = _identity(path)
        prefix = _normalized(path)
        matches: list[ImportMatch] = []
        for source in self.sources:
            if source.connector_id != connector_id:
                continue
            same_identity = (
                identity is not None
                and source.device is not None
                and source.inode is not None
                and (source.device, source.inode) == identity
            )
            inside = source.approved_root is not None and _is_within(
                source.approved_root, prefix
            )
            if same_identity or inside:
                matches.append(ImportMatch(events=source.events))
        return tuple(matches)


def _identity(path: Path) -> tuple[int, int] | None:
    try:
        status = path.stat()
    except OSError:
        return None
    return status.st_dev, status.st_ino


def _normalized(path: Path) -> str:
    return os.path.normcase(os.path.join(str(path), ""))


def _is_within(candidate: str, prefix: str) -> bool:
    """Compare a stored root with a directory, tolerating case and separators."""
    normalized = os.path.normcase(os.path.join(candidate, ""))
    if normalized == prefix:
        return True
    try:
        return os.path.commonpath([normalized, prefix]) == prefix.rstrip(os.sep)
    except ValueError:
        return False
