# How PowerSync, PocketBase and DBOS save query work

**Date:** 2026-10-19
**Scope:** shipped-artifact analysis of PocketBase 0.40.5, PowerSync
`@powersync/service-core` 1.27.0 and DBOS SDK 5.3.0, plus the TokenHub changes
that apply the same techniques.
**Benchmark:** 300,000 synthetic usage events, 180 days, 5 providers, 13 models,
2,000 sessions (Apple-silicon macOS, SQLite 3.47.0).

## Evidence

REA was used for artifact identity and inventory; source was read from the
same shipped packages (PowerSync ships `src/`, PocketBase is tagged
`v0.40.5`, DBOS ships compiled `dist/`).

| Target | Version | Artifact SHA-256 | REA evidence |
| --- | --- | --- | --- |
| PocketBase (`darwin_arm64`) | 0.40.5, Go 1.27.2, modernc.org/sqlite 1.60.1 | `420d4c192368646c2209ba67c6b741de4a47b2550576dd6676228f6d5c97374c` | `ev_b13b4ee0a172c2a9bf08bfacda4f5983b7cdbd4c130a1a05c832561ed7e57b2a` |
| PowerSync service-core (npm tree) | 1.27.0 | `dce4487e579a3c237637684db075906c2e2284354ed4e93ceb6dc135eecd97aa` | `ev_cfcc0ea597533ceaa107e6ca1aaecee12fb6376fcfe5ede3d6a1105b2c797e0e` |
| DBOS SDK (npm tree) | 5.3.0 | `46564480db1e642ae202944e949a08e0597990373a8763132ab285772bcc2342` | `ev_c8dfe1832c192607f376862c5a85f7906a251a20a896ef247a4cfff4ff73b4f4` |

These are static observations. Nothing here claims runtime execution, and the
JS analyzers explicitly leave dynamic property resolution unknown.

## 1. PocketBase — configure the engine once, maintain it on a schedule

PocketBase's entire SQLite policy is one DSN string in
`core/db_connect.go:14`:

```
?_pragma=busy_timeout(10000)&_pragma=journal_mode(WAL)
&_pragma=journal_size_limit(200000000)&_pragma=synchronous(NORMAL)
&_pragma=foreign_keys(ON)&_pragma=temp_store(MEMORY)
&_pragma=cache_size(-32000)&_defensive=1
```

The comment above it records an ordering constraint that is easy to miss:
`busy_timeout` must be applied **first**, before WAL negotiation, or a second
connection can fail with `SQLITE_BUSY` before the timeout exists.

A nightly cron (`__pbDBOptimize__`, `core/base.go:1450`) runs:

```sql
PRAGMA wal_checkpoint(TRUNCATE);
PRAGMA optimize;
```

`PRAGMA optimize` is also run immediately after a record-table sync
(`core/collection_record_table_sync.go:147`), because a schema change
invalidates the planner's assumptions.

Two more choices matter:

- The collection schema is stored in the app store
  (`StoreKeyCachedCollections`, `core/collection_query.go:16`) and reloaded only
  on a collection change — request paths do not re-read schema tables.
- Indexes are created for the exact access shapes the product queries, e.g.
  `idx_logs_created_hour` on `strftime('%Y-%m-%d %H:00:00', created)` and
  `idx_logs_level` / `idx_logs_message`.

**Lesson:** engine pragmas and planner statistics are part of the schema, not
deployment trivia.

## 2. PowerSync — cache the expensive answer, and deduplicate the work

`src/storage/ChecksumCache.ts` is an LRU of `(checkpoint, bucket)` checksums:

- 100,000 entries (~5 MB), 1-hour TTL, `ttlResolution: 1_000`.
- `fetchMethod` **deduplicates in-flight requests**: concurrent callers for the
  same key await one upstream fetch.
- Per bucket it keeps an `OrderedSet` of cached checkpoints, so a new checkpoint
  can be computed incrementally from the last cached one instead of recomputing
  the bucket.
- Eviction is O(1) and keeps the checkpoint index consistent
  (`dispose` + `noDisposeOnSet`).

`src/util/write-checkpoint-batcher.ts` solves the write-side version of the same
problem: concurrent HTTP requests are queued and dispatched on the next timer
turn so they share one source-head read and one storage write batch, with at
most 3 batches in flight. The comments are explicit that the goal is to bound
database connections, not to maximise batch size.

Its defaults are all explicit backpressure:
`max_pool_size 8`, `max_concurrent_connections 200`,
`max_data_fetch_concurrency 10`, `max_buckets_per_connection 1000`,
`max_parameter_query_results 1000`, `bucket_count_cache_ttl_minutes 60`.

**Lesson:** the cheapest query is one that a previous request already
answered — and a cache needs single-flight, a TTL, and a bounded size, not just
a dictionary.

## 3. DBOS — make the index match the predicate, and coalesce wakeups

DBOS's Postgres schema uses **partial indexes** for every queue state:

```sql
CREATE INDEX idx_workflow_status_in_flight
  ON workflow_status (queue_name, status, priority, created_at)
  WHERE status IN ('ENQUEUED', 'PENDING');

CREATE UNIQUE INDEX uq_workflow_status_dedup_id
  ON workflow_status (queue_name, deduplication_id)
  WHERE deduplication_id IS NOT NULL;
```

It also uses `INCLUDE` covering indexes for the dequeue projection. A dequeue is
`FOR UPDATE SKIP LOCKED`, or `FOR UPDATE NOWAIT` when a shared budget is in
play; the candidate set is fixed before locking so `SKIP LOCKED` cannot slide
past a locked head and admit items out of order.

Write-path work is deliberately moved off the hot path:

- `LISTEN/NOTIFY` payloads are coalesced (default 10 ms) and flushed as one
  transaction per channel with `SELECT pg_notify($1, p) FROM unnest($2::text[])`.
- Workflow state is written with `ON CONFLICT` upserts (`DO NOTHING` for
  idempotent message insertion, `DO UPDATE` for output/error), so a retry
  cannot duplicate a row.
- Observability queries carry `statement_timeout` (30 s) and idle transactions
  `idle_in_transaction_session_timeout` (60 s), so an expensive scan cannot pin
  `xmin` and stall vacuum.
- Bulk renames use range predicates on the key rather than `LIMIT`/`OFFSET`;
  the code comments why: `LIMIT` repages rows already moved, and an `IN` list
  plans as a whole-table hash join.

**Lesson:** index the predicate the query actually uses, make retries
idempotent, and keep the expensive work out of the request that triggers it.

## 4. Technique map

| Technique | PowerSync | PocketBase | DBOS | TokenHub |
| --- | --- | --- | --- | --- |
| Engine pragmas (WAL, NORMAL, busy timeout, cache) | driver-level | yes | n/a (Postgres) | **added** |
| Periodic `PRAGMA optimize` / WAL checkpoint | compact action | nightly cron | autovacuum settings | **added** |
| Partial / covering indexes | bucket keys | log/index shapes | queue states | **added** |
| Materialized derived data | bucket checksums | cached schema | memoized operation outputs | **added** (`usage_events_canonical`) |
| Incremental refresh | per-bucket checkpoint sets | schema reload | per-workflow step rows | **added** (per touched identity) |
| Single-flight cache | LRU `fetchMethod` | — | notification coalescing | **added** |
| Conditional GET / revalidation | checkpoint polling | — | — | **added** (ETag/304) |
| Batched idempotent writes | checkpoint batcher | upserts | `ON CONFLICT` | already present (500-row chunks) |

## 5. What TokenHub had

Before this change TokenHub already had WAL, a 32-entry/16 MB response cache
keyed by `data_version`, 500-row batched upserts, and a byte-offset sync cursor.
Two structural costs remained:

1. **Every analytics read recomputed the canonical set.** `_canonical_deltas`
   ran a `UNION ALL` of the non-cross-file rows and a
   `row_number() OVER (PARTITION BY connector_id, record_identity ...)` over the
   cross-file rows. At 300k events that window was ~1.4 s before any
   aggregation, and eight endpoints paid it independently.
2. **The cache had no single-flight.** Two dashboard polls arriving together
   both ran the full aggregate; only the last result was stored.

## 6. Changes applied

- `backend/tokenhub/database/session.py`
  - PocketBase pragma set, with `busy_timeout` first, plus `mmap_size=256 MB`.
  - `optimize_database()` runs `PRAGMA optimize` and
    `wal_checkpoint(TRUNCATE)` outside the snapshot transaction.
- `backend/tokenhub/database/models.py` + migration
  `0008_canonical_usage.py`
  - `usage_events_canonical`: same shape as `usage_events`, no uniqueness
    constraint, indexed by time, `(provider, model_name, timestamp)` and
    `(connector_id, session_id)`.
  - Partial index `ix_usage_events_delta_identity` on
    `(connector_id, record_identity) WHERE measurement_type = 'delta'`.
- `backend/tokenhub/database/repositories.py`
  - `_canonical_select()` is now the single ranking rule.
  - `_refresh_canonical()` rebuilds only the `(connector_id, record_identity)`
    groups an ingest touched; `_rebuild_canonical()` is the full path.
  - `persist_scan()` refreshes inside the same transaction, so readers never
    see a projection that disagrees with the raw rows.
  - Readers (`observed_events`, `dashboard_totals`, `quality_page`,
    `AnalyticsService._events`) read the projection directly.
  - Dashboard totals and quality counts now share one scan.
- `backend/tokenhub/api/container.py`
  - Startup backfill (`ensure_canonical`) before the app reports ready.
  - Single-flight response cache: one build per cache key, waiters reuse it.
  - `optimize_database()` after an ingest that inserted events or rebuilt.
- `backend/tokenhub/app.py`
  - ETag + `Cache-Control: no-cache` on `GET /api/v1/*` JSON; `If-None-Match`
    returns `304` with no body.

**Invariant:** the projection is derived data. It is rebuilt from
`UsageEventRecord` in the ingest transaction, and the parity test
(`tests/database/test_canonical_projection.py`) asserts an incremental refresh
equals a full rebuild, including cross-source Claude dedupe and `replace_events`
pruning.

## 7. Measurements

Same 300k-event database, median of 3–5 runs (`scripts/bench_analytics.py`).

| Endpoint | Before | After | Speed-up |
| --- | ---: | ---: | ---: |
| `/api/v1/dashboard` | 2,738 ms | **140 ms** | 19.6× |
| `/api/v1/usage/summary` | 5,966 ms | **1,200 ms** | 5.0× |
| `/api/v1/usage` (full) | 10,153 ms | **3,508 ms** | 2.9× |
| `/api/v1/usage/models` | 3,880 ms | **972 ms** | 4.0× |
| `/api/v1/usage/sessions` | 7,444 ms | **3,105 ms** | 2.4× |
| `/api/v1/usage/trends` | 12,600 ms | **4,003 ms** | 3.1× |

The projection is not free: the same 300k-event database grows from 108 MB to
189 MB (projection rows plus its three indexes). That is the deliberate
trade — disk is cheap locally, repeated window functions are not.

Write-path cost of maintaining the projection (21k-event database):

| Operation | Time |
| --- | ---: |
| First 20k ingest (4 upsert chunks + scoped projection) | 0.88 s |
| Next 1k ingest (projection scoped to 1k identities) | 0.05 s |
| Full 21k projection rebuild | 0.14 s |
| Full 300k projection rebuild | 2.6 s |

The incremental path is why routine scans stay cheap: a source that appends
500 records only re-ranks those 500 identities.

## 8. What is still slow, and the next step

The remaining multi-second endpoints are not scanning the wrong table; they do
several passes and call Python per row:

- `tokenhub_session_key` (SHA-256) is a Python UDF called once per row for
  session grouping.
- `tokenhub_trend_bucket` parses a timestamp and constructs `ZoneInfo` per row.
- `_summary` computes `COUNT(DISTINCT ...)` for sessions and models on every
  grouping (totals, providers, models, sessions), so a full breakdown reads the
  projection four times.

The next structural step is a rollup table, in the shape PowerSync uses for
bucket checksums and DBOS uses for operation outputs:

- `usage_rollup_hourly(hour_utc, provider, model_key, counters, event_count,
  incomplete_count)` — trends only groups by provider/model, so hour granularity
  is enough to re-bucket into any request time zone.
- `usage_rollup_daily(day_utc, provider, model_key, session_key, connector_id,
  source_id, quality, counters, event_count, first_seen, last_seen)` — dashboard
  and explorer pages.
- Rebuild the affected hours/days inside the ingest transaction, exactly as the
  canonical projection is refreshed now; keep a parity test against the
  canonical table before switching reads.

That should collapse the 1–4 s endpoints to low tens of milliseconds without
changing any response shape. It was deliberately not bundled into this change:
it needs its own parity work for `session_count`, `model_count`, and local-day
boundaries.

## 9. Reproduce

```bash
cd ~/Developer/TokenHub
PYTHONPATH=backend .venv/bin/python scripts/bench_analytics.py 300000
```

The script creates its database under the system temporary directory and never
opens `~/.tokenhub`.
