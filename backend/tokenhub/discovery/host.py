"""Host-side probes: what is installed, what is running, what was configured.

These are the only parts of discovery that touch the host outside a directory
listing. Each probe is best-effort and returns an empty result on a platform or
permission it cannot serve, so a failed probe degrades a discovery pass instead
of failing it.

Nothing here opens a provider's session or configuration file. Application
metadata is read from the operating system's own registries, and a process
contributes only the environment variables a layout explicitly asks for.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from tokenhub.discovery.locations import ProcessInfo

_APPLICATION_SEARCH_PATHS = ("/Applications", "/System/Applications")


def read_user_location_config(
    path: Path, *, home: Path | None = None
) -> dict[str, tuple[Path, ...]]:
    """Locations an operator added to TokenHub's own discovery file.

    Shape: ``{"agents": {"codex-local": {"locations": ["/Volumes/work/codex"]}}}``.
    Unreadable, malformed, or relative entries are dropped: a discovery file can
    never widen a scan boundary by accident. ``~`` is expanded against the
    injected home so a test or embedder never reads the real user's home.
    """
    home = Path.home() if home is None else home
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    agents = payload.get("agents")
    if not isinstance(agents, dict):
        return {}
    configured: dict[str, tuple[Path, ...]] = {}
    for agent_id, entry in agents.items():
        if not isinstance(agent_id, str) or not isinstance(entry, dict):
            continue
        locations = entry.get("locations")
        if not isinstance(locations, list):
            continue
        accepted: list[Path] = []
        for value in locations:
            if not isinstance(value, str) or not value.strip():
                continue
            expanded = str(home) + value[1:] if value.startswith("~") else value
            candidate = Path(expanded)
            if not candidate.is_absolute() or any(part in {".", ".."} for part in candidate.parts):
                continue
            accepted.append(candidate)
        if accepted:
            configured[agent_id] = tuple(accepted)
    return configured


def installed_applications(
    names: Sequence[str],
    *,
    home: Path,
    environment: Mapping[str, str],
    platform: str = sys.platform,
) -> tuple[str, ...]:
    """The subset of ``names`` installed on this host.

    macOS checks application bundles, Linux checks desktop entries, and Windows
    reads the uninstall registry. A name is returned when *any* platform
    directory proves it is installed; the data directory is validated later.
    """
    if not names:
        return ()
    wanted = {name.casefold(): name for name in names}
    found: set[str] = set()

    if platform == "win32":
        return tuple(sorted(_windows_installed(wanted)))

    directories, suffix = _application_registries(platform, home, environment)
    for root in directories:
        if not root.is_dir():
            continue
        try:
            entries = {entry.name.casefold() for entry in root.iterdir()}
        except OSError:
            continue
        for folded, original in wanted.items():
            if f"{folded}{suffix}" in entries:
                found.add(original)
    return tuple(sorted(found))


def _application_registries(
    platform: str, home: Path, environment: Mapping[str, str]
) -> tuple[tuple[Path, ...], str]:
    """The directories that list installed applications, and their entry suffix."""
    if platform == "darwin":
        return (
            (*(Path(base) for base in _APPLICATION_SEARCH_PATHS), home / "Applications"),
            ".app",
        )
    data_home = Path(environment.get("XDG_DATA_HOME") or home / ".local/share")
    if not data_home.is_absolute():
        # A relative XDG value would make the search depend on the process cwd.
        data_home = home / ".local/share"
    candidates = (
        home / ".local/share/applications",
        Path("/usr/share/applications"),
        data_home / "applications",
    )
    seen: set[str] = set()
    roots: list[Path] = []
    for root in candidates:
        key = os.path.normcase(str(root))
        if key not in seen:
            seen.add(key)
            roots.append(root)
    return (tuple(roots), ".desktop")


def _windows_installed(wanted: Mapping[str, str]) -> set[str]:
    """Display names from the per-machine and per-user uninstall keys."""
    try:
        import winreg
    except ImportError:
        return set()
    # winreg is only typed on a Windows host, so treat it as untyped here.
    registry = cast(Any, winreg)
    found: set[str] = set()
    locations = (
        (registry.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (registry.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (registry.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    for root, key_path in locations:
        try:
            with registry.OpenKey(root, key_path) as key:
                for index in range(registry.QueryInfoKey(key)[0]):
                    try:
                        name = registry.EnumKey(key, index)
                        with registry.OpenKey(key, name) as entry:
                            display, _ = registry.QueryValueEx(entry, "DisplayName")
                    except OSError:
                        continue
                    if isinstance(display, str) and display.casefold() in wanted:
                        found.add(wanted[display.casefold()])
        except OSError:
            continue
    return found


def running_agents(
    executable_names: Sequence[str],
    environment_keys: Sequence[str],
    *,
    platform: str = sys.platform,
    runner: Callable[[list[str]], str] | None = None,
) -> tuple[ProcessInfo, ...]:
    """Live agent processes, reduced to the environment keys discovery needs.

    A running agent states its own effective data root, which is how an install
    started from a wrapper script or a non-interactive shell is found without
    reading any of its files. Only ``environment_keys`` are retained.
    """
    if platform not in {"darwin", "linux"} or not executable_names:
        return ()
    run = runner or _run
    listing = run(["ps", "-axo", "pid=,comm="])
    if not listing:
        return ()
    wanted = {name.casefold() for name in executable_names}
    keys = tuple(environment_keys)
    processes: list[ProcessInfo] = []
    for line in listing.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        pid, command = int(parts[0]), parts[1].strip()
        if Path(command).name.casefold() not in wanted:
            continue
        environment = _process_environment(pid, keys, platform=platform, run=run)
        if environment:
            processes.append(ProcessInfo(pid=pid, executable=command, environment=environment))
    return tuple(processes)


def _process_environment(
    pid: int,
    keys: Sequence[str],
    *,
    platform: str,
    run: Callable[[list[str]], str],
) -> dict[str, str]:
    if platform == "linux":
        try:
            raw = Path(f"/proc/{pid}/environ").read_bytes()
        except OSError:
            return {}
        pairs = (chunk.split(b"=", 1) for chunk in raw.split(b"\0") if b"=" in chunk)
        mapping = {
            key.decode("utf-8", "replace"): value.decode("utf-8", "replace")
            for key, value in pairs
        }
        return {key: mapping[key] for key in keys if key in mapping}
    output = run(["ps", "-E", "-o", "command=", "-p", str(pid)])
    if not output:
        return {}
    # macOS prints the environment after the command line as ``KEY=value``.
    found: dict[str, str] = {}
    for token in output.split():
        key, separator, value = token.partition("=")
        if separator and key in keys:
            found[key] = value
    return found


def _run(argv: list[str]) -> str:
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout if completed.returncode == 0 else ""
