import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';
import App from './App';

it('offers the approved overview hierarchy and opens coverage at its explanation', async () => {
  render(<App />);
  expect(await screen.findByRole('heading', { name: 'Usage overview' })).toBeInTheDocument();
  expect(screen.getByRole('navigation', { name: 'Dashboard sections' })).toBeInTheDocument();
  expect(screen.getByText('Stored on this device')).toBeInTheDocument();
  await userEvent.setup().click(screen.getByRole('button', { name: 'Review coverage' }));
  expect(screen.getByText('Counter details and accuracy').closest('details')).toHaveAttribute('open');
  expect(screen.getByRole('note', { name: 'Usage freshness and completeness' })).toBeVisible();
});

it('limits the compact counter toggle to the overview where it changes the table', async () => {
  render(<App />);
  await screen.findByRole('table', { name: 'Session usage' });
  await userEvent.setup().click(screen.getByRole('button', { name: 'Detailed counters' }));
  expect(screen.getByRole('button', { name: 'Compact view' })).toBeInTheDocument();
  await userEvent.setup().click(screen.getByRole('link', { name: 'Models' }));
  expect(screen.queryByRole('button', { name: 'Detailed counters' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Compact view' })).not.toBeInTheDocument();
});
