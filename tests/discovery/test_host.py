"""Host probes reject unsafe configuration and honour each platform's registry."""

import json
import os
from pathlib import Path

import pytest
from tokenhub.discovery.host import (
    _application_registries,
    installed_applications,
    read_user_location_config,
    running_agents,
)


def test_user_locations_keep_absolute_paths_and_drop_the_rest(tmp_path: Path) -> None:
    file = tmp_path / "discovery.json"
    file.write_text(
        json.dumps(
            {
                "agents": {
                    "codex-local": {
                        "locations": [
                            str(tmp_path / "archive"),
                            "~/relative-home",
                            "../escape",
                            "",
                            7,
                        ]
                    },
                    "claude-code-local": {"locations": []},
                    "broken": "not-a-table",
                }
            }
        )
    )

    configured = read_user_location_config(file, home=tmp_path / "home")

    assert configured == {
        "codex-local": (tmp_path / "archive", tmp_path / "home" / "relative-home")
    }


def test_tilde_entries_expand_against_the_injected_home(tmp_path: Path) -> None:
    file = tmp_path / "discovery.json"
    file.write_text(json.dumps({"agents": {"codex-local": {"locations": ["~/archive"]}}}))

    configured = read_user_location_config(file, home=tmp_path / "home")

    assert configured == {"codex-local": (tmp_path / "home" / "archive",)}


def test_missing_or_malformed_user_config_is_empty(tmp_path: Path) -> None:
    malformed = tmp_path / "discovery.json"
    malformed.write_text("{not json")
    array = tmp_path / "array.json"
    array.write_text("[]")

    assert read_user_location_config(malformed) == {}
    assert read_user_location_config(array) == {}
    assert read_user_location_config(tmp_path / "absent.json") == {}


def test_macos_application_bundle_is_detected_in_the_user_applications_folder(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    (home / "Applications" / "Synthetic Agent.app").mkdir(parents=True)

    found = installed_applications(
        ("Synthetic Agent", "Another Agent"), home=home, environment={}, platform="darwin"
    )

    assert found == ("Synthetic Agent",)


def test_linux_desktop_entry_is_detected(tmp_path: Path) -> None:
    home = tmp_path / "home"
    applications = home / ".local" / "share" / "applications"
    applications.mkdir(parents=True)
    (applications / "synthetic-agent.desktop").write_text("[Desktop Entry]\n")

    found = installed_applications(
        ("synthetic-agent",), home=home, environment={}, platform="linux"
    )

    assert found == ("synthetic-agent",)


def test_linux_registry_roots_are_absolute_and_deduplicated(tmp_path: Path) -> None:
    """An unset XDG_DATA_HOME must not list the local share folder twice, and a
    relative value must not make the search depend on the process cwd."""
    home = tmp_path / "home"

    unset, suffix = _application_registries("linux", home, {})
    relative, _ = _application_registries("linux", home, {"XDG_DATA_HOME": "relative"})

    assert suffix == ".desktop"
    assert len(set(unset)) == len(unset) == 2
    assert all(root.is_absolute() for root in unset)
    assert all(root.is_absolute() for root in relative)


def test_windows_registry_probe_is_skipped_off_windows(tmp_path: Path) -> None:
    assert installed_applications(
        ("Antigravity",), home=tmp_path, environment={}, platform="linux"
    ) == ()


def test_running_agents_keeps_only_the_keys_a_layout_asked_for() -> None:
    """The ps layer is exercised through the runner seam; no real process is read."""
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> str:
        calls.append(argv)
        if "pid=,comm=" in argv:
            return "  123 /usr/local/bin/Codex\n  456 /usr/bin/other\n"
        return "codex CODEX_HOME=/srv/codex-home SECRET_TOKEN=never-retained"

    found = running_agents(("codex",), ("CODEX_HOME",), platform="darwin", runner=runner)

    assert [(process.pid, process.name) for process in found] == [(123, "Codex")]
    assert dict(found[0].environment) == {"CODEX_HOME": "/srv/codex-home"}
    assert ["ps", "-axo", "pid=,comm="] in calls


def test_running_agents_returns_nothing_when_the_process_list_fails() -> None:
    assert running_agents(("codex",), ("CODEX_HOME",), platform="darwin", runner=lambda _: "") == ()


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_application_directory_that_cannot_be_listed_is_ignored(tmp_path: Path) -> None:
    home = tmp_path / "home"
    broken = home / "Applications"
    broken.mkdir(parents=True)
    broken.chmod(0o000)
    try:
        assert installed_applications(
            ("Synthetic Agent",), home=home, environment={}, platform="darwin"
        ) == ()
    finally:
        broken.chmod(0o755)
