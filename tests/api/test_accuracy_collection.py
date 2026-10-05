"""Accuracy regressions exercise discovery, consent, persistence and API views."""

import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.api.conftest import ORIGIN
from tests.service_support import token_record


def response_record(ordinal: int, response_id: str, input_tokens: int = 100) -> bytes:
    record = json.loads(token_record(ordinal, input_tokens=input_tokens, output_tokens=25))
    record["payload"].update(response_id=response_id, thread_id="original-thread")
    return (json.dumps(record) + "\n").encode()


def test_archived_responses_are_discovered_and_require_their_own_folder_consent(
    client: TestClient, tmp_path: Path,
) -> None:
    client.post('/api/v1/collection/codex/enable', headers=ORIGIN)
    archive = tmp_path / 'home/.codex/archived_sessions'
    archive.mkdir()
    (archive / 'older.jsonl').write_bytes(response_record(1, 'archived-response', 20))
    client.app.state.container.collect()
    codex = next(p for p in client.get('/api/v1/discovery').json()['providers'] if p['provider'] == 'codex')
    assert len(codex['sources']) == 2
    assert client.get('/api/v1/dashboard').json()['workload_tokens'] == 125
    assert client.get('/api/v1/collection').json()['requires_reapproval_connectors'] == ['codex-local']
    assert client.post('/api/v1/collection/codex/enable', headers=ORIGIN).status_code == 200
    assert client.get('/api/v1/dashboard').json()['workload_tokens'] == 170
    assert client.get('/api/v1/collection').json()['requires_reapproval_connectors'] == []
    (archive / 'future.jsonl').write_bytes(response_record(1, 'future-response', 10))
    client.app.state.container.collect()
    assert client.get('/api/v1/dashboard').json()['workload_tokens'] == 205


def test_copied_codex_response_counts_once_across_active_and_archived_sources(
    client: TestClient, tmp_path: Path,
) -> None:
    active = tmp_path / 'home/.codex/sessions/synthetic.jsonl'
    active.write_bytes(response_record(1, 'same-response'))
    active.with_name('fork.jsonl').write_bytes(response_record(99, 'same-response'))
    archive = tmp_path / 'home/.codex/archived_sessions'
    archive.mkdir()
    # Ordinals identify rollout rows, not provider responses.
    (archive / 'copy.jsonl').write_bytes(response_record(99, 'same-response'))
    client.post('/api/v1/collection/codex/enable', headers=ORIGIN)
    for path in ['/api/v1/dashboard', '/api/v1/usage/summary']:
        payload = client.get(path).json()
        totals = payload.get('totals', payload)
        assert totals['workload_tokens'] == 125
        assert totals['event_count'] == 1
    usage = client.get('/api/v1/usage').json()
    assert sum(row['workload_tokens'] for row in usage['sessions']) == 125
    assert client.get('/api/v1/usage/trends').json()['current']['workload_tokens'] == 125
    client.post('/api/v1/rebuild', headers=ORIGIN)
    assert client.get('/api/v1/dashboard').json()['workload_tokens'] == 125


def test_same_response_repeated_with_another_ordinal_is_not_added_again(
    client: TestClient, tmp_path: Path,
) -> None:
    active = tmp_path / 'home/.codex/sessions/synthetic.jsonl'
    active.write_bytes(response_record(1, 'same-response'))
    client.post('/api/v1/collection/codex/enable', headers=ORIGIN)
    with active.open('ab') as stream:
        stream.write(response_record(2, 'same-response'))
    client.app.state.container.collect()
    assert client.get('/api/v1/dashboard').json()['workload_tokens'] == 125


def test_revised_counters_replace_previous_values_and_invalidate_cached_views(
    client: TestClient, tmp_path: Path,
) -> None:
    active = tmp_path / 'home/.codex/sessions/synthetic.jsonl'
    active.write_bytes(response_record(1, 'revised-response'))
    client.post('/api/v1/collection/codex/enable', headers=ORIGIN)
    before = client.get('/api/v1/usage/summary').json()
    active.write_bytes(response_record(1, 'revised-response', 10))
    client.app.state.container.collect()
    after = client.get('/api/v1/usage/summary').json()
    assert after['totals']['workload_tokens'] == 35
    assert after['totals']['event_count'] == 1
    assert after['data_version'] > before['data_version']
    client.app.state.container.collect()
    assert client.get('/api/v1/usage/summary').json()['data_version'] == after['data_version']


def test_reconnect_explicitly_renews_replaced_folder_and_existing_source_approval(
    client: TestClient, tmp_path: Path,
) -> None:
    client.post('/api/v1/collection/codex/enable', headers=ORIGIN)
    sessions = tmp_path / 'home/.codex/sessions'
    sessions.rename(tmp_path / 'original-sessions')
    sessions.mkdir()
    (sessions / 'synthetic.jsonl').write_bytes(token_record(1, input_tokens=500, output_tokens=500))
    client.app.state.container.collect()
    assert client.get('/api/v1/dashboard').json()['workload_tokens'] == 125
    assert client.get('/api/v1/collection').json()['requires_reapproval_connectors'] == ['codex-local']
    assert client.post('/api/v1/collection/codex/enable').status_code == 403
    assert client.get('/api/v1/dashboard').json()['workload_tokens'] == 125
    assert client.post('/api/v1/collection/codex/enable', headers=ORIGIN).status_code == 200
    assert client.get('/api/v1/dashboard').json()['workload_tokens'] == 1000
    assert client.get('/api/v1/collection').json()['requires_reapproval_connectors'] == []


def test_parser_upgrade_replaces_legacy_ordinal_rows_without_inflating_usage(
    client: TestClient, tmp_path: Path,
) -> None:
    active = tmp_path / 'home/.codex/sessions/synthetic.jsonl'
    active.write_bytes(response_record(1, 'upgrade-response'))
    client.post('/api/v1/collection/codex/enable', headers=ORIGIN)
    services = client.app.state.container.services
    with services.session.begin():
        services.session.execute(text("UPDATE usage_events SET record_identity='1', parser_version='codex-jsonl-v5'"))
        services.session.execute(text("UPDATE sync_cursors SET parser_version='codex-jsonl-v5'"))
    client.app.state.container.collect()
    totals = client.get('/api/v1/dashboard').json()
    assert totals['workload_tokens'] == 125
    assert totals['event_count'] == 1
    with services.session.begin():
        identity = services.session.execute(text('SELECT record_identity FROM usage_events')).scalar_one()
    assert identity == 'response:upgrade-response'


def test_corrected_record_date_refreshes_cached_summary_and_trends(
    client: TestClient, tmp_path: Path,
) -> None:
    active = tmp_path / 'home/.codex/sessions/synthetic.jsonl'
    active.write_bytes(response_record(1, 'dated-response'))
    client.post('/api/v1/collection/codex/enable', headers=ORIGIN)
    before = client.get('/api/v1/usage/summary').json()
    client.get('/api/v1/usage/trends')  # cache the old date's buckets too
    record = json.loads(response_record(1, 'dated-response'))
    record['timestamp'] = '2026-09-21T10:00:00Z'
    active.write_text(json.dumps(record) + '\n')
    client.app.state.container.collect()
    after = client.get('/api/v1/usage/summary').json()
    assert after['totals']['first_seen'] == '2026-09-21T10:00:00+00:00'
    assert after['data_version'] > before['data_version']
    assert client.get('/api/v1/usage/trends').json()['buckets'][0]['from'] == '2026-09-21'


def test_parser_upgrade_preserves_legacy_history_absent_from_shortened_file(
    client: TestClient, tmp_path: Path,
) -> None:
    active = tmp_path / 'home/.codex/sessions/synthetic.jsonl'
    first = response_record(1, 'retained-response')
    active.write_bytes(first + response_record(2, 'removed-response'))
    client.post('/api/v1/collection/codex/enable', headers=ORIGIN)
    services = client.app.state.container.services
    with services.session.begin():
        services.session.execute(text("UPDATE usage_events SET record_identity=CASE record_identity "
            "WHEN 'response:retained-response' THEN '1' ELSE '2' END, parser_version='codex-jsonl-v5'"))
        services.session.execute(text("UPDATE sync_cursors SET parser_version='codex-jsonl-v5'"))
    active.write_bytes(first)
    client.app.state.container.collect()
    totals = client.get('/api/v1/dashboard').json()
    assert totals['workload_tokens'] == 250
    assert totals['event_count'] == 2


def test_corrected_response_overrides_stale_copies_and_unchanged_replays(
    client: TestClient, tmp_path: Path,
) -> None:
    active = tmp_path / 'home/.codex/sessions/synthetic.jsonl'
    original = response_record(1, 'copied-correction')
    active.write_bytes(original)
    active.with_name('copy.jsonl').write_bytes(original)
    client.post('/api/v1/collection/codex/enable', headers=ORIGIN)
    corrected = json.loads(response_record(1, 'copied-correction', 10))
    corrected['timestamp'] = '2026-09-21T10:00:00Z'
    active.write_text(json.dumps(corrected) + '\n')
    client.app.state.container.collect()
    for _ in range(2):
        totals = client.get('/api/v1/usage/summary').json()['totals']
        assert totals['workload_tokens'] == 35
        assert totals['first_seen'] == '2026-09-21T10:00:00+00:00'
        client.app.state.container.collect()
    # A later discovery of an old copy is not a new correction.
    active.with_name('later-copy.jsonl').write_bytes(original)
    client.app.state.container.collect()
    assert client.get('/api/v1/dashboard').json()['workload_tokens'] == 35
    client.post('/api/v1/rebuild', headers=ORIGIN)
    totals = client.get('/api/v1/usage/summary').json()['totals']
    assert totals['workload_tokens'] == 35
    assert totals['first_seen'] == '2026-09-21T10:00:00+00:00'


def test_string_ordinals_cannot_impersonate_global_response_identities(
    client: TestClient, tmp_path: Path,
) -> None:
    active = tmp_path / 'home/.codex/sessions/synthetic.jsonl'
    fallback = token_record('response:collision', input_tokens=100, output_tokens=25)
    active.write_bytes(fallback)
    active.with_name('fallback-copy.jsonl').write_bytes(fallback)
    active.with_name('response.jsonl').write_bytes(response_record(1, 'collision'))
    client.post('/api/v1/collection/codex/enable', headers=ORIGIN)
    totals = client.get('/api/v1/dashboard').json()
    assert totals['workload_tokens'] == 375
    assert totals['event_count'] == 3


def test_unchanged_copy_history_does_not_claim_a_new_correction_on_rebuild(
    client: TestClient, tmp_path: Path,
) -> None:
    active = tmp_path / 'home/.codex/sessions/synthetic.jsonl'
    copy = active.with_name('history-copy.jsonl')
    active.write_bytes(response_record(1, 'historical-copy'))
    copy.write_bytes(response_record(1, 'historical-copy', 90) + response_record(2, 'historical-copy'))
    client.post('/api/v1/collection/codex/enable', headers=ORIGIN)
    active.write_bytes(response_record(1, 'historical-copy', 10))
    client.app.state.container.collect()
    copy_id = 'codex-local:' + hashlib.sha256(str(copy).encode()).hexdigest()
    services = client.app.state.container.services
    services.ingestion.rescan(copy_id, rebuild=True)
    assert services.analytics.dashboard().workload_tokens == 35


def test_parser_upgrade_preserves_correction_baseline_against_stale_copies(
    client: TestClient, tmp_path: Path,
) -> None:
    active = tmp_path / 'home/.codex/sessions/synthetic.jsonl'
    active.write_bytes(response_record(1, 'upgrade-correction'))
    active.with_name('old-copy.jsonl').write_bytes(response_record(1, 'upgrade-correction'))
    client.post('/api/v1/collection/codex/enable', headers=ORIGIN)
    services = client.app.state.container.services
    with services.session.begin():
        services.session.execute(text("UPDATE usage_events SET record_identity='1', parser_version='codex-jsonl-v5'"))
        services.session.execute(text("UPDATE sync_cursors SET parser_version='codex-jsonl-v5'"))
    active.write_bytes(response_record(1, 'upgrade-correction', 10))
    client.app.state.container.collect()
    assert client.get('/api/v1/dashboard').json()['workload_tokens'] == 35
