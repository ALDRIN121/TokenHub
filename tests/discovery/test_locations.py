"""Layer order and validation for agents installed outside their default home."""

from pathlib import Path

from tokenhub.discovery.locations import (
    AgentLayout,
    ApplicationHint,
    Location,
    LocationOrigin,
    LocationState,
    ProcessInfo,
    resolve_locations,
    validate_location,
)


def _codex_layout() -> AgentLayout:
    return AgentLayout(
        agent_id="codex-local",
        display_name="OpenAI Codex",
        env_keys=("CODEX_HOME",),
        names=(".codex",),
        process_names=("codex",),
        markers=("sessions",),
    )


def _claude_layout() -> AgentLayout:
    return AgentLayout(
        agent_id="claude-code-local",
        env_keys=("CLAUDE_CONFIG_DIR",),
        names=(".claude",),
        markers=("projects",),
    )


def _home_with_root(tmp_path: Path, root: str = ".codex") -> Path:
    home = tmp_path / "home"
    (home / root / "sessions").mkdir(parents=True)
    return home


def test_environment_override_replaces_home_default(tmp_path: Path) -> None:
    home = _home_with_root(tmp_path)
    custom = tmp_path / "work" / "codex"
    (custom / "sessions").mkdir(parents=True)

    locations = resolve_locations(
        _codex_layout(), home, {"CODEX_HOME": str(custom)}
    )

    assert [location.path for location in locations] == [custom]
    assert locations[0].origin is LocationOrigin.ENVIRONMENT
    assert locations[0].evidence == "environment_override"


def test_operator_locations_are_additive_to_every_discovered_root(tmp_path: Path) -> None:
    home = _home_with_root(tmp_path)
    archive = tmp_path / "archive" / "codex"
    (archive / "sessions").mkdir(parents=True)

    locations = resolve_locations(
        _codex_layout(), home, {}, user_locations=(archive,)
    )

    assert {location.path for location in locations} == {home / ".codex", archive}
    assert {location.origin for location in locations} == {
        LocationOrigin.HOME_DEFAULT,
        LocationOrigin.USER_CONFIG,
    }


def test_all_search_roots_are_searched_not_only_the_first(tmp_path: Path) -> None:
    """Two drives holding the same agent layout are two importable locations."""
    home = _home_with_root(tmp_path)
    other_drive = tmp_path / "d"
    (other_drive / ".codex" / "sessions").mkdir(parents=True)

    locations = resolve_locations(
        _codex_layout(), home, {}, search_roots=(other_drive,)
    )

    assert [location.path for location in locations] == [
        home / ".codex",
        other_drive / ".codex",
    ]
    assert locations[1].origin is LocationOrigin.SEARCH_ROOT


def test_running_process_environment_locates_a_relocated_home(tmp_path: Path) -> None:
    home = _home_with_root(tmp_path)
    relocated = tmp_path / "sessions-on-another-volume"
    (relocated / "sessions").mkdir(parents=True)

    locations = resolve_locations(
        _codex_layout(),
        home,
        {},
        processes=(
            ProcessInfo(
                pid=42,
                executable="/usr/local/bin/codex",
                environment={"CODEX_HOME": str(relocated)},
            ),
        ),
    )

    # A live agent adds its effective home; the default home stays because it
    # may still hold earlier sessions.
    assert {location.path for location in locations} == {home / ".codex", relocated}
    process_locations = [
        location for location in locations if location.origin is LocationOrigin.PROCESS
    ]
    assert [location.path for location in process_locations] == [relocated]


def test_process_with_a_different_name_is_ignored(tmp_path: Path) -> None:
    home = _home_with_root(tmp_path)
    relocated = tmp_path / "not-codex"
    (relocated / "sessions").mkdir(parents=True)

    locations = resolve_locations(
        _codex_layout(),
        home,
        {},
        processes=(
            ProcessInfo(pid=1, executable="/usr/bin/other", environment={"CODEX_HOME": str(relocated)}),
        ),
    )

    assert [location.path for location in locations] == [home / ".codex"]


def test_installed_application_metadata_adds_a_custom_data_directory(tmp_path: Path) -> None:
    home = tmp_path / "home"
    insider_data = home / "Library" / "Application Support" / "Code - Insiders"
    (insider_data / "User" / "workspaceStorage").mkdir(parents=True)
    layout = AgentLayout(
        agent_id="vscode-copilot-local",
        names=("Library/Application Support/Code",),
        applications=(ApplicationHint("Visual Studio Code - Insiders", ("Library/Application Support/Code - Insiders",)),),
        markers=("User/workspaceStorage",),
    )

    without_app = resolve_locations(layout, home, {}, platform="darwin")
    with_app = resolve_locations(
        layout, home, {}, installed_applications=("Visual Studio Code - Insiders",), platform="darwin"
    )

    assert without_app == ()
    assert [location.path for location in with_app] == [insider_data]
    assert with_app[0].origin is LocationOrigin.APPLICATION


def test_markerless_directory_is_located_but_not_a_data_source(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)

    located = resolve_locations(_codex_layout(), home, {})
    verdict = validate_location(_codex_layout(), located[0])

    assert [location.path for location in located] == [home / ".codex"]
    assert verdict.state is LocationState.EMPTY
    assert verdict.usable is False


def test_partial_layout_is_unrecognized_not_empty(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".codex" / "unrelated").mkdir(parents=True)
    (home / ".codex" / "unrelated" / "file.jsonl").write_text("{}\n")

    location = Location(home / ".codex", LocationOrigin.HOME_DEFAULT)
    verdict = validate_location(_codex_layout(), location)

    assert verdict.state is LocationState.UNRECOGNIZED
    assert verdict.usable is False


def test_symlinked_root_is_reported_as_linked(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    (outside / "sessions").mkdir(parents=True)
    (home / ".codex").symlink_to(outside, target_is_directory=True)

    assert resolve_locations(_codex_layout(), home, {}) == ()
    verdict = validate_location(
        _codex_layout(), Location(home / ".codex", LocationOrigin.HOME_DEFAULT)
    )
    assert verdict.state is LocationState.LINKED


def test_unset_variable_in_a_layout_name_skips_that_name(tmp_path: Path) -> None:
    home = _home_with_root(tmp_path)
    layout = AgentLayout(
        agent_id="synthetic",
        names=("$MISSING_VARIABLE/codex", ".codex"),
        markers=("sessions",),
    )

    locations = resolve_locations(layout, home, {})

    assert [location.path for location in locations] == [home / ".codex"]


def test_a_search_root_equal_to_home_does_not_undo_platform_replacement(
    tmp_path: Path,
) -> None:
    """``candidate_roots`` includes home, so the search layer must skip it."""
    home = tmp_path / "home"
    (home / "generic" / "Code" / "User").mkdir(parents=True)
    platform = home / "Library" / "Application Support" / "Code"
    (platform / "User").mkdir(parents=True)
    layout = AgentLayout(
        agent_id="vscode-copilot-local",
        names=("generic/Code",),
        platform_names={"darwin": ("~/Library/Application Support/Code",)},
        markers=("User",),
    )

    locations = resolve_locations(
        layout, home, {}, search_roots=(home,), platform="darwin"
    )

    assert [location.path for location in locations] == [platform]


def test_a_directory_named_with_a_dollar_sign_is_still_a_location(tmp_path: Path) -> None:
    """Variables expand like ``os.path.expandvars``: an unset name stays literal."""
    home = tmp_path / "home"
    root = home / "$archive" / "claude"
    (root / "projects").mkdir(parents=True)

    locations = resolve_locations(
        _claude_layout(), home, {"CLAUDE_CONFIG_DIR": str(root)}
    )

    assert [location.path for location in locations] == [root]


def test_platform_names_replace_the_generic_default(tmp_path: Path) -> None:
    """Both directories exist, so only a replacing implementation passes."""
    home = tmp_path / "home"
    generic = home / "generic" / "Code"
    generic.mkdir(parents=True)
    platform = home / "Library" / "Application Support" / "Code"
    (platform / "User").mkdir(parents=True)
    layout = AgentLayout(
        agent_id="vscode-copilot-local",
        names=("generic/Code",),
        platform_names={"darwin": ("~/Library/Application Support/Code",)},
        markers=("User",),
    )

    locations = resolve_locations(layout, home, {}, platform="darwin")

    assert [location.path for location in locations] == [platform]
    assert locations[0].origin is LocationOrigin.PLATFORM_DEFAULT
