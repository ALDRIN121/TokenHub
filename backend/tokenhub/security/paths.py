"""Filesystem containment checks for user-approved source roots."""

from pathlib import Path


def validate_source_path(candidate: Path, root: Path) -> Path:
    """Return a canonical existing path only when it is contained by ``root``."""
    resolved_candidate = candidate.resolve(strict=True)
    resolved_root = root.resolve(strict=True)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError("source path is outside the approved root") from error
    return resolved_candidate
