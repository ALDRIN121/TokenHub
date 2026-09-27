# TokenHub

A local-first usage observatory. TokenHub detects the coding agents installed on
this machine, imports usage from the one source you explicitly approve, and
shows the totals on a local dashboard. Nothing leaves the machine: no provider
API is called, no provider executable is launched, and no prompt, transcript,
credential, or provider account is ever read.

Currently supported: **OpenAI Codex** is the one importable source. **Claude
Code** and **Hermes Agent** are detection-only in this milestone.

## Install and run

```bash
uv sync --all-groups          # Python 3.12+, uv
uv run tokenhub               # serves the API and the built UI
```

TokenHub binds to `127.0.0.1:7432` and prints that URL on startup. It is
loopback-only by design:

- the bind host is validated at startup — `0.0.0.0`, `::`, or any non-loopback
  value raises rather than serving;
- every request must carry a loopback `Host` header (`localhost`, `127.0.0.1`,
  `[::1]`, with the configured port), so a DNS-rebinding page cannot reach the
  API even from the local browser;
- state-changing requests (`approve`, `rescan`, `rebuild`) must also carry the
  request's own origin; otherwise they are refused with `403`;
- no CORS headers are ever added, and `X-Forwarded-*` headers are ignored.

To serve the UI from the same process, build it first:

```bash
cd frontend && npm ci && npm run build      # writes frontend/dist
```

`frontend/dist` is mounted at `/` when it exists; the API works without it.

## Discovery and approval

`GET /api/v1/discovery` reports each provider's display name, connection state,
a confidence level derived from its evidence (`high` when two or more
independent signals were found, `medium` for one, `low` for none), the evidence
codes it found (`executable_on_path`, `known_root_exists`, `configuration_found`,
`session_source_found`, `state_database_found`), and a stable path fingerprint.
Discovery reads **presence only** — it stats candidate roots and directories and
never opens a provider file.

Reading telemetry requires two explicit steps:

1. `POST /api/v1/sources/{source_id}/approve` persists the canonical path for
   that one source. Before approval, responses contain no absolute path at all.
2. `POST /api/v1/sources/{source_id}/rescan` imports new records through the
   connector's parser. Repeating a rescan over unchanged bytes inserts nothing;
   `POST /api/v1/rebuild` re-derives all normalized events from scratch and
   leaves totals unchanged.

`GET /api/v1/dashboard` and `GET /api/v1/data-quality` report observed deltas.
Workload is input total plus output total; cache and reasoning are breakdowns,
never extra usage. A metric the data cannot support is returned as `null` and
rendered as an em dash — TokenHub never substitutes `0` for "unknown".

## Data that is intentionally never read

- Prompts, messages, tool output, transcript bodies, and raw provider records.
- Provider credentials, API keys, `auth.json`, keychain, and cookie stores.
- Claude Code and Hermes Agent telemetry. Their connectors expose presence and
  a discovery-only source entry; `scan` returns `unsupported` for them, and the
  ingestion service refuses any connector that is not `codex-local`.
- Anything outside an approved source's own approved root: canonical-path
  validation rejects symlink escapes, and the approved root is re-validated by
  device/inode at every scan.

## Tests

```bash
uv run pytest -q                              # unit + API
uv run pytest tests/integration -m integration -q   # real app stack, synthetic homes
uv run ruff check backend tests
uv run mypy backend
cd frontend && npm run test -- --run && npm run build
```

Every test is offline and deterministic: synthetic fixtures in a temp directory,
no real home directory, no network, no provider subprocess. A test that points
at a real provider path or opens a real credential store is a bug.

## Layout

| Path | Responsibility |
|---|---|
| `backend/tokenhub/domain/` | provider-neutral enums and data models |
| `backend/tokenhub/security/` | path containment, redaction, Host/Origin policy |
| `backend/tokenhub/connectors/` | one module per provider behind a shared contract |
| `backend/tokenhub/discovery/` | presence discovery and the pre-approval handoff |
| `backend/tokenhub/ingestion/` | approval gate, incremental scan, rebuild |
| `backend/tokenhub/database/` | SQLAlchemy models, repositories, Alembic migrations |
| `backend/tokenhub/analytics/` | dashboard aggregates over observed deltas |
| `backend/tokenhub/api/` | versioned `/api/v1` routes and the app container |
| `frontend/` | React + TypeScript + Vite client (Vitest, MSW) |

## Current limitations

- **Only Codex can be imported.** Claude Code and Hermes Agent are detected but
  their telemetry is never read; a schema-specific, metadata-only connector must
  be designed before either becomes importable.
- **No cost or pricing.** TokenHub reports token counts only — no currency, no
  provider plan data.
- **No per-model, per-session, or time-series views** in this milestone: totals,
  breakdowns, and data quality only.
