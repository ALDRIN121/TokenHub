import { useState } from 'react';
import { usageDateRange, type UsagePeriod } from '../dateRange';
import { Icon } from './Icon';

export function DateRangeControl({ period, selectedDay, selectedEndDay, onPeriodChange, onSelectedDayChange, onCustomRangeChange }: {
  period: UsagePeriod; selectedDay: string; selectedEndDay: string;
  onPeriodChange?: (period: UsagePeriod) => void; onSelectedDayChange?: (day: string) => void;
  onCustomRangeChange?: (from: string, to: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({ from: selectedDay, to: selectedEndDay });
  const activeDraft = editing ? draft : { from: selectedDay, to: selectedEndDay };
  const valid = Boolean(usageDateRange('custom', activeDraft.from, undefined, activeDraft.to));
  return <div className="date-range-control">
    <label className="date-select"><Icon name="calendar" /><span className="sr-only">Date range</span><select aria-label="Date range" value={editing ? 'custom' : period} onChange={event => {
      const next = event.target.value as UsagePeriod;
      setEditing(next === 'custom');
      if (next === 'custom') setDraft({ from: selectedDay, to: selectedEndDay });
      else onPeriodChange?.(next);
    }}>
      <option value="all">All time</option><option value="today">Today</option><option value="yesterday">Yesterday</option><option value="last7">Last 7 days</option><option value="last30">Last 30 days</option><option value="day">Choose a date</option><option value="custom">Custom range</option>
    </select></label>
    {period === 'day' && !editing ? <label>Usage date<input aria-label="Usage date" type="date" value={selectedDay} onChange={event => onSelectedDayChange?.(event.target.value)} /></label> : null}
    {period === 'custom' && !editing ? <button type="button" className="text-button custom-range-edit" onClick={() => { setDraft({ from: selectedDay, to: selectedEndDay }); setEditing(true); }}>Edit dates</button> : null}
    {editing ? <form className="custom-date-range" aria-label="Custom date range" onSubmit={event => { event.preventDefault(); if (valid) { onCustomRangeChange?.(activeDraft.from, activeDraft.to); setEditing(false); } }}>
      <label>From<input aria-label="From date" type="date" required value={editing ? draft.from : selectedDay} onChange={event => { setDraft({ from: event.target.value, to: editing ? draft.to : selectedEndDay }); setEditing(true); }} /></label>
      <label>To<input aria-label="To date" type="date" required value={editing ? draft.to : selectedEndDay} onChange={event => { setDraft({ from: editing ? draft.from : selectedDay, to: event.target.value }); setEditing(true); }} /></label>
      <button className="button button--primary" type="submit" disabled={!valid}>Apply range</button>
      {editing ? <button className="text-button" type="button" onClick={() => { setEditing(false); setDraft({ from: selectedDay, to: selectedEndDay }); }}>Cancel</button> : null}
      {editing && !valid ? <p className="date-range-error" role="status">{draft.from && draft.to ? 'End date must be on or after start date.' : 'Choose both dates.'}</p> : null}
    </form> : null}
  </div>;
}
