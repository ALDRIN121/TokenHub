import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { usageFixture } from './test/fixtures';
import { server } from './test/server';

const now = Date.parse('2026-10-05T12:00:00Z');
const healthyQuality = { quality_counts: { exact: 12 }, source_freshness: [], state_counts: { healthy: 1 }, source_count: 1, approved_source_count: 1 };
const collectionStatus = { scan_interval_seconds: 30, codex_auto_import: true, auto_import_connectors: ['codex-local'], last_scan_at: '2026-10-05T12:00:00Z', failed_source_count: 0, data_version: 1 };
function serveHealthyQuality() {
  server.use(http.get('/api/v1/data-quality', () => HttpResponse.json(healthyQuality)));
}
async function accuracyNotice() {
  const summary = await screen.findByText('Counter details and accuracy');
  if (!summary.closest('details')?.open) await userEvent.setup().click(summary);
  return screen.findByRole('note', { name: 'Usage freshness and completeness' });
}

describe('usage accuracy guidance', () => {
  afterEach(() => vi.restoreAllMocks());

  it('renews consent through enable even when automatic collection is already enabled', async () => {
    let renewed = false;
    server.use(
      http.get('/api/v1/collection', () => HttpResponse.json({ ...collectionStatus, requires_reapproval_connectors: renewed ? [] : ['codex-local'] })),
      http.post('/api/v1/collection/codex/enable', () => {
        renewed = true;
        return HttpResponse.json({ ...collectionStatus, requires_reapproval_connectors: [] });
      }),
    );
    render(<App />);
    await userEvent.setup().click(screen.getByRole('link', { name: 'Sources' }));
    const codex = await screen.findByRole('article', { name: 'OpenAI Codex' });
    expect(within(codex).getByText(/folder.*permission.*renew/i)).toHaveTextContent(/existing.*new.*archived.*Codex/i);
    await userEvent.setup().click(within(codex).getByRole('button', { name: 'Reconnect and import Codex sessions' }));
    await waitFor(() => expect(within(codex).queryByRole('button', { name: /Reconnect and import/ })).not.toBeInTheDocument());
    expect(within(codex).getByRole('button', { name: 'Stop including new sessions' })).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('offers consent renewal for a connector even if its files are currently absent', async () => {
    server.use(http.get('/api/v1/collection', () => HttpResponse.json({ ...collectionStatus, requires_reapproval_connectors: ['claude-code-local'] })));
    render(<App />);
    await userEvent.setup().click(screen.getByRole('link', { name: 'Sources' }));
    const claude = await screen.findByRole('article', { name: 'Claude Code' });
    expect(within(claude).getByRole('button', { name: 'Reconnect and import Claude Code sessions' })).toBeEnabled();
    expect(within(screen.getByRole('article', { name: 'OpenAI Codex' })).queryByRole('button', { name: /Reconnect/ })).not.toBeInTheDocument();
  });

  it.each([[10, 60, false], [10, 61, true], [30, 90, false], [30, 91, true], [120, 359, false], [120, 361, true]])('uses scan interval %s and age %s to warn about freshness (%s)', async (interval, age, stale) => {
    vi.spyOn(Date, 'now').mockReturnValue(now);
    serveHealthyQuality();
    server.use(http.get('/api/v1/collection', () => HttpResponse.json({ ...collectionStatus, scan_interval_seconds: interval, last_scan_at: new Date(now - Number(age) * 1000).toISOString() })));
    render(<App />);
    const note = await accuracyNotice();
    expect(note).toHaveTextContent(/imported.*observed records/i);
    if (stale) expect(note).toHaveTextContent(/out of date/i);
    else expect(note).not.toHaveTextContent(/out of date/i);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(note.closest('details')).toHaveAttribute('open');
  });

  it('does not claim freshness when records exist without a completed scan', async () => {
    serveHealthyQuality();
    server.use(http.get('/api/v1/collection', () => HttpResponse.json({ ...collectionStatus, last_scan_at: null })));
    render(<App />);
    expect(await accuracyNotice()).toHaveTextContent(/freshness not confirmed.*no completed scan has been reported/i);
    expect(within(screen.getByText('Workload tokens').parentElement!).getByText('125')).toBeInTheDocument();
  });

  it.each(['partial', 'unsupported', 'error', 'permission_denied', 'source_missing', 'discovered'])('warns about %s sources outside the displayed health page', async (state) => {
    vi.spyOn(Date, 'now').mockReturnValue(now);
    server.use(
      http.get('/api/v1/collection', () => HttpResponse.json(collectionStatus)),
      http.get('/api/v1/data-quality', () => HttpResponse.json({ ...healthyQuality, source_count: 301, state_counts: { healthy: 300, [state]: 1 } })),
    );
    render(<App />);
    const note = await accuracyNotice();
    expect(note).toHaveTextContent(/some usage may be missing/i);
    if (state === 'source_missing') expect(note).toHaveTextContent(/previously imported records remain counted/i);
  });

  it('warns about incomplete event counters even when source states are healthy', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(now);
    serveHealthyQuality();
    server.use(
      http.get('/api/v1/collection', () => HttpResponse.json(collectionStatus)),
      http.get('/api/v1/usage', () => HttpResponse.json({ ...usageFixture, totals: { ...usageFixture.totals, incomplete_event_count: 2, workload_tokens: null } })),
    );
    render(<App />);
    expect(await accuracyNotice()).toHaveTextContent(/some usage may be missing/i);
    expect(within(screen.getByText('Workload tokens').parentElement!).getByText('—')).toBeInTheDocument();
  });

  it('warns about failed source reads even when quality still reports healthy', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(now);
    serveHealthyQuality();
    server.use(http.get('/api/v1/collection', () => HttpResponse.json({ ...collectionStatus, failed_source_count: 2 })));
    render(<App />);
    expect(await accuracyNotice()).toHaveTextContent(/some usage may be missing/i);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole('button', { name: /Sync details/ }));
    expect(screen.getByText(/could not read 2 sources/i)).toBeInTheDocument();
    expect(await accuracyNotice()).toHaveTextContent(/last completed scan/i);
    expect(await accuracyNotice()).not.toHaveTextContent(/successful refresh/i);
  });

  it('warns when provider discovery fails even though imported sources remain healthy', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(now);
    serveHealthyQuality();
    server.use(
      http.get('/api/v1/collection', () => HttpResponse.json(collectionStatus)),
      http.get('/api/v1/discovery', () => HttpResponse.json({ providers: [{ connector_id: 'codex-local', provider: 'codex', display_name: 'OpenAI Codex', state: 'error', confidence: 'low', evidence_codes: ['discovery_error'], sources: [] }] })),
    );
    render(<App />);
    expect(await accuracyNotice()).toHaveTextContent(/some usage may be missing/i);
  });

  it('clears stale and incomplete warnings after a successful refresh', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(now);
    let recovered = false;
    server.use(
      http.get('/api/v1/collection', () => HttpResponse.json({ ...collectionStatus, last_scan_at: recovered ? collectionStatus.last_scan_at : '2026-10-04T12:00:00Z', failed_source_count: recovered ? 0 : 1, data_version: recovered ? 2 : 1 })),
      http.get('/api/v1/data-quality', () => HttpResponse.json(recovered ? healthyQuality : { ...healthyQuality, state_counts: { partial: 1 } })),
      http.post('/api/v1/collection/refresh', () => { recovered = true; return HttpResponse.json(collectionStatus); }),
    );
    render(<App />);
    expect(await accuracyNotice()).toHaveTextContent(/out of date/i);
    expect(await accuracyNotice()).toHaveTextContent(/some usage may be missing/i);
    await userEvent.setup().click(screen.getByRole('button', { name: /Sync details/ }));
    await userEvent.setup().click(screen.getByRole('button', { name: 'Refresh data' }));
    await waitFor(() => expect(screen.getByRole('note', { name: 'Usage freshness and completeness' })).not.toHaveTextContent(/out of date|some usage may be missing/i));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('warns about refresh errors without hiding imported totals and recovers on polling', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(now);
    const timers = vi.spyOn(window, 'setInterval');
    serveHealthyQuality();
    let failing = false;
    server.use(http.get('/api/v1/collection', () => failing ? new HttpResponse(null, { status: 503 }) : HttpResponse.json(collectionStatus)));
    const view = render(<App />);
    try {
      await accuracyNotice();
      const poll = timers.mock.calls.find(([, delay]) => delay === 10_000)![0] as () => Promise<void>;
      failing = true;
      await act(async () => { await poll(); });
      expect(await accuracyNotice()).toHaveTextContent(/some usage may be missing/i);
      expect(within(screen.getByText('Workload tokens').parentElement!).getByText('125')).toBeInTheDocument();
      failing = false;
      await act(async () => { await poll(); });
      expect(await accuracyNotice()).not.toHaveTextContent(/some usage may be missing/i);
    } finally { view.unmount(); }
  });
});
