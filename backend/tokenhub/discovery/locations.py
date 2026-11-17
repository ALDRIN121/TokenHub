"""Layered location discovery for agents installed outside their default home.

Every connector answers three questions before it reads anything:

1. **Where might the data be?**  :func:`resolve_locations` gathers candidates
   from the agent's own environment overrides, the operator's TokenHub
   configuration, a running agent process, installed-application metadata, the
   platform's own directories, the home directory and finally extra search
   roots (other drives, redirected profiles).
2. **Is this actually a data source?**  :func:`validate_location` checks the
   layout's markers without opening a provider file.
3. **Have we already imported it?**  ``discovery.ledger`` compares the
   candidate's directory identity with stored sources.

Precedence follows the agents themselves, not TokenHub: an agent that honours a
documented environment variable reads *there* and nowhere else, so a resolved
override replaces the platform and home defaults. Locations an operator adds in
TokenHub's own configuration are additive, because they are an explicit request
to look somewhere the agent's defaults never mention.
"""

from __future__ import annotations

import glob
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from tokenhub.compat import StrEnum


class LocationOrigin(StrEnum):
    """Why TokenHub considered a directory, most specific first."""

    ENVIRONMENT = "environment"
    USER_CONFIG = "user_config"
    PROCESS = "process"
    APPLICATION = "application"
    PLATFORM_DEFAULT = "platform_default"
    HOME_DEFAULT = "home_default"
    SEARCH_ROOT = "search_root"


#: Evidence code recorded for each origin, so confidence stays derived.
ORIGIN_EVIDENCE: Mapping[LocationOrigin, str] = {
    LocationOrigin.ENVIRONMENT: "environment_override",
    LocationOrigin.USER_CONFIG: "user_configured_location",
    LocationOrigin.PROCESS: "process_environment",
    LocationOrigin.APPLICATION: "application_metadata",
    LocationOrigin.PLATFORM_DEFAULT: "platform_default_root",
    LocationOrigin.HOME_DEFAULT: "known_root_exists",
    LocationOrigin.SEARCH_ROOT: "search_root",
}


class LocationState(StrEnum):
    """Outcome of validating one candidate directory as a data source."""

    VALID = "valid"
    EMPTY = "empty"
    UNRECOGNIZED = "unrecognized"
    UNREADABLE = "unreadable"
    MISSING = "missing"
    LINKED = "linked"


@dataclass(frozen=True, slots=True)
class ApplicationHint:
    """An installed application whose presence implies a data directory.

    ``name`` is matched against the platform's application registries (an
    ``.app`` bundle on macOS, a ``.desktop`` entry on Linux, an uninstall
    registry display name on Windows). The hint only proves installation; the
    ``data_names`` locations are still validated like any other candidate.
    """

    name: str
    data_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AgentLayout:
    """Where one agent may keep its data, in the words of the agent itself."""

    agent_id: str
    #: Human-readable provider name, used by the location report.
    display_name: str = ""
    #: Documented environment variables that re-root the agent's data.
    env_keys: tuple[str, ...] = ()
    #: Default locations. ``~`` is the injected home, ``$VAR``/``%VAR%`` come
    #: from the injected environment, and ``*``/``?``/``[`` glob within a base.
    names: tuple[str, ...] = ()
    #: Extra or replaced names for one ``sys.platform`` value.
    platform_names: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: Installed applications whose metadata points at a custom data directory.
    applications: tuple[ApplicationHint, ...] = ()
    #: Executable names whose live process environment re-roots the agent.
    process_names: tuple[str, ...] = ()
    #: Directories or files whose presence makes a location a real data source.
    markers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProcessInfo:
    """One running agent process, reduced to the facts discovery needs.

    Only the environment variables named by a layout are retained, so a
    process scan never carries unrelated secrets into a discovery pass.
    """

    pid: int
    executable: str
    environment: Mapping[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return Path(self.executable).name


@dataclass(frozen=True, slots=True)
class Location:
    """One place an agent's data may be, and the reason it was considered."""

    path: Path
    origin: LocationOrigin

    @property
    def evidence(self) -> str:
        return ORIGIN_EVIDENCE[self.origin]


@dataclass(frozen=True, slots=True)
class LocationVerdict:
    """Question two: is this directory really a data source?"""

    location: Location
    state: LocationState
    markers: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return self.state is LocationState.VALID


_VARIABLE = re.compile(r"\$(?:\{(\w+)\}|(\w+))|%(\w+)%")
#: A value may reference another variable; longer chains are unresolvable
#: rather than something to loop over.
_MAX_EXPANSION_PASSES = 5


def _expand(text: str, home: Path, environment: Mapping[str, str]) -> str:
    """Expand ``~``, ``$VAR`` and ``%VAR%`` like ``os.path.expandvars``.

    An unset name stays literal, so a directory genuinely called ``$archive``
    still works; whether the result is a location is decided by the directory
    check that follows, not here. A variable's value may reference another one
    (``CODEX_HOME`` pointing at ``$WORK/data``), so substitution repeats until
    stable or the pass budget runs out.
    """

    def replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2) or match.group(3) or ""
        return environment.get(name) or match.group(0)

    expanded = text
    for _ in range(_MAX_EXPANSION_PASSES):
        substituted = _VARIABLE.sub(replace, expanded)
        if substituted == expanded:
            break
        expanded = substituted
    # An overriding variable may itself be home-relative (``~/custom-codex``),
    # which is how agents document a relocatable home.
    if expanded.startswith("~"):
        expanded = str(home) + expanded[1:]
    return expanded


def _usable(path: Path) -> bool:
    """An existing directory, not a link to somewhere discovery never approved."""
    return path.is_dir() and not path.is_symlink()


def _expand_all(
    names: Sequence[str],
    *,
    base: Path,
    home: Path,
    environment: Mapping[str, str],
) -> list[Path]:
    """Every path a name pattern denotes, in a stable order.

    A relative name hangs off ``base``; ``~`` and absolute names resolve against
    ``home``, so the same layout works for the home directory and for an extra
    drive.
    """
    found: list[Path] = []
    for name in names:
        candidate = Path(_expand(name, home, environment))
        if not candidate.is_absolute():
            candidate = base / candidate
        if any(token in str(candidate) for token in "*?["):
            # Bounded by the pattern the layout itself declares: never a walk.
            found.extend(Path(match) for match in sorted(glob.glob(str(candidate), recursive=False)))
        else:
            found.append(candidate)
    return found


def resolve_locations(
    layout: AgentLayout,
    home: Path,
    environment: Mapping[str, str],
    *,
    search_roots: Sequence[Path] = (),
    user_locations: Sequence[Path] = (),
    processes: Sequence[ProcessInfo] = (),
    installed_applications: Sequence[str] = (),
    platform: str = sys.platform,
) -> tuple[Location, ...]:
    """Question one: every existing directory this agent may be reading.

    Candidates are returned most-specific first and deduplicated by normalized
    spelling. A documented environment override replaces the platform and home
    defaults, exactly as the agent replaces them; operator locations and live
    process evidence are additive on top.
    """
    located: list[Location] = []

    overrides = [
        Location(path, LocationOrigin.ENVIRONMENT)
        for key in layout.env_keys
        for path in _expand_all(
            [f"${key}"], base=home, home=home, environment=environment
        )
        if _usable(path)
    ]
    located.extend(overrides)
    located.extend(
        Location(path, LocationOrigin.USER_CONFIG)
        for path in user_locations
        if _usable(path)
    )

    # A running agent states its own effective paths, including ones a shell
    # profile or wrapper set without exporting them to TokenHub. Executable
    # names compare case-insensitively: a bundle is "Code" on macOS and "code"
    # on Linux, and the host probe already matches that way.
    process_names = {name.casefold() for name in layout.process_names}
    for process in processes:
        if process.name.casefold() not in process_names:
            continue
        for key in layout.env_keys:
            value = process.environment.get(key)
            if not value:
                continue
            path = Path(_expand(value, home, environment))
            if _usable(path):
                located.append(Location(path, LocationOrigin.PROCESS))

    installed = {name.casefold() for name in installed_applications}
    for hint in layout.applications:
        if hint.name.casefold() not in installed:
            continue
        located.extend(
            Location(path, LocationOrigin.APPLICATION)
            for path in _expand_all(hint.data_names, base=home, home=home, environment=environment)
            if _usable(path)
        )

    if not overrides:
        # A platform entry replaces the generic names: the macOS and Windows
        # user-data directories are alternatives, never both.
        platform_names = layout.platform_names.get(platform)
        defaults = platform_names if platform_names else layout.names
        origin = LocationOrigin.PLATFORM_DEFAULT if platform_names else LocationOrigin.HOME_DEFAULT
        located.extend(
            Location(path, origin)
            for path in _expand_all(defaults, base=home, home=home, environment=environment)
            if _usable(path)
        )
        # Another drive is still "the default layout under a different base":
        # an agent that honours an override does not read there. ``home`` is
        # itself a candidate base, so it is skipped here: re-expanding the
        # generic names under it would undo the platform replacement above.
        for base in _search_bases(search_roots, home):
            located.extend(
                Location(path, LocationOrigin.SEARCH_ROOT)
                for path in _expand_all(
                    layout.names, base=base, home=home, environment=environment
                )
                if _usable(path)
            )

    return _deduplicated(located)


def _search_bases(search_roots: Sequence[Path], home: Path) -> tuple[Path, ...]:
    """Extra bases to try, excluding the home directory already tried."""
    home_key = os.path.normcase(str(home))
    return tuple(
        base for base in search_roots if os.path.normcase(str(base)) != home_key
    )


def _deduplicated(locations: Sequence[Location]) -> tuple[Location, ...]:
    seen: set[str] = set()
    ordered: list[Location] = []
    for location in locations:
        key = os.path.normcase(str(location.path))
        if key in seen:
            continue
        seen.add(key)
        ordered.append(location)
    return tuple(ordered)


def validate_location(layout: AgentLayout, location: Location) -> LocationVerdict:
    """Question two: does this directory hold the agent's own data layout?

    Only names are inspected. No provider file is opened, so presence
    discovery keeps its "never read a session to detect it" contract.
    """
    path = location.path
    if path.is_symlink():
        # Checked before existence so a dangling link reads as linked, not absent.
        return LocationVerdict(location, LocationState.LINKED)
    if not path.exists():
        return LocationVerdict(location, LocationState.MISSING)
    if not path.is_dir():
        return LocationVerdict(location, LocationState.UNRECOGNIZED)
    try:
        found = tuple(marker for marker in layout.markers if (path / marker).exists())
        if found:
            return LocationVerdict(location, LocationState.VALID, found)
        if not any(path.iterdir()):
            return LocationVerdict(location, LocationState.EMPTY)
    except PermissionError:
        return LocationVerdict(location, LocationState.UNREADABLE)
    except OSError:
        return LocationVerdict(location, LocationState.UNREADABLE)
    return LocationVerdict(location, LocationState.UNRECOGNIZED)
