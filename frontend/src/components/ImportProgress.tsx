import type { CollectionJob } from '../types';
import { Icon } from './Icon';

const names: Record<string, string> = { codex: 'Codex', claude_code: 'Claude Code', hermes: 'Hermes Agent', vscode_copilot: 'Copilot', antigravity: 'Antigravity' };
export const jobIsActive = (job: CollectionJob | null | undefined) => job?.state === 'queued' || job?.state === 'running';
const count = (value: number) => value.toLocaleString('en-US');
const size = (value: number) => value >= 1_048_576 ? `${(value / 1_048_576).toFixed(1)} MB` : `${(value / 1024).toFixed(1)} KB`;

export function ImportProgress({ job, onRetry }: { job: CollectionJob; onRetry?: () => void }) {
  const active = jobIsActive(job);
  const name = job.provider ? names[job.provider] ?? job.provider : 'local';
  const phase = job.state === 'queued' ? 'Waiting to start import' : job.stage === 'discovering' ? 'Finding local usage files' : job.stage === 'saving' ? `Saving ${name} usage` : `Reading ${name} usage`;
  const problem = ['failed', 'interrupted', 'completed_with_errors'].includes(job.state);
  const heading = active ? phase : job.state === 'completed' ? 'Import complete' : job.state === 'completed_with_errors' ? 'Import finished with some unreadable sources' : job.state === 'interrupted' ? 'Import was interrupted' : 'Import could not finish';
  const progress = active ? job.files_total ? job.files_completed / job.files_total * 100 : undefined : job.state === 'completed' ? 100 : undefined;
  const currentTotal = job.stage === 'saving' ? job.records_total : job.bytes_total;
  const currentValue = job.stage === 'saving' ? job.records_saved : job.bytes_read;
  const elapsed = job.elapsed_seconds >= 60 ? `${Math.floor(job.elapsed_seconds / 60)}m ${Math.floor(job.elapsed_seconds % 60)}s` : `${Math.floor(job.elapsed_seconds)}s`;
  return <section className={`import-progress${problem ? ' import-progress--warning' : ''}`} aria-label="Import activity">
    <div className="import-progress__heading"><Icon name={active ? 'refresh' : problem ? 'info' : 'check'} className={active ? 'is-spinning' : ''} /><strong role="status" aria-live="polite">{heading}</strong><span>{elapsed} elapsed</span></div>
    <progress aria-label="Import progress" max={100} value={progress} />
    <div className="import-progress__counts"><span>{job.files_total !== null ? `${count(job.files_completed)} of ${count(job.files_total)} files processed` : 'Counting available files…'}</span><span>{count(job.inserted_events)} records imported{job.skipped_files ? ` · ${count(job.skipped_files)} unchanged files skipped` : ''}</span></div>
    {active && job.stage !== 'discovering' && job.state !== 'queued' && currentTotal !== null && currentTotal > 0 ? <div className="import-progress__file"><span>{job.stage === 'saving' ? `${count(currentValue)} of ${count(currentTotal)} records saved in this file` : `${size(currentValue)} of ${size(currentTotal)} read in this file`}</span><progress aria-label={job.stage === 'saving' ? 'Saving current file' : 'Reading current file'} max={currentTotal} value={Math.min(currentValue, currentTotal)} /></div> : null}
    <p>{active ? 'You can keep browsing your imported data while this runs.' : job.error ?? (problem ? 'Previously imported data is still available. Refresh data to retry unreadable sources.' : `${count(job.duplicate_events)} duplicate records ignored · ${count(job.unsupported_records)} unsupported records excluded.`)}</p>
    {problem && onRetry ? <button type="button" className="button button--secondary" onClick={onRetry}>Retry import</button> : null}
  </section>;
}
