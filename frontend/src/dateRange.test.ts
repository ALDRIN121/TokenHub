import { expect, it } from 'vitest';
import { usageDateRange } from './dateRange';
import { readDashboardLocation, saveDashboardLocation } from './dashboardNavigation';

it('converts an inclusive custom range to exclusive local calendar boundaries', () => {
  const range = usageDateRange('custom', '2026-09-15', undefined, '2026-09-20')!;
  expect(new Date(range.from).getDate()).toBe(15);
  expect(new Date(range.to).getDate()).toBe(21);
  expect(usageDateRange('custom', '2026-09-20', undefined, '2026-09-15')).toBeUndefined();
  expect(usageDateRange('custom', '2026-02-30', undefined, '2026-03-03')).toBeUndefined();
});

it('round-trips custom dates through the URL and drops them for preset ranges', () => {
  saveDashboardLocation('codex', 'custom', '2026-09-15', 'overview', '2026-09-20');
  expect(readDashboardLocation()).toMatchObject({ period: 'custom', selectedDay: '2026-09-15', selectedEndDay: '2026-09-20' });
  saveDashboardLocation('codex', 'today', '2026-09-15', 'overview');
  expect(new URL(location.href).searchParams.has('from')).toBe(false);
});
