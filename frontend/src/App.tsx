import { useCallback, useEffect, useState } from 'react';

import {
  ApiError,
  approveSource,
  getDashboard,
  getDataQuality,
  getDiscovery,
  rebuildIndex,
  rescanSource,
} from './api/client';
import { MetricCard } from './components/MetricCard';
import { ProviderCard, RESCANNABLE_STATES } from './components/ProviderCard';
import { Icon } from './components/Icon';
import { UsageComposition } from './components/UsageComposition';
import type {
  DashboardSummary,
  DataQualityResponse,
  DiscoveryResponse,
  ImportOutcome,
  RebuildOutcome,
  SourceFreshness,
} from './types';
import './styles.css';

/** States that mean TokenHub could not read everything a source contains. */
const INCOMPLETE_STATES = new Set([
  'partial',
  'unsupported',
  'error',
  'permission_denied',
  'source_missing',
]);

function describeError(cause: unknown): string {
  if (cause instanceof ApiError) {
    return `The local server could not complete this request (status ${cause.status}). Try refreshing the data.`;
  }
  return 'TokenHub could not reach its local API. Start the local server and reload this page.';
}

function describeImport(outcome: ImportOutcome): string {
  const parts = [
    `Imported ${outcome.inserted_events} new events`,
    `${outcome.duplicate_events} duplicates skipped`,
  ];
  if (outcome.partial_final_record) {
    parts.push('the final record is partial and will be read on the next scan');
  }
  if (outcome.unsupported_records > 0) {
    parts.push(`${outcome.unsupported_records} records were unsupported`);
  }
  return `${parts.join('; ')}.`;
}

function describeRebuild(outcome: RebuildOutcome): string {
  const failed = outcome.failed_source_ids.length;
  const rebuilt = `Rebuilt the local index from ${outcome.inserted_events} events`;
  return failed > 0 ? `${rebuilt}; ${failed} sources could not be rebuilt.` : `${rebuilt}.`;
}

/** Timestamps are shown as UTC instants; an absent one is never guessed. */
function formatTimestamp(value: string | null): string {
  if (value === null || value === '') {
    return 'no events imported yet';
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return 'not reported';
  }
  return `${new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }).format(parsed)} UTC`;
}

function displayNameForSource(discovery: DiscoveryResponse | null, sourceId: string): string {
  for (const provider of discovery?.providers ?? []) {
    for (const source of provider.sources) {
      if (source.source_id === sourceId) {
        return source.display_name;
      }
    }
  }
  return sourceId;
}

function formatQualityCounts(counts: Record<string, number>): string {
  const entries = Object.entries(counts);
  if (entries.length === 0) {
    return 'No import quality counts have been recorded yet.';
  }
  return `Import quality: ${entries.map(([name, count]) => `${name} ${count}`).join(', ')}.`;
}

function explainQuality(freshness: SourceFreshness[]): string {
  if (freshness.length === 0) {
    return 'No source data has been imported yet. Approve a supported source, then choose Rescan source to get started.';
  }
  const incomplete = freshness.filter((entry) => INCOMPLETE_STATES.has(entry.state));
  if (incomplete.length === 0) {
    return freshness.every((entry) => entry.state === 'healthy')
      ? 'Imported sources are healthy. Totals include only observed usage.'
      : 'Some sources are waiting for an import. Approve a supported source, then choose Rescan source.';
  }
  const states = incomplete.map((entry) => entry.state).join(', ');
  return (
    `Some sources are not fully readable (${states}). TokenHub reports only the records it could ` +
    'read: a partial import means the newest record was still being written, and an unsupported ' +
    'source means its records could not be read at all. Neither case is ever estimated or filled in.'
  );
}

export default function App() {
  const [discovery, setDiscovery] = useState<DiscoveryResponse | null>(null);
  const [dashboard, setDashboard] = useState<DashboardSummary | null>(null);
  const [quality, setQuality] = useState<DataQualityResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busySourceId, setBusySourceId] = useState<string | null>(null);
  const [rebuilding, setRebuilding] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [activeSection, setActiveSection] = useState('observed-workload');

  const refresh = useCallback(async () => {
    const [nextDiscovery, nextDashboard, nextQuality] = await Promise.all([
      getDiscovery(),
      getDashboard(),
      getDataQuality(),
    ]);
    setDiscovery(nextDiscovery);
    setDashboard(nextDashboard);
    setQuality(nextQuality);
  }, []);

  useEffect(() => {
    let active = true;
    refresh().catch((cause: unknown) => {
      if (active) {
        setError(describeError(cause));
      }
    });
    return () => {
      active = false;
    };
  }, [refresh]);

  async function handleRefresh() {
    setError(null);
    setStatus(null);
    setRefreshing(true);
    try {
      await refresh();
    } catch (cause: unknown) {
      setError(describeError(cause));
    } finally {
      setRefreshing(false);
    }
  }

  async function handleApprove(sourceId: string) {
    setError(null);
    setStatus(null);
    setBusySourceId(sourceId);
    try {
      const result = await approveSource(sourceId);
      setStatus(
        `${result.display_name} is ${result.state}. Choose "Rescan source" to import its events.`,
      );
      await refresh();
    } catch (cause: unknown) {
      setError(describeError(cause));
    } finally {
      setBusySourceId(null);
    }
  }

  async function handleRescan(sourceId: string) {
    setError(null);
    setStatus(null);
    setBusySourceId(sourceId);
    try {
      const outcome = await rescanSource(sourceId);
      setStatus(describeImport(outcome));
      await refresh();
    } catch (cause: unknown) {
      setError(describeError(cause));
    } finally {
      setBusySourceId(null);
    }
  }

  async function handleRebuild() {
    setError(null);
    setStatus(null);
    setRebuilding(true);
    try {
      const outcome = await rebuildIndex();
      setStatus(describeRebuild(outcome));
      await refresh();
    } catch (cause: unknown) {
      setError(describeError(cause));
    } finally {
      setRebuilding(false);
    }
  }

  const loading = discovery === null && dashboard === null && quality === null && error === null;
  const actionsDisabled = busySourceId !== null || rebuilding || refreshing || loading;
  const sources = discovery?.providers.flatMap((provider) => provider.sources) ?? [];
  const approvedCount = sources.filter((source) => source.scan_supported && RESCANNABLE_STATES.has(source.state)).length;
  const detectedCount = discovery?.providers.filter((provider) => provider.evidence_codes.some((code) => code !== 'discovery_error')).length ?? 0;
  const providers = [...(discovery?.providers ?? [])].sort((a, b) => Number(b.sources.some((source) => source.scan_supported)) - Number(a.sources.some((source) => source.scan_supported)));

  return (
    <div className="app">
      <a className="skip-link" href="#main-content">Skip to dashboard</a>
      <aside className="sidebar">
        <a className="brand" href="#main-content" aria-label="TokenHub dashboard">
          <span className="brand__mark"><Icon name="layers" /></span>
          <span>TokenHub<small>Local usage observatory</small></span>
        </a>
        <div className="sidebar__label">Your workspace</div>
        <nav className="sidebar__nav" aria-label="Dashboard sections">
          {([
            ['observed-workload', 'overview', 'Overview'],
            ['local-sources', 'sources', 'Local sources'],
            ['data-quality', 'quality', 'Data quality'],
          ] as const).map(([id, icon, label]) => (
            <a key={id} href={`#${id}`} aria-current={activeSection === id ? 'location' : undefined} onClick={() => setActiveSection(id)}>
              <Icon name={icon} /><span>{label}</span>
            </a>
          ))}
        </nav>
        <div className="sidebar__privacy">
          <Icon name="shield" />
          <strong>Private by default</strong>
          <p>Your data stays on this device. No accounts. No cloud sync.</p>
          <a href="#privacy-note">How your data is handled</a>
        </div>
        <div className="sidebar__footer"><span className="status-dot" />Local workspace</div>
      </aside>

      <main className="app__main" id="main-content" tabIndex={-1}>
        <div className="topbar">
          <span className="breadcrumb">Workspace <span>/</span> <strong>Overview</strong></span>
          <span className="device-pill"><Icon name="device" />On this device</span>
        </div>
        <header className="app__header">
          <div>
            <h1>Your usage, in focus.</h1>
            <p className="app__tagline">A clear view of your coding agents. Everything stays on this machine.</p>
          </div>
          <button type="button" className="button button--secondary" onClick={handleRefresh} disabled={actionsDisabled} aria-busy={refreshing}>
            <Icon name="refresh" className={refreshing ? 'is-spinning' : ''} />Refresh data
          </button>
        </header>

        {error !== null ? <p className="notice notice--error" role="alert"><Icon name="info" />{error}</p> : null}
        {status !== null ? <p className="notice notice--success" role="status"><Icon name="check" />{status}</p> : null}
        {loading ? (
          <div className="loading-state" aria-live="polite" aria-busy="true">
            <Icon name="refresh" className="is-spinning" />Reading local provider data…
            <div className="loading-state__cards" aria-hidden="true"><span /><span /><span /></div>
          </div>
        ) : null}

        {dashboard !== null ? (
          <section className="panel overview-panel" id="observed-workload" aria-labelledby="observed-workload-heading">
            <div className="section-heading">
              <h2 id="observed-workload-heading">Observed workload</h2>
              <span className="section-meta">All imported events</span>
            </div>
            <dl className="metric-grid">
              <MetricCard label="Workload tokens" value={dashboard.workload_tokens} icon="layers" note="Complete input + output records" featured />
              <MetricCard label="Input tokens" value={dashboard.input_total_tokens} icon="input" note="Includes cached input" />
              <MetricCard label="Output tokens" value={dashboard.output_total_tokens} icon="output" note="Includes reasoning" />
            </dl>
            <dl className="breakdown-grid">
              <MetricCard label="Cache read tokens" value={dashboard.cache_read_tokens} icon="cache" note="Within input" />
              <MetricCard label="Cache write tokens" value={dashboard.cache_write_tokens} icon="cache" note="Reported separately" />
              <MetricCard label="Reasoning tokens" value={dashboard.reasoning_tokens} icon="reasoning" note="Within output" />
            </dl>
            <div className="overview-details">
              <UsageComposition dashboard={dashboard} />
              <aside className="privacy-card" id="privacy-note" aria-labelledby="privacy-heading">
                <span className="privacy-card__icon"><Icon name="shield" /></span>
                <h3 id="privacy-heading">Your data stays yours.</h3>
                <p>TokenHub reads approved usage metadata locally. It never reads prompts, transcripts, credentials, or provider accounts.</p>
                <span><Icon name="check" />No data leaves this machine</span>
              </aside>
            </div>
            <p className="panel__footnote">Events imported: {dashboard.event_count.toLocaleString('en-US')}. Unknown values appear as an em dash.</p>
            {dashboard.event_count === 0 ? (
              <div className="getting-started">
                <span className="getting-started__icon"><Icon name="sources" /></span>
                <div><strong>Your first import starts here</strong><p>Approve a supported local source, then rescan it to see your usage.</p></div>
                <a className="button button--secondary" href="#local-sources" onClick={() => setActiveSection('local-sources')}>View local sources<Icon name="arrow" /></a>
              </div>
            ) : null}
          </section>
        ) : null}

        {discovery !== null ? (
          <section className="panel" id="local-sources" aria-labelledby="local-sources-heading">
            <div className="section-heading">
              <div><h2 id="local-sources-heading">Local sources</h2><p className="panel__intro">Your coding agents, connected on your terms. Nothing is imported until you approve it.</p></div>
              <span className="section-meta">{detectedCount} providers detected</span>
            </div>
            <div className="provider-grid">
              {providers.map((provider) => (
                <ProviderCard key={provider.connector_id} provider={provider} busySourceId={busySourceId} actionsDisabled={actionsDisabled} onApprove={handleApprove} onRescan={handleRescan} />
              ))}
            </div>
            <p className="source-disclaimer"><Icon name="info" />Codex supports usage imports. Claude Code and Hermes are detection-only for now.</p>
          </section>
        ) : null}

        {quality !== null ? (
          <section className="panel" id="data-quality" aria-labelledby="data-quality-heading">
            <div className="section-heading">
              <div><h2 id="data-quality-heading">Data quality</h2><p className="panel__intro">Know what was read, and what is still missing.</p></div>
              <button type="button" className="button button--secondary" onClick={handleRebuild} disabled={actionsDisabled || approvedCount === 0} aria-busy={rebuilding} title={approvedCount === 0 ? 'Approve a supported source before rebuilding' : 'Re-import approved sources to rebuild the local index'}>
                <Icon name="refresh" className={rebuilding ? 'is-spinning' : ''} />{rebuilding ? 'Rebuilding index…' : 'Rebuild index'}
              </button>
            </div>
            <div className="quality-panel">
              {quality.source_freshness.length > 0 ? (
                <ul className="source-status">
                  {quality.source_freshness.map((entry) => (
                    <li key={entry.source_id} className="source-status__item">
                      <span className="source-status__name"><Icon name="sources" />{displayNameForSource(discovery, entry.source_id)}</span>
                      <span className={`badge ${entry.state === 'healthy' ? 'badge--success' : INCOMPLETE_STATES.has(entry.state) ? 'badge--warning' : 'badge--neutral'}`}>{entry.state.replaceAll('_', ' ')}</span>
                      <span className="source-status__detail">Latest event: {formatTimestamp(entry.latest_event_at)}</span>
                      {entry.unsupported_records != null ? <span className="source-status__detail">Unsupported records: {entry.unsupported_records}</span> : null}
                    </li>
                  ))}
                </ul>
              ) : null}
              <div className="quality-panel__note"><Icon name="info" /><p className="source-status__explainer">{explainQuality(quality.source_freshness)}</p></div>
              <p className="panel__footnote">{formatQualityCounts(quality.quality_counts)}</p>
            </div>
            {approvedCount === 0 ? <p className="panel__footnote">Rebuilding becomes available after a supported source is approved.</p> : null}
          </section>
        ) : null}
        <footer className="app__footer"><span>TokenHub · Local usage observatory</span><span><Icon name="shield" />Observed data. No estimates.</span></footer>
      </main>
    </div>
  );
}
