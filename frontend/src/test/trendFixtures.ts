import { usageFixture } from './fixtures';

export const trendMetrics = ['workload_tokens', 'input_total_tokens', 'output_total_tokens', 'cache_read_tokens', 'cache_write_tokens', 'reasoning_tokens'] as const;
export const trendCurrent = { ...usageFixture.totals, workload_tokens: 150, input_total_tokens: 100, output_total_tokens: 50, event_count: 1,
  cache_write_tokens: null, reasoning_tokens: null,
  reported_counts: { workload_tokens: 1, input_total_tokens: 1, output_total_tokens: 1, cache_read_tokens: 1, cache_write_tokens: 0, reasoning_tokens: 0 } };
export const trendPrevious = { ...trendCurrent, workload_tokens: 100, input_total_tokens: 80, output_total_tokens: 20 };
export const trendComparison = {
  workload_tokens: { difference: 50, percent_change: 50, state: 'compared' },
  input_total_tokens: { difference: 20, percent_change: 25, state: 'compared' },
  output_total_tokens: { difference: 30, percent_change: 150, state: 'compared' },
  cache_read_tokens: { difference: 0, percent_change: 0, state: 'compared' },
  cache_write_tokens: { difference: null, percent_change: null, state: 'incomplete' },
  reasoning_tokens: { difference: null, percent_change: null, state: 'incomplete' },
};
export const trendFixture = {
  period: { from: '2026-09-27T00:00:00+00:00', to: '2026-09-28T00:00:00+00:00', previous_from: '2026-09-26T00:00:00+00:00', previous_to: '2026-09-27T00:00:00+00:00', days: 1, time_zone: 'UTC', granularity: 'day', all_time: false, includes_today: false },
  current: trendCurrent, previous: trendPrevious, comparison: trendComparison,
  buckets: [{ from: '2026-09-27', to: '2026-09-28', previous_from: '2026-09-26', previous_to: '2026-09-27', current: trendCurrent, previous: trendPrevious }],
  series_limited: false,
  breakdown: { dimension: 'providers', items: [{ provider: 'codex', model_name: null, current: trendCurrent, previous: trendPrevious, comparison: trendComparison }], total: 1, offset: 0, limit: 25 },
  data_version: 1,
};
