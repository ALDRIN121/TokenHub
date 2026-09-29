import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it, vi } from 'vitest';

import App from './App';
import { server } from './test/server';
import {
  discoveryFixture,
  usageFixture,
} from './test/fixtures';

describe('TokenHub client', () => {
  it('scans on manual refresh before showing new usage and its refresh time', async () => {
    let workload = 125;
    let lastScan: string | null = null;
    server.use(
      http.get('/api/v1/usage', () => HttpResponse.json({ ...usageFixture, totals: { ...usageFixture.totals, workload_tokens: workload } })),
      http.get('/api/v1/collection', () => HttpResponse.json({ scan_interval_seconds: 30, codex_auto_import: true, auto_import_connectors: ['codex-local'], last_scan_at: lastScan, failed_source_count: 0, data_version: workload })),
      http.post('/api/v1/collection/refresh', () => {
        workload = 140;
        lastScan = '2026-09-29T08:30:00+00:00';
        return HttpResponse.json({ scan_interval_seconds: 30, codex_auto_import: true, auto_import_connectors: ['codex-local'], last_scan_at: lastScan, failed_source_count: 0, data_version: workload });
      }),
    );
    render(<App />);
    await screen.findByRole('table', { name: 'Model usage' });
    expect(screen.queryByText(/Last refreshed/)).not.toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole('button', { name: 'Refresh data' }));
    await waitFor(() => expect(within(screen.getByText('Workload tokens').parentElement!).getByText('140')).toHaveAttribute('title', '140'));
    expect(screen.getByText(/Last refreshed Sep 29, 2026/)).toBeInTheDocument();
  });

  it('waits for all requests in a failed batch before starting another refresh', async () => {
    const timers = vi.spyOn(window, 'setInterval');
    const fetches = vi.spyOn(globalThis, 'fetch');
    let finish!: () => void;
    let failedResponse = false;
    const pending = new Promise<void>((resolve) => { finish = resolve; });
    const view = render(<App />);
    let polling: Promise<void> | undefined;
    try {
      await screen.findByRole('table', { name: 'Model usage' });
      let version = 1;
      server.use(
        http.get('/api/v1/collection', () => HttpResponse.json({ scan_interval_seconds: 30, codex_auto_import: false, auto_import_connectors: [], last_scan_at: null, failed_source_count: 0, data_version: ++version })),
        http.get('/api/v1/discovery', () => {
          failedResponse = true;
          return new HttpResponse(null, { status: 503 });
        }),
        http.get('/api/v1/data-quality', async () => {
          await pending;
          return HttpResponse.json({ quality_counts: {}, source_freshness: [] });
        }),
      );
      const call = timers.mock.calls.find(([, delay]) => delay === 10_000)!;
      const initialCalls = fetches.mock.calls.length;
      polling = (call[0] as () => Promise<void>)();
      await waitFor(() => expect(failedResponse).toBe(true));
      await userEvent.setup().click(screen.getByRole('button', { name: 'Refresh data' }));
      // The tick's status read, its refresh (status + 3 requests), and the manual scan.
      expect(fetches.mock.calls.length - initialCalls).toBe(6);
    } finally {
      finish();
      await act(async () => { await polling; });
      view.unmount();
      timers.mockRestore();
      fetches.mockRestore();
    }
  });

  it('does not overlap a manual refresh with a pending automatic refresh', async () => {
    const timers = vi.spyOn(window, 'setInterval');
    const fetches = vi.spyOn(globalThis, 'fetch');
    let finish!: () => void;
    const pending = new Promise<void>((resolve) => { finish = resolve; });
    const view = render(<App />);
    let polling: Promise<void> | undefined;
    try {
      await screen.findByRole('table', { name: 'Model usage' });
      let version = 1;
      server.use(
        http.get('/api/v1/collection', () => HttpResponse.json({ scan_interval_seconds: 30, codex_auto_import: false, auto_import_connectors: [], last_scan_at: null, failed_source_count: 0, data_version: ++version })),
        http.get('/api/v1/discovery', async () => {
          await pending;
          return HttpResponse.json(discoveryFixture);
        }),
      );
      const call = timers.mock.calls.find(([, delay]) => delay === 10_000)!;
      const initialCalls = fetches.mock.calls.length;
      polling = (call[0] as () => Promise<void>)();
      await userEvent.setup().click(screen.getByRole('button', { name: 'Refresh data' }));
      expect(fetches.mock.calls.length - initialCalls).toBe(6);
    } finally {
      finish();
      await act(async () => { await polling; });
      view.unmount();
      timers.mockRestore();
      fetches.mockRestore();
    }
  });

  it('updates displayed usage automatically and stops polling on unmount', async () => {
    const timers = vi.spyOn(window, 'setInterval');
    const cleanup = vi.spyOn(window, 'clearInterval');
    let workload = 125;
    let version = 1;
    server.use(
      http.get('/api/v1/usage', () => HttpResponse.json({ ...usageFixture, totals: { ...usageFixture.totals, workload_tokens: workload } })),
      http.get('/api/v1/collection', () => HttpResponse.json({ scan_interval_seconds: 30, codex_auto_import: false, auto_import_connectors: [], last_scan_at: null, failed_source_count: 0, data_version: version })),
    );
    const view = render(<App />);
    try {
      await screen.findByRole('table', { name: 'Model usage' });
      const call = timers.mock.calls.find(([, delay]) => delay === 10_000);
      expect(call).toBeDefined();
      workload = 10_000;
      version = 2;
      await act(async () => { await (call![0] as () => Promise<void>)(); });
      expect(within(screen.getByText('Workload tokens').parentElement!).getByText('10K')).toHaveAttribute('title', '10,000');
      view.unmount();
      expect(cleanup).toHaveBeenCalled();
    } finally {
      view.unmount();
      timers.mockRestore();
      cleanup.mockRestore();
    }
  });

  it('reads only the small status while nothing has changed', async () => {
    const timers = vi.spyOn(window, 'setInterval');
    const fetches = vi.spyOn(globalThis, 'fetch');
    const view = render(<App />);
    try {
      await screen.findByRole('table', { name: 'Model usage' });
      const call = timers.mock.calls.find(([, delay]) => delay === 10_000)!;
      const before = fetches.mock.calls.length;
      await act(async () => { await (call[0] as () => Promise<void>)(); });
      await act(async () => { await (call[0] as () => Promise<void>)(); });
      const urls = fetches.mock.calls.slice(before).map(([input]) => String(input instanceof Request ? input.url : input));
      expect(urls).toHaveLength(2);
      expect(urls.every((url) => url.endsWith('/api/v1/collection'))).toBe(true);
    } finally {
      view.unmount();
      timers.mockRestore();
      fetches.mockRestore();
    }
  });

  it('offers one Copilot approval for every discovered session', async () => {
    const source = discoveryFixture.providers[1].sources[0];
    const provider = {
      ...discoveryFixture.providers[1],
      connector_id: 'vscode-copilot-local',
      provider: 'vscode_copilot',
      display_name: 'VS Code Copilot',
      sources: ['healthy', 'discovered', 'discovered'].map((state, index) => ({
        ...source,
        source_id: `vscode-copilot-local:session-${index}`,
        connector_id: 'vscode-copilot-local',
        provider: 'vscode_copilot',
        display_name: 'VS Code Copilot chat session',
        state,
      })),
    };
    let enabled = false;
    server.use(
      http.get('/api/v1/discovery', () => HttpResponse.json({ providers: [provider] })),
      http.get('/api/v1/collection', () => HttpResponse.json({
        scan_interval_seconds: 30, codex_auto_import: false,
        auto_import_connectors: enabled ? ['vscode-copilot-local'] : [],
        last_scan_at: null, failed_source_count: 0, data_version: 1,
      })),
      http.post('/api/v1/collection/vscode_copilot/enable', () => {
        enabled = true;
        return HttpResponse.json({
          scan_interval_seconds: 30, codex_auto_import: false,
          auto_import_connectors: ['vscode-copilot-local'],
          last_scan_at: null, failed_source_count: 0, data_version: 1,
        });
      }),
    );
    render(<App />);
    const card = await screen.findByRole('article', { name: 'VS Code Copilot' });
    expect(within(card).getByText(/3 sessions found/)).toBeInTheDocument();
    expect(within(card).getByText(/2 awaiting approval/)).toBeInTheDocument();
    expect(within(card).queryByRole('button', { name: 'Approve source' })).not.toBeInTheDocument();
    expect(within(card).queryByRole('button', { name: 'Rescan source' })).not.toBeInTheDocument();

    await userEvent.setup().click(within(card).getByRole('button', { name: 'Approve and import Copilot sessions' }));
    expect(await screen.findByRole('status')).toHaveTextContent(/existing and new VS Code Copilot usage/);
    expect(within(card).queryByRole('button', { name: 'Approve and import Copilot sessions' })).not.toBeInTheDocument();
  });

  it('filters model usage to a chosen local calendar day', async () => {
    const requested: string[] = [];
    server.use(http.get('/api/v1/usage', ({ request }) => {
      requested.push(new URL(request.url).search);
      return HttpResponse.json({
        totals: { ...usageFixture.totals, workload_tokens: 7 },
        providers: [{ ...usageFixture.providers[0], workload_tokens: 7 }],
        models: [{ ...usageFixture.models[0], workload_tokens: 7 }],
        sessions: [],
      });
    }));
    render(<App />);
    await screen.findByRole('table', { name: 'Model usage' });
    await userEvent.setup().selectOptions(screen.getByRole('combobox', { name: 'Date range' }), 'day');
    fireEvent.change(screen.getByLabelText('Usage date'), { target: { value: '2026-09-28' } });
    await waitFor(() => expect(requested.some((search) => {
      const bounds = new URLSearchParams(search);
      return bounds.get('from') === new Date(2026, 8, 28).toISOString()
        && bounds.get('to') === new Date(2026, 8, 29).toISOString();
    })).toBe(true));
  });

  it.each([
    ['OpenAI Codex', 'codex-local', 'codex', 'Codex sessions'],
    ['Claude Code', 'claude-code-local', 'claude_code', 'Claude Code sessions'],
    ['Hermes Agent', 'hermes-local', 'hermes', 'Hermes Agent usage'],
    ['Antigravity', 'antigravity-local', 'antigravity', 'Antigravity conversations'],
  ])('offers one approval for all %s sources', async (name, connectorId, providerId, label) => {
    const baseline = discoveryFixture.providers.find((item) => item.connector_id === connectorId) ?? discoveryFixture.providers[1];
    const sample = discoveryFixture.providers[1].sources[0];
    const provider = {
      ...baseline, connector_id: connectorId, display_name: name, provider: providerId, state: 'discovered',
      sources: ['healthy', 'discovered', 'discovered'].map((state, index) => ({
        ...sample, connector_id: connectorId, provider: providerId,
        source_id: `${connectorId}:sample-${index}`, state, scan_supported: true,
      })),
    };
    let enabled = false;
    server.use(
      http.get('/api/v1/discovery', () => HttpResponse.json({ providers: [provider] })),
      http.get('/api/v1/collection', () => HttpResponse.json({
        scan_interval_seconds: 30, codex_auto_import: enabled && providerId === 'codex',
        auto_import_connectors: enabled ? [connectorId] : [], last_scan_at: null, failed_source_count: 0, data_version: 1,
      })),
      http.post(`/api/v1/collection/${providerId}/enable`, () => {
        enabled = true;
        return HttpResponse.json({
          scan_interval_seconds: 30, codex_auto_import: providerId === 'codex',
          auto_import_connectors: [connectorId], last_scan_at: null, failed_source_count: 0, data_version: 1,
        });
      }),
    );
    render(<App />);
    const card = await screen.findByRole('article', { name });
    expect(within(card).getByText(/3 sources found/)).toBeInTheDocument();
    expect(within(card).getByText(/2 awaiting approval/)).toBeInTheDocument();
    expect(within(card).queryByRole('button', { name: 'Approve source' })).not.toBeInTheDocument();
    expect(within(card).queryByRole('button', { name: 'Rescan source' })).not.toBeInTheDocument();
    await userEvent.setup().click(within(card).getByRole('button', { name: `Approve and import ${label}` }));
    await waitFor(() => expect(enabled).toBe(true));
    expect(within(card).getByRole('button', { name: 'Stop including new sessions' })).toBeEnabled();
  });

  it('does not turn a failed detection into a missing installation', async () => {
    server.use(http.get('/api/v1/discovery', () => HttpResponse.json({
      providers: [{ connector_id: 'codex-local', display_name: 'OpenAI Codex', provider: 'codex', state: 'error', confidence: 'low', evidence_codes: ['discovery_error'], sources: [] }],
    })));
    render(<App />);
    const card = await screen.findByRole('article', { name: 'OpenAI Codex' });
    expect(within(card).queryByText('Not detected')).not.toBeInTheDocument();
    expect(within(card).getByText(/could not be checked/)).toBeInTheDocument();
  });

  it('omits composition percentages when incomplete records make totals non-comparable', async () => {
    // One complete 100 + 25 record and one input-only 50 record: workload
    // contains only complete events, but input sums all observed input values.
    server.use(http.get('/api/v1/usage', () => HttpResponse.json({ ...usageFixture, totals: { ...usageFixture.totals, workload_tokens: 125, input_total_tokens: 150, output_total_tokens: 25 } })));
    render(<App />);
    await screen.findByRole('heading', { name: 'Token summary' });
    expect(screen.queryByRole('img', { name: /Workload composition/ })).not.toBeInTheDocument();
    expect(screen.getByText(/incomplete token records/)).toBeInTheDocument();
  });

  it('shows composition percentages only from complete observed counts', async () => {
    render(<App />);
    expect(await screen.findByRole('img', { name: 'Workload composition: input 80.0%, output 20.0%' })).toBeInTheDocument();
  });

  it('shows genuine imported zero totals without dividing by zero', async () => {
    server.use(http.get('/api/v1/usage', () => HttpResponse.json({ ...usageFixture, totals: { ...usageFixture.totals, workload_tokens: 0, input_total_tokens: 0, output_total_tokens: 0 } })));
    render(<App />);
    await screen.findByRole('heading', { name: 'Token summary' });
    expect(within(screen.getByText('Workload tokens').parentElement!).getByText('0')).toBeInTheDocument();
    expect(screen.queryByRole('img', { name: /Workload composition/ })).not.toBeInTheDocument();
    expect(screen.getByText(/imported workload is zero/)).toBeInTheDocument();
  });

  it('explains missing counts when events are imported but composition is unknown', async () => {
    server.use(http.get('/api/v1/usage', () => HttpResponse.json({ ...usageFixture, totals: { ...usageFixture.totals, workload_tokens: null, output_total_tokens: null, event_count: 1 } })));
    render(<App />);
    await screen.findByRole('heading', { name: 'Token summary' });
    expect(screen.queryByRole('img', { name: /Workload composition/ })).not.toBeInTheDocument();
    expect(screen.getByText(/missing token counts/)).toBeInTheDocument();
  });

  it('recovers from an API failure without reloading the page', async () => {
    server.use(http.get('/api/v1/discovery', () => new HttpResponse(null, { status: 503 })));
    const user = userEvent.setup();
    render(<App />);
    expect(await screen.findByRole('alert')).toBeInTheDocument();

    server.use(http.get('/api/v1/discovery', () => HttpResponse.json(discoveryFixture)));
    await user.click(screen.getByRole('button', { name: 'Refresh data' }));
    expect(await screen.findByRole('article', { name: 'OpenAI Codex' })).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('does not report an empty import as fully readable', async () => {
    server.use(
      http.get('/api/v1/data-quality', () =>
        HttpResponse.json({ quality_counts: {}, source_freshness: [] }),
      ),
    );
    render(<App />);
    await screen.findByRole('heading', { name: 'Data quality' });
    expect(screen.queryByText(/fully readable/)).not.toBeInTheDocument();
    expect(screen.getByText(/No source data has been imported yet/)).toBeInTheDocument();
  });

  it('shows the cache-write breakdown returned by the API', async () => {
    render(<App />);
    const label = await screen.findByText('Cache write tokens');
    expect(within(label.parentElement!).getByText('10')).toBeInTheDocument();
  });

  it('disables rebuilding until a supported source has been approved', async () => {
    render(<App />);
    await screen.findByRole('heading', { name: 'Data quality' });
    expect(screen.getByRole('button', { name: 'Rebuild index' })).toBeDisabled();
  });

  it('shows a detected Codex source and makes approval available', async () => {
    server.use(http.get('/api/v1/discovery', () => HttpResponse.json(discoveryFixture)));
    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Local sources' })).toBeInTheDocument();
    expect(screen.getByText('OpenAI Codex')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Approve and import Codex sessions' })).toBeEnabled();

    // Only the scan-capable Codex source offers an action; the discovery-only
    // Hermes source stays read-only, and Claude Code reports no source at all.
    expect(screen.queryAllByRole('button', { name: 'Approve source' })).toHaveLength(0);
    expect(screen.queryAllByRole('button', { name: 'Rescan source' })).toHaveLength(0);

    const claudeCard = screen.getByRole('article', { name: 'Claude Code' });
    expect(within(claudeCard).getByText(/No local source was found/)).toBeInTheDocument();
    expect(within(claudeCard).queryAllByRole('button')).toHaveLength(0);

    const hermesCard = screen.getByRole('article', { name: 'Hermes Agent' });
    expect(within(hermesCard).getByText('Detection only')).toBeInTheDocument();
    expect(within(hermesCard).queryAllByRole('button')).toHaveLength(0);

    const codexCard = screen.getByRole('article', { name: 'OpenAI Codex' });
    expect(within(codexCard).getByRole('button', { name: 'Approve and import Codex sessions' })).toBeEnabled();
  });

  it('renders unknown metrics as an em dash instead of a false zero', async () => {
    server.use(
      http.get('/api/v1/usage', () =>
        HttpResponse.json({ ...usageFixture, totals: { ...usageFixture.totals, workload_tokens: null } }),
      ),
    );
    render(<App />);

    expect(within((await screen.findByText('Workload tokens')).parentElement!).getByText('—')).toBeInTheDocument();
  });

  it('keeps a genuinely reported zero as a zero', async () => {
    server.use(
      http.get('/api/v1/usage', () =>
        HttpResponse.json({ ...usageFixture, totals: { ...usageFixture.totals, workload_tokens: null, input_total_tokens: 0 } }),
      ),
    );
    render(<App />);

    expect(within((await screen.findByText('Input tokens', { selector: 'dt' })).parentElement!).getByText('0')).toBeInTheDocument();
    expect(within(screen.getByText('Workload tokens').parentElement!).getByText('—')).toBeInTheDocument();
  });

  it('renders the confidence the API reported, not one it worked out itself', async () => {
    // Two evidence codes would be "high" under the old client-side rule; the API
    // says "low" here, so a client that derives confidence instead of reading it
    // renders the wrong value and fails this test.
    const contradicted = {
      providers: discoveryFixture.providers.map((provider) =>
        provider.connector_id === 'codex-local'
          ? { ...provider, confidence: 'low', evidence_codes: ['known_root_exists', 'session_source_found'] }
          : provider,
      ),
    };
    server.use(http.get('/api/v1/discovery', () => HttpResponse.json(contradicted)));

    render(<App />);

    const codexCard = await screen.findByRole('article', { name: 'OpenAI Codex' });
    expect(within(codexCard).getByText(/Confidence: low/)).toBeInTheDocument();
    expect(within(codexCard).queryByText(/derived/)).not.toBeInTheDocument();
  });

  it('lists source status in data quality and explains partial and unsupported sources', async () => {
    server.use(
      http.get('/api/v1/data-quality', () =>
        HttpResponse.json({
          quality_counts: { partial: 1, unavailable: 2 },
          source_freshness: [
            {
              source_id:
                'codex-local:3f9a1c47d2b8e05f6a1c93d47e0b25a8c6f1d93e47b2a05c8d1f63e9a472c8b05',
              state: 'partial',
              latest_event_at: '2026-09-20T12:00:00Z',
              unsupported_records: 2,
            },
            {
              source_id:
                'hermes-local:a1d7f3c95b2e84016f9c3a75d0b8e142c6f3d91a7b2e50834c9d1f6a3b8e2074',
              state: 'unsupported',
              latest_event_at: null,
              unsupported_records: null,
            },
          ],
        }),
      ),
    );

    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Data quality' })).toBeInTheDocument();
    expect(screen.getByText('Codex session')).toBeInTheDocument();
    expect(screen.getByText('Hermes state database')).toBeInTheDocument();
    expect(screen.getByText('Unsupported records: 2')).toHaveAttribute('title', expect.stringContaining('no input/output split'));
    expect(screen.getByText(/not fully readable \(partial, unsupported\)/)).toBeInTheDocument();
    expect(screen.getByText(/only a combined total, with no input\/output split/)).toBeInTheDocument();
  });
});

it('shows summary and source health beside the explorer without duplicate navigation', async () => {
  render(<App />);
  await screen.findByRole('table', { name: 'Model usage' });
  expect(screen.getByRole('heading', { name: 'Token summary' })).toBeInTheDocument();
  const sources = screen.getByRole('heading', { name: 'Local sources' }).closest('section')!;
  expect(within(sources).getByRole('heading', { name: 'Data quality' })).toBeInTheDocument();
  expect(screen.queryByRole('link', { name: 'Overview' })).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: 'Data quality' })).not.toBeInTheDocument();
});
