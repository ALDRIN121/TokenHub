import { describe, expect, it } from 'vitest';
import { formatCost } from './MetricCard';

describe('formatCost', () => {
  it('keeps unknown costs distinct from zero', () => {
    expect(formatCost(null)).toBe('—');
    expect(formatCost(0)).toBe('$0.00');
  });

  it('keeps sub-cent estimates readable', () => {
    expect(formatCost(0.0042)).toBe('<$0.01');
    expect(formatCost(0.1234)).toBe('$0.123');
  });

  it('uses two decimals once the estimate reaches a dollar', () => {
    expect(formatCost(12.3456)).toBe('$12.35');
  });
});
