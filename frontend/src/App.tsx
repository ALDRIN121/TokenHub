import { useCallback, useEffect, useRef, useState } from 'react';

import {
  ApiError,
  getUsageBreakdown,
  getDataQuality,
  getCollectionStatus,
  getDiscovery,
  rebuildIndex,
  setProviderAutoImport,
} from './api/client';
import { ProviderCard, RESCANNABLE_STATES } from './components/ProviderCard';
import { Icon } from './components/Icon';
import { UsageExplorer } from './components/UsageExplorer';
import { localDay, usageDateRange, type UsagePeriod } from './dateRange';
import type {
  UsageBreakdown,
  CollectionStatus,
  DataQualityResponse,
  DiscoveryResponse,
  ProviderSummary,
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
    return 'No source data has been imported yet. Approve a supported source to start automatic imports.';
  }
  const incomplete = freshness.filter((entry) => INCOMPLETE_STATES.has(entry.state));
  if (incomplete.length === 0) {
    return freshness.every((entry) => entry.state === 'healthy')
      ? 'Imported sources are healthy. Totals include only observed usage.'
      : 'Some sources are waiting for an import. Approve a supported source to start automatic imports.';
  }
  const states = [...new Set(incomplete.map((entry) => entry.state))].join(', ');
  return (
    `Some sources are not fully readable (${states}). TokenHub reports only the records it could ` +
    'read: a partial import means some usage records were unsupported or the newest record was still being written, and an unsupported ' +
    'source means its records could not be read at all. Neither case is ever estimated or filled in.'
  );
}

export default function App() {
  const [usage, setUsage] = useState<UsageBreakdown | null>(null);
  const [discovery, setDiscovery] = useState<DiscoveryResponse | null>(null);
  const [quality, setQuality] = useState<DataQualityResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [rebuilding, setRebuilding] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [activeSection, setActiveSection] = useState('usage-explorer');
  const [collection, setCollection] = useState<CollectionStatus | null>(null);
  const [settingAutoImport, setSettingAutoImport] = useState(false);
  const [period, setPeriod] = useState<UsagePeriod>('all');
  const [selectedDay, setSelectedDay] = useState(() => localDay(new Date()));
  const usageRange = useRef(usageDateRange(period, selectedDay));
  usageRange.current = usageDateRange(period, selectedDay);
  const refreshInFlight = useRef<Promise<void> | null>(null);
  const refreshAgain = useRef(false);
  const mounted = useRef(true);

  const refresh = useCallback(async () => {
    if (refreshInFlight.current !== null) {
      refreshAgain.current = true;
      await refreshInFlight.current;
      return;
    }
    const operation = (async () => {
      do {
        refreshAgain.current = false;
        const requests = [
          getDiscovery(), getDataQuality(), getCollectionStatus(), getUsageBreakdown(usageRange.current),
        ] as const;
        const [nextDiscovery, nextQuality, nextCollection, nextUsage] = await Promise.all(requests)
          .catch(async (cause: unknown) => {
            await Promise.allSettled(requests);
            throw cause;
          });
        if (!mounted.current) return;
        if (refreshAgain.current) continue;
        setDiscovery(nextDiscovery);
        setQuality(nextQuality);
        setCollection(nextCollection);
        setUsage(nextUsage);
      } while (refreshAgain.current);
    })();
    refreshInFlight.current = operation;
    try {
      await operation;
    } finally {
      refreshInFlight.current = null;
    }
  }, []);

  useEffect(() => {
    let active = true;
    mounted.current = true;
    refresh().catch((cause: unknown) => {
      if (active) {
        setError(describeError(cause));
      }
    });
    return () => {
      active = false;
      mounted.current = false;
    };
  }, [refresh, period, selectedDay]);

  useEffect(() => {
    if (rebuilding || refreshing || settingAutoImport) return;
    let active = true;
    let pending = false;
    const timer = window.setInterval(async () => {
      if (!active || pending) return;
      pending = true;
      try {
        await refresh();
        if (active) setError(null);
      } catch (cause: unknown) {
        if (active) setError(describeError(cause));
      } finally {
        pending = false;
      }
    }, 10_000);
    return () => { active = false; window.clearInterval(timer); };
  }, [refresh, rebuilding, refreshing, settingAutoImport]);

  async function handleAutoImport(provider: ProviderSummary, enabled: boolean) {
    setError(null);
    setStatus(null);
    setSettingAutoImport(true);
    try {
      const result = await setProviderAutoImport(provider.provider!, enabled);
      await refresh();
      setCollection(result);
      const name = provider.provider === 'codex' ? 'Codex' : provider.display_name;
      setStatus(enabled ? `Automatic collection is enabled for existing and new ${name} usage.` : `New ${name} sessions will need approval. Previously approved sessions keep updating automatically.`);
    } catch (cause: unknown) {
      setError(describeError(cause));
    } finally {
      setSettingAutoImport(false);
    }
  }

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

  const loading = discovery === null && quality === null && usage === null && error === null;
  const actionsDisabled = rebuilding || refreshing || settingAutoImport || loading;
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
            ['usage-explorer', 'layers', 'Usage explorer'],
            ['local-sources', 'sources', 'Local sources'],
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
          <a href="#privacy-note" onClick={() => setActiveSection('usage-explorer')}>How your data is handled</a>
        </div>
        <div className="sidebar__footer"><span className="status-dot" />Local workspace</div>
      </aside>

      <main className="app__main" id="main-content" tabIndex={-1}>
        <div className="topbar">
          <span className="breadcrumb">Workspace <span>/</span> <strong>Usage explorer</strong></span>
          <span className="device-pill"><Icon name="device" />On this device</span>
        </div>
        <header className="app__header">
          <div>
            <h1>Your token hub.</h1>
            <p className="app__tagline">Token usage across your agents, models, and sessions.</p>
            {collection ? <p className="sync-status"><span className="status-dot" />Auto sync every {collection.scan_interval_seconds} seconds{collection.last_scan_at ? ` · Last checked ${formatTimestamp(collection.last_scan_at)}` : ''}</p> : null}
          </div>
          <button type="button" className="button button--secondary" onClick={handleRefresh} disabled={actionsDisabled} aria-busy={refreshing}>
            <Icon name="refresh" className={refreshing ? 'is-spinning' : ''} />Refresh data
          </button>
        </header>

        {error !== null ? <p className="notice notice--error" role="alert"><Icon name="info" />{error}</p> : null}
        {status !== null ? <p className="notice notice--success" role="status"><Icon name="check" />{status}</p> : null}
        {collection && collection.failed_source_count > 0 ? <p className="notice notice--error" role="alert"><Icon name="info" />Automatic sync could not read {collection.failed_source_count} sources. It will retry on the next scan.</p> : null}
        {loading ? (
          <div className="loading-state" aria-live="polite" aria-busy="true">
            <Icon name="refresh" className="is-spinning" />Reading local provider data…
            <div className="loading-state__cards" aria-hidden="true"><span /><span /><span /></div>
          </div>
        ) : null}

        {usage !== null ? <UsageExplorer data={usage} period={period} selectedDay={selectedDay} onPeriodChange={setPeriod} onSelectedDayChange={setSelectedDay} /> : null}

        {discovery !== null ? (
          <section className="panel" id="local-sources" aria-labelledby="local-sources-heading">
            <div className="section-heading">
              <div><h2 id="local-sources-heading">Local sources</h2><p className="panel__intro">Your coding agents, connected on your terms. Nothing is imported until you approve it.</p></div>
              <span className="section-meta">{detectedCount} providers detected</span>
            </div>
            <div className="provider-grid">
              {providers.map((provider) => (
                <ProviderCard key={provider.connector_id} provider={provider} actionsDisabled={actionsDisabled} autoImportEnabled={collection?.auto_import_connectors?.includes(provider.connector_id) ?? (provider.provider === 'codex' && collection?.codex_auto_import)} onAutoImportChange={provider.provider ? (enabled) => handleAutoImport(provider, enabled) : undefined} />
              ))}
            </div>
            <p className="source-disclaimer"><Icon name="info" />Codex, Claude Code, Hermes Agent, VS Code Copilot Chat, and Antigravity support local usage imports from approved sources. Copilot inline suggestions are not included.</p>
            {quality !== null ? <section className="source-health" id="data-quality" aria-labelledby="data-quality-heading">
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
            </section> : null}
          </section>
        ) : null}
        <footer className="app__footer"><span>TokenHub · Local usage observatory</span><span><Icon name="shield" />Observed data. No estimates.</span></footer>
      </main>
    </div>
  );
}
