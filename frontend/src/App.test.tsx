import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it, vi } from 'vitest';

import App from './App';
import { server } from './test/server';
import {
  dashboardFixture,
  discoveryFixture,
  discoveryWithCodexState,
  importOutcomeFixture,
} from './test/fixtures';

describe('TokenHub client', () => {
  it('waits for all requests in a failed batch before starting another refresh', async () => {
    const timers = vi.spyOn(window, 'setInterval');
    const fetches = vi.spyOn(globalThis, 'fetch');
    let finish!: () => void;
    let failedResponse = false;
    const pending = new Promise<void>((resolve) => { finish = resolve; });
    const view = render(<App initialView="overview" />);
    let polling: Promise<void> | undefined;
    try {
      await screen.findByText('125');
      server.use(
        http.get('/api/v1/discovery', () => {
          failedResponse = true;
          return new HttpResponse(null, { status: 503 });
        }),
        http.get('/api/v1/dashboard', async () => {
          await pending;
          return HttpResponse.json(dashboardFixture);
        }),
      );
      const call = timers.mock.calls.find(([, delay]) => delay === 10_000)!;
      const initialCalls = fetches.mock.calls.length;
      polling = (call[0] as () => Promise<void>)();
      await waitFor(() => expect(failedResponse).toBe(true));
      await userEvent.setup().click(screen.getByRole('button', { name: 'Refresh data' }));
      expect(fetches.mock.calls.length - initialCalls).toBe(5);
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
    const view = render(<App initialView="overview" />);
    let polling: Promise<void> | undefined;
    try {
      await screen.findByText('125');
      server.use(http.get('/api/v1/discovery', async () => {
        await pending;
        return HttpResponse.json(discoveryFixture);
      }));
      const call = timers.mock.calls.find(([, delay]) => delay === 10_000)!;
      const initialCalls = fetches.mock.calls.length;
      polling = (call[0] as () => Promise<void>)();
      await userEvent.setup().click(screen.getByRole('button', { name: 'Refresh data' }));
      expect(fetches.mock.calls.length - initialCalls).toBe(5);
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
    server.use(http.get('/api/v1/dashboard', () => HttpResponse.json({ ...dashboardFixture, workload_tokens: workload })));
    const view = render(<App initialView="overview" />);
    try {
      await screen.findByText('125');
      const call = timers.mock.calls.find(([, delay]) => delay === 10_000);
      expect(call).toBeDefined();
      workload = 10_000;
      await act(async () => { await (call![0] as () => Promise<void>)(); });
      expect(screen.getByText('10K')).toHaveAttribute('title', '10,000');
      view.unmount();
      expect(cleanup).toHaveBeenCalled();
    } finally {
      view.unmount();
      timers.mockRestore();
      cleanup.mockRestore();
    }
  });

  it('starts importing immediately after approving a source', async () => {
    let scans = 0;
    server.use(http.post('/api/v1/sources/:sourceId/rescan', () => {
      scans += 1;
      return HttpResponse.json(importOutcomeFixture);
    }));
    render(<App initialView="overview" />);
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Approve source' }));
    await screen.findByRole('status');
    expect(scans).toBe(1);
  });

  it('offers explicit consent to automatically include future Codex sessions', async () => {
    render(<App initialView="overview" />);
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Include new sessions automatically' }));
    expect(await screen.findByRole('status')).toHaveTextContent(/existing and new Codex sessions/);
  });

  it('does not turn a failed detection into a missing installation', async () => {
    server.use(http.get('/api/v1/discovery', () => HttpResponse.json({
      providers: [{ connector_id: 'codex-local', display_name: 'OpenAI Codex', provider: 'codex', state: 'error', confidence: 'low', evidence_codes: ['discovery_error'], sources: [] }],
    })));
    render(<App initialView="overview" />);
    const card = await screen.findByRole('article', { name: 'OpenAI Codex' });
    expect(within(card).queryByText('Not detected')).not.toBeInTheDocument();
    expect(within(card).getByText(/could not be checked/)).toBeInTheDocument();
  });

  it('omits composition percentages when incomplete records make totals non-comparable', async () => {
    // One complete 100 + 25 record and one input-only 50 record: workload
    // contains only complete events, but input sums all observed input values.
    server.use(http.get('/api/v1/dashboard', () => HttpResponse.json({ ...dashboardFixture, workload_tokens: 125, input_total_tokens: 150, output_total_tokens: 25 })));
    render(<App initialView="overview" />);
    await screen.findByRole('heading', { name: 'Observed workload' });
    expect(screen.queryByRole('img', { name: /Workload composition/ })).not.toBeInTheDocument();
    expect(screen.getByText(/incomplete token records/)).toBeInTheDocument();
  });

  it('shows composition percentages only from complete observed counts', async () => {
    render(<App initialView="overview" />);
    expect(await screen.findByRole('img', { name: 'Workload composition: input 80.0%, output 20.0%' })).toBeInTheDocument();
  });

  it('shows genuine imported zero totals without dividing by zero', async () => {
    server.use(http.get('/api/v1/dashboard', () => HttpResponse.json({ ...dashboardFixture, workload_tokens: 0, input_total_tokens: 0, output_total_tokens: 0 })));
    render(<App initialView="overview" />);
    await screen.findByRole('heading', { name: 'Observed workload' });
    expect(screen.getAllByText('0')).toHaveLength(3);
    expect(screen.queryByRole('img', { name: /Workload composition/ })).not.toBeInTheDocument();
    expect(screen.getByText(/imported workload is zero/)).toBeInTheDocument();
  });

  it('explains missing counts when events are imported but composition is unknown', async () => {
    server.use(http.get('/api/v1/dashboard', () => HttpResponse.json({ ...dashboardFixture, workload_tokens: null, output_total_tokens: null, event_count: 1 })));
    render(<App initialView="overview" />);
    await screen.findByRole('heading', { name: 'Observed workload' });
    expect(screen.queryByRole('img', { name: /Workload composition/ })).not.toBeInTheDocument();
    expect(screen.getByText(/missing token counts/)).toBeInTheDocument();
  });

  it('recovers from an API failure without reloading the page', async () => {
    server.use(http.get('/api/v1/discovery', () => new HttpResponse(null, { status: 503 })));
    const user = userEvent.setup();
    render(<App initialView="overview" />);
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
    render(<App initialView="overview" />);
    await screen.findByRole('heading', { name: 'Data quality' });
    expect(screen.queryByText(/fully readable/)).not.toBeInTheDocument();
    expect(screen.getByText(/No source data has been imported yet/)).toBeInTheDocument();
  });

  it('shows the cache-write breakdown returned by the API', async () => {
    render(<App initialView="overview" />);
    const label = await screen.findByText('Cache write tokens');
    expect(within(label.parentElement!).getByText('10')).toBeInTheDocument();
  });

  it('disables rebuilding until a supported source has been approved', async () => {
    render(<App initialView="overview" />);
    await screen.findByRole('heading', { name: 'Data quality' });
    expect(screen.getByRole('button', { name: 'Rebuild index' })).toBeDisabled();
  });

  it('locks other source actions while an approval is in progress', async () => {
    const codex = discoveryFixture.providers.find((provider) => provider.connector_id === 'codex-local')!;
    const twoSources = {
      providers: [{ ...codex, sources: [codex.sources[0], { ...codex.sources[0], source_id: 'codex-local:second' }] }],
    };
    let finish!: () => void;
    const pending = new Promise<void>((resolve) => { finish = resolve; });
    server.use(
      http.get('/api/v1/discovery', () => HttpResponse.json(twoSources)),
      http.post('/api/v1/sources/:sourceId/approve', async ({ params }) => {
        await pending;
        return HttpResponse.json({ source_id: params.sourceId, provider: 'codex', display_name: 'Codex session', state: 'approved' });
      }),
    );
    const user = userEvent.setup();
    render(<App initialView="overview" />);
    const buttons = await screen.findAllByRole('button', { name: 'Approve source' });
    try {
      await user.click(buttons[0]);
      expect(buttons[1]).toBeDisabled();
      expect(screen.getByRole('button', { name: 'Refresh data' })).toBeDisabled();
    } finally {
      finish();
    }
    await waitFor(() => expect(buttons[1]).toBeEnabled());
  });

  it('shows a detected Codex source and makes approval available', async () => {
    server.use(http.get('/api/v1/discovery', () => HttpResponse.json(discoveryFixture)));
    render(<App initialView="overview" />);

    expect(await screen.findByRole('heading', { name: 'Local sources' })).toBeInTheDocument();
    expect(screen.getByText('OpenAI Codex')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Approve source' })).toBeEnabled();

    // Only the scan-capable Codex source offers an action; the discovery-only
    // Hermes source stays read-only, and Claude Code reports no source at all.
    expect(screen.getAllByRole('button', { name: 'Approve source' })).toHaveLength(1);
    expect(screen.queryAllByRole('button', { name: 'Rescan source' })).toHaveLength(0);

    const claudeCard = screen.getByRole('article', { name: 'Claude Code' });
    expect(within(claudeCard).getByText(/No local source was found/)).toBeInTheDocument();
    expect(within(claudeCard).queryAllByRole('button')).toHaveLength(0);

    const hermesCard = screen.getByRole('article', { name: 'Hermes Agent' });
    expect(within(hermesCard).getByText(/Discovery only/, { selector: 'p' })).toBeInTheDocument();
    expect(within(hermesCard).queryAllByRole('button')).toHaveLength(0);

    const codexCard = screen.getByRole('article', { name: 'OpenAI Codex' });
    expect(within(codexCard).getByRole('button', { name: 'Approve source' })).toBeEnabled();
  });

  it('renders unknown metrics as an em dash instead of a false zero', async () => {
    server.use(
      http.get('/api/v1/dashboard', () =>
        HttpResponse.json({ ...dashboardFixture, workload_tokens: null }),
      ),
    );
    render(<App initialView="overview" />);

    expect(await screen.findByText('—')).toBeInTheDocument();
  });

  it('keeps a genuinely reported zero as a zero', async () => {
    server.use(
      http.get('/api/v1/dashboard', () =>
        HttpResponse.json({ ...dashboardFixture, workload_tokens: null, input_total_tokens: 0 }),
      ),
    );
    render(<App initialView="overview" />);

    expect(await screen.findByText('0')).toBeInTheDocument();
    expect(screen.queryAllByText('—')).toHaveLength(1);
  });

  it('approves a discovered source through the same-origin API', async () => {
    const user = userEvent.setup();
    const seenOrigins: Array<string | null> = [];

    server.use(
      http.post('/api/v1/sources/:sourceId/approve', ({ request, params }) => {
        seenOrigins.push(request.headers.get('origin'));
        return HttpResponse.json({
          source_id: String(params.sourceId),
          provider: 'codex',
          display_name: 'Codex session',
          state: 'approved',
        });
      }),
    );

    render(<App initialView="overview" />);
    await user.click(await screen.findByRole('button', { name: 'Approve source' }));

    expect(await screen.findByRole('status')).toHaveTextContent(/Codex session is approved/i);
    expect(seenOrigins).toEqual([window.location.origin]);
  });

  it('rescans an approved source and reports only the counts the API returned', async () => {
    const user = userEvent.setup();

    server.use(
      http.get('/api/v1/discovery', () => HttpResponse.json(discoveryWithCodexState('approved'))),
      http.post('/api/v1/sources/:sourceId/rescan', () => HttpResponse.json(importOutcomeFixture)),
    );

    render(<App initialView="overview" />);
    await user.click(await screen.findByRole('button', { name: 'Rescan source' }));

    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent(/Imported 3 new events/);
    expect(status).toHaveTextContent(/1 duplicates skipped/);
    expect(status).toHaveTextContent(/final record is partial/);
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

    render(<App initialView="overview" />);

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

    render(<App initialView="overview" />);

    expect(await screen.findByRole('heading', { name: 'Data quality' })).toBeInTheDocument();
    expect(screen.getByText('Codex session')).toBeInTheDocument();
    expect(screen.getByText('Hermes state database')).toBeInTheDocument();
    expect(screen.getByText('Unsupported records: 2')).toBeInTheDocument();
    expect(screen.getByText(/not fully readable \(partial, unsupported\)/)).toBeInTheDocument();
  });
});

it.each([
  ['Claude Code', 'claude-code-local', 'claude_code', 'jsonl', 'claude-jsonl-v1'],
  ['Hermes Agent', 'hermes-local', 'hermes', 'sqlite', 'hermes-sqlite-v1'],
])('imports %s usage and enables automatic collection on its own card', async (name, connectorId, providerId, sourceType, parserVersion) => {
  const sourceId = `${connectorId}:synthetic-usage-source`;
  const fixture = {
    providers: discoveryFixture.providers.map((provider) => provider.connector_id === connectorId ? {
      ...provider, state: 'discovered',
      sources: [{
        ...discoveryFixture.providers[1].sources[0],
        source_id: sourceId, connector_id: connectorId, provider: providerId,
        display_name: `${name} usage`, source_type: sourceType,
        parser_version: parserVersion, scan_supported: true,
      }],
    } : provider),
  };
  let approvedSource = '';
  let scannedSource = '';
  server.use(
    http.get('/api/v1/discovery', () => HttpResponse.json(fixture)),
    http.post('/api/v1/sources/:sourceId/approve', ({ params }) => {
      approvedSource = String(params.sourceId);
      return HttpResponse.json({ source_id: approvedSource, provider: providerId, display_name: name, state: 'approved' });
    }),
    http.post('/api/v1/sources/:sourceId/rescan', ({ params }) => {
      scannedSource = String(params.sourceId);
      return HttpResponse.json(importOutcomeFixture);
    }),
  );
  render(<App initialView="overview" />);
  const card = await screen.findByRole('article', { name });
  expect(within(card).getByText('Import supported')).toBeInTheDocument();
  await userEvent.setup().click(within(card).getByRole('button', { name: 'Approve source' }));
  await screen.findByRole('status');
  expect(approvedSource).toBe(sourceId);
  expect(scannedSource).toBe(sourceId);
  await userEvent.setup().click(within(card).getByRole('button', { name: 'Include new sessions automatically' }));
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(`existing and new ${name} sessions`));
  expect(within(card).getByRole('button', { name: 'Stop including new sessions' })).toBeEnabled();
  await userEvent.setup().click(within(card).getByRole('button', { name: 'Stop including new sessions' }));
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(`New ${name} sessions will need approval`));
});


it('opens the usage explorer by default and retains the overview', async () => {
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole('table', { name: 'Model usage' });
  await user.click(screen.getByRole('link', { name: 'Overview' }));
  expect(screen.getByRole('heading', { name: 'Observed workload' })).toBeInTheDocument();
  expect(screen.queryByRole('table', { name: 'Model usage' })).not.toBeInTheDocument();
  await user.click(screen.getByRole('link', { name: 'Usage explorer' }));
  expect(screen.getByRole('table', { name: 'Model usage' })).toBeInTheDocument();
});
