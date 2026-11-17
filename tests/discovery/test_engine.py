"""The engine answers where, whether, and already-imported for every agent."""

from pathlib import Path

from tokenhub.discovery.engine import DiscoveryEngine
from tokenhub.discovery.ledger import ImportedSource, ImportLedger
from tokenhub.discovery.locations import AgentLayout


def _layout(agent_id: str, name: str) -> AgentLayout:
    return AgentLayout(
        agent_id=agent_id,
        display_name=agent_id,
        env_keys=(f"{agent_id.upper()}_HOME",),
        names=(name,),
        markers=("sessions",),
    )


def test_engine_separates_pending_from_already_imported(tmp_path: Path) -> None:
    home = tmp_path / "home"
    imported = home / ".codex"
    pending = tmp_path / "archive" / ".codex"
    for root in (imported, pending):
        (root / "sessions").mkdir(parents=True)
    layout = _layout("codex-local", ".codex")
    ledger = ImportLedger(
        sources=(
            ImportedSource(
                source_id="codex-local:one",
                connector_id="codex-local",
                approved_root=str(imported / "sessions"),
                device=1,
                inode=2,
                events=17,
            ),
        )
    )

    answer = DiscoveryEngine.for_layouts(
        [layout], home=home, environment={}, user_locations={"codex-local": (pending,)}
    ).answers(ledger)[0]

    assert [entry.state.value for entry in answer.locations] == ["valid", "valid"]
    # Operator locations are considered first, then the agent's default home.
    assert [entry.origin for entry in answer.locations] == ["user_config", "home_default"]
    assert [entry.imported for entry in answer.locations] == [False, True]
    assert [entry.imported_events for entry in answer.locations] == [0, 17]
    assert len(answer.already_imported) == 1
    assert len(answer.pending) == 1
    assert answer.pending[0].origin == "user_config"


def test_engine_ignores_another_connectors_ledger_rows(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = home / ".codex"
    (root / "sessions").mkdir(parents=True)
    ledger = ImportLedger(
        sources=(
            ImportedSource(
                source_id="claude-code-local:one",
                connector_id="claude-code-local",
                approved_root=str(root),
                device=None,
                inode=None,
                events=4,
            ),
        )
    )

    answer = DiscoveryEngine.for_layouts(
        [_layout("codex-local", ".codex")], home=home, environment={}
    ).answers(ledger)[0]

    assert answer.already_imported == ()


def test_engine_reports_invalid_locations_without_offering_them(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".codex" / "cache").mkdir(parents=True)

    answer = DiscoveryEngine.for_layouts(
        [_layout("codex-local", ".codex")], home=home, environment={}
    ).answers()[0]

    assert [entry.state.value for entry in answer.locations] == ["unrecognized"]
    assert answer.importable == ()


def test_ledger_matches_a_stored_root_by_identity_after_a_rename(tmp_path: Path) -> None:
    root = tmp_path / "one"
    (root / "sessions").mkdir(parents=True)
    status = root.stat()
    ledger = ImportLedger(
        sources=(
            ImportedSource(
                source_id="codex-local:one",
                connector_id="codex-local",
                approved_root=None,
                device=status.st_dev,
                inode=status.st_ino,
                events=3,
            ),
        )
    )

    assert ledger.for_location("codex-local", root)[0].imported is True
    assert ledger.for_location("codex-local", tmp_path / "two") == ()
