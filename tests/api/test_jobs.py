"""Background imports retain the real parser, database and HTTP trust gates."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from tokenhub.connectors.codex.connector import CodexConnector

from tests.api.conftest import ORIGIN, codex_id


def finished_job(client, job_id):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get(f'/api/v1/jobs/{job_id}')
        assert response.status_code == 200
        job = response.json()
        if job['state'] not in {'queued', 'running'}:
            return job
        time.sleep(.01)
    raise AssertionError('job did not finish')


def test_background_import_returns_before_parser_finishes_and_keeps_reads_live(client, monkeypatch):
    codex_id(client)
    entered = threading.Event()
    release = threading.Event()
    original = CodexConnector.scan
    def slow(self, source, cursor):
        entered.set()
        assert release.wait(3)
        return original(self, source, cursor)
    monkeypatch.setattr(CodexConnector, 'scan', slow)
    with ThreadPoolExecutor(max_workers=3) as pool:
        response = pool.submit(client.post, '/api/v1/collection/codex/enable?background=true', headers=ORIGIN)
        try:
            accepted = response.result(timeout=.5)
            assert entered.wait(2)
            assert accepted.status_code == 202
            status = pool.submit(client.get, '/api/v1/collection').result(timeout=.5)
            assert status.json()['active_job']['state'] == 'running'
            dashboard = pool.submit(client.get, '/api/v1/dashboard').result(timeout=.5)
            assert dashboard.status_code == 200
            assert dashboard.json()['event_count'] == 0
            repeated = client.post('/api/v1/collection/codex/enable?background=true', headers=ORIGIN)
            assert repeated.json()['job_id'] == accepted.json()['job_id']
        finally:
            release.set()
        accepted = response.result()
    job = finished_job(client, accepted.json()['job_id'])
    assert job['state'] == 'completed'
    assert job['files_completed'] == job['files_total'] == 1
    assert job['inserted_events'] == 1
    assert client.get('/api/v1/dashboard').json()['workload_tokens'] == 125


def test_background_jobs_require_origin_and_expose_only_safe_outcomes(client, tmp_path):
    assert client.post('/api/v1/collection/codex/enable?background=true').status_code == 403
    accepted = client.post('/api/v1/collection/codex/enable?background=true', headers=ORIGIN)
    assert accepted.status_code == 202
    job = finished_job(client, accepted.json()['job_id'])
    assert job['state'] == 'completed'
    assert str(tmp_path) not in str(job)
    assert 'canonical_path' not in str(job)
    assert 'payload' not in str(job)
    assert client.get('/api/v1/jobs/unknown').status_code == 404


def test_background_rescan_reports_safe_failure_without_losing_totals(client, tmp_path):
    source_id = codex_id(client)
    client.post(f'/api/v1/sources/{source_id}/approve', headers=ORIGIN)
    client.post(f'/api/v1/sources/{source_id}/rescan', headers=ORIGIN)
    (tmp_path/'home/.codex/sessions/synthetic.jsonl').unlink()
    response = client.post(f'/api/v1/sources/{source_id}/rescan?background=true', headers=ORIGIN)
    assert response.status_code == 202
    job = finished_job(client, response.json()['job_id'])
    assert job['state'] == 'failed'
    assert job['error'] == 'Source is unavailable'
    assert client.get('/api/v1/dashboard').json()['workload_tokens'] == 125


def test_restart_serves_status_and_dashboard_while_initial_import_is_reading(client, api_app, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from tokenhub.app import create_app
    from tokenhub.connectors.protocol import DiscoveryContext

    from tests.service_support import token_record
    client.post('/api/v1/collection/codex/enable', headers=ORIGIN)
    source = tmp_path / 'home/.codex/sessions/synthetic.jsonl'
    with source.open('ab') as stream:
        stream.write(token_record(2, input_tokens=20, output_tokens=5))
    entered, release = threading.Event(), threading.Event()
    original = CodexConnector.scan
    def held(self, source, cursor):
        entered.set()
        assert release.wait(3)
        return original(self, source, cursor)
    monkeypatch.setattr(CodexConnector, 'scan', held)
    restarted = create_app(api_app.state.container.settings)
    restarted.state.container.discovery_context = DiscoveryContext(tmp_path / 'home', {}, lambda _: None)
    try:
        with TestClient(restarted, base_url='http://127.0.0.1:7432') as second:
            assert entered.wait(2)
            with ThreadPoolExecutor() as pool:
                assert pool.submit(second.get, '/api/v1/status').result(.5).status_code == 200
                assert pool.submit(second.get, '/api/v1/dashboard').result(.5).json()['workload_tokens'] == 125
            assert second.get('/api/v1/collection').json()['active_job']['state'] == 'running'
            release.set()
            restarted.state.container.collect()
            assert second.get('/api/v1/dashboard').json()['workload_tokens'] == 150
    finally:
        release.set()


def test_failed_history_write_does_not_strand_an_import(client, monkeypatch):
    import tokenhub.api.container as module
    def disk_full(*args, **kwargs):
        raise OSError('synthetic disk full')
    monkeypatch.setattr(module, 'save_history', disk_full)
    response = client.post('/api/v1/collection/codex/enable?background=true', headers=ORIGIN)
    assert response.status_code == 202
    assert finished_job(client, response.json()['job_id'])['state'] == 'completed'
    assert client.get('/api/v1/dashboard').json()['workload_tokens'] == 125
    assert client.post('/api/v1/collection/refresh', headers=ORIGIN).status_code == 200


def test_restart_marks_unfinished_history_as_interrupted(client, api_app):
    from fastapi.testclient import TestClient
    from tokenhub.app import create_app
    from tokenhub.ingestion.jobs import CollectionJob, save_history
    job = CollectionJob('rebuild')
    save_history(api_app.state.container.settings.data_directory, [job.snapshot()])
    restarted = create_app(api_app.state.container.settings)
    with TestClient(restarted, base_url='http://127.0.0.1:7432') as second:
        recovered = second.get(f'/api/v1/jobs/{job.job_id}').json()
        assert recovered['state'] == 'interrupted'
        assert 'refresh data' in recovered['error']


def test_reads_and_job_acceptance_stay_live_during_uncommitted_database_batches(client, api_app, tmp_path, monkeypatch):
    from tests.service_support import token_record
    source_id = codex_id(client)
    client.post('/api/v1/collection/codex/enable', headers=ORIGIN)
    with (tmp_path / 'home/.codex/sessions/synthetic.jsonl').open('ab') as stream:
        for ordinal in range(2, 1002):
            stream.write(token_record(ordinal, input_tokens=100, output_tokens=25))
    entered, release = threading.Event(), threading.Event()
    container = api_app.state.container
    original = container._report
    def held(job, **values):
        original(job, **values)
        if values.get('stage') == 'saving' and values.get('records_saved') == 500:
            entered.set()
            assert release.wait(3)
    monkeypatch.setattr(container, '_report', held)
    response = client.post(f'/api/v1/sources/{source_id}/rescan?background=true', headers=ORIGIN)
    try:
        assert entered.wait(2)
        with ThreadPoolExecutor() as pool:
            assert pool.submit(client.get, '/api/v1/dashboard').result(.5).json()['workload_tokens'] == 125
            status = pool.submit(client.get, '/api/v1/collection').result(.5).json()
            assert status['active_job']['records_saved'] == 500
            queued = pool.submit(client.post, '/api/v1/collection/refresh?background=true', headers=ORIGIN).result(.5)
            assert queued.status_code == 202
    finally:
        release.set()
    assert finished_job(client, response.json()['job_id'])['state'] == 'completed'
    assert client.get('/api/v1/dashboard').json()['workload_tokens'] == 125125
