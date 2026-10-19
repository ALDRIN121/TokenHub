"""Benchmark the analytics read path on a synthetic database.

Usage:
    PYTHONPATH=backend .venv/bin/python scripts/bench_analytics.py [EVENTS]

The database is created under the system temporary directory and removed on
the next run; it never touches ``~/.tokenhub``.
"""

from __future__ import annotations

import random
import shutil
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session
from tokenhub.analytics.service import AnalyticsService
from tokenhub.compat import UTC
from tokenhub.database.models import Base
from tokenhub.database.repositories import UsageRepository
from tokenhub.database.session import create_engine_for
from tokenhub.settings import TokenHubSettings

PROVIDERS = {
    "codex": ["gpt-5.1-codex", "gpt-5-codex-mini", "o4-mini"],
    "claude_code": ["claude-sonnet-5", "claude-opus-4.1", "claude-haiku-4.5"],
    "hermes": ["deepseek-v4.1", "qwen3-coder", "kimi-k2"],
    "vscode_copilot": ["gpt-4.1", "claude-sonnet-4"],
    "antigravity": ["gemini-3-pro", "gemini-3-flash"],
}


def insert_events(engine, count: int) -> None:
    random.seed(7)
    rows = []
    start = datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)
    for index in range(count):
        provider = random.choices(list(PROVIDERS), weights=[55, 20, 15, 5, 5])[0]
        input_tokens = random.randint(100, 150_000)
        output_tokens = random.randint(50, 20_000)
        rows.append({
            "connector_id": f"{provider}-local",
            "provider": provider,
            "source_id": "s1",
            "record_identity": f"response:{index}",
            "timestamp": start + timedelta(seconds=random.randint(0, 180 * 86400)),
            "input_total_tokens": input_tokens,
            "output_total_tokens": output_tokens,
            "cache_read_tokens": random.randint(0, input_tokens // 2),
            "cache_write_tokens": random.randint(0, input_tokens // 8),
            "reasoning_tokens": random.randint(0, output_tokens // 3),
            "measurement_type": "delta",
            "quality": random.choices(["exact", "estimated"], weights=[97, 3])[0],
            "parser_version": "v1",
            "model_name": random.choice(PROVIDERS[provider]),
            "session_id": f"ses-{index % 2000}",
            "model_attribution": "reported",
        })
    columns = list(rows[0])
    statement = text(
        f"INSERT INTO usage_events ({','.join(columns)}) "
        f"VALUES ({','.join(':' + column for column in columns)})"
    )
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO sources (source_id, connector_id, provider, display_name,"
            " source_type, path_fingerprint, state, evidence_codes, scan_supported)"
            " VALUES ('s1','codex-local','codex','Codex','jsonl','fp','approved','',1)"
        ))
        for start_index in range(0, len(rows), 5000):
            connection.execute(statement, rows[start_index:start_index + 5000])


def bench(label: str, call, repeats: int = 3) -> None:
    timings = []
    for _ in range(repeats):
        started = time.perf_counter()
        call()
        timings.append((time.perf_counter() - started) * 1000)
    print(f"{label:24s} median={statistics.median(timings):8.1f}ms")


def main() -> None:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 300_000
    root = Path(tempfile.gettempdir()) / "tokenhub-analytics-bench"
    shutil.rmtree(root, ignore_errors=True)
    engine = create_engine_for(TokenHubSettings(data_directory=root))
    Base.metadata.create_all(engine)
    insert_events(engine, count)

    session = Session(engine)
    repository = UsageRepository(session)
    started = time.perf_counter()
    repository.rebuild_canonical()
    print(f"inserted {count:,} events; projection rebuild "
          f"{time.perf_counter() - started:.2f}s")
    service = AnalyticsService(repository)

    bench("dashboard", service.dashboard)
    bench("usage summary", lambda: service.usage_breakdown(summary_only=True))
    bench("usage full", service.usage_breakdown)
    bench("models page", lambda: service.usage_page("models", limit=25))
    bench("sessions page", lambda: service.usage_page("sessions", limit=25))
    bench("trends", lambda: service.usage_trends(
        granularity="day", dimension="providers", limit=25))
    print(f"database: {(root / 'tokenhub.sqlite3').stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
