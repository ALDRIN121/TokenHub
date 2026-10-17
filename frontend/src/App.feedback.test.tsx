import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { expect, it } from 'vitest';
import App from './App';
import { server } from './test/server';

it('offers an icon-bearing agent picker with keyboard selection and dismissal', async () => {
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole('heading', { name: 'Usage overview' });
  const picker = screen.getByRole('combobox', { name: 'Agent' });
  await user.click(picker);
  const option = screen.getByRole('option', { name: 'Codex' });
  expect(option.querySelector('img')).toBeInTheDocument();
  await user.keyboard('{Home}{ArrowDown}{Enter}');
  expect(picker).toHaveTextContent('Codex');
  expect(screen.queryByRole('listbox', { name: 'Agents' })).not.toBeInTheDocument();
  await user.click(picker);
  await user.keyboard('{Escape}');
  expect(picker).toHaveFocus();
});

it('applies custom dates atomically and restores them through navigation', async () => {
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole('table', { name: 'Session usage' });
  await user.selectOptions(screen.getByRole('combobox', { name: 'Date range' }), 'custom');
  fireEvent.change(screen.getByLabelText('From date'), { target: { value: '2026-09-01' } });
  fireEvent.change(screen.getByLabelText('To date'), { target: { value: '2026-09-30' } });
  await user.click(screen.getByRole('button', { name: 'Apply range' }));
  await waitFor(() => expect(location.search).toContain('from=2026-09-01'));
  expect(location.search).toContain('to=2026-09-30');
  await user.click(screen.getByRole('link', { name: 'Models' }));
  await user.click(screen.getByRole('button', { name: 'Edit dates' }));
  expect(screen.getByLabelText('From date')).toHaveValue('2026-09-01');
});

it('adds monthly grouping and explains what each plotted interval contains', async () => {
  render(<App />);
  const monthly = await screen.findByRole('button', { name: 'Monthly' });
  await userEvent.setup().click(monthly);
  expect(monthly).toHaveAttribute('aria-pressed', 'true');
  expect(await screen.findByText(/Each point totals one calendar month/)).toBeInTheDocument();
  expect(screen.getByText(/Grouping changes the chart, not the selected total/)).toBeInTheDocument();
});

it('shows completed refresh age without inventing an unknown scan time', async () => {
  server.use(http.get('/api/v1/collection', () => HttpResponse.json({ scan_interval_seconds: 30, last_scan_at: new Date(Date.now() - 56_000).toISOString(), failed_source_count: 0, data_version: 1 })));
  render(<App />);
  expect(await screen.findByText(/Refreshed 5\d seconds ago/)).toBeInTheDocument();
});

it('puts recorded model names under agents in the compact session view', async () => {
  render(<App />);
  const table = await screen.findByRole('table', { name: 'Session usage' });
  expect(within(table).getByText('gpt-test')).toBeInTheDocument();
  const button = within(table).getByRole('button', { name: /View models used by Codex/ });
  await userEvent.setup().click(button);
  expect(within(table).getByText('Models in this session')).toBeInTheDocument();
});

it('does not apply an inverted custom draft or discard the previous range on cancel', async () => {
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole('table', { name: 'Session usage' });
  await user.selectOptions(screen.getByRole('combobox', { name: 'Date range' }), 'custom');
  fireEvent.change(screen.getByLabelText('From date'), { target: { value: '2026-09-30' } });
  fireEvent.change(screen.getByLabelText('To date'), { target: { value: '2026-09-01' } });
  expect(screen.getByRole('button', { name: 'Apply range' })).toBeDisabled();
  expect(screen.getByText('End date must be on or after start date.')).toBeInTheDocument();
  expect(location.search).not.toContain('period=custom');
  await user.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(screen.getByRole('combobox', { name: 'Date range' })).toHaveValue('all');
});

it('keeps refresh age unconfirmed when no completed scan is recorded', async () => {
  render(<App />);
  await screen.findByRole('table', { name: 'Session usage' });
  expect(screen.getByText('Refresh not confirmed')).toBeInTheDocument();
  expect(screen.queryByText(/Refreshed just now/)).not.toBeInTheDocument();
});
