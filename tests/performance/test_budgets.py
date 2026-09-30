"""Deterministic work/payload budgets, without machine-specific timing limits."""

from fastapi.testclient import TestClient
from sqlalchemy import event
from tokenhub.app import create_app
from tokenhub.settings import TokenHubSettings

from scripts.benchmark_performance import create_workload
from tests.api.conftest import ORIGIN


def test_large_history_keeps_pages_and_idle_queries_bounded(tmp_path):
    context = create_workload(tmp_path, 10000)
    app = create_app(TokenHubSettings(home_directory=context.home, data_directory=tmp_path / 'data', scan_interval_seconds=3600))
    app.state.container.discovery_context = context
    with TestClient(app, base_url='http://127.0.0.1:7432') as client:
        app.state.container.collect()
        assert client.post('/api/v1/collection/codex/enable', headers=ORIGIN).status_code == 200
        summary = client.get('/api/v1/usage/summary')
        page = client.get('/api/v1/usage/sessions')
        assert summary.json()['totals']['event_count'] == 10000
        assert page.json()['total'] == 100
        assert len(page.json()['items']) == 25
        assert len(summary.content) + len(page.content) < 20000
        selects = []
        engine = app.state.container.services.session.get_bind()
        def observe(_connection, _cursor, statement, *_args):
            if statement.lstrip().upper().startswith('SELECT'):
                selects.append(statement)
        event.listen(engine, 'before_cursor_execute', observe)
        try:
            for _ in range(3):
                client.get('/api/v1/collection')
                client.get('/api/v1/usage/summary')
                client.get('/api/v1/usage/sessions')
            assert selects == []  # Progress and unchanged pages have no database work.
            app.state.container.collect()
            assert len(selects) < 15  # Cursors are fetched together, regardless of file count.
        finally:
            event.remove(engine, 'before_cursor_execute', observe)
