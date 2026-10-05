"""Automatic collection uses synthetic sources and durable approval boundaries."""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from tokenhub.app import create_app
from tokenhub.connectors.protocol import DiscoveryContext
from tokenhub.settings import TokenHubSettings

from tests.api.conftest import ORIGIN, codex_id
from tests.service_support import token_record


def test_collection_imports_approved_appends_and_skips_unchanged_files(
    client: TestClient, tmp_path: Path
) -> None:
    source_id = codex_id(client)
    client.post(f"/api/v1/sources/{source_id}/approve", headers=ORIGIN)
    container = client.app.state.container
    container.collect()
    assert client.get('/api/v1/dashboard').json()['workload_tokens'] == 125
    with (tmp_path / 'home/.codex/sessions/synthetic.jsonl').open('ab') as stream:
        stream.write(token_record(2, input_tokens=10, output_tokens=5))
    container.collect()
    assert client.get('/api/v1/dashboard').json()['workload_tokens'] == 140
    runs = container.services.session.execute(
        text('select count(*) from import_runs')
    ).scalar()
    container.services.session.rollback()
    container.collect()
    assert client.get('/api/v1/dashboard').json()['workload_tokens'] == 140
    again = container.services.session.execute(
        text('select count(*) from import_runs')
    ).scalar()
    container.services.session.rollback()
    assert again == runs


def test_collection_does_not_import_unapproved_sources(client: TestClient) -> None:
    client.app.state.container.collect()
    assert client.get('/api/v1/dashboard').json()['event_count'] == 0


def test_folder_consent_covers_new_sessions_and_survives_restart(
    client: TestClient, tmp_path: Path
) -> None:
    enabled = client.post('/api/v1/collection/codex/enable', headers=ORIGIN)
    assert enabled.status_code == 200
    assert enabled.json()['codex_auto_import'] is True
    assert client.get('/api/v1/dashboard').json()['workload_tokens'] == 125
    (tmp_path / 'home/.codex/sessions/new.jsonl').write_bytes(
        token_record(1, input_tokens=20, output_tokens=10)
    )
    restarted = create_app(client.app.state.container.settings)
    restarted.state.container.discovery_context = DiscoveryContext(
        tmp_path / 'home', {}, lambda _: None
    )
    with TestClient(restarted, base_url='http://127.0.0.1:7432') as second:
        restarted.state.container.collect()
        assert second.get('/api/v1/collection').json()['codex_auto_import'] is True
        assert second.get('/api/v1/dashboard').json()['workload_tokens'] == 155


def test_folder_consent_does_not_follow_a_replaced_root(
    client: TestClient, tmp_path: Path
) -> None:
    assert client.post('/api/v1/collection/codex/enable', headers=ORIGIN).status_code == 200
    sessions = tmp_path / 'home/.codex/sessions'
    sessions.rename(tmp_path / 'original-sessions')
    sessions.mkdir()
    (sessions / 'unapproved.jsonl').write_bytes(
        token_record(1, input_tokens=500, output_tokens=500)
    )
    client.app.state.container.collect()
    assert client.get('/api/v1/dashboard').json()['workload_tokens'] == 125
    views = client.get('/api/v1/discovery').json()['providers'][1]['sources']
    assert views[0]['state'] == 'discovered'


def test_collection_enable_requires_origin(client: TestClient) -> None:
    assert client.post('/api/v1/collection/codex/enable').status_code == 403
    assert client.get('/api/v1/dashboard').json()['event_count'] == 0


def test_background_worker_updates_without_a_manual_scan(tmp_path: Path) -> None:
    home = tmp_path / 'home'
    source = home / '.codex/sessions/test.jsonl'
    source.parent.mkdir(parents=True)
    source.write_bytes(token_record(1, input_tokens=100, output_tokens=25))
    settings = TokenHubSettings(
        home_directory=home, data_directory=tmp_path / 'data', scan_interval_seconds=0.05
    )
    app = create_app(settings)
    app.state.container.discovery_context = DiscoveryContext(home, {}, lambda _: None)
    with TestClient(app, base_url='http://127.0.0.1:7432') as client:
        source_id = codex_id(client)
        client.post(f'/api/v1/sources/{source_id}/approve', headers=ORIGIN)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if client.get('/api/v1/dashboard').json()['workload_tokens'] == 125:
                break
            time.sleep(0.01)
        assert client.get('/api/v1/dashboard').json()['workload_tokens'] == 125
    assert app.state.container.collection_thread is None


def test_unchanged_partial_tail_does_not_repeat_import_history(
    client: TestClient, tmp_path: Path
) -> None:
    source_id = codex_id(client)
    client.post(f'/api/v1/sources/{source_id}/approve', headers=ORIGIN)
    source = tmp_path / 'home/.codex/sessions/synthetic.jsonl'
    with source.open('ab') as stream:
        stream.write(b'{"ordinal":2')
    container = client.app.state.container
    container.collect()
    runs = container.services.session.execute(text('select count(*) from import_runs')).scalar()
    container.services.session.rollback()
    container.collect()
    again = container.services.session.execute(text('select count(*) from import_runs')).scalar()
    container.services.session.rollback()
    assert again == runs


def test_disabling_folder_consent_keeps_new_sessions_unapproved(
    client: TestClient, tmp_path: Path
) -> None:
    client.post('/api/v1/collection/codex/enable', headers=ORIGIN)
    disabled = client.post('/api/v1/collection/codex/disable', headers=ORIGIN)
    assert disabled.json()['codex_auto_import'] is False
    (tmp_path / 'home/.codex/sessions/new.jsonl').write_bytes(
        token_record(1, input_tokens=900, output_tokens=100)
    )
    client.app.state.container.collect()
    assert client.get('/api/v1/dashboard').json()['workload_tokens'] == 125


def test_restart_reparses_old_quality_without_duplicating_totals(
    client: TestClient, tmp_path: Path
) -> None:
    source_id = codex_id(client)
    source = tmp_path / 'home/.codex/sessions/synthetic.jsonl'
    source.write_bytes(b'{"type":"session_meta","payload":{}}\n' + source.read_bytes())
    client.post(f'/api/v1/sources/{source_id}/approve', headers=ORIGIN)
    container = client.app.state.container
    container.collect()
    with container.services.session.begin():
        container.services.session.execute(text(
            "update sync_cursors set parser_version='codex-jsonl-v1', source_unsupported_records=1"
        ))
    restarted = create_app(container.settings)
    restarted.state.container.discovery_context = DiscoveryContext(tmp_path / 'home', {}, lambda _: None)
    with TestClient(restarted, base_url='http://127.0.0.1:7432') as second:
        restarted.state.container.collect()
        assert second.get('/api/v1/dashboard').json()['workload_tokens'] == 125
        freshness = second.get('/api/v1/data-quality').json()['source_freshness'][0]
        assert freshness['unsupported_records'] == 0
        assert freshness['state'] == 'healthy'


def test_restart_does_not_add_unchanged_import_history(client: TestClient, tmp_path: Path) -> None:
    source_id = codex_id(client)
    client.post(f'/api/v1/sources/{source_id}/approve', headers=ORIGIN)
    container = client.app.state.container
    container.collect()
    runs = container.services.session.execute(text('select count(*) from import_runs')).scalar()
    container.services.session.rollback()
    restarted = create_app(container.settings)
    restarted.state.container.discovery_context = DiscoveryContext(tmp_path / 'home', {}, lambda _: None)
    with TestClient(restarted, base_url='http://127.0.0.1:7432'):
        restarted.state.container.collect()
        session = restarted.state.container.services.session
        assert session.execute(text('select count(*) from import_runs')).scalar() == runs
        session.rollback()


def test_out_of_range_usage_does_not_break_collection_or_restart(
    client: TestClient, tmp_path: Path
) -> None:
    source = tmp_path / 'home/.codex/sessions/oversized.jsonl'
    source.write_bytes(token_record(1, input_tokens=2**63, output_tokens=0))
    assert client.post('/api/v1/collection/codex/enable', headers=ORIGIN).status_code == 200
    assert client.get('/api/v1/dashboard').json()['workload_tokens'] == 125
    restarted = create_app(client.app.state.container.settings)
    restarted.state.container.discovery_context = DiscoveryContext(tmp_path / 'home', {}, lambda _: None)
    with TestClient(restarted, base_url='http://127.0.0.1:7432') as second:
        restarted.state.container.collect()
        assert second.get('/api/v1/dashboard').json()['workload_tokens'] == 125
        assert any(source['unsupported_records'] == 1
                   for source in second.get('/api/v1/data-quality').json()['source_freshness'])


def test_unexpected_source_failure_does_not_starve_other_sources(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.post('/api/v1/collection/codex/enable', headers=ORIGIN)
    failed_id = codex_id(client)
    source = tmp_path / 'home/.codex/sessions/other.jsonl'
    source.write_bytes(token_record(1, input_tokens=20, output_tokens=5))
    container = client.app.state.container
    original_rescan = container.services.ingestion.rescan

    def rescan(source_id: str, **kwargs: object):
        if source_id == failed_id:
            raise RuntimeError('synthetic unexpected parser failure')
        return original_rescan(source_id, **kwargs)

    with (tmp_path / 'home/.codex/sessions/synthetic.jsonl').open('ab') as stream:
        stream.write(token_record(2, input_tokens=10, output_tokens=5))
    monkeypatch.setattr(container.services.ingestion, 'rescan', rescan)
    container.collect()
    assert client.get('/api/v1/dashboard').json()['workload_tokens'] == 150
    assert client.get('/api/v1/collection').json()['failed_source_count'] == 1
    monkeypatch.setattr(container.services.ingestion, 'rescan', original_rescan)
    container.collect()
    assert client.get('/api/v1/dashboard').json()['workload_tokens'] == 165
    assert client.get('/api/v1/collection').json()['failed_source_count'] == 0


def test_removed_source_is_missing_not_failed_and_keeps_usage(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tokenhub.ingestion.collection as collection_module

    source_id = codex_id(client)
    client.post('/api/v1/collection/codex/enable', headers=ORIGIN)
    container = client.app.state.container
    session_file = tmp_path / 'home/.codex/sessions/synthetic.jsonl'
    assert client.get('/api/v1/dashboard').json()['workload_tokens'] == 125

    session_file.unlink()
    container.collect()
    quality = client.get('/api/v1/data-quality').json()
    states = {item['source_id']: item['state'] for item in quality['source_freshness']}
    assert states[source_id] == 'source_missing'
    assert 'error' not in states.values()
    assert client.get('/api/v1/collection').json()['failed_source_count'] == 0
    assert client.get('/api/v1/dashboard').json()['workload_tokens'] == 125

    opened: list[object] = []
    original = collection_module.open_source_path
    monkeypatch.setattr(
        collection_module, 'open_source_path',
        lambda *args, **kwargs: opened.append(args) or original(*args, **kwargs),
    )
    container.collect()
    assert opened == []
    assert client.get('/api/v1/collection').json()['failed_source_count'] == 0

    session_file.write_bytes(token_record(1, input_tokens=100, output_tokens=25))
    container.collect()
    assert opened
    states = {
        item['source_id']: item['state']
        for item in client.get('/api/v1/data-quality').json()['source_freshness']
    }
    assert states[source_id] == 'healthy'
    assert client.get('/api/v1/dashboard').json()['workload_tokens'] == 125


def test_idle_polling_neither_walks_the_filesystem_nor_bumps_the_version(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tokenhub.discovery.service import DiscoveryService

    client.post('/api/v1/collection/codex/enable', headers=ORIGIN)
    container = client.app.state.container
    container.collect()
    assert client.get('/api/v1/discovery').status_code == 200
    version = client.get('/api/v1/collection').json()['data_version']

    walks: list[int] = []
    original = DiscoveryService.discover
    monkeypatch.setattr(
        DiscoveryService, 'discover', lambda self: walks.append(1) or original(self)
    )
    for _ in range(3):
        assert client.get('/api/v1/discovery').status_code == 200
        assert client.get('/api/v1/collection').json()['data_version'] == version
    assert walks == []

    container.collect()  # a scan that finds nothing new is still a discovery pass
    assert walks == [1]
    assert client.get('/api/v1/collection').json()['data_version'] == version

    with (tmp_path / 'home/.codex/sessions/synthetic.jsonl').open('ab') as stream:
        stream.write(token_record(2, input_tokens=10, output_tokens=5))
    container.collect()
    changed = client.get('/api/v1/collection').json()['data_version']
    assert changed > version
    client.get('/api/v1/discovery')
    assert len(walks) == 2  # reads reuse the collector's cached discovery
    client.get('/api/v1/discovery')
    assert len(walks) == 2


def test_new_session_file_bumps_the_version_so_discovery_refreshes(
    client: TestClient, tmp_path: Path
) -> None:
    container = client.app.state.container
    container.collect()
    before = client.get('/api/v1/collection').json()['data_version']
    (tmp_path / 'home/.codex/sessions/fresh.jsonl').write_bytes(
        token_record(1, input_tokens=4, output_tokens=6)
    )
    container.collect()
    assert client.get('/api/v1/collection').json()['data_version'] > before
    codex = client.get('/api/v1/discovery').json()['providers'][1]
    assert len(codex['sources']) == 2


def test_rediscovering_unchanged_sources_writes_nothing(client: TestClient) -> None:
    from sqlalchemy import event

    container = client.app.state.container
    container.execute("discover")
    writes: list[str] = []

    @event.listens_for(container._engine, 'before_cursor_execute')
    def record(conn, cursor, statement, *args):
        if statement.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')):
            writes.append(statement)

    try:
        results = container.execute("discover")
    finally:
        event.remove(container._engine, 'before_cursor_execute', record)
    assert writes == []
    assert sum(len(result.sources) for result in results) > 0


def test_discovery_walks_each_provider_tree_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tokenhub.connectors.codex.connector as codex_connector

    anchors: list[int] = []
    original = codex_connector.anchor_directory

    def counted(root: Path):
        anchors.append(1)
        return original(root)

    monkeypatch.setattr(codex_connector, 'anchor_directory', counted)
    client.app.state.container.execute("discover")
    assert anchors == [1]


def test_shutdown_interrupts_a_scan_between_sources(client: TestClient, tmp_path: Path) -> None:
    client.post('/api/v1/collection/codex/enable', headers=ORIGIN)
    sessions = tmp_path / 'home/.codex/sessions'
    for number in range(3):
        (sessions / f'extra{number}.jsonl').write_bytes(token_record(1, input_tokens=1, output_tokens=1))
    container = client.app.state.container
    collection = container.services.collection
    scanned: list[str] = []
    original = container.services.ingestion.rescan
    container.services.ingestion.rescan = lambda source_id, **kw: scanned.append(source_id) or original(source_id, **kw)
    collection.run_once(lambda: bool(scanned))
    assert len(scanned) == 1
    container.services.ingestion.rescan = original


def test_retry_recovers_when_source_identity_is_unchanged(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tokenhub.ingestion.collection as collection_module

    source_id = codex_id(client)
    client.post(f'/api/v1/sources/{source_id}/approve', headers=ORIGIN)
    container = client.app.state.container
    container.collect()
    with monkeypatch.context() as patch:
        def unavailable(*args: object, **kwargs: object) -> int:
            raise PermissionError('synthetic temporary permission failure')
        patch.setattr(collection_module, 'open_source_path', unavailable)
        container.collect()
    assert client.get('/api/v1/collection').json()['failed_source_count'] == 1
    container.collect()
    assert client.get('/api/v1/data-quality').json()['source_freshness'][0]['state'] == 'healthy'
    assert client.get('/api/v1/collection').json()['failed_source_count'] == 0


def test_stored_parser_upgrade_does_not_need_rediscovery(client: TestClient, tmp_path: Path) -> None:
    source_id = codex_id(client)
    client.post(f'/api/v1/sources/{source_id}/approve', headers=ORIGIN)
    container = client.app.state.container
    container.collect()
    with container.services.session.begin():
        container.services.session.execute(text(
            "update sources set parser_version='codex-jsonl-v1'"
        ))
        container.services.session.execute(text(
            "update sync_cursors set parser_version='codex-jsonl-v1', source_unsupported_records=1"
        ))
    empty_home = tmp_path / 'empty-home'
    empty_home.mkdir()
    restarted = create_app(container.settings)
    restarted.state.container.discovery_context = DiscoveryContext(empty_home, {}, lambda _: None)
    with TestClient(restarted, base_url='http://127.0.0.1:7432') as second:
        restarted.state.container.collect()
        assert second.get('/api/v1/dashboard').json()['workload_tokens'] == 125
        freshness = second.get('/api/v1/data-quality').json()['source_freshness'][0]
        assert freshness['state'] == 'healthy'
        services = restarted.state.container.services
        assert services.source_repository.get(source_id).parser_version == 'codex-jsonl-v6'
        assert services.usage_repository.current_cursor(source_id).parser_version == 'codex-jsonl-v6'
        assert freshness['unsupported_records'] == 0


def test_parser_upgrade_backfills_task_and_model_without_changing_tokens(
    client: TestClient, tmp_path: Path
) -> None:
    import json

    source = tmp_path / 'home/.codex/sessions/synthetic.jsonl'
    context = json.dumps({'type': 'turn_context', 'payload': {'model': 'recorded-model', 'turn_id': 'turn-one'}})
    row = json.loads(token_record(1, input_tokens=100, output_tokens=25))
    row['payload'].update(session_id='shared-app', thread_id='task-one', turn_id='turn-one')
    source.write_text(context + '\n' + json.dumps(row) + '\n')
    source_id = codex_id(client)
    client.post(f'/api/v1/sources/{source_id}/approve', headers=ORIGIN)
    client.app.state.container.collect()
    container = client.app.state.container
    with container.services.session.begin():
        container.services.session.execute(text("UPDATE usage_events SET model_name=NULL, session_id=NULL, model_attribution='unknown'"))
        container.services.session.execute(text("UPDATE sources SET parser_version='codex-jsonl-v2'"))
        container.services.session.execute(text("UPDATE sync_cursors SET parser_version='codex-jsonl-v2'"))
    restarted = create_app(container.settings)
    empty_home = tmp_path / 'empty-home'
    empty_home.mkdir()
    restarted.state.container.discovery_context = DiscoveryContext(empty_home, {}, lambda _: None)
    with TestClient(restarted, base_url='http://127.0.0.1:7432') as second:
        restarted.state.container.collect()
        result = second.get('/api/v1/usage').json()
        assert result['totals']['workload_tokens'] == 125
        assert result['totals']['event_count'] == 1
        assert result['totals']['session_count'] == 1
        assert result['models'][0]['model_name'] == 'recorded-model'
        assert container.services.source_repository.get(source_id).state == 'healthy'


def test_usage_api_keeps_tasks_separate_with_a_shared_app_session(client: TestClient, tmp_path: Path) -> None:
    import json

    source = tmp_path / 'home/.codex/sessions/synthetic.jsonl'
    rows = []
    for ordinal, thread in [(1, 'task-one'), (2, 'task-two')]:
        row = json.loads(token_record(ordinal, input_tokens=100, output_tokens=25))
        row['payload'].update(session_id='shared-app', thread_id=thread, model='recorded-model')
        rows.append(json.dumps(row))
    source.write_text('\n'.join(rows) + '\n')
    source_id = codex_id(client)
    client.post(f'/api/v1/sources/{source_id}/approve', headers=ORIGIN)
    client.app.state.container.collect()
    result = client.get('/api/v1/usage').json()
    assert result['totals']['session_count'] == 2
    assert result['models'][0]['session_count'] == 2
    assert result['totals']['workload_tokens'] == 250
    assert len(result['sessions']) == 2
    assert 'task-one' not in str(result)


def test_append_without_usage_does_not_invalidate_dashboard(client, tmp_path):
    import json
    client.post('/api/v1/collection/codex/enable', headers=ORIGIN)
    container = client.app.state.container
    container.collect()
    container.collect()
    version = client.get('/api/v1/collection').json()['data_version']
    with (tmp_path / 'home/.codex/sessions/synthetic.jsonl').open('ab') as stream:
        stream.write((json.dumps({'type': 'turn_context', 'payload': {'model': 'synthetic'}}) + '\n').encode())
    container.collect()
    assert client.get('/api/v1/collection').json()['data_version'] == version
