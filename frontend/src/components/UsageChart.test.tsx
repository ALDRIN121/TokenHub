import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { expect, it } from 'vitest';
import { UsageTrends } from './UsageTrends';
import { server } from '../test/server';
import { trendCurrent, trendFixture } from '../test/trendFixtures';

it('plots recorded agent layers and exposes exact bucket values to keyboard users', async () => {
  const providers = [
    { provider: 'codex', totals: { ...trendCurrent, workload_tokens: 90 } },
    { provider: 'claude_code', totals: { ...trendCurrent, workload_tokens: 60 } },
  ];
  server.use(http.get('/api/v1/usage/trends', () => HttpResponse.json({ ...trendFixture,
    buckets: [{ ...trendFixture.buckets[0], providers }] })));
  const { container } = render(<UsageTrends compact />);
  await waitFor(() => expect(container.querySelectorAll('.usage-area[data-provider]')).toHaveLength(2));
  const point = screen.getByRole('img', { name: /Sep 27.*150.*100/ });
  fireEvent.focus(point);
  expect(screen.getByRole('status', { name: 'Chart interval details' })).toHaveTextContent('150');
  expect(screen.getByRole('status', { name: 'Chart interval details' })).toHaveTextContent('Codex: 90');
});

it('marks unreported intervals instead of drawing a fabricated zero', async () => {
  const unknown = { ...trendCurrent, workload_tokens: null, reported_counts: { ...trendCurrent.reported_counts, workload_tokens: 0 } };
  server.use(http.get('/api/v1/usage/trends', () => HttpResponse.json({ ...trendFixture,
    buckets: [{ ...trendFixture.buckets[0], current: unknown, providers: [{ provider: 'codex', totals: unknown }] }] })));
  const { container } = render(<UsageTrends compact />);
  await waitFor(() => expect(container.querySelector('.chart-unreported')).toBeInTheDocument());
  expect(container.querySelector('.usage-area')).not.toBeInTheDocument();
  expect(screen.getByRole('img', { name: /Not reported.*incomplete/ })).toBeInTheDocument();
});

it('leaves gaps for partially reported counters while retaining their observed values', async () => {
  const partial = { ...trendCurrent, event_count: 2 };
  server.use(http.get('/api/v1/usage/trends', () => HttpResponse.json({ ...trendFixture,
    buckets: [{ ...trendFixture.buckets[0], current: partial }] })));
  const { container } = render(<UsageTrends compact />);
  const point = await screen.findByRole('img', { name: /150 current.*incomplete/ });
  expect(container.querySelector('.usage-area')).not.toBeInTheDocument();
  fireEvent.focus(point);
  expect(screen.getByRole('status', { name: 'Chart interval details' })).toHaveTextContent('150 tokens · Incomplete counters');
});

it('shows a previous-period marker for a single interval', async () => {
  const { container } = render(<UsageTrends compact range={{ from: '2026-09-27T00:00:00+00:00', to: '2026-09-28T00:00:00+00:00' }} />);
  await screen.findByRole('img', { name: /150 current.*100 previous/ });
  expect(container.querySelector('.usage-previous-point')).toBeInTheDocument();
});

it('uses the inclusive end date year for an interval crossing New Year', async () => {
  server.use(http.get('/api/v1/usage/trends', () => HttpResponse.json({ ...trendFixture,
    buckets: [{ ...trendFixture.buckets[0], from: '2025-12-29', to: '2026-01-01' }] })));
  render(<UsageTrends compact />);
  expect(await screen.findByRole('img', { name: /Dec 29, 2025 – Dec 31, 2025/ })).toBeInTheDocument();
});

it('keeps the static chart hint mounted while hover details appear and disappear', async () => {
  render(<UsageTrends compact />);
  const point = await screen.findByRole('img', { name: /150 current/ });
  const hint = screen.getByText(/^Hover or focus an interval/);
  fireEvent.mouseEnter(point);
  expect(screen.getByRole('status', { name: 'Chart interval details' })).toHaveTextContent('150');
  expect(screen.getByText(/^Hover or focus an interval/)).toBe(hint);
  fireEvent.mouseLeave(point);
  expect(screen.getByText(/^Hover or focus an interval/)).toBe(hint);
  expect(screen.queryByRole('status', { name: 'Chart interval details' })).not.toBeInTheDocument();
});
