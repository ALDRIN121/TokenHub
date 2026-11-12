"""Candidate roots and layered layout lookup find an agent on any drive."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tokenhub.app import create_app
from tokenhub.connectors.antigravity.connector import AntigravityConnector
from tokenhub.connectors.claude.connector import ClaudeConnector
from tokenhub.connectors.codex.connector import CodexConnector
from tokenhub.connectors.hermes.connector import HermesConnector
from tokenhub.connectors.protocol import (
    DiscoveryContext,
    find_layout_roots,
    locate_locations,
)
from tokenhub.connectors.roots import candidate_roots
from tokenhub.settings import TokenHubSettings

from tests.api.conftest import ORIGIN
from tests.connectors.antigravity.test_antigravity import _database


def _context(home: Path, *search: Path, environment: dict[str, str] | None = None) -> DiscoveryContext:
    return DiscoveryContext(home, environment or {}, lambda _: None, tuple(search))


def test_candidate_roots_on_windows_cover_each_drive_for_current_user_only(tmp_path: Path) -> None:
    home = tmp_path / "c/Users/me"
    d_profile = tmp_path / "d/Users/me"
    other_profile = tmp_path / "d/Users/other"
    appdata = home / "AppData/Roaming"
    for path in (home, d_profile, other_profile, appdata):
        path.mkdir(parents=True)
    environment = {"USERPROFILE": str(home), "APPDATA": str(appdata), "USERNAME": "me"}

    def drives() -> tuple[Path, ...]:
        return (tmp_path / "c", tmp_path / "d", tmp_path / "e")

    roots = candidate_roots(environment, home, platform="win32", list_drives=drives)

    assert roots == (home, appdata, d_profile)
    assert other_profile not in roots


def test_candidate_roots_skip_missing_symlinked_and_duplicate_entries(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    link = tmp_path / "link"
    link.symlink_to(home)
    environment = {"USERPROFILE": str(home), "XDG_CONFIG_HOME": str(link), "XDG_DATA_HOME": str(tmp_path / "gone")}

    assert candidate_roots(environment, home, platform="linux") == (home,)


def test_candidate_roots_ignore_drives_off_windows(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (tmp_path / "d/Users/me").mkdir(parents=True)
    home.mkdir()

    roots = candidate_roots(
        {"USERNAME": "me"}, home, platform="linux", list_drives=lambda: (tmp_path / "d",)
    )

    assert roots == (home,)


def test_a_profile_only_root_is_still_a_valid_hermes_location(tmp_path: Path) -> None:
    """A root whose only data is ``profiles/<name>/state.db`` must survive."""
    home = tmp_path / "home"
    profile = home / ".hermes" / "profiles" / "work"
    profile.mkdir(parents=True)
    (profile / "state.db").write_text("profile database")

    roots = find_layout_roots(_context(home), HermesConnector.layout)

    assert roots == (home / ".hermes",)


def test_channel_and_package_roots_are_all_expanded(tmp_path: Path) -> None:
    home, local = tmp_path / "home", tmp_path / "Local"
    home.mkdir()
    (local / "hermes").mkdir(parents=True)
    (local / "hermes-beta").mkdir()
    (local / "hermes" / "state.db").write_text("database")
    (local / "hermes-beta" / "state.db").write_text("database")
    package = local / "Packages" / "NousResearch.Hermes_abc" / "LocalCache" / "Local" / "hermes"
    package.mkdir(parents=True)
    (package / "state.db").write_text("database")

    roots = find_layout_roots(_context(home, local), HermesConnector.layout)

    assert set(roots) == {local / "hermes", local / "hermes-beta", package}


def test_an_empty_home_stub_does_not_hide_data_on_another_drive(tmp_path: Path) -> None:
    home, other = tmp_path / "home", tmp_path / "other"
    (home / ".codex").mkdir(parents=True)
    (other / ".codex/sessions").mkdir(parents=True)

    located = locate_locations(_context(home, other), CodexConnector.layout)
    roots = find_layout_roots(_context(home, other), CodexConnector.layout)

    # The stub is still reported as a location, but only the real one imports.
    assert {location.path for location in located} == {home / ".codex", other / ".codex"}
    assert roots == (other / ".codex",)


def test_override_replaces_search_roots(tmp_path: Path) -> None:
    home, other, custom = tmp_path / "home", tmp_path / "other", tmp_path / "custom"
    (other / ".codex/sessions").mkdir(parents=True)
    (custom / "sessions").mkdir(parents=True)
    home.mkdir()
    context = _context(home, other, environment={"CODEX_HOME": str(custom)})

    assert find_layout_roots(context, CodexConnector.layout) == (custom,)


def test_override_expands_windows_style_home_and_environment_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, drive = tmp_path / "home", tmp_path / "drive"
    (home / "agents" / "sessions").mkdir(parents=True)
    (drive / "data" / "sessions").mkdir(parents=True)
    tilde = _context(home, environment={"CODEX_HOME": "~\\agents"})
    variable = _context(
        home,
        environment={
            "CODEX_HOME": "$TOKENHUB_TEST_DRIVE/data",
            "TOKENHUB_TEST_DRIVE": str(drive),
        },
    )

    if sys.platform == "win32":  # backslash is a path separator only on Windows
        assert find_layout_roots(tilde, CodexConnector.layout) == (home / "agents",)
    assert find_layout_roots(variable, CodexConnector.layout) == (drive / "data",)


def test_antigravity_found_under_a_secondary_root(tmp_path: Path) -> None:
    home, other = tmp_path / "home", tmp_path / "d/Users/me"
    home.mkdir()
    _database(other / ".gemini/antigravity/conversations/conversation.db")
    context = _context(home, other)

    detected = AntigravityConnector().detect(context)

    assert len(detected.sources) == 1
    assert "session_source_found" in detected.evidence_codes


def test_codex_and_claude_found_under_a_secondary_root(tmp_path: Path) -> None:
    home, other = tmp_path / "home", tmp_path / "d/Users/me"
    home.mkdir()
    (other / ".codex/sessions/2026").mkdir(parents=True)
    (other / ".codex/sessions/2026/rollout-a.jsonl").write_text("{}\n")
    (other / ".claude/projects/p").mkdir(parents=True)
    (other / ".claude/projects/p/a.jsonl").write_text("{}\n")
    context = _context(home, other)

    assert CodexConnector().detect(context).sources
    assert ClaudeConnector().detect(context).sources


def test_app_imports_usage_from_a_profile_outside_home_using_default_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The production context, not a test double, finds data on a redirected profile."""
    home, profile = tmp_path / "home", tmp_path / "d/Users/me"
    (home / ".gemini/antigravity").mkdir(parents=True)  # empty stub that used to hide the data
    _database(profile / ".gemini/antigravity/conversations/conversation.db")
    monkeypatch.setenv("USERPROFILE", str(profile))
    monkeypatch.delenv("ANTIGRAVITY_HOME", raising=False)
    app = create_app(TokenHubSettings(home_directory=home, data_directory=tmp_path / "data"))

    with TestClient(app, base_url="http://127.0.0.1:7432") as client:
        providers = client.get("/api/v1/discovery").json()["providers"]
        provider = next(item for item in providers if item["provider"] == "antigravity")
        assert len(provider["sources"]) == 1
        assert client.post("/api/v1/collection/antigravity/enable", headers=ORIGIN).status_code == 200
        usage = client.get("/api/v1/usage").json()
        assert usage["totals"]["event_count"] == 1
        assert "private prompt" not in str(usage)
