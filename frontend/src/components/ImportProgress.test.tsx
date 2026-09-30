import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { ImportProgress } from './ImportProgress';
import type { CollectionJob } from '../types';

export const runningJob = { job_id: 'job-1', kind: 'enable', state: 'running', stage: 'reading', provider: 'codex', files_total: 10, files_completed: 2, bytes_read: 50, bytes_total: 100, records_read: 3, records_saved: 0, records_total: null, skipped_files: 1, inserted_events: 7, duplicate_events: 0, unsupported_records: 0, elapsed_seconds: 12, error: null, result: null } as CollectionJob;

it('shows truthful file progress while keeping the dashboard available', () => {
  render(<ImportProgress job={runningJob} />);
  expect(screen.getByRole('progressbar', { name: 'Import progress' })).toHaveAttribute('value', '20');
  expect(screen.getByText(/Reading Codex usage/)).toBeInTheDocument();
  expect(screen.getByText(/2 of 10 files/)).toBeInTheDocument();
  expect(screen.getByText(/12s elapsed/)).toBeInTheDocument();
  expect(screen.getByText(/keep browsing/)).toBeInTheDocument();
});
it('does not invent a percentage during discovery and offers retry after failure', () => {
  const view = render(<ImportProgress job={{ ...runningJob, stage: 'discovering', files_total: null }} />);
  expect(screen.getByRole('progressbar')).not.toHaveAttribute('value');
  view.rerender(<ImportProgress job={{ ...runningJob, state: 'failed', error: 'Source is unavailable' }} onRetry={() => {}} />);
  expect(screen.getByRole('button', { name: 'Retry import' })).toBeEnabled();
  expect(screen.getByText('Source is unavailable')).toBeInTheDocument();
});
