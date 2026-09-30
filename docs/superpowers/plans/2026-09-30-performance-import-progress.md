# TokenHub performance and import progress implementation

Approved in chat on 2026-09-30. Target: an older 8 GB laptop with an SSD.

## Global constraints

Local-only operation; retain approvals, no-follow file access, path-free APIs, nullable counters, and provider accounting semantics. Preserve synchronous mutation API shapes; new UI uses background jobs. Progress and browsing are included; pause/resume and the subsequent feature roadmap are deferred.

## Task 1: Measurements and import persistence

Add deterministic provider workloads and phase/SQL/memory benchmarks. Batch SQLite writes without changing insert/duplicate counts or metadata enrichment. Retain source-level transaction rollback. Fetch cursors together. Rebuild each source atomically and retain previous usage on failure.

## Task 2: Background collection and progress

One managed worker owns writes and its own session. Read requests use independent sessions. Jobs coalesce, persist path-free state, and report stages, file counts, bytes/records, elapsed time, and outcomes. Startup queues a scan. Legacy routes wait for the same worker; background=true responds 202. Collection status reads an in-memory snapshot and never waits for ingestion. Shutdown interrupts safely.

## Task 3: Bounded analytics and frontend requests

SQL aggregates preserve Claude canonical selection before date filtering. Add query indexes, bounded caches, summary/model/session/detail endpoints, lightweight discovery/quality, and paginated source health. UI loads bounded pages, ignores obsolete requests, and keeps existing data usable during updates.

## Task 4: Progress UI and verification

Persistent accessible progress with one-second active polling, ten-second idle polling, hidden-tab suspension, provider-specific activity, and reconnect after reload. Add API concurrency, job lifecycle, accounting/pagination, and UI regressions. Run full tests, lint, types, production build, benchmark workloads, browser checks, and an independent final review. Record results and material limits.

## Review focus

- Errors after partially reading a source must not publish a cursor or partial replacement.
- Claude copies outside date bounds must not change canonical selection.
- Worker and API sessions must never share ORM state.
- Restart/shutdown must preserve committed data and account for unfinished jobs.
- Background APIs must retain all existing trust and origin restrictions.
