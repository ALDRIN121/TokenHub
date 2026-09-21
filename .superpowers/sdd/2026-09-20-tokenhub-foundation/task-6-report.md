# Task 6 Report: Approval-Gated Ingestion and Provider-Neutral Analytics

## Status and takeover note

Complete after taking over an uncommitted implementation from an earlier agent
that exhausted its quota. The inherited work was preserved and reviewed rather
than reset. At takeover, the focused Task 6 tests, full suite, Ruff, and backend
mypy had been reported green by the coordinator; those results were treated only
as starting evidence and were independently rerun after the review fixes below.

The previous agent did not leave a Task 6 report or a reproducible transcript of
the original missing-service RED run. This report therefore does not claim that
unavailable evidence. The takeover corrections did exercise genuine RED/GREEN
cycles, recorded below.

## Findings and rulings

- Approval and rescan gating were correctly ordered before connector parsing.
  Ingestion permits only the `codex-local` connector with matching provider,
  source type, scan support, and parser version. Claude Code and Hermes remain
  discovery-only and cannot reach a provider file open through this service.
- Approved lifecycle states remain retryable after healthy, partial, missing,
  permission, or error outcomes; discovered, disabled, and unsupported sources
  cannot be scanned. Successful/partial scan state is committed with events,
  cursor, and audit data.
- The inherited parser reset only when the saved byte offset exceeded current
  file size. A replacement of equal or greater size could therefore seek into
  unrelated content. The correctness policy is now: hash exactly the previously
  consumed prefix, resume only when it still matches, and otherwise restart at
  byte zero while database uniqueness deduplicates already-known records. The
  verification read is bounded by the prior safe cursor offset and does not
  parse or retain the old prefix.
- A requested source-state update could match zero rows while events, cursor,
  and audit data still committed. Persistence now treats that as a transaction
  failure and rolls the whole scan back.
- Aggregate SQL uses `SUM` without `COALESCE`, so wholly unobserved metrics stay
  `None`; observed zero remains zero. Workload is only input plus output, while
  cache and reasoning remain separate breakdowns. Only delta events contribute.
- Rebuild reloads durable approved paths and parser metadata from TokenHub
  storage, so it works after discovery/process cache loss. It clears only
  normalized events and cursors in a TokenHub transaction, preserves approvals,
  audit history, and provider files, and scans only compatible approved sources.
- The existing `0001_initial` migration already contains both
  `ImportRunRecord.partial_final_record` and `unsupported_records`, matching the
  ORM. Prefix validation requires one schema extension, so Task 6 adds a nullable
  `0002_cursor_prefix_fingerprint` migration. Existing cursor rows are preserved
  with `NULL`; the connector safely reparses such a legacy cursor once and then
  persists a fingerprint.

## Changes

- Added `IngestionService` approval, rescan, partial-result, rebuild, compatibility,
  and error-state orchestration.
- Connected the Codex scanner to the approved parser and extended normalized scan
  results with partial/unsupported counts.
- Added consumed-prefix cursor fingerprints for replacement/truncation safety,
  including ORM, repository, and Alembic support.
- Made event insert/deduplication, cursor upsert, import audit, and source-state
  transition one transaction, including a zero-row state-update guard.
- Added provider-neutral dashboard totals, source freshness, and quality counts
  with nullable unknown metrics and no cost/subscription derivation.
- Added focused synthetic service and analytics tests plus test-only composition
  helpers. No test reads the developer's real telemetry.

Files beyond the six paths named in the brief were necessarily updated at the
existing integration boundaries: connector protocol/Codex scan wiring, domain
result models, repositories/ORM, the new additive migration, and shared test
composition. No Task 7 API or application work was added.

## TDD evidence from takeover corrections

### RED

The three focused regressions were run against the inherited implementation:

```text
uv run pytest tests/ingestion/test_service.py::test_missing_source_state_update_rolls_back_events_cursor_and_audit tests/ingestion/test_service.py::test_same_or_larger_replacement_reparses_when_consumed_prefix_changed tests/ingestion/test_service.py::test_cursor_fingerprint_migration_preserves_legacy_rows -q
# 3 failed
# no LookupError for a missing source state row
# equal/larger replacement inserted 0 rather than 2 events
# SQLite reported no prefix_fingerprint column after upgrade to head
```

After the initial prefix implementation, the full suite exposed a genuine
compatibility regression:

```text
uv run pytest -q
# 1 failed, 67 passed
# direct parse_codex_jsonl(..., start_offset=...) reread the prefix
```

### GREEN

```text
uv run pytest tests/ingestion/test_service.py::test_missing_source_state_update_rolls_back_events_cursor_and_audit tests/ingestion/test_service.py::test_same_or_larger_replacement_reparses_when_consumed_prefix_changed tests/ingestion/test_service.py::test_cursor_fingerprint_migration_preserves_legacy_rows -q
# 3 passed

uv run pytest tests/connectors/codex/test_parser.py::test_parser_starts_at_the_provided_completed_line_offset tests/ingestion/test_service.py::test_same_or_larger_replacement_reparses_when_consumed_prefix_changed tests/ingestion/test_service.py::test_cursor_fingerprint_migration_preserves_legacy_rows -q
# 3 passed
```

The compatibility fix keeps the parser's explicit-offset API intact and puts
the legacy durable-cursor restart decision in the connector, where cursor origin
is known.

## Final verification

Fresh commands run on the final tree before commit:

```text
uv run pytest tests/ingestion/test_service.py tests/analytics/test_dashboard.py -q
# 22 passed

uv run pytest -q
# 68 passed

uv run ruff check backend tests
# All checks passed!

uv run mypy backend
# Success: no issues found in 31 source files

git diff --check
# exit 0
```

## Remaining concerns

- Prefix verification reads at most the prior safe byte offset on every
  incremental rescan. This favors replacement correctness over constant-time
  append checks; a future rotation/watcher task could add stable file identity
  and checkpointed hashes if large-session performance requires it.
- Rebuild follows the brief's clear-then-rescan flow. Events/cursors are cleared
  atomically, but rescans are separate per-source transactions, so a later source
  failure can leave an explicitly reported partial rebuild rather than restoring
  the pre-rebuild normalized snapshot.
- Task 3's deferred foreign-key decision remains unchanged. Service scans guard
  source/state atomicity, while the lower-level repository still permits
  provider-neutral synthetic persistence without a source when no state change
  is requested.
- The generated, untracked `uv.lock` remains untouched and unstaged.
