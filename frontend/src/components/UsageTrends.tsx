import { useEffect, useId, useMemo, useState } from 'react';
import { getUsageTrends } from '../api/client';
import type { TrendChange, TrendMetric, TrendTotals, UsageTrendsResponse } from '../types';
import { formatMetric } from './MetricCard';
import { agentName, modelName } from './usageLabels';
import { UsageChart } from './UsageChart';
import { Icon } from './Icon';

const metrics: [TrendMetric, string][] = [
  ['workload_tokens', 'Total tokens'], ['input_total_tokens', 'Input tokens'], ['output_total_tokens', 'Output tokens'],
  ['cache_read_tokens', 'Cache read'], ['cache_write_tokens', 'Cache write'], ['reasoning_tokens', 'Reasoning'],
];
const fullValue = (totals: TrendTotals | null, metric: TrendMetric) => !totals ? '—' : totals.event_count === 0 ? 'No records' : totals[metric] === null ? 'Not reported' : totals[metric]!.toLocaleString('en-US');
const partial = (totals: TrendTotals, metric: TrendMetric) => totals.reported_counts[metric] < totals.event_count;

function changeLabel(change: TrendChange): string {
  if (change.state === 'incomplete') return 'Comparison unavailable: incomplete counters';
  if (change.state === 'no_baseline') return 'No previous usage';
  if (change.percent_change === null) return '—';
  return change.percent_change === 0 ? 'No change' : `${Math.abs(change.percent_change).toFixed(1)}% ${change.percent_change > 0 ? 'more' : 'less'}`;
}

function periodLabel(from: string | null, to: string | null, timeZone: string): string {
  if (!from || !to) return 'No recorded dates';
  const format = new Intl.DateTimeFormat('en', { month: 'short', day: 'numeric', year: 'numeric', timeZone });
  return `${format.format(new Date(from))} – ${format.format(new Date(new Date(to).getTime() - 1))}`;
}

function previousDate(value: string): string { return new Date(new Date(`${value}T12:00:00Z`).getTime() - 86400000).toISOString().slice(0, 10); }

export function UsageTrends({ range, provider, model, unknownModel = false, version, compact = false, isLoading = false }: {
  compact?: boolean; isLoading?: boolean;
  range?: { from: string; to: string }; provider?: string; model?: string; unknownModel?: boolean; version?: number;
}) {
  const heading = useId();
  const [granularity, setGranularity] = useState<'day' | 'week' | 'month'>(range ? 'day' : 'week');
  const [metric, setMetric] = useState<TrendMetric>('workload_tokens');
  const [dimension, setDimension] = useState<'providers' | 'models'>('providers');
  const [offset, setOffset] = useState(0);
  const [result, setResult] = useState<{ key: string; data: UsageTrendsResponse } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  const [showData, setShowData] = useState(false);
  const from = range?.from, to = range?.to;
  useEffect(() => {
    const days = from && to ? (Date.parse(to) - Date.parse(from)) / 86400000 : null;
    setGranularity(days !== null && days <= 31 ? 'day' : days !== null && days > 366 ? 'month' : 'week');
  }, [from, to]);
  const options = useMemo(() => ({ ...(from && to ? { from, to } : {}),
    time_zone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC', granularity, dimension, offset: String(offset),
    ...(provider ? { provider } : {}), ...(model !== undefined ? { model } : {}), ...(unknownModel ? { unknown_model: 'true' } : {}),
  }), [from, to, provider, model, unknownModel, granularity, dimension, offset]);
  // A page turn retains the valid chart and previous page until replacement arrives.
  const key = JSON.stringify({ ...options, offset: undefined });
  const data = !isLoading && result?.key === key ? result.data : null;
  useEffect(() => { setOffset(0); }, [from, to, provider, model, unknownModel, granularity, dimension]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(false);
    getUsageTrends(options, controller.signal).then((next) => {
      if (controller.signal.aborted) return;
      if (offset > 0 && offset >= next.breakdown.total) setOffset(0);
      else setResult({ key, data: next });
    }).catch(() => { if (!controller.signal.aborted) setError(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [options, key, version, offset, retry]);
  const change = data?.comparison[metric];
  const dates = data?.period;
  const currentPartial = data ? partial(data.current, metric) : false;
  const inProgress = dates?.from && dates.to && !dates.all_time && new Date(dates.from).getTime() <= Date.now() && Date.now() < new Date(dates.to).getTime();
  return <section className={`usage-trends${compact ? ' usage-trends--compact' : ''}`} data-compared={Boolean(data?.previous)} aria-labelledby={heading} aria-busy={loading || isLoading}>
    <div className="trend-heading"><div><h3 id={heading}>Usage trends</h3><p>Recorded tokens over time, using the filters above.{model !== undefined || unknownModel ? ` ${modelName(unknownModel ? null : model!)}${provider ? ` in ${agentName(provider)}` : ''}.` : ''}</p></div>
      <div className="trend-controls"><span className="grouping-label">Group by</span><div className="trend-toggle" aria-label="Trend grouping"><button type="button" aria-pressed={granularity === 'day'} onClick={() => setGranularity('day')}>Daily</button><button type="button" aria-pressed={granularity === 'week'} onClick={() => setGranularity('week')}>Weekly</button><button type="button" aria-pressed={granularity === 'month'} onClick={() => setGranularity('month')}>Monthly</button></div>
        <label>Trend metric<select value={metric} onChange={(event) => setMetric(event.target.value as TrendMetric)}>{metrics.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div>
    </div>
    {loading || isLoading ? <p className="page-loading" role="status">Updating trends…</p> : null}
    {error ? <p className="notice notice--error" role="alert">Could not load usage trends. Your imported data is still available.<button className="button button--secondary" type="button" onClick={() => setRetry(retry + 1)}>Retry trends</button></p> : null}
    {data && dates ? <>
      <div className="chart-context"><span><Icon name="calendar" />{dates.all_time ? 'All time' : 'Selected range'}: {periodLabel(dates.from, dates.to, dates.time_zone)}</span><span>{granularity === 'day' ? 'Daily' : granularity === 'week' ? 'Weekly' : 'Monthly'} totals</span></div>
      <p className="grouping-explanation">Each point totals {granularity === 'day' ? 'one calendar day' : granularity === 'week' ? 'one calendar week' : 'one calendar month'}. Grouping changes the chart, not the selected total.</p>
      {dates.days === 1 ? <p className="chart-coverage-note">One date is selected, so the chart contains one interval.</p> : data.buckets.length > 1 && data.buckets.filter(bucket => bucket.current.event_count > 0).length < data.buckets.length / 2 ? <p className="chart-coverage-note"><Icon name="info" />Imported records are present in {data.buckets.filter(bucket => bucket.current.event_count > 0).length} of {data.buckets.length} {granularity === 'day' ? 'days' : granularity === 'week' ? 'weeks' : 'months'}. Empty intervals have no imported records.</p> : null}
      <div className="trend-periods"><div><span>Current period</span><div className="trend-total" role="group" aria-label="Current period total"><strong title={fullValue(data.current, metric)}>{data.current.event_count === 0 ? 'No records' : formatMetric(data.current[metric])}</strong></div><p>{periodLabel(dates.from, dates.to, dates.time_zone)}{currentPartial ? ' · Incomplete counters' : ''}</p></div>
        {data.previous ? <><div><span>Previous period</span><div className="trend-total" role="group" aria-label="Previous period total"><strong title={fullValue(data.previous, metric)}>{data.previous.event_count === 0 ? 'No records' : formatMetric(data.previous[metric])}</strong></div><p>{periodLabel(dates.previous_from, dates.previous_to, dates.time_zone)}{partial(data.previous, metric) ? ' · Incomplete counters' : ''}</p></div>
          <div className="trend-change" role="group" aria-label="Period change"><span>Change in observed tokens</span><strong title={change?.difference?.toLocaleString('en-US')}>{change?.difference === null || change?.difference === undefined ? '—' : `${change.difference > 0 ? '+' : ''}${formatMetric(change.difference)}`}</strong><p>{change ? changeLabel(change) : '—'}</p></div></> : <p className="trend-comparison-note">Choose a date range to compare with the previous period.</p>}
      </div>
      {inProgress ? <p className="trend-note">This period includes today and is still in progress. Its full-length previous period may contain more recorded usage.</p> : null}
      {data.series_limited ? <p className="trend-note">This history is too long to chart at this grouping. Choose a shorter date range{granularity === 'day' ? ' or switch to Weekly or Monthly' : ''}. The period totals still include every record.</p> : data.buckets.length ? <UsageChart key={`${key}:${metric}`} buckets={data.buckets} metric={metric} granularity={granularity} compared={data.previous !== null} compact={compact} /> : <p className="trend-note">No usage was recorded in this period.</p>}
      {data.buckets.length ? <details className="trend-data" onToggle={(event) => setShowData(event.currentTarget.open)}><summary>View chart data</summary>{showData ? <div className="ledger-table-wrap" tabIndex={0} aria-label="Trend data; scroll to see all columns"><table className="ledger-table" aria-label="Trend chart data"><thead><tr><th scope="col">Current dates</th><th scope="col">Current tokens</th>{data.previous ? <><th scope="col">Previous dates</th><th scope="col">Previous tokens</th></> : null}</tr></thead><tbody>{data.buckets.map((bucket) => <tr key={bucket.from}><th scope="row">{bucket.from} – {previousDate(bucket.to)}</th><td>{fullValue(bucket.current, metric)}{partial(bucket.current, metric) ? ' (incomplete)' : ''}</td>{data.previous ? <><td>{bucket.previous_from} – {previousDate(bucket.previous_to!)}</td><td>{fullValue(bucket.previous, metric)}{bucket.previous && partial(bucket.previous, metric) ? ' (incomplete)' : ''}</td></> : null}</tr>)}</tbody></table></div> : null}</details> : null}
      <details className="trend-details" open={compact ? undefined : true}><summary>Period breakdown and accuracy</summary>
      <div className="trend-breakdown-heading"><h4>Period breakdown</h4><div className="trend-toggle" aria-label="Comparison grouping"><button type="button" aria-pressed={dimension === 'providers'} onClick={() => setDimension('providers')}>Compare agents</button><button type="button" aria-pressed={dimension === 'models'} onClick={() => setDimension('models')}>Compare models</button></div></div>
      <div className="ledger-table-wrap" tabIndex={0} aria-label="Period comparison; scroll to see all columns"><table className="ledger-table trend-comparison-table" aria-label={dimension === 'models' ? 'Model period comparison' : 'Agent period comparison'}><thead><tr><th scope="col">{dimension === 'models' ? 'Model / agent' : 'Agent'}</th><th scope="col">Current tokens</th>{data.previous ? <><th scope="col">Previous tokens</th><th scope="col">Change</th></> : null}</tr></thead><tbody>{data.breakdown.items.map((row) => <tr key={JSON.stringify([row.provider, row.model_name])}><th scope="row">{dimension === 'models' ? <>{modelName(row.model_name)}<small>{agentName(row.provider)}</small></> : agentName(row.provider)}</th><td title={fullValue(row.current, metric)}>{row.current.event_count === 0 ? 'No records' : formatMetric(row.current[metric])}{partial(row.current, metric) ? <small>Incomplete</small> : null}</td>{data.previous ? <><td title={fullValue(row.previous, metric)}>{row.previous?.event_count === 0 ? 'No records' : formatMetric(row.previous?.[metric])}{row.previous && partial(row.previous, metric) ? <small>Incomplete</small> : null}</td><td>{row.comparison[metric].state === 'compared' ? changeLabel(row.comparison[metric]) : row.comparison[metric].state === 'no_baseline' ? 'No baseline' : '—'}</td></> : null}</tr>)}</tbody></table></div>
      <div className="trend-page-controls"><span>Showing {data.breakdown.total ? data.breakdown.offset + 1 : 0}–{Math.min(data.breakdown.offset + 25, data.breakdown.total)} of {data.breakdown.total} {dimension === 'models' ? 'model groups' : 'agents'}. Period totals include all groups.</span>{data.breakdown.total > 25 ? <div className="page-controls"><button type="button" className="button button--secondary" disabled={loading || error || data.breakdown.offset === 0} onClick={() => setOffset(Math.max(0, data.breakdown.offset - 25))}>Previous comparisons</button><button type="button" className="button button--secondary" disabled={loading || error || data.breakdown.offset + 25 >= data.breakdown.total} onClick={() => setOffset(data.breakdown.offset + 25)}>Next comparisons</button></div> : null}</div>
      <p className="trend-note">Calendar dates use {dates.time_zone}. Total tokens include complete input + output records; cache and reasoning are subsets. Missing counters are never estimated. Session-reported usage appears on its recorded session date.</p></details>
    </> : null}
  </section>;
}
