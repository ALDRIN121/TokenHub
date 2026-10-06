import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it, vi } from 'vitest';
import App from './App';
import { server } from './test/server';

describe('usage-first application navigation', () => {
  it('keeps the selected agent while navigating analysis and sources', async () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole('table', { name: 'Session usage' });
    await user.click(screen.getByRole('button', { name: 'Filter by Codex' }));
    await user.click(screen.getByRole('link', { name: 'Models' }));
    expect(screen.getByRole('table', { name: 'Model usage' })).toBeInTheDocument();
    expect(window.location.search).toContain('agent=codex');
    expect(window.location.hash).toBe('#models');
    await user.click(screen.getByRole('link', { name: 'Sources' }));
    expect(screen.getByRole('heading', { name: 'Local sources' })).toBeVisible();
    expect(screen.queryByRole('table', { name: 'Model usage' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('link', { name: 'Overview' }));
    expect(screen.getByRole('button', { name: 'Filter by Codex' })).toHaveAttribute('aria-pressed', 'true');
    expect(scroll).toHaveBeenCalled();
  });

  it('restores agent, date and analysis destination from browser history', async () => {
    window.history.replaceState({}, '', '/?agent=codex&period=last7#sessions');
    render(<App />);
    await screen.findByRole('table', { name: 'Session usage' });
    expect(screen.getByRole('button', { name: 'Filter by Codex' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('combobox', { name: 'Date range' })).toHaveValue('last7');
    act(() => {
      window.history.replaceState({}, '', '/?agent=hermes&period=last30#models');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    await screen.findByRole('table', { name: 'Model usage' });
    expect(screen.getByRole('button', { name: 'Filter by Hermes Agent' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('combobox', { name: 'Date range' })).toHaveValue('last30');
  });

  it('persists the header theme switch across reloads', async () => {
    const user = userEvent.setup();
    const view = render(<App />);
    const theme = screen.getByRole('switch', { name: 'Dark mode' });
    await user.click(theme);
    expect(theme).toHaveAttribute('aria-checked', 'true');
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
    view.unmount();
    render(<App />);
    expect(screen.getByRole('switch', { name: 'Dark mode' })).toHaveAttribute('aria-checked', 'true');
  });

  it('shows valid usage when automatic sync metadata cannot be read', async () => {
    server.use(http.get('/api/v1/collection', () => new HttpResponse(null, { status: 503 })));
    render(<App />);
    await screen.findByRole('table', { name: 'Session usage' });
    expect(screen.getByText('Workload tokens')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    const sync = screen.getByRole('button', { name: /Sync details/ });
    expect(sync).toHaveTextContent(/review/i);
    await userEvent.setup().click(sync);
    expect(await screen.findByRole('dialog', { name: 'Sync and coverage' })).toBeVisible();
    expect(screen.getByText(/status 503/)).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole('button', { name: 'Close sync details' }));
    await waitFor(() => expect(sync).toHaveFocus());
  });
  it('keeps unavailable date-scope counters unknown after a failed request', async () => {
    render(<App />);
    await screen.findByRole('table', { name: 'Session usage' });
    server.use(http.get('/api/v1/usage', () => new HttpResponse(null, { status: 503 })));
    await userEvent.setup().selectOptions(screen.getByRole('combobox', { name: 'Date range' }), 'today');
    await screen.findByRole('alert');
    const total = screen.getByText('Workload tokens').parentElement!;
    expect(total.querySelector('dd')).toHaveTextContent('—');
    expect(screen.getByRole('table', { name: 'Session usage' }).querySelector('tbody')).toBeEmptyDOMElement();
    server.resetHandlers();
    await userEvent.setup().click(screen.getByRole('button', { name: 'Refresh data' }));
    await waitFor(() => expect(total.querySelector('dd')).toHaveTextContent('125'));
  });

  it('retains valid counters when two date controls describe the same range', async () => {
    render(<App />);
    await screen.findByRole('table', { name: 'Session usage' });
    const user = userEvent.setup();
    await user.selectOptions(screen.getByRole('combobox', { name: 'Date range' }), 'today');
    await waitFor(() => expect(screen.getByText('Workload tokens').parentElement!.querySelector('dd')).toHaveTextContent('125'));
    await user.selectOptions(screen.getByRole('combobox', { name: 'Date range' }), 'day');
    expect(screen.getByText('Workload tokens').parentElement!.querySelector('dd')).toHaveTextContent('125');
    expect(screen.queryByText('Updating usage page…')).not.toBeInTheDocument();
  });

});
