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
}

export function MetricCard({ label, value }: MetricCardProps) {
  return (
    <div className="metric-card">
      <dt className="metric-card__label">{label}</dt>
      <dd className="metric-card__value">{formatMetric(value)}</dd>
    </div>
  );
}
