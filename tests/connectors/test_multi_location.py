"""Agents found in more than one home are all discovered and imported."""

from pathlib import Path

from tokenhub.connectors.antigravity.connector import AntigravityConnector
from tokenhub.connectors.claude.connector import ClaudeConnector
from tokenhub.connectors.codex.connector import CodexConnector
from tokenhub.connectors.copilot.connector import CopilotConnector
from tokenhub.connectors.protocol import DiscoveryContext
from tokenhub.discovery.locations import ProcessInfo


def _context(
    home: Path,
    *,
    environment: dict[str, str] | None = None,
    search: tuple[Path, ...] = (),
    locations: dict[str, tuple[Path, ...]] | None = None,
    processes: tuple[ProcessInfo, ...] = (),
    applications: tuple[str, ...] = (),
) -> DiscoveryContext:
    return DiscoveryContext(
        home,
        environment or {},
        lambda _: None,
        search,
        locations=locations or {},
        processes=processes,
        applications=applications,
    )


def _codex_sessions(root: Path) -> None:
    (root / "sessions" / "2026" / "10" / "20").mkdir(parents=True, exist_ok=True)
    (root / "sessions" / "2026" / "10" / "20" / "rollout.jsonl").write_text("{}\n")


def test_codex_imports_both_the_default_home_and_an_operator_location(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _codex_sessions(home / ".codex")
    archive = tmp_path / "archive" / "codex"
    _codex_sessions(archive)

    sources = CodexConnector().discover_sources(
        _context(home, locations={"codex-local": (archive,)})
    )

    assert sorted(str(source.canonical_path) for source in sources) == sorted(
        [
            str((home / ".codex" / "sessions" / "2026" / "10" / "20" / "rollout.jsonl").resolve()),
            str((archive / "sessions" / "2026" / "10" / "20" / "rollout.jsonl").resolve()),
        ]
    )
    # Each root is its own approval boundary.
    assert len({str(source.approved_root) for source in sources}) == 2


def test_codex_does_not_duplicate_a_root_reached_two_ways(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _codex_sessions(home / ".codex")

    sources = CodexConnector().discover_sources(
        _context(
            home,
            search=(home,),
            locations={"codex-local": (home / ".codex",)},
        )
    )

    assert len(sources) == 1


def test_claude_follows_a_running_agents_configuration_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    relocated = tmp_path / "work" / "claude"
    project = relocated / "projects" / "-Users-me-work"
    project.mkdir(parents=True)
    (project / "session.jsonl").write_text("{}\n")

    sources = ClaudeConnector().discover_sources(
        _context(
            home,
            processes=(
                ProcessInfo(
                    pid=7,
                    executable="/opt/homebrew/bin/claude",
                    environment={"CLAUDE_CONFIG_DIR": str(relocated)},
                ),
            ),
        )
    )

    assert [source.canonical_path for source in sources] == [
        (project / "session.jsonl").resolve()
    ]
    result = ClaudeConnector().detect(
        _context(
            home,
            processes=(
                ProcessInfo(
                    pid=7,
                    executable="/opt/homebrew/bin/claude",
                    environment={"CLAUDE_CONFIG_DIR": str(relocated)},
                ),
            ),
        )
    )
    assert "session_source_found" in result.evidence_codes


def test_copilot_finds_an_insiders_install_from_application_metadata(tmp_path: Path) -> None:
    home = tmp_path / "home"
    stable = home / "Library" / "Application Support" / "Code" / "User" / "workspaceStorage"
    stable.mkdir(parents=True)
    insiders = home / "Library" / "Application Support" / "Code - Insiders" / "User" / "workspaceStorage"
    workspace = insiders / "0123456789abcdef0123456789abcdef" / "chatSessions"
    workspace.mkdir(parents=True)
    (workspace / "11111111-2222-3333-4444-555555555555.jsonl").write_text("{}\n")

    sources = CopilotConnector().discover_sources(
        _context(home, applications=("Visual Studio Code - Insiders",))
    )

    assert [source.canonical_path.name for source in sources] == [
        "11111111-2222-3333-4444-555555555555.jsonl"
    ]


def test_antigravity_imports_every_located_conversation_directory(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".gemini" / "antigravity" / "conversations").mkdir(parents=True)
    archive = tmp_path / "archive" / "antigravity"
    (archive / "conversations").mkdir(parents=True)
    for index, root in enumerate((home / ".gemini" / "antigravity", archive)):
        (root / "conversations" / f"conversation-{index}.db").write_bytes(b"sqlite")

    sources = AntigravityConnector().discover_sources(
        _context(home, locations={"antigravity-local": (archive,)})
    )

    assert sorted(source.canonical_path.name for source in sources) == [
        "conversation-0.db",
        "conversation-1.db",
    ]


def test_an_override_that_does_not_exist_keeps_the_default_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _codex_sessions(home / ".codex")

    sources = CodexConnector().discover_sources(
        _context(home, environment={"CODEX_HOME": str(tmp_path / "gone")})
    )

    assert len(sources) == 1
    assert home / ".codex" in sources[0].approved_root.parents
