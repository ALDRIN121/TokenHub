# TokenHub

A local-first usage observatory. TokenHub detects the coding agents installed on
this machine, imports usage from sources you explicitly approve, and
shows agent, model, and session usage in a local explorer. Nothing leaves the machine: no provider
API is called, no provider executable is launched, and only usage metadata is retained. Credentials and provider accounts are never
read; transcript content is never stored or shown. Approved session files can contain chat text, which the parsers discard.

Supported sources: **OpenAI Codex** session usage, **Claude Code** project and
subagent session logs, **Hermes Agent** session counters in its local state
database, **VS Code Copilot Chat** saved sessions, and **Antigravity** conversation
databases.

For dashboard navigation, sessions, sync, themes and accuracy, see the [dashboard guide](https://github.com/ALDRIN121/TokenHub/blob/main/docs/dashboard-guide.md).

## Install and run

Requires Python 3.12 or newer. Install from [PyPI](https://pypi.org/project/tokenhub/):

```bash
pip install tokenhub
tokenhub                     # starts in the background and opens the local dashboard
tokenhub status              # prints its local URL
tokenhub open                # opens an already running dashboard
tokenhub stop                # stops this TokenHub instance
tokenhub --version           # prints the installed version
```

`tokenhub start --no-open` starts without opening a browser. Use
`tokenhub start --port 9000` to choose another loopback port. A second start
reuses the running instance, including its chosen port. The server continues
after the terminal closes; run `tokenhub` again after a reboot or login. Logs
and local data are stored in `~/.tokenhub/`, with startup details in
`~/.tokenhub/runtime.log`.

For development from this checkout, run `uv sync --all-groups` and
`uv run tokenhub --no-open`.

TokenHub binds to `127.0.0.1:7432` by default and prints that URL when it
starts. It is loopback-only by design:

- the bind host is validated at startup — `0.0.0.0`, `::`, or any non-loopback
  value raises rather than serving;
- every request must carry a loopback `Host` header (`localhost`, `127.0.0.1`,
  `[::1]`, with the configured port), so a DNS-rebinding page cannot reach the
  API even from the local browser;
- state-changing requests (`approve`, `rescan`, `rebuild`) must also carry the
  request's own origin; otherwise they are refused with `403`;
- no CORS headers are ever added, and `X-Forwarded-*` headers are ignored.

The UI is bundled with the Python package. To refresh it while developing:

```bash
npm --prefix frontend ci
npm --prefix frontend run build              # writes backend/tokenhub/web
```

The packaged UI is mounted at `/`; the API also works if the built UI is absent.

## Automatic collection

While TokenHub is running, it scans approved sources from all five providers at startup and every
30 seconds. Every 10 seconds the explorer checks a small change counter
(`data_version` in `GET /api/v1/collection`) and reloads only when it moved. **Refresh data** in the header sync drawer scans
approved sources immediately and shows the last scan time. Approving a provider
in the UI also starts its first import immediately. Unchanged session files are
skipped. Hermes and Antigravity databases are checked on each scan
because active usage can live in their SQLite journal. Repeated scans do not
increase totals. Automatic scans of unchanged data do not create extra
import-history rows, including after restarting TokenHub.

Choose **Approve and import** on a provider card to approve its discovered
usage folder once and include both existing and future sessions automatically.
This consent survives restarts and is pinned to the folder's device and inode;
replacing the folder or redirecting it through a symlink cannot authorize another
location. **Stop including new sessions** removes that folder consent; previously
approved files keep updating. Without folder consent, new files require approval.

`GET /api/v1/collection` reports the scan interval, folder-consent state, last
completed scan, and failed-source count. Same-origin `POST` requests to
`/api/v1/collection/{provider}/enable` and `/disable` change consent (`codex`,
`claude_code`, `hermes`, `vscode_copilot`, or `antigravity`). Responses contain
no private paths. Failed sources are retried without preventing other sources
from updating, and collection runs even when the dashboard is closed.

For Codex, only per-response `token_usage_record.payload.usage` values contribute
to totals.
Known session messages are skipped. Cumulative `token_count` snapshots are never
added to totals, to avoid double counting; when a session also has per-response
usage they are skipped as redundant. A session with snapshots but no per-response
usage, malformed records, and unknown usage structures remain visible as
unsupported records. Workload is
input plus output, with cached input and reasoning already included in those
counts. Token counts are not billing amounts. The UI uses K/M/B abbreviations
and shows the exact count on hover.

Claude Code reads `message.usage` from assistant records under its `projects`
folder. Repeated streaming chunks are consolidated by message ID, copied history in
resumed/forked session files is counted once, and later usage updates replace
the session's previous normalized records. Hermes reads
only allowlisted session identifiers, timestamps, and token counters from a
no-follow snapshot of `state.db` and its journal; no provider database is written.
Each Hermes session contributes once, with running totals replaced atomically as
they change. Cache reads and writes are included in the input total for Claude
and Hermes because both store them in separate buckets. Hermes session
contributions are marked `high` quality; Claude message usage is marked `exact`.

VS Code Copilot Chat imports saved `chatSessions/*.jsonl` requests from VS Code's
workspace storage. It records each Copilot request's model, session, input tokens,
and output tokens when VS Code saved those counters. Later session patches replace
earlier counters; unchanged files do not add usage again. Requests without token
counters remain unsupported rather than receiving estimated values. Cache and
reasoning counters are not available in these saved requests. Inline suggestions,
Copilot CLI, and Copilot activity in other editors are outside this source's scope.
`VSCODE_USER_DATA_DIR` can point to a different VS Code user-data directory,
such as an Insiders installation.

Antigravity imports per-generation model, timestamp, input, cache-read, output,
and reasoning counters from approved `~/.gemini/antigravity/conversations/*.db`
files. It reads a snapshot of the database and its active journal without writing
to the provider database. Antigravity's local metadata format is private, so
unrecognized or inconsistent generations are excluded and reported as partial
instead of estimated. Only `conversations/*.db` files are read. The desktop app's `.pb` conversation files
appear to be encrypted with a key held by the operating system; TokenHub never reads
that key, so it says so instead of showing an empty source. `ANTIGRAVITY_HOME` can point to another Antigravity root.

## Where TokenHub looks

Each agent keeps a fixed folder layout (`.claude`, `.codex`, `.hermes`,
`.gemini/antigravity`, VS Code's `Code/User`), but the folder it sits under can
differ, for example on Windows when a profile lives on another drive or is
redirected. TokenHub checks, in order: the agent's environment override
(`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `HERMES_HOME`, `ANTIGRAVITY_HOME`,
`VSCODE_USER_DATA_DIR`), your home directory, `USERPROFILE`, `APPDATA` and
`LOCALAPPDATA` (Windows) or `XDG_CONFIG_HOME` and `XDG_DATA_HOME` (Linux), and on
Windows `<drive>:\Users\<you>` for each drive present. A location that already
holds session data wins over an empty folder at home. Only your own locations are
checked, nothing is crawled, and symlinked folders are never followed. Overrides
accept `~/`, `~\` and environment variables such as `%USERPROFILE%`. Anything
found this way still needs your approval before it is imported.

## Discovery and approval

`GET /api/v1/discovery` reports each provider's display name, connection state,
a confidence level derived from its evidence (`high` when two or more
independent signals were found, `medium` for one, `low` for none), the evidence
codes it found (`executable_on_path`, `known_root_exists`, `configuration_found`,
`session_source_found`, `state_database_found`), and a stable path fingerprint.
Discovery reads **presence only** — it stats candidate roots and directories and
never opens a provider file.

The provider card combines approval and first import in one click. It also
enables future session imports for that provider. The lower-level API remains
available for one-source troubleshooting:

1. `POST /api/v1/sources/{source_id}/approve` persists the canonical path for
   that one source. Before approval, responses contain no absolute path at all.
2. `POST /api/v1/sources/{source_id}/rescan` imports new records through the
   connector's parser. Repeating a rescan over unchanged bytes inserts nothing;
   `POST /api/v1/rebuild` re-derives all normalized events from scratch and
   leaves totals unchanged.

`GET /api/v1/dashboard` and `GET /api/v1/data-quality` report observed usage.
Workload is input total plus output total; cache and reasoning are breakdowns,
never extra usage. A metric the data cannot support is returned as `null` and
rendered as an em dash — TokenHub never substitutes `0` for "unknown".

The **Overview** combines a compact token summary with all five agent rankings.
Choose an agent and a local date range, then scroll to its sessions or use the
header **Sessions** or **Models** links. Both tabs share one analysis workspace
and retain independent search, sorting and pagination. Click a model to see its
session contributions, or expand a session for its model counters. Exact counts
are available on hover. **Sources** contains source setup, health and import
quality; the header sync drawer contains operational progress and refresh.
The header theme switch saves your light/dark preference locally.

**Usage trends** shares the explorer's date, agent, and selected-model filters.
Switch between daily totals and Monday-based weeks, and select total, input,
output, cache, or reasoning tokens. Hover or focus a chart bar for exact counts,
or open **View chart data**. **Compare agents** and **Compare models** show the
same recorded counters by group; model comparisons use pages of 25 and include
models used only in the previous period and models with missing names.

Finite date ranges compare with the immediately preceding period of the same
number of local calendar days. Weeks at either edge are clipped to the selected
dates; each previous chart interval is shifted back by that calendar-day count.
All-time totals have no previous-period baseline. Comparisons for missing or
incomplete counters, and percentages against zero usage, are shown as unavailable.
Ranges including today are marked as still in progress. Higher/lower usage is
descriptive, not a claim of better/worse efficiency.

`GET /api/v1/usage/trends` accepts `from`, `to`, `time_zone` (an IANA zone, default
UTC), `granularity=day|week`, `provider`, `model` or `unknown_model=true`, and
`dimension=providers|models` with bounded `offset`/`limit`. Date bounds must
include both local calendar-day boundaries. The same globally canonical delta
events drive period totals, buckets and group comparisons. Daily/weekly charts
are limited to 366/260 points; longer histories keep exact totals and request a
shorter date range. Cached responses refresh when imported data changes.

`GET /api/v1/usage` returns canonical totals plus agent, model, and session
breakdowns. Session identifiers are opaque, stable hashes; original session IDs
and source paths are not exposed. Claude message IDs and Codex response IDs
deduplicate copied records across approved files throughout the explorer.
Optional timezone-aware `from` and `to` query bounds filter usage by recorded
event time; the upper bound is exclusive. Known older parsers re-read approved
sources on startup. Revised records reconcile their counters, metadata, and
dates, and refresh cached totals and trends.

Codex discovery includes active and archived sessions. Each folder has its own
approval; enabling automatic import grants the currently discovered folders.
If a folder's filesystem identity changes, **Reconnect and import** explicitly
renews its approval. Existing imported history remains visible while a source
is unavailable. The token summary reports collection freshness and coverage
limitations. Hermes session totals are attributed to the session's end date
(or start date while active), so daily and weekly totals cannot resolve when
individual tokens were used within that session.

## Data boundaries

- Only usage metadata is normalized. Prompts, messages, tool output, transcript
  bodies, and raw provider records are never retained or returned by the API.
- Provider credentials, API keys, `auth.json`, keychain, and cookie stores are
  never read. Approved session JSONL can contain chat text; only allowlisted
  usage fields are normalized, and raw content is never stored or returned.
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
uv build
python scripts/verify_distribution.py dist/*  # tests installed wheel and source archive
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

- **Local schemas only.** Unknown usage structures and malformed records are
  reported as partial or unavailable. Hermes needs a `sessions` table containing
  session IDs, start timestamps, and input/output counters. An unsupported or
  unreadable database leaves previously imported usage intact.
- **No cost or pricing.** TokenHub reports token counts only — no currency, no
  provider plan data.
- **Model attribution depends on recorded metadata.** Missing model names appear
  in “Model not recorded”. Hermes exposes session totals with a reported model;
  model switches within that session cannot be split by its current counters.
- **Recorded-date trends.** Charts use stored observation timestamps. Session
  totals, including Hermes, appear on the reported session end/start date;
  TokenHub does not estimate how a session's tokens were used across days.
  Distinct session/model counts cannot be added across buckets because the same
  session/model may occur on multiple dates. Token and event sums do reconcile.
