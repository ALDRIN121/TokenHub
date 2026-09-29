# Changelog

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
