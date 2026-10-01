import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { expect, it } from 'vitest';
import { UsageExplorer } from './UsageExplorer';
import { usageFixture } from '../test/fixtures';
import { trendFixture } from '../test/trendFixtures';
import { server } from '../test/server';
import { usageDateRange } from '../dateRange';

function setup(handler = () => HttpResponse.json(trendFixture)) {
  server.use(http.get('/api/v1/usage/trends', handler));
  return render(<UsageExplorer data={usageFixture} period="day" selectedDay="2026-09-27" />);
}

it('shows paired totals, exact accessible chart values and a chart data table', async () => {
  setup();
  const panel = await screen.findByRole('region', { name: 'Usage trends' });
  expect(await within(panel).findByLabelText('Period change')).toHaveTextContent('50.0% more');
  expect(within(panel).getByLabelText('Current period total')).toHaveTextContent('150');
  expect(within(panel).getByLabelText('Previous period total')).toHaveTextContent('100');
  expect(within(panel).getByRole('img', { name: /Sep 27.*150.*100/ })).toBeInTheDocument();
  await userEvent.setup().click(within(panel).getByText('View chart data'));
  const table = within(panel).getByRole('table', { name: 'Trend chart data' });
  expect(within(table).getAllByRole('row')).toHaveLength(2);
  expect(within(table).getByText('150')).toBeInTheDocument();
});

it('switches daily/weekly grouping, changes metrics locally and shares explorer filters', async () => {
  const seen: URLSearchParams[] = [];
  server.use(http.get('/api/v1/usage/trends', ({ request }) => { seen.push(new URL(request.url).searchParams); return HttpResponse.json(trendFixture); }));
  render(<UsageExplorer data={usageFixture} period="day" selectedDay="2026-09-27" />);
  const panel = await screen.findByRole('region', { name: 'Usage trends' });
  expect(await within(panel).findByLabelText('Period change')).toHaveTextContent('50.0% more');
  await userEvent.setup().click(within(panel).getByRole('button', { name: 'Weekly' }));
  await waitFor(() => expect(seen.at(-1)?.get('granularity')).toBe('week'));
  const count = seen.length;
  await userEvent.setup().selectOptions(within(panel).getByLabelText('Trend metric'), 'output_total_tokens');
  await waitFor(() => expect(within(panel).getByLabelText('Period change')).toHaveTextContent('150.0% more'));
  expect(seen).toHaveLength(count);
  await userEvent.setup().click(screen.getByRole('button', { name: 'Filter by Codex' }));
  await waitFor(() => expect(seen.at(-1)?.get('provider')).toBe('codex'));
  await userEvent.setup().click(screen.getByRole('button', { name: 'View sessions for gpt-test in Codex' }));
  await waitFor(() => expect(seen.at(-1)?.get('model')).toBe('gpt-test'));
  expect(seen.at(-1)?.has('from')).toBe(true);
  expect(seen.at(-1)?.has('to')).toBe(true);
  expect(seen.at(-1)?.get('time_zone')).toBeTruthy();
});

it('does not invent percentage changes for unknown metrics or a zero baseline', async () => {
  setup(() => HttpResponse.json({ ...trendFixture, comparison: { ...trendFixture.comparison, workload_tokens: { difference: 150, percent_change: null, state: 'no_baseline' } } }));
  const panel = await screen.findByRole('region', { name: 'Usage trends' });
  await within(panel).findByText('No previous usage');
  expect(within(panel).queryByText(/Infinity|NaN/)).not.toBeInTheDocument();
  await userEvent.setup().selectOptions(within(panel).getByLabelText('Trend metric'), 'cache_write_tokens');
  await within(panel).findByText('Comparison unavailable: incomplete counters');
  expect(within(panel).getAllByText('—').length).toBeGreaterThan(0);
});

it('replaces bounded model comparison pages and includes unknown model labels', async () => {
  server.use(http.get('/api/v1/usage/trends', ({ request }) => {
    const options = new URL(request.url).searchParams;
    const models = options.get('dimension') === 'models';
    const offset = Number(options.get('offset') ?? 0);
    return HttpResponse.json({ ...trendFixture, breakdown: { dimension: models ? 'models' : 'providers', total: models ? 50 : 1, offset, limit: 25,
      items: models ? Array.from({ length: 25 }, (_, index) => ({ ...trendFixture.breakdown.items[0], model_name: index === 0 && offset === 0 ? null : `comparison-${offset + index}` })) : trendFixture.breakdown.items } });
  }));
  render(<UsageExplorer data={usageFixture} period="day" selectedDay="2026-09-27" />);
  const panel = await screen.findByRole('region', { name: 'Usage trends' });
  await userEvent.setup().click(await within(panel).findByRole('button', { name: 'Compare models' }));
  const table = await within(panel).findByRole('table', { name: 'Model period comparison' });
  await within(table).findByText('Model not recorded');
  expect(within(table).getAllByRole('row')).toHaveLength(26);
  await userEvent.setup().click(within(panel).getByRole('button', { name: 'Next comparisons' }));
  await within(table).findByText('comparison-25');
  expect(within(table).queryByText('Model not recorded')).not.toBeInTheDocument();
  expect(within(table).getAllByRole('row')).toHaveLength(26);
});

it('discards stale date responses and refreshes when imported data changes', async () => {
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  const seen: string[] = [];
  const oldRange = usageDateRange('day', '2026-09-26')!.from;
  server.use(http.get('/api/v1/usage/trends', async ({ request }) => {
    const start = new URL(request.url).searchParams.get('from') ?? '';
    seen.push(start);
    if (start === oldRange) await held;
    return HttpResponse.json({ ...trendFixture, current: { ...trendFixture.current, workload_tokens: start === oldRange ? 999 : 150 } });
  }));
  const view = render(<UsageExplorer data={usageFixture} period="day" selectedDay="2026-09-26" />);
  try {
    await waitFor(() => expect(seen).toHaveLength(1));
    view.rerender(<UsageExplorer data={usageFixture} period="day" selectedDay="2026-09-27" />);
    const panel = await screen.findByRole('region', { name: 'Usage trends' });
    await waitFor(() => expect(within(panel).getByLabelText('Current period total')).toHaveTextContent('150'));
    release();
    expect(within(panel).queryByText('999')).not.toBeInTheDocument();
    view.rerender(<UsageExplorer data={{ ...usageFixture, data_version: 2 }} period="day" selectedDay="2026-09-27" />);
    await waitFor(() => expect(seen).toHaveLength(3));
  } finally { release(); view.unmount(); }
});

it('offers retry after a read fails and explains all-time comparison limits', async () => {
  let failed = true;
  setup(() => failed ? HttpResponse.json({}, { status: 500 }) : HttpResponse.json({ ...trendFixture, previous: null, period: { ...trendFixture.period, all_time: true }, comparison: Object.fromEntries(Object.keys(trendFixture.comparison).map((key) => [key, { difference: null, percent_change: null, state: 'all_time' }])) }));
  const panel = await screen.findByRole('region', { name: 'Usage trends' });
  await within(panel).findByText(/Could not load usage trends/);
  failed = false;
  fireEvent.click(within(panel).getByRole('button', { name: 'Retry trends' }));
  await within(panel).findByText('Choose a date range to compare with the previous period.');
});

it('retains exact totals when a long history exceeds chart bounds', async () => {
  setup(() => HttpResponse.json({ ...trendFixture, buckets: [], series_limited: true }));
  const panel = await screen.findByRole('region', { name: 'Usage trends' });
  await within(panel).findByText(/The period totals still include every record/);
  expect(within(panel).getByLabelText('Current period total')).toHaveTextContent('150');
  expect(within(panel).queryByRole('group', { name: 'Token usage over time' })).not.toBeInTheDocument();
});

it('recovers comparison pagination when a rebuild removes later model groups', async () => {
  let reduced = false;
  const offsets: number[] = [];
  server.use(http.get('/api/v1/usage/trends', ({ request }) => {
    const offset = Number(new URL(request.url).searchParams.get('offset') ?? 0);
    offsets.push(offset);
    return HttpResponse.json({ ...trendFixture, breakdown: { ...trendFixture.breakdown, total: reduced ? 1 : 26, offset,
      items: reduced && offset > 0 ? [] : [{ ...trendFixture.breakdown.items[0], model_name: reduced ? 'remaining-comparison' : `page-${offset}` }] } });
  }));
  const view = render(<UsageExplorer data={usageFixture} period="day" selectedDay="2026-09-27" />);
  const panel = await screen.findByRole('region', { name: 'Usage trends' });
  await userEvent.setup().click(await within(panel).findByRole('button', { name: 'Compare models' }));
  await within(panel).findByText('page-0');
  await userEvent.setup().click(within(panel).getByRole('button', { name: 'Next comparisons' }));
  await within(panel).findByText('page-25');
  reduced = true;
  view.rerender(<UsageExplorer data={{ ...usageFixture, data_version: 2 }} period="day" selectedDay="2026-09-27" />);
  await within(panel).findByText('remaining-comparison');
  expect(offsets.at(-1)).toBe(0);
});
