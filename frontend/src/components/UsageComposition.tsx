import type { UsageTotals } from '../types';

export function UsageComposition({ dashboard }: { dashboard: UsageTotals }) {
  const input = dashboard.input_total_tokens;
  const output = dashboard.output_total_tokens;
  const total = dashboard.workload_tokens;
  // Both observed components are required. Never draw a chart for unknown data.
  if (input === null || output === null || total === null || total <= 0) {
    return (
      <div className="composition composition--empty">
        <h3>Usage composition</h3>
        <p>{total === 0 ? 'The imported workload is zero. There are no token proportions to display.' : dashboard.event_count > 0 ? 'Some imported events have missing token counts. Usage composition is unavailable until complete counts are observed.' : 'Input and output proportions will appear after a supported source is imported.'}</p>
      </div>
    );
  }
  // SQL sums each component independently, while workload requires both
  // components on the same event. Missing components make these totals differ.
  if (input + output !== total) {
    return (
      <div className="composition composition--empty">
        <h3>Usage composition</h3>
        <p>Some events have incomplete token records. These totals cannot support an accurate composition chart.</p>
      </div>
    );
  }
  const inputPercent = (input / total) * 100;
  const outputPercent = (output / total) * 100;
  return (
    <div className="composition">
      <div className="composition__heading">
        <h3>Usage composition</h3>
        <span>Input + output = workload</span>
      </div>
      <div className="composition__bar" role="img" aria-label={`Workload composition: input ${inputPercent.toFixed(1)}%, output ${outputPercent.toFixed(1)}%`}>
        <span className="composition__input" style={{ width: `${inputPercent}%` }} />
        <span className="composition__output" style={{ width: `${outputPercent}%` }} />
      </div>
      <div className="composition__legend">
        <span><i className="swatch swatch--input" />Input <strong>{inputPercent.toFixed(1)}%</strong></span>
        <span><i className="swatch swatch--output" />Output <strong>{outputPercent.toFixed(1)}%</strong></span>
      </div>
      <p>Cache is part of input. Reasoning is part of output. Neither adds to your total.</p>
    </div>
  );
}
