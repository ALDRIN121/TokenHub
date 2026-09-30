# Usage trends and comparisons

User-authorized implementation: daily/weekly charts, previous-period comparisons, and provider/model breakdowns that reconcile with existing dashboard accounting. Extend the existing usage explorer; retain local-only operation, nullable counters, Claude canonical-message selection, background imports, and bounded responses.

## Design and decisions

- Share the explorer's date/provider/selected-model filters. Every stored observation already requires a timestamp, so all-time chart sums reconcile directly with its totals. All-time has no meaningful previous-period baseline; selecting a finite range enables comparison.
- Interpret daily/weekly buckets in the browser's IANA time zone. Weeks start Monday and boundary weeks are clipped to the selected range. Previous periods use the same number of calendar days, including across daylight-saving changes. Each previous chart interval is the current interval shifted back by that many days.
- Use SQL aggregates over canonical delta events in one read snapshot. Return totals, reported-counter counts, paired buckets, and a bounded comparison breakdown. Unknown counters stay null; no records and missing counters remain distinguishable. Partial/missing counters and zero baselines never generate misleading percentages.
- Show at most 366 daily or 260 weekly buckets; longer all-time histories retain their totals and ask for a shorter range. Model comparisons use 25-row pages, including previous-only and unknown model groups. Provider comparisons are bounded by the supported provider set.
- Use a small native SVG chart and the existing visual tokens, with exact-count keyboard/hover details and an optional data table. No chart dependency, animation or network service. Include partial-period and provider attribution notes.
- Add tzdata for Windows IANA time zones, consistent with Python's zoneinfo documentation: https://docs.python.org/3.12/library/zoneinfo.html#data-sources.

## Task 1: Canonical trend analytics and API

Files: new `backend/tokenhub/analytics/trends.py`, existing analytics service, SQLite connection functions, API routes, pyproject/lock, new analytics/API tests.

Write and observe failing tests for reconciliation, daily/weekly bounds, previous periods, DST and fractional offsets, unknown/partial counters, previous-only/unknown/provider-separated model groups, copied Claude history outside dates, validation and bounded output. Implement calendar helpers, SQL summaries/buckets/comparison paging, and cached `/api/v1/usage/trends`. Verify focused and full backend tests, lint and types.

## Task 2: Chart and comparison UI

Files: new `frontend/src/components/UsageTrends.tsx` and tests, types/client, explorer integration, styles and test handlers/fixtures.

Write and observe failing interaction tests for chart granularity/metric changes, provider/model/date propagation, comparison labels/zero/unknown/partial states, model paging, stale responses and error/retry. Implement a responsive chart, period labels, paired totals, provider/model comparisons and optional chart data table. Keep read abort/retry and data-version refresh independent of collection. Verify the full UI suite and production build.

## Task 3: Verification and delivery

Add deterministic scale/query/payload/cache checks for trends. Run full backend/UI tests, lint/type checks, build, and wheel/source-archive clean-install smoke tests. Check actual rendered desktop/mobile UI if available, using generated local data only. Update README and report with semantics, performance and material limitations. Obtain one independent final review; resolve material findings with failing regressions, then commit. A new push to main is not requested by this feature instruction.

## Review focus

- Canonical message selection must precede both period and model filters.
- Bucket sums and provider/model token sums must reconcile with period totals; distinct sessions/models are not additive across buckets.
- Date ranges and previous buckets must handle DST, half-hour zones, Monday partial weeks and exclusive upper bounds.
- Partial/missing counters, empty periods and zero baselines must remain explicit; keep the existing required-timestamp import contract.
- SQL work, chart points, comparison rows and cache entries remain bounded as imported history grows; browsing must retain independent read snapshots.
