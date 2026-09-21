import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import App from './App';
import { server } from './test/server';
import {
  dashboardFixture,
  discoveryFixture,
  discoveryWithCodexState,
  importOutcomeFixture,
} from './test/fixtures';

describe('TokenHub client', () => {
  it('shows a detected Codex source and makes approval available', async () => {
    server.use(http.get('/api/v1/discovery', () => HttpResponse.json(discoveryFixture)));
    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Local sources' })).toBeInTheDocument();
    expect(screen.getByText('OpenAI Codex')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Approve source' })).toBeEnabled();

    // Only the scan-capable Codex source offers an action; the discovery-only
    // Claude Code and Hermes sources stay read-only and report their support status.
    expect(screen.getAllByRole('button', { name: 'Approve source' })).toHaveLength(1);
    expect(screen.queryAllByRole('button', { name: 'Rescan source' })).toHaveLength(0);

    const claudeCard = screen.getByRole('article', { name: 'Claude Code' });
    expect(within(claudeCard).getByText(/Discovery only/, { selector: 'p' })).toBeInTheDocument();
    expect(within(claudeCard).queryAllByRole('button')).toHaveLength(0);

    const codexCard = screen.getByRole('article', { name: 'OpenAI Codex' });
    expect(within(codexCard).getByRole('button', { name: 'Approve source' })).toBeEnabled();
  });

  it('renders unknown metrics as an em dash instead of a false zero', async () => {
    server.use(
      http.get('/api/v1/dashboard', () =>
        HttpResponse.json({ ...dashboardFixture, workload_tokens: null }),
      ),
    );
    render(<App />);

    expect(await screen.findByText('—')).toBeInTheDocument();
  });

  it('keeps a genuinely reported zero as a zero', async () => {
    server.use(
      http.get('/api/v1/dashboard', () =>
        HttpResponse.json({ ...dashboardFixture, workload_tokens: null, input_total_tokens: 0 }),
      ),
    );
    render(<App />);

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
          provider: 'openai',
          display_name: 'OpenAI Codex sessions',
          state: 'approved',
        });
      }),
    );

    render(<App />);
    await user.click(await screen.findByRole('button', { name: 'Approve source' }));

    expect(await screen.findByRole('status')).toHaveTextContent(
      /OpenAI Codex sessions is approved/i,
    );
    expect(seenOrigins).toEqual([window.location.origin]);
  });

  it('rescans an approved source and reports only the counts the API returned', async () => {
    const user = userEvent.setup();

    server.use(
      http.get('/api/v1/discovery', () => HttpResponse.json(discoveryWithCodexState('approved'))),
      http.post('/api/v1/sources/:sourceId/rescan', () => HttpResponse.json(importOutcomeFixture)),
    );

    render(<App />);
    await user.click(await screen.findByRole('button', { name: 'Rescan source' }));

    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent(/Imported 3 new events/);
    expect(status).toHaveTextContent(/1 duplicates skipped/);
    expect(status).toHaveTextContent(/final record is partial/);
  });

  it('lists source status in data quality and explains partial and unsupported sources', async () => {
    server.use(
      http.get('/api/v1/data-quality', () =>
        HttpResponse.json({
          quality_counts: { partial: 1, unsupported: 2 },
          source_freshness: [
            {
              source_id: 'codex:sessions',
              state: 'partial',
              latest_event_at: '2026-09-20T12:00:00Z',
              unsupported_records: 2,
            },
            {
              source_id: 'claude-code:config',
              state: 'permission_denied',
              latest_event_at: null,
              unsupported_records: null,
            },
          ],
        }),
      ),
    );

    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Data quality' })).toBeInTheDocument();
    expect(screen.getByText('OpenAI Codex sessions')).toBeInTheDocument();
    expect(screen.getByText('Claude Code configuration directory')).toBeInTheDocument();
    expect(screen.getByText('Unsupported records: 2')).toBeInTheDocument();
    expect(screen.getByText(/not fully readable \(partial, permission_denied\)/)).toBeInTheDocument();
  });
});
