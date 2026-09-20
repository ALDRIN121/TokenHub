# TokenHub Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local-first TokenHub vertical slice that safely detects Claude Code, Codex, and Hermes, imports only an approved Codex JSONL source, and proves discovery through dashboard aggregation with an automated integration test.

**Architecture:** A Python modular monolith owns discovery, ingestion, persistence, analytics, and HTTP routes. A connector registry contains all provider-specific discovery/parsing behavior; the central pipeline only accepts normalized domain events. A small React/Vite client is served from the FastAPI process and consumes versioned local API routes.

**Tech Stack:** Python 3.12+, uv, FastAPI, Pydantic v2, SQLAlchemy 2.x, Alembic, SQLite, pytest/httpx, React, TypeScript, Vite, Vitest, Testing Library.

**Spec:** `docs/superpowers/specs/2026-09-20-tokenhub-foundation-design.md`

## Global Constraints

- Bind Uvicorn to `127.0.0.1` by default; never default to `0.0.0.0` and do not configure permissive CORS.
- Use only local filesystem discovery; never call a provider API, launch a provider executable, inspect a browser/keychain, or make a network request.
- Discover only connector-owned allowlisted roots and immediate candidate names. Do not recurse through arbitrary directories or read telemetry before source approval.
- Resolve roots with `Path.home()` plus `CODEX_HOME`, `CLAUDE_CONFIG_DIR`, or `HERMES_HOME`; never compile a developer-specific username or absolute home path into production code.
- Never read or store provider credentials, configuration contents, prompt text, messages, tool output, transcript bodies, or raw provider records.
- Before approval, return only generic source labels, safe evidence, and a stable path fingerprint. Persist a canonical source path only after explicit approval.
- Codex `payload.usage` is a `DELTA`; ignore `turn_token_usage`, `thread_token_usage`, and other cumulative fields. Workload equals input total plus output total; cache and reasoning are breakdowns only.
- Make event insert and cursor advancement one SQLite transaction. Enforce source-scoped idempotency with a database uniqueness constraint.
- Use UTC timestamps, bound SQL parameters, exact local host/origin checks, central redaction, and canonical path checks that reject symlink escape.
- Use fully synthetic fixtures. Tests may not point at `/Users/aldrinjoseph`, a real provider home, real credentials, actual telemetry, a provider subprocess, or the public network.
- Keep Claude Code and Hermes discovery-only in this milestone. Never import their transcript/database data.
- Write a test first, observe the expected failure, then add the smallest production change that makes it pass. Run focused tests after each change and the complete relevant suite before each commit.

---

## File Structure

| Path | Responsibility |
|---|---|
| `pyproject.toml` | Python packaging, `tokenhub` console script, dependency/test/lint configuration. |
| `backend/tokenhub/settings.py` | Immutable application settings and TokenHub-controlled paths. |
| `backend/tokenhub/domain/models.py` | Provider-neutral enums and Pydantic/domain data objects. |
| `backend/tokenhub/security/redaction.py` | Safe log/error redaction. |
| `backend/tokenhub/security/paths.py` | Canonical source-root validation and symlink checks. |
| `backend/tokenhub/security/http.py` | Host and same-origin request guards. |
| `backend/tokenhub/connectors/protocol.py` | Minimal connector protocol and scan result contract. |
| `backend/tokenhub/connectors/registry.py` | Connector registration and isolated calls. |
| `backend/tokenhub/connectors/{claude,codex,hermes}/connector.py` | Provider-owned discovery; only Codex gets a scanner/parser. |
| `backend/tokenhub/connectors/codex/parser.py` | Metadata-only parsing of known Codex `token_usage_record` rows. |
| `backend/tokenhub/discovery/service.py` | Orchestrates discovery and converts candidates to source records. |
| `backend/tokenhub/database/models.py` | SQLAlchemy application-owned tables. |
| `backend/tokenhub/database/session.py` | SQLite engine/session factory with WAL for TokenHub’s database. |
| `backend/tokenhub/database/repositories.py` | Source, cursor, event, and dashboard persistence methods. |
| `backend/tokenhub/database/migrations/` | Alembic environment and initial generated migration. |
| `backend/tokenhub/ingestion/service.py` | Approval-gated, atomic Codex scan/import/rebuild service. |
| `backend/tokenhub/analytics/service.py` | Provider-neutral dashboard aggregates and quality summary. |
| `backend/tokenhub/api/routes.py` | Versioned HTTP endpoints; routes delegate to services. |
| `backend/tokenhub/app.py` | App factory, dependency wiring, middleware, static UI mounting. |
| `backend/tokenhub/cli.py` | `tokenhub` startup command that opens a browser only after local server startup. |
| `frontend/` | Vite React application and frontend test setup. |
| `tests/` | Unit, route, and end-to-end API tests. |
| `fixtures/codex/` | Synthetic JSONL records that preserve only safe schema and numeric semantics. |

## Task 1: Project Foundation and Tooling

**Files:**

- Create: `.gitignore`
- Create: `pyproject.toml`
- Create: `backend/tokenhub/__init__.py`
- Create: `backend/tokenhub/cli.py`
- Create: `tests/test_package.py`
- Create: `README.md`
- Modify: `docs/superpowers/specs/2026-09-20-tokenhub-foundation-design.md` only if the actual tool versions require a compatible adjustment.

**Interfaces:**

- Produces `tokenhub.cli:main` as the console-script target.
- Produces a `pytest` configuration that collects `tests/` and recognizes `integration` markers.
- Produces `uv run pytest`, `uv run ruff check .`, and `uv run mypy backend` as repository verification commands.

- [ ] **Step 1: Write the failing package contract test**

```python
from tokenhub.cli import main


def test_console_entrypoint_is_importable() -> None:
    assert callable(main)
```

The production break this catches is an invalid package layout or console-entrypoint module that would make installation fail before TokenHub starts.

- [ ] **Step 2: Run the focused test and verify the expected import failure**

Run: `uv run pytest tests/test_package.py -q`

Expected: collection fails because `tokenhub` does not yet exist.

- [ ] **Step 3: Add the smallest installable package scaffold**

Create a root `pyproject.toml` with `requires-python = ">=3.12"`, package discovery rooted at `backend`, a `tokenhub = "tokenhub.cli:main"` script, runtime dependencies `fastapi`, `uvicorn[standard]`, `pydantic`, `sqlalchemy`, and `alembic`, and development dependencies `pytest`, `pytest-asyncio`, `httpx`, `ruff`, and `mypy`.

Use this console-entrypoint shape:

```python
def main() -> None:
    # Server startup is implemented in Task 7. Keep this first importable
    # entrypoint free of provider discovery and provider-side effects.
    return None
```

Add `.gitignore` entries for `.venv/`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `frontend/node_modules/`, `frontend/dist/`, `.superpowers/`, `*.db`, and `*.sqlite`.

Write `README.md` with the exact development commands:

```text
uv sync --all-groups
uv run pytest
cd frontend && npm ci && npm run test && npm run build
```

Do not document an installer that contacts a provider or assumes a user home path.

- [ ] **Step 4: Run the focused test and packaging checks**

Run:

```bash
uv run pytest tests/test_package.py -q
uv run python -c "from tokenhub.cli import main; assert callable(main)"
uv run ruff check backend tests
```

Expected: the test passes, the import exits zero, and Ruff reports no diagnostics.

- [ ] **Step 5: Commit the tested foundation**

```bash
git add .gitignore pyproject.toml README.md backend/tokenhub/__init__.py backend/tokenhub/cli.py tests/test_package.py
git commit -m "chore: scaffold TokenHub Python package"
```

## Task 2: Canonical Domain, Settings, and Security Primitives

**Files:**

- Create: `backend/tokenhub/settings.py`
- Create: `backend/tokenhub/domain/__init__.py`
- Create: `backend/tokenhub/domain/models.py`
- Create: `backend/tokenhub/security/__init__.py`
- Create: `backend/tokenhub/security/redaction.py`
- Create: `backend/tokenhub/security/paths.py`
- Create: `tests/domain/test_models.py`
- Create: `tests/security/test_redaction.py`
- Create: `tests/security/test_paths.py`

**Interfaces:**

- `Provider` enum values: `claude_code`, `codex`, `hermes`.
- `SourceState` enum values: `discovered`, `approved`, `importing`, `healthy`, `partial`, `unsupported`, `permission_denied`, `source_missing`, `error`, `disabled`.
- `MeasurementType` enum values: `delta`, `cumulative`, `snapshot`, `gauge`.
- `Quality` enum values: `exact`, `high`, `estimated`, `partial`, `unavailable`.
- `UsageEvent` must expose `workload_tokens` equal to `input_total_tokens + output_total_tokens`, without cache/reasoning additions.
- `TokenHubSettings` must accept injected `home_directory`, `data_directory`, `host`, and `port` for tests.
- `redact_sensitive(str) -> str` and `validate_source_path(candidate: Path, root: Path) -> Path` must be public functions.

- [ ] **Step 1: Write failing behavior tests**

```python
def test_workload_excludes_cache_and_reasoning() -> None:
    event = UsageEvent(
        connector_id="codex-local",
        provider=Provider.CODEX,
        source_id="source-a",
        record_identity="7",
        timestamp=datetime(2026, 9, 20, tzinfo=UTC),
        input_total_tokens=100,
        cache_read_tokens=70,
        cache_write_tokens=10,
        output_total_tokens=25,
        reasoning_tokens=20,
        measurement_type=MeasurementType.DELTA,
        quality=Quality.EXACT,
        parser_version="codex-jsonl-v1",
    )
    assert event.workload_tokens == 125


def test_redactor_hides_bearer_and_openai_style_keys() -> None:
    value = "Authorization: Bearer secret-value OPENAI_API_KEY=sk-abcdef1234567890"
    assert "secret-value" not in redact_sensitive(value)
    assert "sk-abcdef1234567890" not in redact_sensitive(value)


def test_source_path_rejects_symlink_escape(tmp_path: Path) -> None:
    approved_root = tmp_path / "approved"
    approved_root.mkdir()
    outside = tmp_path / "outside.jsonl"
    outside.write_text("{}\n")
    escaped = approved_root / "escaped.jsonl"
    escaped.symlink_to(outside)
    with pytest.raises(ValueError, match="approved root"):
        validate_source_path(escaped, approved_root)
```

The first test catches the accounting bug of double-counting cache/reasoning. The second catches accidental secret exposure. The third catches a symlink path escape.

- [ ] **Step 2: Run tests and verify correct missing-symbol failures**

Run: `uv run pytest tests/domain tests/security -q`

Expected: tests fail during import because domain/security modules are absent.

- [ ] **Step 3: Implement the smallest typed domain and guardrails**

Use Pydantic models or frozen dataclasses with explicit `int | None` token fields. Treat missing token values as `None`, not zero. Implement `workload_tokens` only when both total input and total output are known; otherwise return `None`.

Compute `TokenHubSettings.data_directory` from an injected path or `home_directory / ".tokenhub"`; never read an environment variable’s secret value. `validate_source_path` must call `resolve(strict=True)`, resolve the approved root, and use `relative_to` to prove containment. Do not use string-prefix comparison.

Redaction must replace recognized bearer values and `sk-`-like strings with `[REDACTED]`; preserve unrelated text so diagnostics remain useful.

- [ ] **Step 4: Run focused tests, lint, and type checks**

Run:

```bash
uv run pytest tests/domain tests/security -q
uv run ruff check backend tests
uv run mypy backend/tokenhub/domain backend/tokenhub/security backend/tokenhub/settings.py
```

Expected: all three commands exit zero.

- [ ] **Step 5: Commit canonical primitives**

```bash
git add backend/tokenhub/settings.py backend/tokenhub/domain backend/tokenhub/security tests/domain tests/security
git commit -m "feat: add canonical usage and security primitives"
```

## Task 3: Application Database and Migration

**Files:**

- Create: `backend/tokenhub/database/__init__.py`
- Create: `backend/tokenhub/database/models.py`
- Create: `backend/tokenhub/database/session.py`
- Create: `backend/tokenhub/database/repositories.py`
- Create: `backend/tokenhub/database/migrations/env.py`
- Create: `backend/tokenhub/database/migrations/versions/0001_initial.py`
- Create: `tests/database/test_repositories.py`

**Interfaces:**

- `create_engine_for(settings: TokenHubSettings) -> Engine` creates only TokenHub-owned SQLite databases and enables WAL for that connection.
- `initialize_database(engine: Engine) -> None` runs application migration metadata in tests; production migration commands remain Alembic-owned.
- `SourceRepository.upsert_discovery(candidate: SourceDescriptor) -> SourceRecord` never stores a pre-approval absolute path.
- `SourceRepository.approve(source_id: str) -> SourceRecord` stores the validated canonical path and moves only `DISCOVERED → APPROVED`.
- `UsageRepository.persist_scan(events: list[UsageEvent], cursor: SyncCursor) -> ImportOutcome` inserts events and cursor atomically.
- Usage-event uniqueness: `(source_id, record_identity)`.

- [ ] **Step 1: Write failing repository tests**

```python
def test_discovery_does_not_persist_path_until_approval(session: Session) -> None:
    candidate = synthetic_codex_candidate(path=Path("/tmp/fake/sessions/a.jsonl"))
    source = SourceRepository(session).upsert_discovery(candidate)
    assert source.canonical_path is None
    assert source.path_fingerprint


def test_duplicate_event_and_cursor_are_atomic(session: Session) -> None:
    repo = UsageRepository(session)
    first = repo.persist_scan([synthetic_event(record_identity="1")], cursor_for(12))
    second = repo.persist_scan([synthetic_event(record_identity="1")], cursor_for(12))
    assert first.inserted_events == 1
    assert second.inserted_events == 0
    assert repo.current_cursor("source-a").byte_offset == 12
    assert repo.dashboard_totals().workload_tokens == 125
```

The first test catches an early privacy leak. The second catches import inflation and separate, non-atomic cursor writes.

- [ ] **Step 2: Run the tests and verify imports fail because persistence is absent**

Run: `uv run pytest tests/database/test_repositories.py -q`

Expected: test collection fails because database classes are missing.

- [ ] **Step 3: Implement SQLite models and repositories**

Create application-owned tables for `sources`, `usage_events`, `sync_cursors`, and `import_runs`. Store `path_fingerprint`, not an unapproved absolute path. Include `parser_version`, `quality`, `measurement_type`, provenance fields, and nullable token columns in `usage_events`.

Use SQLAlchemy bound parameters only. Configure `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON` on the TokenHub engine; never use this engine against a provider SQLite file. `persist_scan` must use one `with session.begin():` block that inserts non-duplicate events and then upserts the cursor.

Generate the first Alembic migration from the same metadata and make it create the exact tables/unique index above. Do not silently call ad-hoc DDL at FastAPI startup in production.

- [ ] **Step 4: Run database tests and migration validation**

Run:

```bash
uv run pytest tests/database/test_repositories.py -q
uv run alembic -c backend/alembic.ini upgrade head
uv run ruff check backend tests
```

Expected: duplicate persistence reports zero new events, the migration completes on a temporary configured database, and lint is clean.

- [ ] **Step 5: Commit persistence**

```bash
git add backend/tokenhub/database backend/alembic.ini tests/database
git commit -m "feat: add TokenHub SQLite persistence"
```

## Task 4: Connector Contract, Registry, and Safe Presence Discovery

**Files:**

- Create: `backend/tokenhub/connectors/__init__.py`
- Create: `backend/tokenhub/connectors/protocol.py`
- Create: `backend/tokenhub/connectors/registry.py`
- Create: `backend/tokenhub/connectors/claude/__init__.py`
- Create: `backend/tokenhub/connectors/claude/connector.py`
- Create: `backend/tokenhub/connectors/codex/__init__.py`
- Create: `backend/tokenhub/connectors/codex/connector.py`
- Create: `backend/tokenhub/connectors/hermes/__init__.py`
- Create: `backend/tokenhub/connectors/hermes/connector.py`
- Create: `backend/tokenhub/discovery/__init__.py`
- Create: `backend/tokenhub/discovery/service.py`
- Create: `tests/connectors/test_discovery.py`
- Create: `tests/connectors/test_registry.py`

**Interfaces:**

- `UsageConnector` protocol: `connector_id`, `display_name`, `detect(context) -> DetectionResult`, `discover_sources(context) -> list[SourceDescriptor]`, `scan(source, cursor) -> ScanResult`, `capabilities() -> ConnectorCapabilities`.
- `DiscoveryContext(home: Path, environment: Mapping[str, str], which: Callable[[str], str | None])` is injected; it must not invoke subprocesses.
- `ConnectorRegistry.discover_all(context) -> list[DetectionResult]` catches one connector exception and returns an `ERROR` result for that connector while preserving the other results.
- Claude and Hermes `scan` return a clear `UNSUPPORTED` result without opening any provider file or database.
- Codex source candidates are only direct `sessions/**/*.jsonl` files under the approved Codex root. `history.jsonl`, configuration, auth, logs, caches, and arbitrary children are excluded.

- [ ] **Step 1: Write failing discovery and isolation tests**

```python
def test_codex_detection_uses_override_and_reports_safe_evidence(tmp_path: Path) -> None:
    home = tmp_path / "home"
    codex_root = tmp_path / "custom-codex"
    session_dir = codex_root / "sessions" / "2026" / "09" / "20"
    session_dir.mkdir(parents=True)
    (session_dir / "rollout.jsonl").write_text("{}\n")

    result = CodexConnector().detect(
        DiscoveryContext(home=home, environment={"CODEX_HOME": str(codex_root)}, which=lambda _: None)
    )

    assert result.provider is Provider.CODEX
    assert "known_root_exists" in result.evidence_codes
    assert "session_source_found" in result.evidence_codes
    assert str(codex_root) not in result.model_dump_json()


def test_discovery_never_runs_provider_executables(tmp_path: Path) -> None:
    calls: list[str] = []
    context = DiscoveryContext(
        home=tmp_path,
        environment={},
        which=lambda executable: calls.append(executable) or "/bin/fake",
    )
    ConnectorRegistry.default().discover_all(context)
    assert calls == ["claude", "codex", "hermes"]


def test_one_connector_failure_does_not_hide_other_results(tmp_path: Path) -> None:
    registry = ConnectorRegistry([FailingConnector(), CodexConnector()])
    results = registry.discover_all(DiscoveryContext(home=tmp_path, environment={}, which=lambda _: None))
    assert [result.connector_id for result in results] == ["failing", "codex-local"]
    assert results[0].state is SourceState.ERROR
```

These tests catch secret/path disclosure, accidental provider execution, and connector-wide availability failures.

- [ ] **Step 2: Run focused discovery tests and verify missing-module failures**

Run: `uv run pytest tests/connectors/test_discovery.py tests/connectors/test_registry.py -q`

Expected: collection fails because connector modules do not exist.

- [ ] **Step 3: Implement pure connector discovery**

Use `shutil.which` only in the real app adapter, not `subprocess.run`. For each connector choose the environment override only when it names an existing directory, otherwise use `context.home / ".codex"`, `context.home / ".claude"`, or `context.home / ".hermes"`.

`SourceDescriptor` must retain a private canonical candidate path for service use but expose only a SHA-256 path fingerprint and generic label through `safe_view()`. Ensure Codex candidates are regular `.jsonl` files below `sessions/`; skip symlinks, `history.jsonl`, `auth.json`, `config.toml`, and log/cache directories. Claude detection reports installation/configuration evidence but creates no importable source. Hermes reports a direct regular `state.db` candidate but keeps it unsupported for scanning.

- [ ] **Step 4: Run discovery tests, lint, and an import-side-effect check**

Run:

```bash
uv run pytest tests/connectors/test_discovery.py tests/connectors/test_registry.py -q
uv run ruff check backend tests
uv run python -c "from tokenhub.connectors.registry import ConnectorRegistry; print(len(ConnectorRegistry.default().connectors))"
```

Expected: tests pass, lint is clean, and importing the registry neither starts a server nor reads a provider file.

- [ ] **Step 5: Commit discovery**

```bash
git add backend/tokenhub/connectors backend/tokenhub/discovery tests/connectors
git commit -m "feat: add safe connector discovery"
```

## Task 5: Codex Parser and Synthetic Fixtures

**Files:**

- Create: `backend/tokenhub/connectors/codex/parser.py`
- Create: `fixtures/codex/normal.jsonl`
- Create: `fixtures/codex/duplicate.jsonl`
- Create: `fixtures/codex/partial.jsonl`
- Create: `fixtures/codex/unsupported.jsonl`
- Create: `tests/connectors/codex/test_parser.py`

**Interfaces:**

- `parse_codex_jsonl(source: SourceDescriptor, start_offset: int) -> ParsedCodexScan` returns normalized delta events, `safe_byte_offset`, `partial_final_record`, and `unsupported_records`.
- The parser accepts only a top-level `type == "token_usage_record"` with object-valued `payload.usage` and a nonempty `ordinal`.
- Each parsed event uses `record_identity = ordinal`, `MeasurementType.DELTA`, `Quality.EXACT`, and `parser_version = "codex-jsonl-v1"`.
- The parser must never retain or return arbitrary payload content, messages, commands, prompts, response text, cumulative counters, or raw record dictionaries.

- [ ] **Step 1: Write failing parser tests with literal numeric expectations**

```python
def test_parser_uses_delta_usage_and_excludes_cumulative_breakdowns() -> None:
    result = parse_codex_jsonl(synthetic_source("normal.jsonl"), start_offset=0)
    assert len(result.events) == 1
    event = result.events[0]
    assert event.input_total_tokens == 100
    assert event.cache_read_tokens == 70
    assert event.cache_write_tokens == 10
    assert event.output_total_tokens == 25
    assert event.reasoning_tokens == 20
    assert event.workload_tokens == 125
    assert event.measurement_type is MeasurementType.DELTA


def test_parser_stops_before_partial_final_line() -> None:
    result = parse_codex_jsonl(synthetic_source("partial.jsonl"), start_offset=0)
    assert [event.record_identity for event in result.events] == ["1"]
    assert result.partial_final_record is True
    assert result.safe_byte_offset < synthetic_source("partial.jsonl").canonical_path.stat().st_size


def test_parser_marks_unknown_shape_without_guessing_tokens() -> None:
    result = parse_codex_jsonl(synthetic_source("unsupported.jsonl"), start_offset=0)
    assert result.events == []
    assert result.unsupported_records == 1
```

The first test would fail if the production parser accidentally switched to a cumulative field or adds cache/reasoning twice. The second catches lost partial records. The third catches fabricated data.

- [ ] **Step 2: Run the parser tests and verify expected import failures**

Run: `uv run pytest tests/connectors/codex/test_parser.py -q`

Expected: collection fails because the parser module does not exist.

- [ ] **Step 3: Add fully synthetic fixtures and minimal streaming parser**

Use a fixture row shaped like this, with invented identifiers/timestamps and no user text:

```json
{"ordinal":1,"timestamp":"2026-09-20T10:00:00Z","type":"token_usage_record","payload":{"session_id":"session-fixture","thread_id":"thread-fixture","turn_id":"turn-fixture","usage":{"input_tokens":100,"cached_input_tokens":70,"cache_write_input_tokens":10,"output_tokens":25,"reasoning_output_tokens":20,"total_tokens":125},"turn_token_usage":{"input_tokens":999},"thread_token_usage":{"input_tokens":9999}}}
```

Read the file in binary mode from `start_offset`. Split only on completed newline bytes. Decode and parse each complete line; if the final bytes do not end in a newline, leave `safe_byte_offset` before the partial line. Validate integer token fields as nonnegative; malformed/unsupported rows increment an issue count without throwing away valid preceding rows.

Construct only `UsageEvent` fields explicitly listed in the contract. Use source fingerprint plus ordinal as event fingerprint material. No field from `turn_token_usage` or `thread_token_usage` may appear in the event.

- [ ] **Step 4: Run focused parser tests and lint**

Run:

```bash
uv run pytest tests/connectors/codex/test_parser.py -q
uv run ruff check backend tests
```

Expected: all parser tests pass and lint is clean.

- [ ] **Step 5: Commit Codex parsing**

```bash
git add backend/tokenhub/connectors/codex/parser.py fixtures/codex tests/connectors/codex/test_parser.py
git commit -m "feat: parse approved Codex usage records"
```

## Task 6: Approval-Gated Ingestion and Provider-Neutral Analytics

**Files:**

- Create: `backend/tokenhub/ingestion/__init__.py`
- Create: `backend/tokenhub/ingestion/service.py`
- Create: `backend/tokenhub/analytics/__init__.py`
- Create: `backend/tokenhub/analytics/service.py`
- Create: `tests/ingestion/test_service.py`
- Create: `tests/analytics/test_dashboard.py`

**Interfaces:**

- `IngestionService.approve(source_id: str) -> SourceRecord` validates path containment and moves the source to approved.
- `IngestionService.rescan(source_id: str) -> ImportOutcome` refuses unapproved/unsupported sources before opening a file.
- `IngestionService.rebuild() -> RebuildOutcome` clears TokenHub normalized events/cursors only, then rescans approved supported sources.
- `AnalyticsService.dashboard() -> DashboardSummary` returns workload, input, output, cache read, cache write, reasoning, event count, source freshness, and quality counts. It never derives cost or subscription data.

- [ ] **Step 1: Write failing behavior tests**

```python
def test_rescan_requires_approval_before_opening_source(app_services: Services) -> None:
    source = app_services.discovery.discover()[0].sources[0]
    with pytest.raises(SourceNotApprovedError):
        app_services.ingestion.rescan(source.source_id)


def test_idempotent_and_incremental_import(app_services: Services, fixture_session_file: Path) -> None:
    source = discover_and_approve_codex(app_services)
    first = app_services.ingestion.rescan(source.source_id)
    second = app_services.ingestion.rescan(source.source_id)
    append_synthetic_token_record(fixture_session_file, ordinal=2, input_tokens=10, output_tokens=5)
    third = app_services.ingestion.rescan(source.source_id)

    assert first.inserted_events == 1
    assert second.inserted_events == 0
    assert third.inserted_events == 1
    summary = app_services.analytics.dashboard()
    assert summary.workload_tokens == 140
    assert summary.cache_read_tokens == 70
    assert summary.reasoning_tokens == 20


def test_rebuild_matches_existing_totals(app_services: Services) -> None:
    source = discover_and_approve_codex(app_services)
    app_services.ingestion.rescan(source.source_id)
    before = app_services.analytics.dashboard()
    app_services.ingestion.rebuild()
    assert app_services.analytics.dashboard() == before
```

These tests catch silent privacy violations, duplicate reimports, cursor failure on append, breakdown double counting, and non-reproducible rebuilds.

- [ ] **Step 2: Run tests and verify expected missing-service failures**

Run: `uv run pytest tests/ingestion/test_service.py tests/analytics/test_dashboard.py -q`

Expected: collection fails because ingestion/analytics services are missing.

- [ ] **Step 3: Implement transactional ingestion and aggregates**

`approve` must resolve the descriptor path through `validate_source_path` and persist it only after that validation. `rescan` must consult state before parser import and call the Codex parser only for the `codex-local` connector. For a partial parser result, save valid events and the last safe cursor, then mark the source `PARTIAL` rather than `ERROR`.

`rebuild` must delete only TokenHub `usage_events` and `sync_cursors` inside a transaction; never touch a provider path. Re-read approved source rows, parser versions, and canonical paths from TokenHub storage. Aggregate SQL must use `SUM` and return `None` for unsupported/missing values, not false zeroes. Return cache and reasoning separately from workload.

- [ ] **Step 4: Run focused behavior tests, full backend tests, and lint**

Run:

```bash
uv run pytest tests/ingestion/test_service.py tests/analytics/test_dashboard.py -q
uv run pytest -q
uv run ruff check backend tests
```

Expected: idempotent second import inserts zero events, appended fixture adds one, rebuild returns exactly matching dashboard values, and the whole backend suite is green.

- [ ] **Step 5: Commit ingestion and analytics**

```bash
git add backend/tokenhub/ingestion backend/tokenhub/analytics tests/ingestion tests/analytics
git commit -m "feat: add approval-gated ingestion and dashboard analytics"
```

## Task 7: FastAPI App, Local Security, and CLI Startup

**Files:**

- Create: `backend/tokenhub/security/http.py`
- Create: `backend/tokenhub/api/__init__.py`
- Create: `backend/tokenhub/api/routes.py`
- Create: `backend/tokenhub/app.py`
- Modify: `backend/tokenhub/cli.py`
- Create: `tests/api/test_routes.py`
- Create: `tests/api/test_security.py`

**Interfaces:**

- `create_app(settings: TokenHubSettings | None = None) -> FastAPI` builds a no-side-effect app factory.
- Routes under `/api/v1`: `GET /status`, `GET /discovery`, `POST /sources/{source_id}/approve`, `POST /sources/{source_id}/rescan`, `GET /dashboard`, `GET /data-quality`, `POST /rebuild`.
- `require_loopback_host` accepts only `localhost`, `127.0.0.1`, and `[::1]` host forms with an optional port.
- `require_same_origin` rejects missing or mismatched origins on state-changing routes.
- `main()` starts Uvicorn with `host=settings.host` where the default is exactly `127.0.0.1` and `port=7432`.

- [ ] **Step 1: Write failing HTTP contract and security tests**

```python
def test_discovery_to_dashboard_api_flow(client: TestClient) -> None:
    discovery = client.get("/api/v1/discovery", headers={"host": "127.0.0.1:7432"})
    assert discovery.status_code == 200
    source_id = discovery.json()["providers"][1]["sources"][0]["source_id"]

    denied = client.post(f"/api/v1/sources/{source_id}/approve", headers={"host": "127.0.0.1:7432"})
    assert denied.status_code == 403

    approved = client.post(
        f"/api/v1/sources/{source_id}/approve",
        headers={"host": "127.0.0.1:7432", "origin": "http://127.0.0.1:7432"},
    )
    assert approved.status_code == 200


def test_rejects_untrusted_host(client: TestClient) -> None:
    response = client.get("/api/v1/status", headers={"host": "attacker.example"})
    assert response.status_code == 400


def test_discovery_response_never_contains_absolute_fixture_home(client: TestClient, tmp_path: Path) -> None:
    response = client.get("/api/v1/discovery", headers={"host": "127.0.0.1:7432"})
    assert str(tmp_path) not in response.text
```

The flow test catches missing approval protection. The host test catches DNS-rebinding-style Host acceptance. The final test catches path privacy regressions.

- [ ] **Step 2: Run tests and verify collection fails because app/routes are absent**

Run: `uv run pytest tests/api/test_routes.py tests/api/test_security.py -q`

Expected: import failure for `tokenhub.app` or route modules.

- [ ] **Step 3: Implement guarded app wiring and thin routes**

Use a dependency container initialized from `TokenHubSettings` and injected overrides in tests. `GET /discovery` can trigger presence discovery but must not parse source files. State-changing endpoints must reject before service mutation when Origin fails. Error responses and structured logging pass through `redact_sensitive`.

Map service exceptions to concise HTTP status codes: unapproved source `409`, unsupported source `422`, invalid source path `400`, missing source `404`, origin/host rejection `403`/`400`. Mount `frontend/dist` only when it exists; keep API tests independent of a frontend build. `main()` may call `webbrowser.open` only after Uvicorn startup is confirmed and only for the loopback URL.

- [ ] **Step 4: Run API tests, full backend suite, and direct app import**

Run:

```bash
uv run pytest tests/api -q
uv run pytest -q
uv run python -c "from tokenhub.app import create_app; assert create_app().routes"
uv run ruff check backend tests
```

Expected: all routes enforce security tests, all backend tests pass, app construction exits zero, and lint is clean.

- [ ] **Step 5: Commit API and local server**

```bash
git add backend/tokenhub/security/http.py backend/tokenhub/api backend/tokenhub/app.py backend/tokenhub/cli.py tests/api
git commit -m "feat: expose guarded local TokenHub API"
```

## Task 8: React Discovery and Dashboard Client

**Files:**

- Create: `frontend/package.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/types.ts`
- Create: `frontend/src/components/ProviderCard.tsx`
- Create: `frontend/src/components/MetricCard.tsx`
- Create: `frontend/src/styles.css`
- Create: `frontend/src/App.test.tsx`

**Interfaces:**

- `getDiscovery(): Promise<DiscoveryResponse>`, `approveSource(id: string): Promise<void>`, `rescanSource(id: string): Promise<ImportOutcome>`, and `getDashboard(): Promise<DashboardSummary>` call same-origin `/api/v1` routes.
- `ProviderCard` displays provider name, confidence/evidence, source state, support status, and an approval/rescan action only when the API says it is allowed.
- `MetricCard` renders an em dash for `null` values rather than `0`.

- [ ] **Step 1: Write the failing user-visible behavior tests**

```tsx
it("shows a detected Codex source and makes approval available", async () => {
  server.use(http.get("/api/v1/discovery", () => HttpResponse.json(discoveryFixture)));
  render(<App />);

  expect(await screen.findByRole("heading", { name: "Local sources" })).toBeInTheDocument();
  expect(screen.getByText("OpenAI Codex")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Approve source" })).toBeEnabled();
});


it("renders unknown metrics as an em dash instead of a false zero", async () => {
  server.use(http.get("/api/v1/dashboard", () => HttpResponse.json({ workload_tokens: null })));
  render(<App />);
  expect(await screen.findByText("—")).toBeInTheDocument();
});
```

The first test catches a first-run discovery flow regression. The second catches the product’s “never invent data” requirement.

- [ ] **Step 2: Run frontend tests and verify expected missing-project failure**

Run: `cd frontend && npm install && npm run test -- --run`

Expected: the command fails because `package.json` and source files do not exist.

- [ ] **Step 3: Implement a minimal accessible local UI**

Use Vite React TypeScript, Vitest, Testing Library, and Mock Service Worker for API-contract tests. The visual hierarchy is:

```text
TokenHub / Local usage observatory
Local sources: provider cards with evidence and approval state
Observed workload: metric cards for workload, input, output, cache read, reasoning
Data quality: a concise source-status list with partial/unsupported explanation
```

Use semantic buttons, headings, and status text. Do not show absolute source paths, parser raw records, credentials, hard-coded provider accounts, subscription data, pricing, or charts. For state changes, send an Origin header naturally through same-origin browser requests; tests may provide the header through the mocked browser environment.

- [ ] **Step 4: Run frontend tests and production build**

Run:

```bash
cd frontend && npm run test -- --run
cd frontend && npm run build
```

Expected: all tests pass and Vite writes `frontend/dist` without TypeScript errors.

- [ ] **Step 5: Commit the UI source (not generated artifacts)**

```bash
git add frontend/package.json frontend/package-lock.json frontend/tsconfig.json frontend/vite.config.ts frontend/index.html frontend/src
git commit -m "feat: add TokenHub discovery dashboard"
```

## Task 9: Full Integration Suite, Documentation, and Delivery Verification

**Files:**

- Create: `tests/integration/test_discovery_to_dashboard.py`
- Create: `tests/integration/test_security_boundaries.py`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-09-20-tokenhub-foundation-design.md` only to record verified deviations, if any.

**Interfaces:**

- Integration fixture must create a temporary fake home with synthetic `CODEX_HOME/sessions/.../rollout.jsonl`, fake `.claude`, and fake `.hermes/state.db`; neither non-Codex provider may be opened.
- End-to-end test must exercise live `create_app` with a temporary TokenHub database and actual TestClient requests, not mocked services.

- [ ] **Step 1: Write failing end-to-end and security-boundary tests**

```python
@pytest.mark.integration
def test_discovery_approval_import_append_and_rebuild(tmp_path: Path) -> None:
    app = create_app(test_settings_with_synthetic_codex_home(tmp_path))
    client = TestClient(app, base_url="http://127.0.0.1:7432")

    discovery = client.get("/api/v1/discovery", headers={"host": "127.0.0.1:7432"}).json()
    codex = find_provider(discovery, "codex")
    source_id = codex["sources"][0]["source_id"]

    client.post(
        f"/api/v1/sources/{source_id}/approve",
        headers={"host": "127.0.0.1:7432", "origin": "http://127.0.0.1:7432"},
    ).raise_for_status()
    client.post(
        f"/api/v1/sources/{source_id}/rescan",
        headers={"host": "127.0.0.1:7432", "origin": "http://127.0.0.1:7432"},
    ).raise_for_status()
    assert client.get("/api/v1/dashboard", headers={"host": "127.0.0.1:7432"}).json()["workload_tokens"] == 125

    append_synthetic_token_record(session_file=synthetic_session(tmp_path), ordinal=2, input_tokens=10, output_tokens=5)
    client.post(f"/api/v1/sources/{source_id}/rescan", headers=origin_headers()).raise_for_status()
    assert client.get("/api/v1/dashboard", headers={"host": "127.0.0.1:7432"}).json()["workload_tokens"] == 140

    client.post("/api/v1/rebuild", headers=origin_headers()).raise_for_status()
    assert client.get("/api/v1/dashboard", headers={"host": "127.0.0.1:7432"}).json()["workload_tokens"] == 140


@pytest.mark.integration
def test_claude_and_hermes_are_never_opened_during_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    claude_root = tmp_path / "home" / ".claude"
    hermes_db = tmp_path / "home" / ".hermes" / "state.db"
    claude_root.mkdir(parents=True)
    hermes_db.parent.mkdir(parents=True)
    hermes_db.write_bytes(b"SQLite format 3\\x00")
    opened: list[Path] = []
    real_open = Path.open

    def guarded_open(path: Path, *args: object, **kwargs: object):
        if path == hermes_db or claude_root in path.parents:
            opened.append(path)
            raise AssertionError("discovery attempted to open a discovery-only provider path")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    services = services_for_home(tmp_path / "home")
    services.discovery.discover()
    assert opened == []
```

The first test proves the product’s core flow under a real app stack. The second catches accidental data ingestion from discovery-only connectors.

- [ ] **Step 2: Run integration tests and verify they initially fail for the missing suite**

Run: `uv run pytest tests/integration -m integration -q`

Expected: the test path or integration helpers are missing before this task is implemented.

- [ ] **Step 3: Implement deterministic integration fixtures and update usage docs**

Create fixture helpers that make a fresh home/database per test. The e2e path must assert: generic pre-approval response has no temporary absolute path, approval works only with valid Origin, first import totals 125, repeat rescan adds zero, append adds 15 workload, partial line adds zero until completed, and rebuild returns 140.

Instrument Claude/Hermes candidate file opens with a test double at the narrow filesystem boundary; assert discovery reads neither contents nor SQLite databases. Add README sections for local installation, local binding, discovery/approval behavior, data that is intentionally never read, commands to run backend/frontend/integration tests, and the current connector-support limitation.

- [ ] **Step 4: Run full verification from a clean dependency state**

Run:

```bash
uv sync --all-groups
uv run pytest -q
uv run ruff check backend tests
uv run mypy backend
cd frontend && npm ci && npm run test -- --run && npm run build
uv run pytest tests/integration -m integration -q
git diff --check
git status --short
```

Expected: Python test suite, lint, type check, frontend test/build, integration suite, and whitespace check all exit zero; `git status --short` lists only intentional tracked source changes before commit.

- [ ] **Step 5: Commit verified integration coverage and documentation**

```bash
git add tests/integration README.md docs/superpowers/specs/2026-09-20-tokenhub-foundation-design.md
git commit -m "test: verify TokenHub discovery-to-dashboard flow"
```

## Plan Self-Review

### Spec coverage

| Spec requirement | Implementing task(s) |
|---|---|
| Local package, CLI, FastAPI, React/Vite | 1, 7, 8 |
| Canonical accounting and token semantics | 2, 5, 6 |
| SQLite + Alembic + transactional cursor | 3, 6 |
| Connector contract and failure isolation | 4 |
| Automated, approval-gated discovery | 4, 6, 7 |
| Codex complete first connector | 5, 6 |
| No Claude/Hermes import | 4, 9 |
| Host/origin/path/redaction security | 2, 7, 9 |
| First-run UI and unknown display behavior | 8 |
| Unit/API/frontend/integration proof | 1–9, especially 9 |

### Placeholder scan

The plan contains no deferred implementation placeholders. The unsupported Claude/Hermes parsing decision is an explicit product-scope boundary, not a missing task.

### Type consistency

The sequence produces domain types before persistence, connector contract before parser, persistence/parser before ingestion, services before HTTP, API contracts before UI, and full integration verification last. Source approval and scan methods retain the exact `source_id: str` shape across Tasks 3–9.
