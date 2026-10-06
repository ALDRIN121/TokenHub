import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { expect, it } from 'vitest';
import { UsageExplorer } from './UsageExplorer';
import { usageFixture } from '../test/fixtures';
import { server } from '../test/server';

it('requests bounded pages and replaces rows instead of growing the table', async () => {
  const requests: string[] = [];
  server.use(http.get('/api/v1/usage/models', ({ request }) => {
    const query = new URL(request.url).searchParams;
    requests.push(query.toString());
    const offset = Number(query.get('offset'));
    return HttpResponse.json({ items: Array.from({ length: 25 }, (_, index) => ({ ...usageFixture.models[0], model_name: `model-${index + offset}` })), total: 60, limit: 25, offset });
  }));
  render(<UsageExplorer data={{ ...usageFixture, models: [], sessions: [], paging: true }} />);
  await screen.findByRole('button', { name: /View sessions for model-0 / });
  const table = screen.getByRole('table', { name: 'Model usage' });
  expect(within(table).getAllByRole('row')).toHaveLength(26);
  await userEvent.setup().click(screen.getByRole('button', { name: 'Next page' }));
  await screen.findByRole('button', { name: /View sessions for model-25 / });
  expect(within(table).getAllByRole('row')).toHaveLength(26);
  expect(requests.every((query) => new URLSearchParams(query).get('limit') === '25')).toBe(true);
});

it('discards obsolete search responses', async () => {
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  const seen: string[] = [];
  server.use(http.get('/api/v1/usage/models', async ({ request }) => {
    const q = new URL(request.url).searchParams.get('q') ?? '';
    seen.push(q);
    if (q === 'old') await held;
    return HttpResponse.json({ items: [{ ...usageFixture.models[0], model_name: q || 'initial' }], total: 1, limit: 25, offset: 0 });
  }));
  const view = render(<UsageExplorer data={{ ...usageFixture, models: [], sessions: [], paging: true }} />);
  try {
    await screen.findByRole('button', { name: /View sessions for initial / });
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'old' } });
    await waitFor(() => expect(seen).toContain('old'));
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'new' } });
    await screen.findByRole('button', { name: /View sessions for new / });
    release();
    await waitFor(() => expect(screen.queryByRole('button', { name: /View sessions for old / })).not.toBeInTheDocument());
  } finally { release(); view.unmount(); }
});

it('switches grouping safely and loads model detail only when expanded', async () => {
  let details = 0;
  server.use(
    http.get('/api/v1/usage/models', () => HttpResponse.json({ items: usageFixture.models, total: 1, offset: 0, limit: 25 })),
    http.get('/api/v1/usage/sessions', () => HttpResponse.json({ items: usageFixture.sessions.map((row) => ({ ...row, models: [] })), total: 1, offset: 0, limit: 25 })),
    http.get('/api/v1/usage/sessions/:key/models', () => { details++; return HttpResponse.json({ items: usageFixture.models, total: 1, offset: 0, limit: 25 }); }),
  );
  render(<UsageExplorer data={{ ...usageFixture, models: [], sessions: [], paging: true }} />);
  await screen.findByRole('button', { name: /View sessions for gpt-test / });
  await userEvent.setup().click(screen.getByRole('tab', { name: /^Sessions / }));
  const toggle = await screen.findByRole('button', { name: /Show model breakdown for session/ });
  expect(details).toBe(0);
  await userEvent.setup().click(toggle);
  await screen.findByText('Models in this session');
  await waitFor(() => expect(details).toBe(1));
  expect(await screen.findByText('gpt-test')).toBeInTheDocument();
  await userEvent.setup().click(screen.getByRole('tab', { name: /^Models / }));
  await screen.findByRole('button', { name: /View sessions for gpt-test / });
});

it('recovers detail pagination after date bounds or refreshed data shrink a session', async () => {
  let reduced = false;
  const offsets: number[] = [];
  server.use(
    http.get('/api/v1/usage/models', () => HttpResponse.json({ items: usageFixture.models, total: 1, offset: 0, limit: 25 })),
    http.get('/api/v1/usage/sessions', () => HttpResponse.json({ items: usageFixture.sessions.map((row) => ({ ...row, models: [] })), total: 1, offset: 0, limit: 25 })),
    http.get('/api/v1/usage/sessions/:key/models', ({ request }) => {
      const query = new URL(request.url).searchParams;
      const offset = Number(query.get('offset'));
      offsets.push(offset);
      const narrow = query.has('from') || reduced;
      const total = narrow ? 1 : 26;
      return HttpResponse.json({ items: offset >= total ? [] : [{ ...usageFixture.models[0], model_name: narrow ? 'remaining-model' : `group-${offset}` }], total, offset, limit: 25 });
    }),
  );
  const data = { ...usageFixture, models: [], sessions: [], paging: true, data_version: 1 };
  const view = render(<UsageExplorer data={data} />);
  await screen.findByRole('button', { name: /View sessions for gpt-test / });
  await userEvent.setup().click(screen.getByRole('tab', { name: /^Sessions / }));
  const toggle = await screen.findByRole('button', { name: /Show model breakdown for session/ });
  await userEvent.setup().click(toggle);
  await screen.findByText('group-0');
  await userEvent.setup().click(await screen.findByRole('button', { name: 'Next models' }));
  await screen.findByText('group-25');
  view.rerender(<UsageExplorer data={data} period="day" selectedDay="2026-09-29" />);
  await userEvent.setup().click(await screen.findByRole('button', { name: /Show model breakdown for session/ }));
  await screen.findByText('remaining-model');
  expect(offsets.at(-1)).toBe(0);
  view.rerender(<UsageExplorer data={data} />);
  await userEvent.setup().click(await screen.findByRole('button', { name: /Show model breakdown for session/ }));
  await screen.findByText('group-0');
  await userEvent.setup().click(await screen.findByRole('button', { name: 'Next models' }));
  await screen.findByText('group-25');
  reduced = true;
  view.rerender(<UsageExplorer data={{ ...data, data_version: 2 }} />);
  await screen.findByText('remaining-model');
  expect(offsets.at(-1)).toBe(0);
});

it('labels actual model groups including the same name on different providers and unknown models', async () => {
  server.use(
    http.get('/api/v1/usage/models', () => HttpResponse.json({ items: [usageFixture.models[0], { ...usageFixture.models[0], provider: 'claude_code' }, { ...usageFixture.models[0], model_name: null }], total: 3, offset: 0, limit: 25 })),
    http.get('/api/v1/usage/sessions', () => HttpResponse.json({ items: usageFixture.sessions, total: 1, offset: 0, limit: 25 })),
  );
  render(<UsageExplorer data={{ ...usageFixture, models: [], sessions: [], paging: true, providers: [usageFixture.providers[0], { ...usageFixture.providers[0], provider: 'claude_code' }], unknown_model_events: { codex: 1 } }} />);
  await screen.findByRole('button', { name: 'View sessions for Model not recorded in Codex' });
  expect(screen.getByRole('tab', { name: 'Models 3 groups' })).toBeInTheDocument();
  await userEvent.setup().click(screen.getByRole('tab', { name: /^Sessions / }));
  await screen.findByRole('button', { name: /Show model breakdown for session/ });
  expect(screen.getByRole('tab', { name: 'Models 3 groups' })).toBeInTheDocument();
});

it('hides the old page immediately while a new agent scope is pending', async () => {
 let release!: () => void; const held = new Promise<void>(resolve => { release = resolve; });
 server.use(http.get('/api/v1/usage/models', async ({ request }) => {
  const provider = new URL(request.url).searchParams.get('provider');
  if (provider === 'hermes') await held;
  return HttpResponse.json({ items: [{ ...usageFixture.models[0], provider: provider || 'codex', model_name: provider === 'hermes' ? 'fresh' : 'old-scope' }], total: 1, offset: 0, limit: 25 });
 }));
 const data = { ...usageFixture, models: [], sessions: [], paging: true };
 const view = render(<UsageExplorer data={data} provider="all" />);
 try {
  await screen.findByRole('button', { name: /View sessions for old-scope / });
  view.rerender(<UsageExplorer data={data} provider="hermes" />);
  expect(screen.queryByRole('button', { name: /View sessions for old-scope / })).not.toBeInTheDocument();
  release(); await screen.findByRole('button', { name: /View sessions for fresh / });
 } finally { release(); view.unmount(); }
});

it('controlled Models navigation requests the full model scope after drilldown', async () => {
 const requests: URLSearchParams[] = [];
 server.use(
  http.get('/api/v1/usage/models', ({ request }) => {
   const query = new URL(request.url).searchParams; requests.push(query);
   return HttpResponse.json({ items: usageFixture.models, total: 1, offset: 0, limit: 25 });
  }),
  http.get('/api/v1/usage/sessions', () => HttpResponse.json({ items: usageFixture.sessions, total: 1, offset: 0, limit: 25 })),
 );
 const data = { ...usageFixture, models: [], sessions: [], paging: true };
 const view = render(<UsageExplorer data={data} mode="models" />);
 await screen.findByRole('button', { name: /View sessions for gpt-test / });
 await userEvent.setup().click(screen.getByRole('button', { name: /View sessions for gpt-test / }));
 view.rerender(<UsageExplorer data={data} mode="sessions" />);
 await screen.findByRole('table', { name: 'Session usage' });
 await waitFor(() => expect(requests.some(query => query.get('model') === 'gpt-test')).toBe(true));
 const before = requests.length;
 view.rerender(<UsageExplorer data={data} mode="models" />);
 await screen.findByRole('button', { name: /View sessions for gpt-test / });
 await waitFor(() => expect(requests.length).toBeGreaterThan(before));
 expect(requests.at(-1)?.has('model')).toBe(false);
 expect(requests.at(-1)?.has('provider')).toBe(false);
});
