import os
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import tokenhub.connectors.codex.parser as codex_parser
import tokenhub.security.paths as security_paths
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from tokenhub.connectors.codex.connector import CodexConnector
from tokenhub.database.models import (
    ImportRunRecord,
    SourceRecord,
    SyncCursorRecord,
    UsageEventRecord,
)
from tokenhub.domain.models import Provider, Quality, SourceState
from tokenhub.ingestion.service import (
    SourceNotApprovedError,
    SourceNotFoundError,
    UnsupportedSourceError,
)

from tests.service_support import (
    Services,
    discover_and_approve_codex,
    discover_codex,
    services_for,
    token_record,
)


def forbid_provider_open(*args: object, **kwargs: object) -> int:
    pytest.fail("rejected sources must never reach a provider file open")


def exercise_discovery_swap(
    app_services: Services,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    swap_ancestor: bool,
) -> None:
    trusted_sessions = app_services.session_file.parent
    trusted_codex_home = trusted_sessions.parent
    outside_codex_home = tmp_path / "outside-codex"
    outside_sessions = outside_codex_home / "sessions"
    outside_sessions.mkdir(parents=True)
    outside_file = outside_sessions / app_services.session_file.name
    outside_file.write_bytes(token_record(99, input_tokens=999, output_tokens=999))
    original_resolve = Path.resolve
    original_open = os.open
    resolve_calls = 0
    anchor_opens = 0
    swapped = False

    def swap_root() -> None:
        nonlocal swapped
        if swapped:
            return
        if swap_ancestor:
            trusted_codex_home.rename(trusted_codex_home.with_name(".codex-original"))
            trusted_codex_home.symlink_to(outside_codex_home, target_is_directory=True)
        else:
            trusted_sessions.rename(trusted_sessions.with_name("sessions-original"))
            trusted_sessions.symlink_to(outside_sessions, target_is_directory=True)
        swapped = True

    def swap_on_second_resolve(path: Path, *, strict: bool = False) -> Path:
        nonlocal resolve_calls
        if path == trusted_sessions:
            resolve_calls += 1
            if resolve_calls == 2:
                swap_root()
        return original_resolve(path, strict=strict)

    def swap_after_anchor_open(
        path: str | bytes | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal anchor_opens
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        lexical_path = (
            Path(os.fsdecode(path)) if isinstance(path, bytes) else Path(path)
        )
        anchor_component = Path(".codex" if swap_ancestor else "sessions")
        # Discovery walks each tree once per pass, so swap right after the root
        # is anchored. The old implementation calls resolve first; this hook is
        # for the anchored implementation and fires only when none occurred.
        if (
            resolve_calls == 0
            and dir_fd is not None
            and lexical_path == anchor_component
        ):
            anchor_opens += 1
            if anchor_opens == 1:
                swap_root()
        return descriptor

    monkeypatch.setattr(Path, "resolve", swap_on_second_resolve)
    monkeypatch.setattr(os, "open", swap_after_anchor_open)
    monkeypatch.setattr(codex_parser, "parse_codex_jsonl", forbid_provider_open)

    results = app_services.discovery.discover()
    codex = next(result for result in results if result.connector_id == "codex-local")
    assert swapped is True
    assert len(codex.sources) == 1
    source_id = codex.sources[0].source_id
    with pytest.raises(ValueError):
        app_services.ingestion.approve(source_id)
    with pytest.raises(ValueError):
        app_services.ingestion.rescan(source_id)

    assert app_services.analytics.dashboard().event_count == 0
    stored = app_services.source_repository.get(source_id)
    assert stored.canonical_path is None
    assert stored.approved_root is None
    assert outside_file.read_bytes() == token_record(
        99, input_tokens=999, output_tokens=999
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX openat race injection")
def test_discovery_root_swap_cannot_become_trusted_source(
    app_services: Services,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exercise_discovery_swap(
        app_services,
        tmp_path,
        monkeypatch,
        swap_ancestor=False,
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX openat race injection")
def test_discovery_ancestor_swap_cannot_become_trusted_source(
    app_services: Services,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exercise_discovery_swap(
        app_services,
        tmp_path,
        monkeypatch,
        swap_ancestor=True,
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX openat capability gate")
def test_discovery_fails_closed_without_secure_directory_traversal(
    app_services: Services,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(security_paths, "_SECURE_DIR_FD_TRAVERSAL", False)

    results = app_services.discovery.discover()

    codex = next(result for result in results if result.connector_id == "codex-local")
    assert codex.state == SourceState.ERROR
    assert codex.sources == ()
    with app_services.session.begin():
        assert (
            app_services.session.scalar(
                select(func.count())
                .select_from(SourceRecord)
                .where(SourceRecord.connector_id == "codex-local")
            )
            == 0
        )


def test_rescan_requires_approval_before_opening_source(
    app_services: Services,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_id = discover_codex(app_services)
    monkeypatch.setattr(os, "open", forbid_provider_open)
    with pytest.raises(SourceNotApprovedError):
        app_services.ingestion.rescan(source_id)
    with pytest.raises(SourceNotFoundError):
        app_services.ingestion.rescan("not-discovered")
    assert app_services.usage_repository.current_cursor(source_id) is None


@pytest.mark.parametrize(
    "connector_id,provider",
    [
        ("claude-code-local", Provider.CLAUDE_CODE),
        ("hermes-local", Provider.HERMES),
    ],
)
def test_unsupported_even_if_approved_metadata_claims_scan_support(
    app_services: Services,
    monkeypatch: pytest.MonkeyPatch,
    connector_id: str,
    provider: Provider,
) -> None:
    source_id = discover_and_approve_codex(app_services)
    with app_services.session.begin():
        app_services.session.execute(
            update(SourceRecord)
            .where(SourceRecord.source_id == source_id)
            .values(connector_id=connector_id, provider=provider.value)
        )
    monkeypatch.setattr(os, "open", forbid_provider_open)
    with pytest.raises(UnsupportedSourceError):
        app_services.ingestion.rescan(source_id)
    assert app_services.analytics.dashboard().event_count == 0


def test_approval_revalidates_containment_before_persisting_path(
    app_services: Services,
    tmp_path: Path,
) -> None:
    source_id = discover_codex(app_services)
    assert app_services.source_repository.get(source_id).canonical_path is None
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(token_record(99, input_tokens=999))
    app_services.session_file.unlink()
    app_services.session_file.symlink_to(outside)
    with pytest.raises(ValueError, match="approved root"):
        app_services.ingestion.approve(source_id)
    source = app_services.source_repository.get(source_id)
    assert source.canonical_path is None
    assert source.approved_root is None
    assert source.state == SourceState.DISCOVERED


def test_approval_rejects_discovered_root_replaced_by_outside_symlink(
    app_services: Services,
    tmp_path: Path,
) -> None:
    source_id = discover_codex(app_services)
    discovered_root = app_services.session_file.parent
    discovered_root.rename(discovered_root.with_name("sessions-original"))
    outside_root = tmp_path / "outside-sessions"
    outside_root.mkdir()
    (outside_root / app_services.session_file.name).write_bytes(
        token_record(99, input_tokens=999)
    )
    discovered_root.symlink_to(outside_root, target_is_directory=True)

    with pytest.raises(ValueError, match="approved root"):
        app_services.ingestion.approve(source_id)

    source = app_services.source_repository.get(source_id)
    assert source.canonical_path is None
    assert source.approved_root is None
    assert source.state == SourceState.DISCOVERED


def test_approval_rejects_discovered_ancestor_replaced_by_outside_symlink(
    app_services: Services,
    tmp_path: Path,
) -> None:
    source_id = discover_codex(app_services)
    codex_home = app_services.session_file.parent.parent
    codex_home.rename(codex_home.with_name(".codex-original"))
    outside_home = tmp_path / "outside-codex"
    outside_sessions = outside_home / "sessions"
    outside_sessions.mkdir(parents=True)
    (outside_sessions / app_services.session_file.name).write_bytes(
        token_record(99, input_tokens=999)
    )
    codex_home.symlink_to(outside_home, target_is_directory=True)

    with pytest.raises(ValueError, match="approved root"):
        app_services.ingestion.approve(source_id)

    source = app_services.source_repository.get(source_id)
    assert source.canonical_path is None
    assert source.approved_root is None
    assert source.state == SourceState.DISCOVERED


def test_approval_rejects_discovered_root_replaced_by_new_directory(
    app_services: Services,
) -> None:
    source_id = discover_codex(app_services)
    discovered_root = app_services.session_file.parent
    discovered_root.rename(discovered_root.with_name("sessions-original"))
    discovered_root.mkdir()
    app_services.session_file.write_bytes(token_record(99, input_tokens=999))

    with pytest.raises(ValueError, match="approved root"):
        app_services.ingestion.approve(source_id)

    source = app_services.source_repository.get(source_id)
    assert source.canonical_path is None
    assert source.approved_root is None
    assert source.state == SourceState.DISCOVERED


def test_idempotent_and_incremental_import(app_services: Services) -> None:
    source_id = discover_and_approve_codex(app_services)
    first = app_services.ingestion.rescan(source_id)
    second = app_services.ingestion.rescan(source_id)
    with app_services.session_file.open("ab") as stream:
        stream.write(token_record(2, input_tokens=10, output_tokens=5))
    third = app_services.ingestion.rescan(source_id)
    assert (first.inserted_events, second.inserted_events, third.inserted_events) == (
        1,
        0,
        1,
    )
    summary = app_services.analytics.dashboard()
    assert summary.workload_tokens == 140
    assert summary.cache_read_tokens == 70
    assert summary.reasoning_tokens == 20
    assert summary.event_count == 2
    assert app_services.source_repository.get(source_id).state == SourceState.HEALTHY


def test_partial_commits_valid_events_and_safe_cursor_then_retries(
    app_services: Services,
) -> None:
    source_id = discover_and_approve_codex(app_services)
    original_bytes = app_services.session_file.read_bytes()
    second = token_record(2, input_tokens=10, output_tokens=5)
    app_services.session_file.write_bytes(original_bytes + second[:-2])
    partial = app_services.ingestion.rescan(source_id)
    assert partial.inserted_events == 1
    assert partial.partial_final_record is True
    assert partial.cursor.byte_offset == len(original_bytes)
    assert app_services.source_repository.get(source_id).state == SourceState.PARTIAL
    again = app_services.ingestion.rescan(source_id)
    assert again.inserted_events == 0
    assert again.cursor == partial.cursor
    with app_services.session_file.open("ab") as stream:
        stream.write(second[-2:])
    complete = app_services.ingestion.rescan(source_id)
    assert complete.inserted_events == 1
    assert complete.partial_final_record is False
    assert complete.cursor.source_unsupported_records == 0
    assert app_services.source_repository.get(source_id).state == SourceState.HEALTHY
    assert app_services.analytics.dashboard().workload_tokens == 140
    with app_services.session.begin():
        runs = list(
            app_services.session.scalars(
                select(ImportRunRecord).order_by(ImportRunRecord.id)
            )
        )
        assert [run.partial_final_record for run in runs] == [True, True, False]


def test_malformed_completed_records_preserve_events_and_issue_count(
    app_services: Services,
) -> None:
    source_id = discover_and_approve_codex(app_services)
    with app_services.session_file.open("ab") as stream:
        stream.write(b'{"unknown":"synthetic"}\n')
    result = app_services.ingestion.rescan(source_id)
    assert result.inserted_events == 1
    assert result.unsupported_records == 1
    assert app_services.source_repository.get(source_id).state == SourceState.PARTIAL
    assert result.cursor.byte_offset == app_services.session_file.stat().st_size
    with app_services.session.begin():
        assert (
            app_services.session.scalar(select(ImportRunRecord.unsupported_records))
            == 1
        )


def test_malformed_quality_survives_unchanged_rescan(app_services: Services) -> None:
    source_id = discover_and_approve_codex(app_services)
    with app_services.session_file.open("ab") as stream:
        stream.write(b'{"unknown":"synthetic"}\n')
    first = app_services.ingestion.rescan(source_id)

    second = app_services.ingestion.rescan(source_id)

    assert first.unsupported_records == 1
    assert second.unsupported_records == 0
    assert second.cursor.source_unsupported_records == 1
    assert app_services.source_repository.get(source_id).state == SourceState.PARTIAL
    freshness = next(
        item
        for item in app_services.analytics.dashboard().source_freshness
        if item.source_id == source_id
    )
    assert freshness.state == SourceState.PARTIAL
    assert freshness.unsupported_records == 1
    assert app_services.analytics.dashboard().quality_counts[Quality.EXACT] == 1


def test_malformed_quality_survives_valid_append(app_services: Services) -> None:
    source_id = discover_and_approve_codex(app_services)
    with app_services.session_file.open("ab") as stream:
        stream.write(b'{"unknown":"synthetic"}\n')
    app_services.ingestion.rescan(source_id)
    with app_services.session_file.open("ab") as stream:
        stream.write(token_record(2, input_tokens=10, output_tokens=5))

    appended = app_services.ingestion.rescan(source_id)

    assert appended.inserted_events == 1
    assert appended.unsupported_records == 0
    assert appended.cursor.source_unsupported_records == 1
    assert app_services.source_repository.get(source_id).state == SourceState.PARTIAL


def test_malformed_quality_survives_process_restart(
    app_services: Services,
    tmp_path: Path,
) -> None:
    source_id = discover_and_approve_codex(app_services)
    with app_services.session_file.open("ab") as stream:
        stream.write(b'{"unknown":"synthetic"}\n')
    app_services.ingestion.rescan(source_id)
    restarted = services_for(
        app_services.session, tmp_path / "empty-home", app_services.session_file
    )

    rescanned = restarted.ingestion.rescan(source_id)

    assert rescanned.unsupported_records == 0
    assert rescanned.cursor.source_unsupported_records == 1
    assert restarted.source_repository.get(source_id).state == SourceState.PARTIAL


def test_malformed_quality_survives_rebuild(app_services: Services) -> None:
    source_id = discover_and_approve_codex(app_services)
    with app_services.session_file.open("ab") as stream:
        stream.write(b'{"unknown":"synthetic"}\n')
    app_services.ingestion.rescan(source_id)

    rebuilt = app_services.ingestion.rebuild()

    assert rebuilt.imports[0].unsupported_records == 1
    assert rebuilt.imports[0].cursor.source_unsupported_records == 1
    assert app_services.source_repository.get(source_id).state == SourceState.PARTIAL


def test_changed_source_full_reparse_clears_malformed_quality(
    app_services: Services,
) -> None:
    source_id = discover_and_approve_codex(app_services)
    with app_services.session_file.open("ab") as stream:
        stream.write(b'{"unknown":"synthetic"}\n')
    app_services.ingestion.rescan(source_id)
    replacement = token_record(2, input_tokens=10, output_tokens=5)
    app_services.session_file.write_bytes(replacement)

    replaced = app_services.ingestion.rescan(source_id)

    assert replaced.cursor.byte_offset == len(replacement)
    assert replaced.cursor.source_unsupported_records == 0
    assert app_services.source_repository.get(source_id).state == SourceState.HEALTHY


def test_scan_failure_cannot_commit_events_cursor_or_audit(
    app_services: Services,
) -> None:
    source_id = discover_and_approve_codex(app_services)
    with app_services.session.begin():
        app_services.session.execute(
            text(
                "CREATE TRIGGER reject_cursor BEFORE INSERT ON sync_cursors "
                "BEGIN SELECT RAISE(ABORT, 'synthetic cursor failure'); END"
            )
        )
    with pytest.raises(IntegrityError):
        app_services.ingestion.rescan(source_id)
    assert app_services.analytics.dashboard().event_count == 0
    assert app_services.usage_repository.current_cursor(source_id) is None
    assert app_services.source_repository.get(source_id).state == SourceState.APPROVED
    with app_services.session.begin():
        assert (
            app_services.session.scalar(
                select(func.count()).select_from(ImportRunRecord)
            )
            == 0
        )


def test_missing_source_state_update_rolls_back_events_cursor_and_audit(
    app_services: Services,
) -> None:
    source_id = discover_codex(app_services)
    candidate = app_services.discovery.candidate(source_id)
    app_services.ingestion.approve(source_id)
    scan = CodexConnector().scan(
        replace(candidate, state=SourceState.APPROVED),
        None,
    )
    assert scan.cursor is not None
    with app_services.session.begin():
        app_services.session.execute(
            delete(SourceRecord).where(SourceRecord.source_id == source_id)
        )

    with pytest.raises(LookupError, match="source disappeared"):
        app_services.usage_repository.persist_scan(
            list(scan.events),
            scan.cursor,
            state=SourceState.HEALTHY,
        )

    with app_services.session.begin():
        assert (
            app_services.session.scalar(
                select(func.count()).select_from(UsageEventRecord)
            )
            == 0
        )
        assert (
            app_services.session.scalar(
                select(func.count()).select_from(SyncCursorRecord)
            )
            == 0
        )
        assert (
            app_services.session.scalar(
                select(func.count()).select_from(ImportRunRecord)
            )
            == 0
        )


def test_deduplication_is_scoped_to_each_source(app_services: Services) -> None:
    other = app_services.session_file.with_name("other.jsonl")
    other.write_bytes(app_services.session_file.read_bytes() * 2)
    codex = next(
        r for r in app_services.discovery.discover() if r.connector_id == "codex-local"
    )
    for source in codex.sources:
        app_services.ingestion.approve(source.source_id)
        app_services.ingestion.rescan(source.source_id)
    assert app_services.analytics.dashboard().event_count == 2
    assert app_services.analytics.dashboard().workload_tokens == 250


def test_rebuild_uses_saved_sources_and_preserves_provider_files_and_audit(
    app_services: Services,
    tmp_path: Path,
) -> None:
    source_id = discover_and_approve_codex(app_services)
    app_services.ingestion.rescan(source_id)
    before = app_services.analytics.dashboard()
    original_bytes = app_services.session_file.read_bytes()
    # A newly created file is not approved and must not enter a rebuild.
    app_services.session_file.with_name("new.jsonl").write_bytes(
        token_record(2, input_tokens=999)
    )
    # No discovery cache or current provider home is available in this composition.
    restarted = services_for(
        app_services.session, tmp_path / "empty-home", app_services.session_file
    )
    rebuilt = restarted.ingestion.rebuild()
    assert rebuilt.inserted_events == 1
    assert rebuilt.failed_source_ids == ()
    assert restarted.analytics.dashboard() == before
    assert app_services.session_file.read_bytes() == original_bytes
    assert restarted.source_repository.get(source_id).canonical_path is not None
    with app_services.session.begin():
        assert (
            app_services.session.scalar(
                select(func.count()).select_from(ImportRunRecord)
            )
            == 2
        )


def test_rescan_after_approval_rejects_replacement_symlink(
    app_services: Services,
    tmp_path: Path,
) -> None:
    source_id = discover_and_approve_codex(app_services)
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(token_record(99, input_tokens=999, output_tokens=999))
    app_services.session_file.unlink()
    app_services.session_file.symlink_to(outside)
    with pytest.raises(ValueError, match="safely"):
        app_services.ingestion.rescan(source_id)
    assert app_services.analytics.dashboard().event_count == 0
    assert app_services.usage_repository.current_cursor(source_id) is None


def test_rescan_rejects_approved_root_replaced_by_new_directory(
    app_services: Services,
) -> None:
    source_id = discover_and_approve_codex(app_services)
    approved_root = app_services.session_file.parent
    approved_root.rename(approved_root.with_name("sessions-original"))
    approved_root.mkdir()
    app_services.session_file.write_bytes(token_record(99, input_tokens=999))

    with pytest.raises(ValueError, match="safely"):
        app_services.ingestion.rescan(source_id)

    assert app_services.analytics.dashboard().event_count == 0
    assert app_services.usage_repository.current_cursor(source_id) is None


def test_persisted_raw_dot_path_rejected_before_any_open(
    app_services: Services,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_id = discover_and_approve_codex(app_services)
    with app_services.session.begin():
        app_services.session.execute(
            update(SourceRecord)
            .where(SourceRecord.source_id == source_id)
            .values(
                canonical_path=f"{app_services.session_file.parent}/./synthetic.jsonl"
            )
        )
    monkeypatch.setattr(os, "open", forbid_provider_open)
    with pytest.raises(ValueError, match="dot components"):
        app_services.ingestion.rescan(source_id)


def test_connector_refuses_unapproved_descriptor_before_open(
    app_services: Services,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_id = discover_codex(app_services)
    candidate = app_services.discovery.candidate(source_id)
    monkeypatch.setattr(os, "open", forbid_provider_open)
    result = CodexConnector().scan(candidate, None)
    assert result.events == ()
    assert result.cursor is None
    for changed in (
        replace(candidate, state=SourceState.APPROVED, connector_id="hermes-local"),
        replace(candidate, state=SourceState.APPROVED, scan_supported=False),
    ):
        assert CodexConnector().scan(changed, None).state is SourceState.UNSUPPORTED


def test_truncation_restarts_from_zero_on_securely_opened_file(
    app_services: Services,
) -> None:
    source_id = discover_and_approve_codex(app_services)
    app_services.ingestion.rescan(source_id)
    app_services.session_file.write_bytes(
        token_record(2, input_tokens=10, output_tokens=5)
    )
    imported = app_services.ingestion.rescan(source_id)
    assert imported.inserted_events == 1
    assert imported.cursor.byte_offset == app_services.session_file.stat().st_size
    assert app_services.analytics.dashboard().workload_tokens == 140


def test_same_or_larger_replacement_reparses_when_consumed_prefix_changed(
    app_services: Services,
) -> None:
    source_id = discover_and_approve_codex(app_services)
    first = app_services.ingestion.rescan(source_id)
    replacement = token_record(2, input_tokens=10, output_tokens=5) + token_record(
        3, input_tokens=20, output_tokens=10
    )
    assert len(replacement) >= first.cursor.byte_offset
    app_services.session_file.write_bytes(replacement)

    imported = app_services.ingestion.rescan(source_id)

    assert imported.inserted_events == 2
    assert imported.unsupported_records == 0
    assert imported.cursor.byte_offset == len(replacement)
    assert app_services.analytics.dashboard().workload_tokens == 170


def test_cursor_fingerprint_migration_preserves_legacy_rows(tmp_path: Path) -> None:
    data_directory = tmp_path / "migration-data"
    environment = {**os.environ, "TOKENHUB_DATA_DIRECTORY": str(data_directory)}
    environment.pop("TOKENHUB_DATABASE_URL", None)
    command = [
        sys.executable,
        "-m",
        "alembic",
        "-c",
        "backend/alembic.ini",
        "upgrade",
    ]
    initial = subprocess.run(
        [*command, "0001_initial"],
        cwd=Path(__file__).parents[2],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert initial.returncode == 0, initial.stderr
    database = data_directory / "tokenhub.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO sync_cursors "
            "(source_id, byte_offset, source_mtime_ns, parser_version) "
            "VALUES (?, ?, ?, ?)",
            ("legacy-source", 42, 123, "codex-jsonl-v1"),
        )

    upgraded = subprocess.run(
        [*command, "head"],
        cwd=Path(__file__).parents[2],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert upgraded.returncode == 0, upgraded.stderr
    with sqlite3.connect(database) as connection:
        cursor_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(sync_cursors)")
        }
        source_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(sources)")
        }
        row = connection.execute(
            "SELECT byte_offset, prefix_fingerprint, source_unsupported_records "
            "FROM sync_cursors "
            "WHERE source_id = ?",
            ("legacy-source",),
        ).fetchone()
    assert "prefix_fingerprint" in cursor_columns
    assert "source_unsupported_records" in cursor_columns
    assert "approved_root_device" in source_columns
    assert "approved_root_inode" in source_columns
    assert row == (42, None, 0)


def test_disabled_source_cannot_be_read_or_rebuilt(
    app_services: Services,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_id = discover_and_approve_codex(app_services)
    app_services.ingestion.rescan(source_id)
    app_services.source_repository.set_state(source_id, SourceState.DISABLED)
    monkeypatch.setattr(os, "open", forbid_provider_open)
    with pytest.raises(SourceNotApprovedError):
        app_services.ingestion.rescan(source_id)
    result = app_services.ingestion.rebuild()
    assert result.inserted_events == 0
    assert app_services.analytics.dashboard().event_count == 0
    with app_services.session.begin():
        assert (
            app_services.session.scalar(
                select(func.count()).select_from(UsageEventRecord)
            )
            == 0
        )
