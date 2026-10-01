"""Reproducible, isolated performance checks; never reads installed provider data.

Run: .venv/bin/python scripts/benchmark_performance.py --events 1000 10000 50000
Use --provider all to exercise every supported parser, or one provider name.
"""

from __future__ import annotations

import argparse
import json
import platform
import sqlite3
import statistics
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

import psutil
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session
from tokenhub.analytics.service import AnalyticsService
from tokenhub.app import create_app
from tokenhub.connectors.protocol import DiscoveryContext
from tokenhub.connectors.registry import ConnectorRegistry
from tokenhub.database.migrations import run_migrations
from tokenhub.database.repositories import SourceRepository, UsageRepository
from tokenhub.database.session import create_engine_for
from tokenhub.discovery.service import DiscoveryService
from tokenhub.ingestion.collection import CollectionService
from tokenhub.ingestion.service import IngestionService
from tokenhub.settings import TokenHubSettings

_PROVIDERS = ('codex', 'claude_code', 'hermes', 'vscode_copilot', 'antigravity')


def _varint(value: int) -> bytes:
    parts = []
    while value > 127:
        parts.append((value & 127) | 128)
        value >>= 7
    return bytes([*parts, value])


def _number(field: int, value: int) -> bytes:
    return _varint(field << 3) + _varint(value)


def _bytes(field: int, value: bytes) -> bytes:
    return _varint((field << 3) | 2) + _varint(len(value)) + value


def create_workload(root: Path, count: int, provider: str = 'codex') -> DiscoveryContext:
    """Fixed counters, timestamps, and identifiers in generated files only."""
    home = root / 'home'
    home.mkdir(parents=True)
    groups: dict[str, list[int]] = {}
    for index in range(count):
        name = _PROVIDERS[index % len(_PROVIDERS)] if provider == 'all' else provider
        groups.setdefault(name, []).append(index)
    for name, indices in groups.items():
        if name == 'hermes':
            path = home / '.hermes/state.db'
            path.parent.mkdir(parents=True)
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY, started_at REAL, ended_at REAL, model TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER)')
                db.executemany('INSERT INTO sessions VALUES (?, ?, ?, ?, 100, 25, 70, 10, 8)',
                    [(f'hermes-{index}', 1790668800, 1790668800 + index, f'model-{index % 16}') for index in indices])
            continue
        for offset in range(0, len(indices), 100):
            batch = indices[offset:offset + 100]
            if name == 'codex':
                path = home / f'.codex/sessions/session-{offset}.jsonl'
                records = [{'ordinal': index, 'timestamp': '2026-09-29T08:00:00Z', 'type': 'token_usage_record',
                    'payload': {'model': f'model-{index % 16}', 'usage': {'input_tokens': 100, 'output_tokens': 25}}} for index in batch]
            elif name == 'claude_code':
                path = home / f'.claude/projects/benchmark/session-{offset}.jsonl'
                records = [{'type': 'assistant', 'timestamp': '2026-09-29T08:00:00Z', 'sessionId': f'claude-{offset}',
                    'message': {'id': f'message-{index}', 'model': f'model-{index % 16}',
                    'usage': {'input_tokens': 100, 'output_tokens': 25, 'cache_read_input_tokens': 70, 'cache_creation_input_tokens': 10}}} for index in batch]
            elif name == 'vscode_copilot':
                path = root / f'vscode-data/User/workspaceStorage/{offset:032x}/chatSessions/11111111-2222-4333-8444-555555555555.jsonl'
                requests = [{'requestId': f'request-{index}', 'timestamp': 1790668800000,
                    'modelId': f'copilot/model-{index % 16}', 'result': {'metadata': {'promptTokens': 100, 'outputTokens': 25}}} for index in batch]
                records = [{'kind': 0, 'v': {'version': 3, 'sessionId': f'copilot-{offset}', 'requests': requests}}]
            else:
                path = home / f'.gemini/antigravity/conversations/conversation-{offset}.db'
                path.parent.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(path) as db:
                    db.execute('CREATE TABLE gen_metadata(idx INTEGER PRIMARY KEY, data BLOB)')
                    db.execute('CREATE TABLE steps(idx INTEGER PRIMARY KEY, step_type INTEGER, metadata BLOB)')
                    for index in batch:
                        response = f'response-{index}'.encode()
                        usage = b''.join(_number(field, value) for field, value in ((1, 10), (2, 100), (3, 35), (5, 40), (9, 25), (10, 10))) + _bytes(11, response)
                        db.execute('INSERT INTO gen_metadata VALUES (?, ?)', (index, _bytes(1, _bytes(4, usage) + _bytes(19, f'model-{index % 16}'.encode()))))
                        db.execute('INSERT INTO steps VALUES (?, 15, ?)', (index, _bytes(1, _number(1, 1790668800 + index)) + _bytes(9, _bytes(11, response))))
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(''.join(json.dumps(record, separators=(',', ':')) + '\n' for record in records))
    return DiscoveryContext(home, {'VSCODE_USER_DATA_DIR': str(root / 'vscode-data')}, lambda _: None)


def benchmark(count: int, provider: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix='tokenhub-benchmark-') as temporary:
        root = Path(temporary).resolve()
        context = create_workload(root, count, provider)
        settings = TokenHubSettings(home_directory=context.home, data_directory=root / 'data', scan_interval_seconds=3600)
        run_migrations(settings)
        engine = create_engine_for(settings)
        statements: Counter[str] = Counter()
        @event.listens_for(engine, 'before_cursor_execute')
        def sql(_connection, _cursor, statement, *_args):
            statements[statement.split()[0].upper()] += 1
        phases = {'parse': 0., 'persistence': 0.}
        with Session(engine) as session:
            sources, usage = SourceRepository(session), UsageRepository(session)
            registry = ConnectorRegistry.default()
            def instrument(original, phase):
                def measured(*args, **kwargs):
                    before = time.perf_counter()
                    try:
                        return original(*args, **kwargs)
                    finally:
                        phases[phase] += time.perf_counter() - before
                return measured
            for connector in registry.connectors:
                connector.scan = instrument(connector.scan, 'parse')
            usage.persist_scan = instrument(usage.persist_scan, 'persistence')
            discovery = DiscoveryService(registry, sources, context)
            ingestion = IngestionService(sources, usage, registry)
            collection = CollectionService(sources, usage, discovery, ingestion)
            before = time.perf_counter()
            found = [source for result in discovery.discover() for source in result.sources if source.scan_supported]
            for source in found:
                ingestion.approve(source.source_id)
            discovery_seconds = time.perf_counter() - before
            statements.clear()
            before = time.perf_counter()
            imported = sum(ingestion.rescan(source.source_id).inserted_events for source in found)
            import_seconds = time.perf_counter() - before
            import_sql = dict(statements)
            import_phases = dict(phases)
            analytics = AnalyticsService(usage)
            timings = []
            for _ in range(3):
                before = time.perf_counter()
                analytics.usage_breakdown()
                timings.append(time.perf_counter() - before)
            collection.run_once()  # Warm SQLite file signatures as the first collection does.
            statements.clear()
            before = time.perf_counter()
            collection.run_once()
            idle_seconds = time.perf_counter() - before
            idle_sql = dict(statements)
        engine.dispose()
        app = create_app(settings)
        app.state.container.discovery_context = context
        with TestClient(app, base_url='http://127.0.0.1:7432') as client:
            app.state.container.collect()
            before = time.perf_counter()
            summary = client.get('/api/v1/usage/summary')
            first_summary = time.perf_counter() - before
            before = time.perf_counter()
            page = client.get('/api/v1/usage/sessions')
            first_page = time.perf_counter() - before
            cached = []
            for _ in range(5):
                before = time.perf_counter()
                client.get('/api/v1/usage/summary')
                client.get('/api/v1/usage/sessions')
                cached.append(time.perf_counter() - before)
            assert summary.status_code == page.status_code == 200
            assert summary.json()['totals']['event_count'] == imported == count, (summary.json(), imported, count, len(found))
            assert len(page.json()['items']) <= 25
            trend_query = {'from': '2026-09-01T00:00:00Z', 'to': '2026-10-01T00:00:00Z', 'dimension': 'models'}
            before = time.perf_counter()
            trends = client.get('/api/v1/usage/trends', params=trend_query)
            first_trends = time.perf_counter() - before
            assert trends.status_code == 200
            trend_data = trends.json()
            assert trend_data['current']['event_count'] == imported
            assert trend_data['current']['workload_tokens'] == summary.json()['totals']['workload_tokens']
            assert sum(bucket['current']['workload_tokens'] or 0 for bucket in trend_data['buckets']) == trend_data['current']['workload_tokens']
            assert len(trend_data['breakdown']['items']) <= 25
            trend_cached = []
            for _ in range(5):
                before = time.perf_counter()
                assert client.get('/api/v1/usage/trends', params=trend_query).status_code == 200
                trend_cached.append(time.perf_counter() - before)
            return {'events': count, 'provider': provider, 'source_files': len(found),
                'discovery_and_approval_seconds': discovery_seconds, 'import_seconds': import_seconds,
                **{f'{key}_seconds': value for key, value in import_phases.items()}, 'import_sql': import_sql,
                'legacy_analytics_median_seconds': statistics.median(timings),
                'idle_seconds': idle_seconds, 'idle_sql': idle_sql,
                'first_summary_seconds': first_summary, 'first_session_page_seconds': first_page,
                'cached_summary_and_page_median_seconds': statistics.median(cached),
                'summary_and_page_bytes': len(summary.content) + len(page.content),
                'first_trends_seconds': first_trends,
                'cached_trends_median_seconds': statistics.median(trend_cached),
                'trends_bytes': len(trends.content), 'trend_buckets': len(trend_data['buckets']),
                'trend_page_rows': len(trend_data['breakdown']['items']), 'trend_model_groups': trend_data['breakdown']['total'],
                'rss_mb': psutil.Process().memory_info().rss / 1024**2}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--events', type=int, nargs='+', default=[1000, 10000, 50000])
    parser.add_argument('--provider', choices=['all', *_PROVIDERS], default='codex')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if any(count <= 0 for count in args.events):
        parser.error("event counts must be positive")
    report = {'platform': platform.platform(), 'python': platform.python_version(),
              'workloads': [benchmark(count, args.provider) for count in args.events]}
    payload = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(payload + '\n')
    print(payload)


if __name__ == '__main__':
    main()
