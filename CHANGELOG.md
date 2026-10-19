# Changelog

## 0.1.8 - 2026-10-19

The usage overview estimates USD cost per model and in total from an editable
`~/.tokenhub/pricing.json`, seeded with bundled per-million-token rates. Cache
read and write counters are priced separately, aliases and dated model names
resolve to their base entry, and provider-prefixed and OpenRouter-style names
fall back to the bare model with per-route overrides. Models without a published
rate remain explicitly unpriced instead of being guessed. `tokenhub pricing`
prints the active file. Analytics reads now scan a materialized canonical
projection that ingest refreshes for the identities it touched, so the
`row_number()` dedupe window no longer runs on every request; concurrent polls
share one build and unchanged API reads return `304`. At 300,000 events the
dashboard median drops from 2,738 ms to 140 ms and trends from 12,600 ms to
4,003 ms, in exchange for 108 MB → 189 MB of local storage. See the
[release notes](docs/releases/0.1.8.md).

## 0.1.7 - 2026-10-19

TokenHub installs and runs on Python 3.10 and newer. Timezone handling, string enums and ISO-8601 parsing use compatibility shims where the standard library only gained the behavior in 3.11, so existing connector files parse unchanged on every supported version. The package metadata now declares `Requires-Python >=3.10`, and CI verifies both the 3.10 floor and 3.12.

## 0.1.6 - 2026-10-18

The dashboard has a refreshed top navigation layout, coordinated light/dark themes, a stacked agent-usage chart, clearer agent shares and compact session previews. Agent selection now includes bundled icons and full keyboard navigation. Custom inclusive date ranges, calendar-month grouping, visible plotted dates and sparse-data guidance make trends easier to interpret. Chart tooltips no longer move surrounding content, and the header shows the age of the last completed scan. Session rows preview recorded model names; model drilldowns correctly retain full-session totals. Subtle transitions respect reduced-motion preferences. See the [dashboard guide](docs/dashboard-guide.md) and [release notes](docs/releases/0.1.6.md).

## 0.1.5

The usage-first dashboard adds a compact summary, authentic bundled agent icons, continuous models/sessions navigation, persistent agent scope, independent table contexts, and a header theme switch. Sync details move to an accessible drawer while source setup stays in Sources. Valid usage remains available during metadata failures; changing scope hides obsolete counters and rows. See [dashboard guide](docs/dashboard-guide.md) and [release notes](docs/releases/0.1.5.md).

## 0.1.4

Improved token accuracy guidance, source-consent recovery and connector normalization. Added checks for trend reconciliation, packaged time-zone data and performance budgets.

## 0.1.3 - 2026-09-29

### Fixed
- Antigravity generations with no cached or thinking tokens were rejected as
  "a usage counter is missing", so a conversation's first short messages could
  import nothing. A counter the writer omitted (protobuf leaves out zeros) now
  counts as 0; output totals must still reconcile, and messages without any input
  or output counter remain unsupported.

### Changed
- Antigravity's card now says when its saved conversations are in a format TokenHub
  cannot read (the desktop app's encrypted `.pb` files) instead of "No local source
  was found".

## 0.1.2 - 2026-09-29

### Fixed
- Session files that a provider deleted (for example Claude Code's own cleanup)
  are now reported as `source_missing` instead of `error`. They no longer count
  as failed sources or get retried every scan, and their imported usage is kept.
- Codex `token_count` snapshots that repeat per-response usage no longer mark
  every session `partial`. A session with snapshots but no per-response usage is
  still reported as unsupported. Codex parser version is now `codex-jsonl-v5`.

### Changed
- The dashboard polls `data_version` and reloads only when it changed, and
  `GET /api/v1/discovery` is served from the last discovery pass until then.
  Each provider folder is walked once per pass and unchanged sources are not
  rewritten to the database.
- Unchanged sources are skipped after a restart, and unchanged Hermes and
  Antigravity databases are no longer copied every scan.
- `tokenhub stop` no longer waits for a whole scan to finish.
- Dropped the unused `uvicorn[standard]` extras (uvloop, httptools, websockets,
  watchfiles, PyYAML, python-dotenv).

### Added
- The data-quality panel explains that some sessions record only a combined total
  with no input/output split, which TokenHub cannot count and never guesses.
- `tokenhub --version`.
- `data_version` in `GET /api/v1/collection`.
