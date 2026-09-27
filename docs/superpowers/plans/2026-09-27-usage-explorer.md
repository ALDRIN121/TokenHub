# Agent and model usage explorer

The default view will compare imported usage by agent, rank models within each agent, and allow drilling into sessions. The existing consolidated overview, source approvals, and quality controls stay accessible. All totals derive from the same deduplicated observations as the overview. Cache and reasoning are subsets, never additional workload. Missing model metadata stays explicitly unknown. Hermes model attribution is session-reported because its counters do not resolve model switches inside a session.

Implementation:
1. Add regression coverage for model/session extraction, incremental Codex model context, metadata backfill, deduplication, and grouped totals.
2. Add nullable model/session metadata, migrate without losing data, and upgrade approved parser versions for automatic backfill.
3. Add a path-free usage API with provider/model/session aggregation, retaining null counters and opaque session identifiers.
4. Build the default usage explorer with agent selectors, ranked model table, search/sort, token columns, and session drilldown. Preserve the overview as a separate view.
5. Test backend and frontend, build, review, restart the local app, and verify the desktop and narrow layouts with real imported data.
