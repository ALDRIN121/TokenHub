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

interface MetricCardProps {
  label: string;
  value: number | null | undefined;
  icon?: IconName;
  note?: string;
  featured?: boolean;
}

export function MetricCard({ label, value, icon, note, featured = false }: MetricCardProps) {
  return (
    <div className={`metric-card${featured ? ' metric-card--featured' : ''}`}>
      <dt className="metric-card__label">{label}{icon ? <Icon name={icon} /> : null}</dt>
      <dd className="metric-card__value" title={value == null ? undefined : value.toLocaleString('en-US')}>{formatMetric(value)}</dd>
      {note ? <dd className="metric-card__note">{note}</dd> : null}
    </div>
  );
}
