"""Ordered directories under which an agent's known relative data path is searched."""

import os
import string
import sys
from collections.abc import Callable, Mapping
from pathlib import Path


def windows_drives() -> tuple[Path, ...]:
    """Root directories of the drive letters that currently exist."""
    return tuple(
        root for letter in string.ascii_uppercase if (root := Path(f"{letter}:\\")).exists()
    )


def candidate_roots(
    environment: Mapping[str, str],
    home: Path,
    *,
    platform: str = sys.platform,
    list_drives: Callable[[], tuple[Path, ...]] = windows_drives,
    current_user: str | None = None,
) -> tuple[Path, ...]:
    """Existing directories that may hold an agent's data folder, best first.

    Agents keep a fixed relative layout (``.codex/sessions``) but the base it
    hangs off varies: a relocated profile, folder redirection, another drive.
    Only the current user's locations are considered, never other profiles, and
    nothing is crawled.
    """
    ordered: list[Path] = [home]
    keys = ["USERPROFILE"]
    if platform == "win32":
        keys += ["APPDATA", "LOCALAPPDATA"]
    else:
        keys += ["XDG_CONFIG_HOME", "XDG_DATA_HOME"]
    for key in keys:
        if value := environment.get(key):
            ordered.append(Path(value))
    if drive := environment.get("HOMEDRIVE"):
        if path := environment.get("HOMEPATH"):
            ordered.append(Path(drive + path))
    user = current_user or environment.get("USERNAME") or environment.get("USER")
    if platform == "win32" and user:
        ordered.extend(drive_root / "Users" / user for drive_root in list_drives())

    seen: set[str] = set()
    result: list[Path] = []
    for path in ordered:
        key = os.path.normcase(str(path))
        if key in seen or not path.is_dir() or path.is_symlink():
            continue
        seen.add(key)
        result.append(path)
    return tuple(result)
