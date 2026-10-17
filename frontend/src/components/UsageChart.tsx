import { useEffect, useId, useRef, useState } from 'react';
import type { TrendBucket, TrendMetric, TrendTotals } from '../types';
import { formatMetric } from './MetricCard';
import { agentName, agents } from './usageLabels';

const day = (date: string) => new Intl.DateTimeFormat('en', { month: 'short', day: 'numeric', timeZone: 'UTC' }).format(new Date(`${date}T12:00:00Z`));
const previousDay = (date: string) => new Date(new Date(`${date}T12:00:00Z`).getTime() - 86400000).toISOString().slice(0, 10);
const interval = (from: string, to: string) => `${day(from)}, ${from.slice(0, 4)}${previousDay(to) !== from ? ` – ${day(previousDay(to))}, ${previousDay(to).slice(0, 4)}` : ''}`;
const exact = (totals: TrendTotals | null, metric: TrendMetric) => !totals ? '—' : !totals.event_count ? 'No records' : totals[metric] == null ? 'Not reported' : totals[metric]!.toLocaleString('en-US');
const observed = (totals: TrendTotals | null, metric: TrendMetric) => !totals ? null : totals.event_count === 0 ? 0 : totals.reported_counts[metric] < totals.event_count ? null : totals[metric];

/** Separate polygons at unknown intervals, so a missing counter is never zero. */
function areaPaths(lower: (number | null)[], upper: (number | null)[], x: (i: number) => number, y: (n: number) => number, half: number): string[] {
  const paths: string[] = [];
  let start = 0;
  while (start < upper.length) {
    if (upper[start] === null) { start++; continue; }
    let end = start;
    while (end + 1 < upper.length && upper[end + 1] !== null) end++;
    const points = [`${x(start) - half},${y(upper[start]!)}`];
    for (let i = start; i <= end; i++) points.push(`${x(i)},${y(upper[i]!)}`);
    points.push(`${x(end) + half},${y(upper[end]!)}`, `${x(end) + half},${y(lower[end]!)}`);
    for (let i = end; i >= start; i--) points.push(`${x(i)},${y(lower[i]!)}`);
    points.push(`${x(start) - half},${y(lower[start]!)}`);
    paths.push(`M${points.join(' L')} Z`);
    start = end + 1;
  }
  return paths;
}

export function UsageChart({ buckets, metric, compared, granularity = 'week' }: { granularity?: 'day' | 'week' | 'month'; buckets: TrendBucket[]; metric: TrendMetric; compared: boolean; compact?: boolean }) {
  const [active, setActive] = useState<number | null>(null);
  const host = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(1100);
  const clip = useId().replace(/:/g, '');
  useEffect(() => {
    const resize = () => setWidth(Math.max(280, host.current?.clientWidth || 1100));
    resize();
    if (typeof ResizeObserver === 'undefined' || !host.current) return;
    const observer = new ResizeObserver(resize);
    observer.observe(host.current);
    return () => observer.disconnect();
  }, []);
  const left = width < 500 ? 48 : 64, right = width - 16, height = width < 500 ? 240 : 260, baseline = height - 40;
  const plotHeight = baseline - 18, step = (right - left) / Math.max(1, buckets.length);
  const x = (i: number) => left + step * (i + .5);
  const maximum = Math.max(1, ...buckets.flatMap(b => [b.current[metric] ?? 0, compared ? b.previous?.[metric] ?? 0 : 0]));
  const ceiling = Math.ceil(maximum / 4 / (10 ** Math.floor(Math.log10(maximum / 4)))) * (10 ** Math.floor(Math.log10(maximum / 4))) * 4;
  const y = (n: number) => baseline - n / ceiling * plotHeight;
  const identities = [...new Set(buckets.flatMap(b => b.providers?.map(p => p.provider) ?? []))].sort((a, b) => {
    const rank = (id: string) => { const i = agents.findIndex(agent => agent.id === id); return i === -1 ? agents.length : i; };
    return rank(a) - rank(b) || a.localeCompare(b);
  });
  // Old servers and inconsistent/unknown splits retain the recorded total area.
  const stacked = identities.length > 0 && buckets.every(b => b.providers !== undefined &&
    b.providers.every(p => p.totals[metric] !== null) &&
    b.providers.reduce((sum, p) => sum + (p.totals[metric] ?? 0), 0) === (b.current[metric] ?? 0));
  let lower: (number | null)[] = buckets.map(b => observed(b.current, metric) === null ? null : 0);
  const layers = (stacked ? identities : ['total']).map(id => {
    const upper = buckets.map((b, i) => lower[i] === null ? null : lower[i]! + (stacked ? b.providers?.find(p => p.provider === id)?.totals[metric] ?? 0 : observed(b.current, metric) ?? 0));
    const paths = areaPaths(lower, upper, x, y, step / 2);
    lower = upper;
    return { id, paths };
  });
  const previous = buckets.map((b, i) => observed(b.previous, metric) === null ? null : `${x(i)},${y(observed(b.previous, metric)!)}`);
  const previousPath = previous.map((point, i) => point === null ? '' : `${i === 0 || previous[i - 1] === null ? 'M' : 'L'}${point}`).join(' ');
  const stride = Math.max(1, Math.ceil(buckets.length / (width < 500 ? 3 : 8)));
  const manyYears = buckets[0]?.from.slice(0, 4) !== buckets.at(-1)?.from.slice(0, 4);
  const selected = active === null ? null : buckets[active];
  return <div className="trend-chart usage-chart" ref={host}>
    {compared ? <div className="trend-legend"><span><i />Current period</span><span><i className="trend-legend__previous" />Previous period</span></div> : null}
    <svg viewBox={`0 0 ${width} ${height}`} role="group" aria-label="Token usage over time" className="trend-chart__plot">
      <defs><clipPath id={clip}><rect className="chart-reveal" x={left} y={0} width={right - left} height={baseline + 1} /></clipPath></defs>
      {[0, 1, 2, 3, 4].map(tick => <g key={tick} className="trend-grid"><line x1={left} x2={right} y1={y(ceiling * tick / 4)} y2={y(ceiling * tick / 4)} /><text x={left - 12} y={y(ceiling * tick / 4) + 4} textAnchor="end">{formatMetric(ceiling * tick / 4)}</text></g>)}
      <g clipPath={`url(#${clip})`}>
        {layers.map(layer => layer.paths.map((path, i) => <path key={`${layer.id}-${i}`} className={`usage-area agent-color--${layer.id}`} data-provider={stacked ? layer.id : undefined} d={path} />))}
        {compared ? <path className="usage-previous-line" d={previousPath} /> : null}
        {compared ? buckets.map((bucket, i) => {
          const value = observed(bucket.previous, metric);
          return value !== null ? <circle key={bucket.from} className="usage-previous-point" cx={x(i)} cy={y(value)} r={buckets.length > 30 ? 2 : 3} /> : null;
        }) : null}
      </g>
      {buckets.map((bucket, i) => {
        const incomplete = bucket.current.reported_counts[metric] < bucket.current.event_count;
        const label = `${interval(bucket.from, bucket.to)}: ${exact(bucket.current, metric)} current${incomplete ? ' (incomplete)' : ''}${compared ? `; ${exact(bucket.previous, metric)} previous (${interval(bucket.previous_from!, bucket.previous_to!)})` : ''}`;
        return <g key={bucket.from} tabIndex={0} role="img" aria-label={label} className={`trend-bar${active === i ? ' is-active' : ''}`} onFocus={() => setActive(i)} onBlur={() => setActive(null)} onMouseEnter={() => setActive(i)} onMouseLeave={() => setActive(null)}>
          <title>{label}</title><rect className="trend-bar__hit" x={left + i * step} y={12} width={Math.max(1, step)} height={baseline - 12} />
          {incomplete ? <text x={x(i)} y={baseline - 8} className="chart-unreported" textAnchor="middle">?</text> : null}
          {active === i ? <line className="chart-crosshair" x1={x(i)} x2={x(i)} y1={12} y2={baseline} /> : null}
          {(i % stride === 0 || i === buckets.length - 1 && i % stride > stride / 2) ? <text className="trend-axis" x={x(i)} y={baseline + 26} textAnchor="middle">{granularity === 'month' ? new Intl.DateTimeFormat('en', { month: 'short', year: '2-digit', timeZone: 'UTC' }).format(new Date(`${bucket.from}T12:00:00Z`)) : day(bucket.from)}{manyYears && granularity !== 'month' ? ` '${bucket.from.slice(2, 4)}` : ''}</text> : null}
        </g>;
      })}
    </svg>
    <p className="chart-hint">Hover or focus an interval for exact counts.{granularity === 'week' ? ' Weeks start on Monday; boundary weeks show only the selected days.' : granularity === 'month' ? ' Boundary months show only the selected days.' : ''}</p>
    <div hidden={!selected} className={`chart-tooltip${selected ? ' chart-tooltip--active' : ''}`} role="status" aria-label="Chart interval details" aria-live="polite">
      {selected ? <><strong>{interval(selected.from, selected.to)}</strong><span>{exact(selected.current, metric)} tokens{selected.current.reported_counts[metric] < selected.current.event_count ? ' · Incomplete counters' : ''}{compared ? `; previous ${interval(selected.previous_from!, selected.previous_to!)}: ${exact(selected.previous, metric)} tokens` : ''}</span>{selected.providers?.map(layer => <span key={layer.provider} className={`agent-color--${layer.provider}`}><i />{agentName(layer.provider)}: {exact(layer.totals, metric)}</span>)}</> : null}
    </div>
  </div>;
}
