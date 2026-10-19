import { Icon, type IconName } from './Icon';

/** Rendered whenever the API reports a metric it could not observe. */
export const UNKNOWN_METRIC = '—';

const compactNumberFormatter = new Intl.NumberFormat('en-US', {
  notation: 'compact',
  maximumFractionDigits: 2,
});

/**
 * "Never invent data": a null/absent metric renders as an em dash, while a
 * genuine zero stays a zero. Large counts use K, M, and B denominations.
 */
export function formatMetric(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return UNKNOWN_METRIC;
  }
  return compactNumberFormatter.format(value);
}

const usdFormatter = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** Estimated cost in USD; tiny non-zero values keep their precision. */
export function formatCost(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return UNKNOWN_METRIC;
  }
  if (value === 0) return '$0.00';
  if (value < 0.01) return '<$0.01';
  if (value < 1) return `$${value.toFixed(3)}`;
  return usdFormatter.format(value);
}

interface MetricCardProps {
  label: string;
  value: number | null | undefined;
  icon?: IconName;
  note?: string;
  featured?: boolean;
  format?: 'number' | 'cost';
}

export function MetricCard({ label, value, icon, note, featured = false, format = 'number' }: MetricCardProps) {
  const display = format === 'cost' ? formatCost(value) : formatMetric(value);
  return (
    <div className={`metric-card${featured ? ' metric-card--featured' : ''}`}>
      <dt className="metric-card__label">{label}{icon ? <Icon name={icon} /> : null}</dt>
      <dd className="metric-card__value" title={value == null ? undefined : value.toLocaleString('en-US', { maximumFractionDigits: 6 })}>{display}</dd>
      {note ? <dd className="metric-card__note">{note}</dd> : null}
    </div>
  );
}
