import { useEffect, useRef } from 'react';
import type { CollectionJob, CollectionStatus, DataQualityResponse, DiscoveryResponse } from '../types';
import { ImportProgress } from './ImportProgress';
import { Icon } from './Icon';

export function SyncDetails({ open, onClose, collection, quality, discovery, job, error, onRefresh, disabled }: {
  open: boolean; onClose: () => void; collection: CollectionStatus | null; quality: DataQualityResponse | null;
  discovery: DiscoveryResponse | null; job: CollectionJob | null | undefined; error: string | null;
  onRefresh: () => void; disabled: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const element = dialog.current!;
    element.showModal();
    return () => { element.close(); previous?.focus(); };
  }, [open]);
  const states = Object.entries(quality?.state_counts ?? {}).filter(([state, count]) => count > 0 && state !== 'healthy');
  const discoveryIssues = discovery?.providers.filter((provider) => provider.state === 'error' || provider.evidence_codes.includes('discovery_error')) ?? [];
  return <dialog ref={dialog} className="sync-drawer" aria-labelledby="sync-heading" onCancel={(event) => { event.preventDefault(); onClose(); }}>
    <div className="drawer-heading"><h2 id="sync-heading">Sync and coverage</h2><button className="button button--secondary" onClick={onClose} aria-label="Close sync details">Close</button></div>
    <p className="panel__intro">Usage remains available while TokenHub checks your approved local sources. Missing records are never estimated.</p>
    {error ? <p className="notice notice--warning"><Icon name="info" />{error}</p> : null}
    <dl className="sync-facts"><div><dt>Automatic checks</dt><dd>{collection ? `Every ${collection.scan_interval_seconds} seconds` : 'Status unavailable'}</dd></div><div><dt>Last completed scan</dt><dd>{collection?.last_scan_at ? `${new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }).format(new Date(collection.last_scan_at))} UTC` : 'Not confirmed'}</dd></div><div><dt>Unreadable sources</dt><dd>{collection?.failed_source_count ?? 'Not confirmed'}</dd></div></dl>
    {collection && collection.failed_source_count > 0 ? <p className="notice notice--warning">Automatic sync could not read {collection.failed_source_count} sources. It will retry on the next scan. A completed scan does not confirm every source was read.</p> : null}
    {states.length ? <section className="drawer-issues"><h3>Source coverage</h3><ul>{states.map(([state, count]) => <li key={state}>{count} {state.replaceAll('_', ' ')} sources</li>)}</ul></section> : null}
    {discoveryIssues.length ? <section className="drawer-issues"><h3>Detection issues</h3><ul>{discoveryIssues.map((provider) => <li key={provider.connector_id}>{provider.display_name}: detection could not finish</li>)}</ul></section> : null}
    {collection?.requires_reapproval_connectors?.length ? <p className="notice notice--warning">Some agents need approval renewed in Sources before all their usage can be imported.</p> : null}
    {job ? <ImportProgress job={job} /> : null}
    <button className="button button--primary" onClick={onRefresh} disabled={disabled}><Icon name="refresh" />Refresh data</button>
    <p className="panel__footnote">Open Sources to review individual files, approve agents, or rebuild the local index.</p>
  </dialog>;
}
