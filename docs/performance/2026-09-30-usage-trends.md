# Usage trends and comparisons

Implemented on `codex/usage-trends-comparisons` as an extension of the existing usage explorer.

## Behavior

- Daily and Monday-based weekly charts use the explorer's date, provider and selected-model filters. Total, input, output, cache-read/write and reasoning metrics switch locally without new requests.
- Finite ranges compare with the immediately preceding period of equal local calendar-day length. Boundary weeks are clipped; previous chart intervals shift back by the selected number of calendar days. Date labels expose both intervals. Local IANA zones handle DST, fractional offsets and midnight transitions.
- All-time displays the recorded history without an invented previous baseline. Every stored observation requires a timestamp; token/event sums across chart buckets and provider/model groups use the dashboard's globally canonical delta records. Distinct sessions/models are not additive across dates.
- Previous-only models remain visible, unknown models have an explicit label, and identical names on different providers remain separate groups. Comparisons replace pages of 25 rows. A rebuild that shrinks the result automatically recovers to a valid page.
- Missing counters remain null. Reported-counter counts distinguish empty periods from incomplete data; incomplete comparisons and percentages against a zero baseline stay unavailable. Today-containing periods are marked as in progress, and session-reported counters stay on their recorded date.
- Exact values are available on keyboard focus/hover and through an optional chart data table. The mobile chart uses the actual panel width so text remains readable. No plotting dependency or external service is added.

## Performance

Synthetic five-provider histories measured on the development Mac, a 30-day range, 80 provider/model groups and a 25-row model comparison page. Five cached samples follow each first request. The workload uses generated files only; this is not a certification of an older 8 GB laptop.

| Events | Uncached request | Cached median | Response bytes |
|---:|---:|---:|---:|
| 10,000 | 0.230 s | 0.001 s | 67,767 |
| 50,000 | 1.003 s | 0.001 s | 68,000 |

SQL aggregation avoids loading event objects. The deterministic budget allows at most seven SELECT/WITH statements, 25 comparison rows and 200 KB for the 10,000-event monthly response; cached reads and unchanged idle scans perform no trend SQL. Charts allow at most 366 daily or 260 weekly points. Exceeding that limit retains exact period totals and asks for a shorter range instead of silently dropping records. Responses share the existing 32-entry/16 MB cache, invalidated by collection data_version.

Machine-readable measurements: [2026-09-30-trends-benchmarks.json](2026-09-30-trends-benchmarks.json). Reproduce trend measurements alongside import benchmarks with `.venv/bin/python scripts/benchmark_performance.py --provider all --events 1000 10000 50000`.

## Verification

532 backend tests passed, with two platform-specific skips; all 53 UI tests passed. Lint, Python/TypeScript checks and the production build passed. Regression coverage includes canonical copied-message selection before filters, token reconciliation, clipped Monday weeks, spring/fall DST, a zone with a midnight transition, fractional offsets, prior-only/unknown/provider-separated models, missing counters, zero baselines, bounds/timezone validation, stale reads, retry, page recovery and deterministic query/payload budgets.

Both wheel and source archive passed clean-install, migration, local server and bundled-asset smoke checks. The smoke server forces the packaged tzdata fallback and successfully serves a local calendar trend request, exercising the Windows timezone-data dependency even on the development Mac.

Real browser checks used 6,000 generated observations across five providers at 1280×900 and 390×844. Chart totals matched the displayed dashboard; 80 model groups paginated in 25-row replacements; no horizontal page overflow or read errors occurred. Screenshots: [desktop](screenshots/trends-desktop.png), [mobile](screenshots/trends-mobile.png).

## Decisions and limits

Work stays in the existing checkout on a feature branch to preserve installed dependencies; main and its remote are unchanged by this feature task. The explicit implementation request supplied scope, so routine defaults were documented without another approval round. The date-only request regression now permits trend analytics while continuing to forbid metadata reloads.

All-time has no previous comparison; select a finite date range to enable it. Long histories may need weekly grouping or shorter dates to chart. Rejected source records remain absent from both dashboard and trends under the existing timestamp/accounting contract. Charts describe recorded token volume, not inferred efficiency or spending. Actual performance on the user's lower-end laptop remains a manual check.
