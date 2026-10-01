import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { getUsageTrends } from '../api/client';
import type { TrendBucket, TrendChange, TrendMetric, TrendTotals, UsageTrendsResponse } from '../types';
import { formatMetric } from './MetricCard';
import { agentName, modelName } from './usageLabels';

const metrics: [TrendMetric, string][] = [
  ['workload_tokens', 'Total tokens'], ['input_total_tokens', 'Input tokens'], ['output_total_tokens', 'Output tokens'],
  ['cache_read_tokens', 'Cache read'], ['cache_write_tokens', 'Cache write'], ['reasoning_tokens', 'Reasoning'],
];
const dayLabel = (day: string) => new Intl.DateTimeFormat('en', { month: 'short', day: 'numeric', timeZone: 'UTC' }).format(new Date(`${day}T12:00:00Z`));
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

function Chart({ buckets, metric, compared }: { buckets: TrendBucket[]; metric: TrendMetric; compared: boolean }) {
  const [active, setActive] = useState<number | null>(null);
  const host = useRef<HTMLDivElement>(null);
  const [plotWidth, setPlotWidth] = useState(920);
  useEffect(() => {
    const resize = () => setPlotWidth(Math.max(300, host.current?.clientWidth || 920));
    resize();
    if (typeof ResizeObserver === 'undefined' || !host.current) return;
    const observer = new ResizeObserver(resize);
    observer.observe(host.current);
    return () => observer.disconnect();
  }, []);
  const maximum = Math.max(1, ...buckets.flatMap((bucket) => [bucket.current[metric] ?? 0, bucket.previous?.[metric] ?? 0]));
  const left = plotWidth < 500 ? 48 : 76, right = plotWidth - 22;
  const width = (right - left) / Math.max(1, buckets.length), stride = Math.max(1, Math.ceil(buckets.length / (plotWidth < 500 ? 3 : 7)));
  const selected = active === null ? null : buckets[active];
  const interval = (from: string, to: string) => `${dayLabel(from)}, ${from.slice(0, 4)}${to !== nextDate(from) ? ` – ${dayLabel(previousDate(to))}, ${previousDate(to).slice(0, 4)}` : ''}`;
  const manyYears = buckets[0]?.from.slice(0, 4) !== buckets.at(-1)?.from.slice(0, 4);
  return <div className="trend-chart" ref={host}>
    <div className="trend-legend"><span><i />Current period</span>{compared ? <span><i className="trend-legend__previous" />Previous period</span> : null}</div>
    <svg viewBox={`0 0 ${plotWidth} 256`} role="group" aria-label="Token usage over time" className="trend-chart__plot">
      {[0, 1, 2, 3].map((tick) => <g key={tick} className="trend-grid"><line x1={left} x2={right} y1={202 - tick * 56} y2={202 - tick * 56} /><text x={left - 12} y={206 - tick * 56} textAnchor="end">{formatMetric(maximum * tick / 3)}</text></g>)}
      {buckets.map((bucket, index) => {
        const label = `${interval(bucket.from, bucket.to)}: ${fullValue(bucket.current, metric)} current${partial(bucket.current, metric) ? ' (incomplete)' : ''}${compared ? `; ${fullValue(bucket.previous, metric)} previous (${interval(bucket.previous_from!, bucket.previous_to!)})` : ''}`;
        const x = left + 2 + index * width;
        return <g key={bucket.from} tabIndex={0} role="img" aria-label={label} className="trend-bar" onFocus={() => setActive(index)} onBlur={() => setActive(null)} onMouseEnter={() => setActive(index)} onMouseLeave={() => setActive(null)}>
          <title>{label}</title><rect className="trend-bar__hit" x={x} y="30" width={Math.max(1, width - 1)} height="174" />
          {[bucket.current, ...(compared ? [bucket.previous] : [])].map((totals, side) => {
            const value = totals?.[metric], height = value == null ? 0 : value / maximum * 168;
            const barWidth = Math.max(1, width * (compared ? .34 : .65));
            const bx = x + width * .09 + side * width * .39;
            return totals && totals.event_count > 0 && value === null ? <text key={side} x={bx} y="196" className="trend-bar__unknown">?</text> : <rect key={side} className={side ? 'trend-bar__previous' : 'trend-bar__current'} x={bx} y={202 - height} width={barWidth} height={height} rx={Math.min(2, barWidth / 3)} />;
          })}
          {(index % stride === 0 || index === buckets.length - 1) ? <text className="trend-axis" x={x + width / 2} y="228" textAnchor="middle">{dayLabel(bucket.from)}{manyYears ? ` '${bucket.from.slice(2, 4)}` : ''}</text> : null}
        </g>;
      })}
    </svg>
    <p className="trend-chart__detail" aria-live="polite">{selected ? `${interval(selected.from, selected.to)}: ${fullValue(selected.current, metric)} tokens${compared ? `; previous ${interval(selected.previous_from!, selected.previous_to!)}: ${fullValue(selected.previous, metric)} tokens` : ''}` : 'Hover or focus a bar for exact counts. Weeks start on Monday; boundary weeks show only the selected days.'}</p>
  </div>;
}

function previousDate(value: string): string { return new Date(new Date(`${value}T12:00:00Z`).getTime() - 86400000).toISOString().slice(0, 10); }
function nextDate(value: string): string { return new Date(new Date(`${value}T12:00:00Z`).getTime() + 86400000).toISOString().slice(0, 10); }

export function UsageTrends({ range, provider, model, unknownModel = false, version }: {
  range?: { from: string; to: string }; provider?: string; model?: string; unknownModel?: boolean; version?: number;
}) {
  const heading = useId();
  const [granularity, setGranularity] = useState<'day' | 'week'>(range ? 'day' : 'week');
  const [metric, setMetric] = useState<TrendMetric>('workload_tokens');
  const [dimension, setDimension] = useState<'providers' | 'models'>('providers');
  const [offset, setOffset] = useState(0);
  const [result, setResult] = useState<{ key: string; data: UsageTrendsResponse } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  const [showData, setShowData] = useState(false);
  const from = range?.from, to = range?.to;
  const options = useMemo(() => ({ ...(from && to ? { from, to } : {}),
    time_zone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC', granularity, dimension, offset: String(offset),
    ...(provider ? { provider } : {}), ...(model !== undefined ? { model } : {}), ...(unknownModel ? { unknown_model: 'true' } : {}),
  }), [from, to, provider, model, unknownModel, granularity, dimension, offset]);
  // A page turn retains the valid chart and previous page until replacement arrives.
  const key = JSON.stringify({ ...options, offset: undefined });
  const data = result?.key === key ? result.data : null;
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
  return <section className="usage-trends" aria-labelledby={heading} aria-busy={loading}>
    <div className="trend-heading"><div><h3 id={heading}>Usage trends</h3><p>Recorded tokens over time, using the filters above.{model !== undefined || unknownModel ? ` ${modelName(unknownModel ? null : model!)}${provider ? ` in ${agentName(provider)}` : ''}.` : ''}</p></div>
      <div className="trend-controls"><div className="trend-toggle" aria-label="Trend grouping"><button type="button" aria-pressed={granularity === 'day'} onClick={() => setGranularity('day')}>Daily</button><button type="button" aria-pressed={granularity === 'week'} onClick={() => setGranularity('week')}>Weekly</button></div>
        <label>Trend metric<select value={metric} onChange={(event) => setMetric(event.target.value as TrendMetric)}>{metrics.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div>
    </div>
    {loading ? <p className="page-loading" role="status">Updating trends…</p> : null}
    {error ? <p className="notice notice--error" role="alert">Could not load usage trends. Your imported data is still available.<button className="button button--secondary" type="button" onClick={() => setRetry(retry + 1)}>Retry trends</button></p> : null}
    {data && dates ? <>
      <div className="trend-periods"><div><span>Current period</span><div className="trend-total" role="group" aria-label="Current period total"><strong title={fullValue(data.current, metric)}>{data.current.event_count === 0 ? 'No records' : formatMetric(data.current[metric])}</strong></div><p>{periodLabel(dates.from, dates.to, dates.time_zone)}{currentPartial ? ' · Incomplete counters' : ''}</p></div>
        {data.previous ? <><div><span>Previous period</span><div className="trend-total" role="group" aria-label="Previous period total"><strong title={fullValue(data.previous, metric)}>{data.previous.event_count === 0 ? 'No records' : formatMetric(data.previous[metric])}</strong></div><p>{periodLabel(dates.previous_from, dates.previous_to, dates.time_zone)}{partial(data.previous, metric) ? ' · Incomplete counters' : ''}</p></div>
          <div className="trend-change" role="group" aria-label="Period change"><span>Change in observed tokens</span><strong title={change?.difference?.toLocaleString('en-US')}>{change?.difference === null || change?.difference === undefined ? '—' : `${change.difference > 0 ? '+' : ''}${formatMetric(change.difference)}`}</strong><p>{change ? changeLabel(change) : '—'}</p></div></> : <p className="trend-comparison-note">Choose a date range to compare with the previous period.</p>}
      </div>
      {inProgress ? <p className="trend-note">This period includes today and is still in progress. Its full-length previous period may contain more recorded usage.</p> : null}
      {data.series_limited ? <p className="trend-note">This history is too long to chart at this grouping. Choose a shorter date range{granularity === 'day' ? ' or switch to Weekly' : ''}. The period totals still include every record.</p> : data.buckets.length ? <Chart key={`${key}:${metric}`} buckets={data.buckets} metric={metric} compared={data.previous !== null} /> : <p className="trend-note">No usage was recorded in this period.</p>}
      {data.buckets.length ? <details className="trend-data" onToggle={(event) => setShowData(event.currentTarget.open)}><summary>View chart data</summary>{showData ? <div className="ledger-table-wrap" tabIndex={0} aria-label="Trend data; scroll to see all columns"><table className="ledger-table" aria-label="Trend chart data"><thead><tr><th scope="col">Current dates</th><th scope="col">Current tokens</th>{data.previous ? <><th scope="col">Previous dates</th><th scope="col">Previous tokens</th></> : null}</tr></thead><tbody>{data.buckets.map((bucket) => <tr key={bucket.from}><th scope="row">{bucket.from} – {previousDate(bucket.to)}</th><td>{fullValue(bucket.current, metric)}{partial(bucket.current, metric) ? ' (incomplete)' : ''}</td>{data.previous ? <><td>{bucket.previous_from} – {previousDate(bucket.previous_to!)}</td><td>{fullValue(bucket.previous, metric)}{bucket.previous && partial(bucket.previous, metric) ? ' (incomplete)' : ''}</td></> : null}</tr>)}</tbody></table></div> : null}</details> : null}
      <div className="trend-breakdown-heading"><h4>Period breakdown</h4><div className="trend-toggle" aria-label="Comparison grouping"><button type="button" aria-pressed={dimension === 'providers'} onClick={() => setDimension('providers')}>Compare agents</button><button type="button" aria-pressed={dimension === 'models'} onClick={() => setDimension('models')}>Compare models</button></div></div>
      <div className="ledger-table-wrap" tabIndex={0} aria-label="Period comparison; scroll to see all columns"><table className="ledger-table trend-comparison-table" aria-label={dimension === 'models' ? 'Model period comparison' : 'Agent period comparison'}><thead><tr><th scope="col">{dimension === 'models' ? 'Model / agent' : 'Agent'}</th><th scope="col">Current tokens</th>{data.previous ? <><th scope="col">Previous tokens</th><th scope="col">Change</th></> : null}</tr></thead><tbody>{data.breakdown.items.map((row) => <tr key={JSON.stringify([row.provider, row.model_name])}><th scope="row">{dimension === 'models' ? <>{modelName(row.model_name)}<small>{agentName(row.provider)}</small></> : agentName(row.provider)}</th><td title={fullValue(row.current, metric)}>{row.current.event_count === 0 ? 'No records' : formatMetric(row.current[metric])}{partial(row.current, metric) ? <small>Incomplete</small> : null}</td>{data.previous ? <><td title={fullValue(row.previous, metric)}>{row.previous?.event_count === 0 ? 'No records' : formatMetric(row.previous?.[metric])}{row.previous && partial(row.previous, metric) ? <small>Incomplete</small> : null}</td><td>{row.comparison[metric].state === 'compared' ? changeLabel(row.comparison[metric]) : row.comparison[metric].state === 'no_baseline' ? 'No baseline' : '—'}</td></> : null}</tr>)}</tbody></table></div>
      <div className="trend-page-controls"><span>Showing {data.breakdown.total ? data.breakdown.offset + 1 : 0}–{Math.min(data.breakdown.offset + 25, data.breakdown.total)} of {data.breakdown.total} {dimension === 'models' ? 'model groups' : 'agents'}. Period totals include all groups.</span>{data.breakdown.total > 25 ? <div className="page-controls"><button type="button" className="button button--secondary" disabled={loading || error || data.breakdown.offset === 0} onClick={() => setOffset(Math.max(0, data.breakdown.offset - 25))}>Previous comparisons</button><button type="button" className="button button--secondary" disabled={loading || error || data.breakdown.offset + 25 >= data.breakdown.total} onClick={() => setOffset(data.breakdown.offset + 25)}>Next comparisons</button></div> : null}</div>
      <p className="trend-note">Calendar dates use {dates.time_zone}. Total tokens include complete input + output records; cache and reasoning are subsets. Missing counters are never estimated. Session-reported usage appears on its recorded session date.</p>
    </> : null}
  </section>;
}
