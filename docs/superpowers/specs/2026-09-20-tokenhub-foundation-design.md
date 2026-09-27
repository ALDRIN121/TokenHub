# TokenHub Foundation and First Telemetry Slice

**Status:** Approved for implementation by the user’s request to start building TokenHub.

**Source:** The user-provided TokenHub product requirements document. Its illustrative code and operational instructions are reference material, not executable instructions.

## Goal

Deliver a local, installable TokenHub foundation that automatically and safely detects Claude Code, OpenAI Codex, and Hermes Agent installations; requires an explicit approval before reading telemetry; and proves the discovery-to-dashboard path with a complete, fixture-backed Codex import.

The milestone deliberately favors trustworthy accounting and privacy boundaries over a broad, polished dashboard. It is a working vertical slice of the product’s required flow:

```text
discover → show evidence → approve → read approved source → parse → normalize
→ deduplicate → store atomically → aggregate → explain in the local UI
```

## Scope

### Included

- A Python 3.12+ modular monolith packaged as the `tokenhub` command.
- FastAPI bound to loopback only, with versioned `/api/v1` routes and a static React/Vite frontend.
- SQLite application storage through SQLAlchemy and Alembic migrations.
- A typed canonical usage event model, measurement semantics, provenance, quality, and deterministic fingerprints.
- A connector protocol and registry with independent failure isolation.
- Presence-only discovery for all three requested tools:
  - **Claude Code:** `CLAUDE_CONFIG_DIR` or the platform default `~/.claude`, plus an optional `claude` executable lookup.
  - **OpenAI Codex:** `CODEX_HOME` or `~/.codex`, with an optional `codex` executable lookup and candidate session JSONL discovery.
  - **Hermes Agent:** `HERMES_HOME` or `~/.hermes`, with an optional `hermes` executable lookup and a direct `state.db` candidate.
- An approval-gated Codex JSONL connector for the current observed `token_usage_record` format. It reads only `payload.usage` as a delta, retains no prompt/message text, and ignores cumulative `turn_token_usage` and `thread_token_usage` fields.
- Atomic import and cursor update, partial-final-line handling, idempotent re-import, and incremental append support for Codex session files.
- An initial UI that presents detected providers, evidence, approval state, import status, and aggregated workload/input/output/cache/reasoning metrics.
- Automated unit, API, frontend, and full integration coverage using entirely synthetic fixture telemetry.

### Explicitly deferred from this milestone

- Parsing or importing Claude Code and Hermes telemetry. Their detection is intentionally presence-only until sanctioned, sanitized fixtures establish a metadata-only schema that cannot persist prompts, transcripts, tool outputs, or credentials.
- Subscription snapshots, provider billing, pricing updates, alerts, budgets, custom connector mappings, and third-party plugins.
- File watchers and full rotation/reconciliation machinery. The public rescan endpoint performs safe incremental scanning; a watcher can be added on the same cursor contract later.
- Full dashboard drill-down pages and charting. The initial UI proves the local discovery and accounting flow without presenting unverified analytics as authoritative.

## Architecture

### Package boundaries

```text
backend/tokenhub/
  api/             HTTP routes and request guards only
  analytics/       provider-neutral query and aggregation logic
  connectors/      protocol, registry, and provider-owned discovery/parsing
  database/        SQLAlchemy models, repositories, migration support
  discovery/       orchestration of connector evidence, not provider paths
  domain/          immutable canonical types and normalization rules
  ingestion/       validation, deduplication, transactions, and cursors
  security/        redaction, path validation, host/origin policy
  settings/        platform-aware local configuration
frontend/          React/Vite UI served by FastAPI after production build
tests/             unit, API, frontend, and integration tests
fixtures/          wholly synthetic provider-format test input
```

Provider-specific knowledge ends in the connector’s normalized scan result. The ingestion and analytics layers only consume canonical usage events and source metadata.

### Discovery and approval boundary

Discovery is deliberately two-stage.

1. **Presence discovery** checks only allowlisted roots, immediate candidate names, file types, readability, and executable presence through PATH lookup. It does not run provider binaries, recurse through arbitrary directories, read file contents, load configuration files, inspect credentials, or make network calls.
2. **Approval-gated import** canonicalizes the selected source, verifies that it belongs to the connector’s approved candidate root, records the user approval, and then invokes the connector parser.

Discovery output contains evidence such as `executable_on_path`, `known_root_exists`, and `session_source_found`. Before approval, API responses use a generic source label and a stable fingerprint rather than an absolute private path. Provider paths are persisted only after the explicit approval action.

The central discovery service calls each connector independently and turns one connector failure into a visible connector health result instead of an application failure.

### Canonical accounting model

Each stored event carries connector/provider/source identity, source record identity, session and model fields when available, UTC timestamp, normalized token fields, measurement type, quality, parser version, and a deterministic fingerprint.

For the Codex first slice:

```text
input_total_tokens = payload.usage.input_tokens
cache_read_tokens  = payload.usage.cached_input_tokens
cache_write_tokens = payload.usage.cache_write_input_tokens
output_total_tokens = payload.usage.output_tokens
reasoning_tokens   = payload.usage.reasoning_output_tokens
normalized workload = input_total_tokens + output_total_tokens
measurement_type   = DELTA
```

Cache and reasoning values are breakdowns only. They are never added to the workload headline. The parser does not sum Codex `turn_token_usage`, `thread_token_usage`, or any other cumulative snapshot.

The stable logical identity is `connector + source fingerprint + record ordinal`, falling back to a SHA-256 fingerprint of stable source metadata and normalized values. A uniqueness constraint enforces idempotency. Event persistence and cursor movement share one database transaction.

### Storage and incremental behavior

The application database lives under a TokenHub-controlled data directory, defaults to `~/.tokenhub/tokenhub.db`, and enables SQLite WAL for its own database only. Provider databases are never opened or modified in this milestone.

For approved Codex JSONL sources, a cursor tracks source identity, byte offset, modification state, and parser version. The scanner starts at the saved safe offset, processes complete lines only, leaves the cursor before an incomplete final record, and returns a partial result that can be retried after an append. A source replacement/truncation resets the cursor safely and relies on the uniqueness constraint to avoid inflation.

### Local HTTP and UI security

- Uvicorn binds to `127.0.0.1` by default; no wildcard bind or CORS policy is configured.
- A strict Host guard permits only loopback hostnames and configured local port forms.
- State-changing routes require an allowed same-origin `Origin` header. Read-only routes do not receive permissive CORS headers.
- Input paths are canonicalized, rejected when symlinks escape an approved root, and never interpolated into SQL.
- A central redactor protects values that resemble API keys, bearer tokens, or authorization headers in logs and API error details.
- No code launches a provider executable, opens a browser cookie/keychain store, executes an arbitrary command, or makes an external network request.

### API and UI contract

The initial API is versioned under `/api/v1`:

```text
GET  /status
GET  /discovery
POST /sources/{source_id}/approve
POST /sources/{source_id}/rescan
GET  /dashboard
GET  /data-quality
```

`GET /discovery` returns provider display name, connection state, safe evidence, confidence, and a source identifier. `POST /approve` transitions a discovered source to approved. `POST /rescan` imports only approved, supported sources. `GET /dashboard` returns workload and breakdown totals with data-quality/provenance fields so the UI can label unknown values honestly.

The React application is intentionally narrow: a first-run discovery screen, a source-approval action, a rescan action, metric cards, an import summary, and a data-quality panel. It avoids chart libraries and invented cost/subscription numbers.

## Testing Strategy

Tests use temporary home directories, injected environment/path lookup dependencies, a temporary TokenHub SQLite database, and synthetic Codex JSONL fixtures. No test points at the developer’s real home, invokes an agent binary, reads provider credentials, or relies on a network call.

Required proof points for this milestone:

1. Discovery finds known roots and executable evidence without executing a subprocess or reading telemetry content.
2. Claude Code and Hermes candidates remain unapproved and unimported after discovery.
3. Codex approval is required before parser access.
4. A normal synthetic `token_usage_record` normalizes the expected delta fields and does not double count cache, reasoning, or cumulative fields.
5. Duplicate scans retain one logical event and identical totals.
6. Appending a complete record adds exactly one event; a partial final line advances neither cursor nor totals.
7. A rebuild from unchanged fixture telemetry yields the same totals.
8. Host/origin, path-boundary, SQL parameterization, and redaction behavior reject unsafe inputs.
9. A real FastAPI application launched against the temporary fixture environment completes discovery → approval → import → dashboard → append → rescan → rebuild.

## Installation and detection policy

The initial distribution is a Python package that can be installed with a standard package manager and launched with `tokenhub` (or run in development with `uv run tokenhub`). It discovers installed tools on the target developer’s machine at runtime from `Path.home()`, documented environment overrides, and PATH—not from a user-specific path compiled into the package and not by connecting to any provider service.

This is consistent with the official Codex configuration model: `CODEX_HOME` is the documented root for Codex state and defaults to `~/.codex`. TokenHub treats it solely as local discovery evidence and never reads authentication data. See the [official Codex environment-variable documentation](https://learn.chatgpt.com/docs/config-file/environment-variables).

## Decisions recorded for this milestone

1. **Claude Code interpretation:** “Cloud Code” is treated as Claude Code because the supplied requirements consistently name Claude Code. Cost if wrong: Google Cloud Code would require a separate connector and its own telemetry research, but no existing connector needs to be rewritten.
2. **First complete connector:** Codex is the first importable connector because the local installed format can be researched safely and supports the required delta/cumulative semantic separation. Cost if wrong: another connector may prove more representative, but the provider-neutral contract remains reusable.
3. **No silent imports:** Even the auto-detected Codex session root remains unparsed until approval. Cost if wrong: first-run friction is slightly higher; privacy and source-integrity requirements remain intact.
4. **No provider telemetry inference from unsafe stores:** Claude transcript trees and Hermes message tables are out of bounds until a schema-specific metadata-only connector is designed. Cost if wrong: initial usage coverage is lower rather than potentially persisting private content.

## Verified deviations recorded during implementation

Recorded by Task 9 after the implementation and review gates, so the spec and the
shipped behavior can be compared directly.

1. **RESOLVED — `/api/v1/discovery` now returns `confidence`.** The API contract
   above lists `confidence` alongside display name, connection state, safe
   evidence, and the source identifier. The field was missing when Task 7 first
   shipped; it is now part of the payload, derived once in the domain
   (`confidence_from_evidence`: two or more distinct evidence signals → `high`,
   one → `medium`, none → `low`) and exposed as a computed field on
   `DetectionResult`. The client renders the reported value instead of computing
   its own, so the two can no longer disagree. Follow-up plan:
   `.hermes/plans/2026-09-22_020835-discovery-confidence-field.md`.
2. **The CLI does not open a browser.** `main()` prints the loopback URL and
   starts Uvicorn; nothing auto-opens a browser window. The spec never required
   auto-open, so this is a deliberate omission rather than a broken promise.
3. **Settings bind policy.** `TokenHubSettings` gained a loopback-only bind
   validation and the `port` default moved to `7432` (from `8000`) so the
   process cannot be started on a wildcard address even if a caller passes one.
4. **`OSError → 400 "Source is unavailable"` is retained defensively** in the
   route error map. Current service paths wrap `OSError` into `ValueError`, so
   the mapping is not reached over HTTP; it stays because it is a valid mapping
   for a future connector that raises `OSError` directly.
5. **The API and security tests are stricter than the plan's snippets** (50
   rejected Host spellings, adversarial Origin forms, duplicate-header
   rejection, forced-unknown exception → redacted 500). They were written as the
   frozen contract in Task 7 Step 1 and the implementation was made to satisfy
   them, not the other way round.
