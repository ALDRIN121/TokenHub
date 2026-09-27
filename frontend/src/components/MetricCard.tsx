import { Icon, type IconName } from './Icon';

/** Rendered whenever the API reports a metric it could not observe. */
export const UNKNOWN_METRIC = '—';

/**
 * "Never invent data": a null/absent metric renders as an em dash, while a
 * genuine zero stays a zero. Locale grouping is applied to real numbers only.
 */
export function formatMetric(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return UNKNOWN_METRIC;
  }
  return value.toLocaleString('en-US');
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
      <dd className="metric-card__value">{formatMetric(value)}</dd>
      {note ? <dd className="metric-card__note">{note}</dd> : null}
    </div>
  );
}
