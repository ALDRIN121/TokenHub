import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { usageFixture } from '../test/fixtures';
import type { UsageBreakdown } from '../types';
import { UsageExplorer } from './UsageExplorer';

const data: UsageBreakdown = {
  ...usageFixture,
  models: [
    usageFixture.models[0],
    { ...usageFixture.models[0], provider: 'hermes', model_name: 'hermes-model', attribution: 'session', workload_tokens: 500, output_total_tokens: 0 },
    { ...usageFixture.models[0], model_name: null, workload_tokens: null, input_total_tokens: null, reasoning_tokens: null },
  ],
  sessions: [...usageFixture.sessions, { ...usageFixture.sessions[0], provider: 'hermes', session_key: 'opaque-two', models: [{ ...usageFixture.totals, model_name: 'hermes-model' }] }],
};

describe('usage explorer', () => {
  it('filters Copilot Chat usage by agent and shows its recorded model and session', async () => {
    const user = userEvent.setup();
    const copilot: UsageBreakdown = {
      ...usageFixture,
      providers: [...usageFixture.providers, { ...usageFixture.totals, provider: 'vscode_copilot', workload_tokens: 24 }],
      models: [...usageFixture.models, { ...usageFixture.models[0], provider: 'vscode_copilot', model_name: 'gpt-5-mini', workload_tokens: 24 }],
      sessions: [...usageFixture.sessions, { ...usageFixture.sessions[0], provider: 'vscode_copilot', session_key: 'copilot-session', workload_tokens: 24,
        models: [{ ...usageFixture.totals, model_name: 'gpt-5-mini', workload_tokens: 24 }] }],
    };
    render(<UsageExplorer data={copilot} />);

    await user.click(screen.getByRole('button', { name: 'Filter by VS Code Copilot' }));
    const table = screen.getByRole('table', { name: 'Model usage' });
    expect(within(table).getByText('gpt-5-mini')).toBeInTheDocument();
    expect(within(table).queryByText('gpt-test')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'View sessions for gpt-5-mini in VS Code Copilot' }));
    expect(screen.getByRole('table', { name: 'Session usage' })).toHaveTextContent('copilot-');
  });

  it('ranks models, filters agents, searches, and drills into model sessions', async () => {
    const user = userEvent.setup();
    render(<UsageExplorer data={data} />);
    const table = screen.getByRole('table', { name: 'Model usage' });
    const rows = within(table).getAllByRole('row');
    expect(rows[1]).toHaveTextContent('hermes-model');
    await user.click(screen.getByRole('button', { name: 'Filter by Codex' }));
    expect(within(table).queryByText('hermes-model')).not.toBeInTheDocument();
    await user.type(screen.getByRole('searchbox', { name: 'Search usage' }), 'gpt');
    expect(within(table).queryByText('Model not recorded')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'View sessions for gpt-test in Codex' }));
    expect(screen.getByRole('heading', { name: 'gpt-test' })).toBeInTheDocument();
    expect(screen.getByRole('table', { name: 'Session usage' })).toHaveTextContent('opaque-s');
    await user.click(screen.getByRole('button', { name: 'All models' }));
    expect(screen.getByRole('table', { name: 'Model usage' })).toBeInTheDocument();
  });

  it('keeps unknown counters distinct from zero and explains session attribution', () => {
    render(<UsageExplorer data={data} />);
    const table = screen.getByRole('table', { name: 'Model usage' });
    const unknown = within(table).getByText('Model not recorded').closest('tr')!;
    expect(unknown).toHaveTextContent('—');
    const hermes = within(table).getByText('hermes-model').closest('tr')!;
    expect(hermes).toHaveTextContent('Session-reported');
    expect(within(hermes).getAllByText('0').length).toBeGreaterThan(0);
  });

  it('shows a useful empty state when nothing is imported', () => {
    render(<UsageExplorer data={{ ...usageFixture, totals: { ...usageFixture.totals, event_count: 0 }, providers: [], models: [], sessions: [] }} />);
    expect(screen.getByText('Your usage explorer starts with an import')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Connect local sources' })).toHaveAttribute('href', '#local-sources');
  });
});


it('does not attribute provider totals to a model removed during refresh', async () => {
  const user = userEvent.setup();
  const view = render(<UsageExplorer data={data} />);
  await user.click(screen.getByRole('button', { name: 'View sessions for gpt-test in Codex' }));
  view.rerender(<UsageExplorer data={{ ...data, models: [], sessions: [] }} />);
  expect(screen.getByText('No usage for this model in the current import.')).toBeInTheDocument();
  const summary = screen.getByText('tokens in this model', { exact: false });
  expect(summary).toHaveTextContent('—');
  expect(summary).not.toHaveTextContent('125');
});

it('sorts model columns in both directions and keeps unknown values last', async () => {
  const user = userEvent.setup();
  render(<UsageExplorer data={data} />);
  const table = screen.getByRole('table', { name: 'Model usage' });
  const total = within(table).getByRole('columnheader', { name: /Total tokens/ });
  expect(total).toHaveAttribute('aria-sort', 'descending');
  await user.click(within(total).getByRole('button'));
  expect(total).toHaveAttribute('aria-sort', 'ascending');
  expect(within(table).getAllByRole('row')[1]).toHaveTextContent('gpt-test');
  expect(within(table).getAllByRole('row').at(-1)).toHaveTextContent('Model not recorded');
  await user.click(within(total).getByRole('button'));
  expect(within(table).getAllByRole('row')[1]).toHaveTextContent('hermes-model');
  const name = within(table).getByRole('columnheader', { name: /Model \/ agent/ });
  await user.click(within(name).getByRole('button'));
  expect(name).toHaveAttribute('aria-sort', 'ascending');
  expect(within(table).getAllByRole('row')[1]).toHaveTextContent('gpt-test');
  expect(screen.getByRole('combobox', { name: 'Sort by' })).toHaveValue('identity');
});

it('sorts cache-write counts and sessions from the table, including keyboard input', async () => {
  const user = userEvent.setup();
  const sortable = {
    ...data,
    models: data.models.map((row, index) => ({ ...row, cache_write_tokens: [10, 100, null][index], session_count: [2, 1, 3][index] })),
  };
  render(<UsageExplorer data={sortable} />);
  const table = screen.getByRole('table', { name: 'Model usage' });
  await user.click(within(table).getByRole('button', { name: 'Sort by Cache write, descending' }));
  expect(within(table).getAllByRole('row')[1]).toHaveTextContent('hermes-model');
  const header = within(table).getByRole('columnheader', { name: /Cache write/ });
  within(header).getByRole('button').focus();
  await user.keyboard('{Enter}');
  expect(header).toHaveAttribute('aria-sort', 'ascending');
  expect(within(table).getAllByRole('row')[1]).toHaveTextContent('gpt-test');
  expect(within(table).getAllByRole('row').at(-1)).toHaveTextContent('Model not recorded');
  await user.click(within(table).getByRole('button', { name: 'Sort by Sessions, descending' }));
  expect(within(table).getAllByRole('row')[1]).toHaveTextContent('Model not recorded');
});

it('sorts session contributions for a selected model rather than the whole session', async () => {
  const user = userEvent.setup();
  const first = usageFixture.sessions[0];
  const sessionData = {
    ...usageFixture,
    sessions: [
      { ...first, session_key: 'session-a', workload_tokens: 900, models: [{ ...first.models[0], workload_tokens: 20 }] },
      { ...first, session_key: 'session-b', workload_tokens: 100, models: [{ ...first.models[0], workload_tokens: 80 }] },
    ],
  };
  render(<UsageExplorer data={sessionData} />);
  await user.click(screen.getByRole('button', { name: 'View sessions for gpt-test in Codex' }));
  const table = screen.getByRole('table', { name: 'Session usage' });
  expect(within(table).getAllByRole('row')[1]).toHaveTextContent('80');
  await user.click(within(table).getByRole('button', { name: 'Sort by Total tokens, ascending' }));
  expect(within(table).getAllByRole('row')[1]).toHaveTextContent('20');
  const names = within(table).getByRole('columnheader', { name: /Session \/ models/ });
  await user.click(within(names).getByRole('button'));
  expect(names).toHaveAttribute('aria-sort', 'ascending');
});

it('explains Hermes date attribution beside totals and follows the selected agent and model', async () => {
  const user = userEvent.setup();
  const hermesData = { ...data, providers: [...data.providers, { ...usageFixture.totals, provider: 'hermes' }] };
  render(<UsageExplorer data={hermesData} />);
  const note = screen.getByRole('note', { name: 'Hermes session attribution' });
  expect(note).toHaveTextContent(/entire session.*end date.*start date.*active/i);
  expect(note).toHaveTextContent(/daily.*weekly.*model.*per-request precision/i);
  expect(note.closest('section')).toHaveAttribute('id', 'overview');
  expect(note.closest('details')).toHaveClass('usage-advanced');
  await user.click(screen.getByRole('button', { name: 'View sessions for gpt-test in Codex' }));
  expect(screen.queryByRole('note', { name: 'Hermes session attribution' })).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Filter by Hermes Agent' }));
  expect(screen.getByRole('note', { name: 'Hermes session attribution' })).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Filter by Codex' }));
  expect(screen.queryByRole('note', { name: 'Hermes session attribution' })).not.toBeInTheDocument();
});

it('does not show a Hermes attribution caveat when Hermes has no recorded events', () => {
  render(<UsageExplorer data={{ ...usageFixture, providers: [...usageFixture.providers, { ...usageFixture.totals, provider: 'hermes', event_count: 0 }] }} />);
  expect(screen.queryByRole('note', { name: 'Hermes session attribution' })).not.toBeInTheDocument();
});

it('preserves independent searches across tabs and same-agent selection', async () => {
 const user = userEvent.setup(); render(<UsageExplorer data={data} />);
 await user.type(screen.getByRole('searchbox'), 'gpt');
 await user.click(screen.getByRole('tab', { name: /^Sessions / }));
 expect(screen.getByRole('searchbox')).toHaveValue('');
 await user.type(screen.getByRole('searchbox'), 'opaque');
 await user.click(screen.getByRole('tab', { name: /^Models / }));
 expect(screen.getByRole('searchbox')).toHaveValue('gpt');
 await user.click(screen.getByRole('button', { name: 'Show all agents' }));
 expect(screen.getByRole('searchbox')).toHaveValue('gpt');
});

it('uses keyboard tabs and hides pending scope counters and empty guidance', async () => {
 const view = render(<UsageExplorer data={data} />);
 const modelsTab = screen.getByRole('tab', { name: /^Models / });
 modelsTab.focus(); fireEvent.keyDown(modelsTab, { key: 'ArrowRight' });
 expect(screen.getByRole('tab', { name: /^Sessions / })).toHaveFocus();
 expect(screen.getByRole('table', { name: 'Session usage' })).toBeInTheDocument();
 fireEvent.keyDown(screen.getByRole('tab', { name: /^Sessions / }), { key: 'Home' });
 expect(modelsTab).toHaveFocus();
 view.rerender(<UsageExplorer data={{ ...data, totals: { ...data.totals, event_count: 0 } }} isLoading />);
 expect(screen.getByText('Workload tokens').closest('div')).toHaveTextContent('—');
 expect(screen.queryByText('Your usage explorer starts with an import')).not.toBeInTheDocument();
 expect(screen.queryByRole('button', { name: /View sessions for gpt-test/ })).not.toBeInTheDocument();
});

it('controlled Models navigation exits model drilldown and preserves model search', async () => {
 const user = userEvent.setup();
 const view = render(<UsageExplorer data={data} mode="models" />);
 await user.type(screen.getByRole('searchbox'), 'gpt');
 await user.click(screen.getByRole('button', { name: 'View sessions for gpt-test in Codex' }));
 view.rerender(<UsageExplorer data={data} mode="sessions" />);
 expect(screen.getByRole('heading', { name: 'gpt-test' })).toBeInTheDocument();
 view.rerender(<UsageExplorer data={data} mode="models" />);
 expect(screen.queryByRole('heading', { name: 'gpt-test' })).not.toBeInTheDocument();
 expect(screen.getByRole('searchbox')).toHaveValue('gpt');
 expect(screen.getByRole('table', { name: 'Model usage' })).toHaveTextContent('gpt-test');
});
