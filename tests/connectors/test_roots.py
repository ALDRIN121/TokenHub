"""Candidate roots and multi-root lookup find an agent's data on any drive."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tokenhub.app import create_app
from tokenhub.connectors.antigravity.connector import AntigravityConnector
from tokenhub.connectors.claude.connector import ClaudeConnector
from tokenhub.connectors.codex.connector import CodexConnector
from tokenhub.connectors.protocol import DiscoveryContext, find_root
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


def test_find_root_prefers_the_root_holding_data_over_an_empty_home_stub(tmp_path: Path) -> None:
    home, other = tmp_path / "home", tmp_path / "other"
    (home / ".codex").mkdir(parents=True)
    (other / ".codex/sessions").mkdir(parents=True)

    found = find_root(_context(home, other), "CODEX_HOME", ".codex", marker="sessions")

    assert found == other / ".codex"


def test_find_root_keeps_home_when_no_root_has_the_marker(tmp_path: Path) -> None:
    home, other = tmp_path / "home", tmp_path / "other"
    (home / ".codex").mkdir(parents=True)
    (other / ".codex").mkdir(parents=True)

    assert find_root(_context(home, other), "CODEX_HOME", ".codex", marker="sessions") == home / ".codex"


def test_find_root_falls_back_to_the_home_default_when_nothing_exists(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()

    assert find_root(_context(home, tmp_path / "x"), "CODEX_HOME", ".codex") == home / ".codex"


def test_find_root_does_not_follow_symlinked_candidates(tmp_path: Path) -> None:
    home, other = tmp_path / "home", tmp_path / "other"
    real = tmp_path / "real"
    (real / "sessions").mkdir(parents=True)
    other.mkdir()
    home.mkdir()
    (other / ".codex").symlink_to(real)

    assert find_root(_context(home, other), "CODEX_HOME", ".codex", marker="sessions") == home / ".codex"


def test_override_wins_over_search_roots(tmp_path: Path) -> None:
    home, other, custom = tmp_path / "home", tmp_path / "other", tmp_path / "custom"
    (other / ".codex/sessions").mkdir(parents=True)
    custom.mkdir()
    home.mkdir()
    context = _context(home, other, environment={"CODEX_HOME": str(custom)})

    assert find_root(context, "CODEX_HOME", ".codex", marker="sessions") == custom


def test_override_expands_windows_style_home_and_environment_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, drive = tmp_path / "home", tmp_path / "drive"
    (home / "agents").mkdir(parents=True)
    (drive / "data").mkdir(parents=True)
    monkeypatch.setenv("TOKENHUB_TEST_DRIVE", str(drive))

    tilde = _context(home, environment={"CODEX_HOME": "~\\agents"})
    variable = _context(home, environment={"CODEX_HOME": "$TOKENHUB_TEST_DRIVE/data"})

    if sys.platform == "win32":  # backslash is a path separator only on Windows
        assert find_root(tilde, "CODEX_HOME", ".codex") == home / "agents"
    assert find_root(variable, "CODEX_HOME", ".codex") == drive / "data"


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
