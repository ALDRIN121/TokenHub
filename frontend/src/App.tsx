import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  ApiError,
  getUsageBreakdown,
  getCollectionJob,
  getDataQuality,
  getCollectionStatus,
  getDiscovery,
  rebuildIndex,
  refreshCollection,
  setProviderAutoImport,
} from './api/client';
import { ProviderCard, RESCANNABLE_STATES } from './components/ProviderCard';
import { Icon } from './components/Icon';
import { jobIsActive } from './components/ImportProgress';
import { UsageExplorer } from './components/UsageExplorer';
import { SyncDetails } from './components/SyncDetails';
import { readDashboardLocation, saveDashboardLocation, scrollDashboard, type DashboardSection, type AnalysisMode } from './dashboardNavigation';
import { usageDateRange, type UsagePeriod } from './dateRange';
import type {
  UsageBreakdown,
  AcceptedJob,
  CollectionJob,
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
    'read: a partial import means some usage records were unsupported (for example a session that records only a combined total, with no ' +
    'input/output split) or the newest record was still being written, and an unsupported source means its records could not be read at all. ' +
    'Neither case is ever estimated or filled in.'
  );
}

/** Summary guidance covers all sources, including those outside the health page. */
function usageAccuracyNotice(usage: UsageBreakdown, collection: CollectionStatus | null, quality: DataQualityResponse | null, error: string | null, discovery: DiscoveryResponse | null) {
  const messages = ['Totals cover imported, observed records. Unrecorded usage is not included.'];
  const lastScan = collection?.last_scan_at ? Date.parse(collection.last_scan_at) : NaN;
  const stale = collection !== null && Number.isFinite(lastScan) && Date.now() - lastScan > Math.max(collection.scan_interval_seconds * 3, 60) * 1000;
  if (stale) messages.push(`Usage may be out of date. Last completed scan: ${formatTimestamp(collection!.last_scan_at)}. Choose Refresh data to check for newer usage.`);
  else if (Number.isFinite(lastScan)) messages.push(`Last completed scan: ${formatTimestamp(collection!.last_scan_at)}.`);
  else if (usage.totals.event_count > 0) messages.push('Freshness not confirmed. No completed scan has been reported for the imported usage.');

  const states = new Set([
    ...Object.entries(quality?.state_counts ?? {}).filter(([, count]) => count > 0).map(([state]) => state),
    ...(quality?.source_freshness.map((source) => source.state) ?? []),
  ]);
  const incomplete = error !== null || (collection?.failed_source_count ?? 0) > 0 ||
    (discovery?.providers.some((provider) => provider.state === 'error' || provider.evidence_codes.includes('discovery_error')) ?? false) ||
    (collection?.requires_reapproval_connectors?.length ?? 0) > 0 || usage.totals.incomplete_event_count > 0 ||
    [...states].some((state) => INCOMPLETE_STATES.has(state) || state === 'discovered') ||
    Object.entries(quality?.quality_counts ?? {}).some(([state, count]) => count > 0 && (INCOMPLETE_STATES.has(state) || state === 'unavailable')) ||
    (quality?.source_freshness.some((source) => (source.unsupported_records ?? 0) > 0) ?? false);
  if (incomplete) messages.push('Some usage may be missing: connected sources may need approval, could not be read fully, or contain incomplete token counts. Totals include only the records and counters that were imported.');
  if (states.has('source_missing')) messages.push('Previously imported records remain counted even when their original files are no longer available.');
  if (quality === null) messages.push('Source completeness has not been confirmed.');
  return { messages, warning: stale || incomplete || !Number.isFinite(lastScan) || quality === null };
}

const UNSUPPORTED_RECORDS_HINT =
  'TokenHub could not count these records, so their tokens are not in your totals. ' +
  'For example, a session may record only a combined total with no input/output split; TokenHub never guesses one.';

export default function App() {
  const [usage, setUsage] = useState<UsageBreakdown | null>(null);
  const [discovery, setDiscovery] = useState<DiscoveryResponse | null>(null);
  const [quality, setQuality] = useState<DataQualityResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [rebuilding, setRebuilding] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [activeSection, setActiveSection] = useState<DashboardSection>(() => readDashboardLocation().section);
  const [provider, setProvider] = useState(() => readDashboardLocation().provider);
  const [mode, setMode] = useState<AnalysisMode>(() => readDashboardLocation().mode);
  const [syncOpen, setSyncOpen] = useState(false);
  const [metadataError, setMetadataError] = useState<string | null>(null);
  const metadataNeedsRetry = useRef(false);
  const [scopeLoading, setScopeLoading] = useState(false);
  const [dark, setDark] = useState(() => {
    try { const theme = localStorage.getItem('tokenhub-theme'); if (theme) return theme === 'dark'; } catch { /* Storage can be unavailable in private contexts. */ }
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false;
  });
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    try { localStorage.setItem('tokenhub-theme', dark ? 'dark' : 'light'); } catch { /* The current-page toggle still works without storage. */ }
  }, [dark]);
  const [collection, setCollection] = useState<CollectionStatus | null>(null);
  const [settingAutoImport, setSettingAutoImport] = useState(false);
  const [period, setPeriod] = useState<UsagePeriod>(() => readDashboardLocation().period);
  const [trackedJob, setTrackedJob] = useState<CollectionJob | null>(null);
  const [pendingJobId, setPendingJobId] = useState<string | null>(null);
  const [healthOffset, setHealthOffset] = useState(0);
  const [healthLoading, setHealthLoading] = useState(false);
  const healthOffsetRef = useRef(0);
  healthOffsetRef.current = healthOffset;
  const [selectedDay, setSelectedDay] = useState(() => readDashboardLocation().selectedDay);
  const usageRange = useRef(usageDateRange(period, selectedDay));
  usageRange.current = usageDateRange(period, selectedDay);
  const refreshInFlight = useRef<Promise<void> | null>(null);
  const refreshAgain = useRef(false);
  const refreshMetadata = useRef(false);
  const seenVersion = useRef<number | null>(null);
  const mounted = useRef(true);

  const refresh = useCallback(async (usageOnly = false) => {
    if (!usageOnly) refreshMetadata.current = true;
    if (refreshInFlight.current !== null) {
      refreshAgain.current = true;
      await refreshInFlight.current;
      return;
    }
    const operation = (async () => {
      do {
        refreshAgain.current = false;
        const withMetadata = refreshMetadata.current;
        refreshMetadata.current = false;
        let nextCollection: CollectionStatus | null = null;
        let metadataCause: unknown = null;
        if (withMetadata) {
          try { nextCollection = await getCollectionStatus(); } catch (cause) { metadataCause = cause; }
          if (mounted.current && nextCollection) setCollection(nextCollection);
        }
        const requestedHealthOffset = healthOffsetRef.current;
        const requests = withMetadata ? [getUsageBreakdown(usageRange.current), getDiscovery(), getDataQuality(requestedHealthOffset)] : [getUsageBreakdown(usageRange.current)];
        const results = await Promise.allSettled(requests);
        if (!mounted.current) return;
        if (refreshAgain.current) { if (withMetadata) refreshMetadata.current = true; continue; }
        const nextUsage = results[0];
        if (withMetadata) {
          const nextDiscovery = results[1], nextQuality = results[2];
          if (nextDiscovery.status === 'fulfilled') setDiscovery(nextDiscovery.value as DiscoveryResponse);
          else metadataCause ??= nextDiscovery.reason;
          if (nextQuality.status === 'fulfilled') {
            if (requestedHealthOffset === healthOffsetRef.current) setQuality(nextQuality.value as DataQualityResponse);
          } else metadataCause ??= nextQuality.reason;
          metadataNeedsRetry.current = metadataCause !== null;
          setMetadataError(metadataCause === null ? null : describeError(metadataCause));
          if (nextCollection) seenVersion.current = nextCollection.data_version;
        }
        if (nextUsage.status === 'fulfilled') { setScopeLoading(false); setUsage(nextUsage.value as UsageBreakdown); setError(null); }
        else throw nextUsage.reason;
      } while (refreshAgain.current);
    })();
    refreshInFlight.current = operation;
    try { await operation; } finally { refreshInFlight.current = null; }
  }, []);

  useEffect(() => {
    mounted.current = true;
    refresh().catch((cause: unknown) => { if (mounted.current) setError(describeError(cause)); });
    return () => { mounted.current = false; };
  }, [refresh]);

  const lastRange = useRef(JSON.stringify(usageRange.current));
  useEffect(() => {
    const next = JSON.stringify(usageRange.current);
    if (lastRange.current === next) return;
    lastRange.current = next;
    refresh(true).catch((cause: unknown) => { if (mounted.current) setError(describeError(cause)); });
  }, [period, selectedDay, refresh]);

  const visibleJob = collection?.active_job ?? (pendingJobId ? trackedJob ?? collection?.latest_job : collection?.latest_job ?? trackedJob);
  const importing = jobIsActive(visibleJob) || pendingJobId !== null;
  useEffect(() => {
    let active = true;
    let pending = false;
    const poll = async () => {
      if (!active || pending || document.visibilityState === 'hidden') return;
      pending = true;
      try {
        const next = await getCollectionStatus();
        if (!active) return;
        setCollection(next);
        if (pendingJobId) {
          const job = [next.active_job, next.latest_job].find((item) => item?.job_id === pendingJobId) ?? await getCollectionJob(pendingJobId);
          if (!active) return;
          setTrackedJob(job);
          if (!jobIsActive(job)) setPendingJobId(null);
        }
        if (next.data_version !== seenVersion.current || metadataNeedsRetry.current) await refresh();
      } catch (cause: unknown) { if (active) { metadataNeedsRetry.current = true; setMetadataError(describeError(cause)); } }
      finally { pending = false; }
    };
    const timer = window.setInterval(poll, importing ? 1_000 : 10_000);
    document.addEventListener('visibilitychange', poll);
    return () => { active = false; window.clearInterval(timer); document.removeEventListener('visibilitychange', poll); };
  }, [refresh, importing, pendingJobId]);

  async function acceptJob(result: AcceptedJob) {
    setPendingJobId(result.job_id);
    const next = await getCollectionStatus();
    if (!mounted.current) return;
    setCollection(next);
    const job = [next.active_job, next.latest_job].find((item) => item?.job_id === result.job_id) ?? await getCollectionJob(result.job_id);
    if (!mounted.current) return;
    setTrackedJob(job);
    if (!jobIsActive(job)) { setPendingJobId(null); await refresh(); }
  }

  const initialHealthPage = useRef(true);
  useEffect(() => {
    if (initialHealthPage.current) { initialHealthPage.current = false; return; }
    let active = true;
    setHealthLoading(true);
    getDataQuality(healthOffset).then((next) => { if (active) setQuality(next); })
      .catch((cause: unknown) => { if (active) setError(describeError(cause)); })
      .finally(() => { if (active) setHealthLoading(false); });
    return () => { active = false; };
  }, [healthOffset]);

  async function handleAutoImport(provider: ProviderSummary, enabled: boolean) {
    setError(null);
    setStatus(null);
    setSettingAutoImport(true);
    try {
      const result = await setProviderAutoImport(provider.provider!, enabled);
      if ('job_id' in result) await acceptJob(result);
      else { await refresh(); setCollection(result); }
      const name = provider.provider === 'codex' ? 'Codex' : provider.display_name;
      if (!('job_id' in result)) setStatus(enabled ? `Automatic collection is enabled for existing and new ${name} usage.` : `New ${name} sessions will need approval. Previously approved sessions keep updating automatically.`);
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
      const result = await refreshCollection();
      if ('job_id' in result) await acceptJob(result);
      else await refresh();
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
      if ('job_id' in outcome) await acceptJob(outcome);
      else { setStatus(describeRebuild(outcome)); await refresh(); }
    } catch (cause: unknown) {
      setError(describeError(cause));
    } finally {
      setRebuilding(false);
    }
  }

  const loading = discovery === null && quality === null && usage === null && error === null;
  const actionsDisabled = rebuilding || refreshing || settingAutoImport || importing || loading;
  const sources = discovery?.providers.flatMap((provider) => provider.sources) ?? [];
  const approvedCount = quality?.approved_source_count ?? discovery?.providers.reduce((sum, item) => sum + (item.approved_count ?? item.sources.filter((source) => source.scan_supported && RESCANNABLE_STATES.has(source.state)).length), 0) ?? sources.filter((source) => source.scan_supported && RESCANNABLE_STATES.has(source.state)).length;
  const detectedCount = discovery?.providers.filter((provider) => provider.evidence_codes.some((code) => code !== 'discovery_error')).length ?? 0;
  const providers = [...(discovery?.providers ?? [])].sort((a, b) => Number((b.supported_source_count ?? b.sources.filter((source) => source.scan_supported).length) > 0) - Number((a.supported_source_count ?? a.sources.filter((source) => source.scan_supported).length) > 0));

  const sourceNames = useMemo(() => new Map(discovery?.providers.flatMap((provider) => provider.sources.map((source) => [source.source_id, source.display_name] as const)) ?? []), [discovery]);

  function navigate(section: DashboardSection) {
    setActiveSection(section);
    if (section === 'models' || section === 'sessions') setMode(section);
    saveDashboardLocation(provider, period, selectedDay, section);
    window.requestAnimationFrame(() => scrollDashboard(section));
  }
  function changeProvider(id: string) {
    setProvider(id);
    saveDashboardLocation(id, period, selectedDay, activeSection);
  }
  function changePeriod(next: UsagePeriod) {
    if (next === period) return;
    if (JSON.stringify(usageDateRange(next, selectedDay)) !== JSON.stringify(usageRange.current)) setScopeLoading(true);
    setPeriod(next);
    saveDashboardLocation(provider, next, selectedDay, activeSection);
  }
  function changeDay(day: string) {
    if (day === selectedDay || !usageDateRange('day', day)) return;
    if (JSON.stringify(usageDateRange(period, day)) !== JSON.stringify(usageRange.current)) setScopeLoading(true);
    setSelectedDay(day);
    saveDashboardLocation(provider, period, day, activeSection);
  }
  useEffect(() => {
    const restore = () => {
      const next = readDashboardLocation();
      if (JSON.stringify(usageDateRange(next.period, next.selectedDay)) !== JSON.stringify(usageRange.current)) setScopeLoading(true);
      setProvider(next.provider); setPeriod(next.period); setSelectedDay(next.selectedDay);
      setActiveSection(next.section); setMode(next.mode);
      window.requestAnimationFrame(() => scrollDashboard(next.section, false));
    };
    window.addEventListener('popstate', restore);
    window.addEventListener('hashchange', restore);
    return () => { window.removeEventListener('popstate', restore); window.removeEventListener('hashchange', restore); };
  }, []);
  useEffect(() => {
    if (usage) window.requestAnimationFrame(() => scrollDashboard(readDashboardLocation().section, false));
  }, [usage === null]);
  const needsReview = metadataError !== null || error !== null || (collection?.failed_source_count ?? 0) > 0 ||
    (collection?.requires_reapproval_connectors?.length ?? 0) > 0 ||
    Object.entries(quality?.state_counts ?? {}).some(([state, count]) => count > 0 && INCOMPLETE_STATES.has(state));
  const syncLabel = importing ? 'Syncing' : needsReview ? 'Sync · review' : collection ? 'Auto sync' : 'Sync · unconfirmed';

  return (
    <div className="app">
      <a className="skip-link" href="#main-content">Skip to dashboard</a>
      <header className="dashboard-header">
        <a className="brand" href="#overview" aria-label="TokenHub dashboard" onClick={(event) => { event.preventDefault(); navigate('overview'); }}><span className="brand__mark"><Icon name="layers" /></span><span>TokenHub</span></a>
        <nav className="dashboard-nav" aria-label="Dashboard sections">
          {(['overview', 'models', 'sessions', 'sources'] as const).map((section) => <a key={section} href={`#${section}`} aria-current={activeSection === section ? 'location' : undefined} onClick={(event) => { event.preventDefault(); navigate(section); }}>{section[0].toUpperCase() + section.slice(1)}</a>)}
        </nav>
        <div className="header-tools"><button className={`sync-button${needsReview ? ' sync-button--warning' : ''}`} type="button" aria-label={`Sync details: ${syncLabel}`} aria-haspopup="dialog" onClick={() => setSyncOpen(true)}><span className="status-dot" /><span>{syncLabel}</span><Icon name="chevron" /></button><button className="theme-toggle" type="button" role="switch" aria-label="Dark mode" aria-checked={dark} title={dark ? 'Switch to light mode' : 'Switch to dark mode'} onClick={() => setDark(!dark)}><span aria-hidden="true">{dark ? '☾' : '☀'}</span></button></div>
      </header>
      <main className="app__main" id="main-content" tabIndex={-1}>
        {error !== null && (usage === null || scopeLoading) ? <p className="notice notice--error" role="alert"><Icon name="info" />{error}<button type="button" className="button button--secondary" onClick={handleRefresh} disabled={actionsDisabled}>Refresh data</button></p> : null}
        {status !== null ? <p className="notice notice--success" role="status"><Icon name="check" />{status}</p> : null}
        {loading ? (
          <div className="loading-state" aria-live="polite" aria-busy="true">
            <Icon name="refresh" className="is-spinning" />Reading local provider data…
            <div className="loading-state__cards" aria-hidden="true"><span /><span /><span /></div>
          </div>
        ) : null}

        <div hidden={activeSection === 'sources'}>
          {usage !== null ? <UsageExplorer data={usage} accuracyNotice={usageAccuracyNotice(usage, collection, quality, error ?? metadataError, discovery)} period={period} selectedDay={selectedDay} onPeriodChange={changePeriod} onSelectedDayChange={changeDay} provider={provider} onProviderChange={changeProvider} mode={mode} onModeChange={(next) => { setMode(next); setActiveSection(next); saveDashboardLocation(provider, period, selectedDay, next); }} isLoading={scopeLoading} /> : null}
        </div>

        {discovery !== null ? (
          <section className="panel sources-view" hidden={activeSection !== 'sources'} id="local-sources" aria-labelledby="local-sources-heading">
            <div className="section-heading">
              <div><h2 id="local-sources-heading">Local sources</h2><p className="panel__intro">Your coding agents, connected on your terms. Nothing is imported until you approve it.</p></div>
              <span className="section-meta">{detectedCount} providers detected</span>
            </div>
            <div className="provider-grid">
              {providers.map((provider) => (
                <ProviderCard key={provider.connector_id} provider={provider} actionsDisabled={actionsDisabled} requiresReapproval={collection?.requires_reapproval_connectors?.includes(provider.connector_id) ?? false} autoImportEnabled={collection?.auto_import_connectors?.includes(provider.connector_id) ?? (provider.provider === 'codex' && collection?.codex_auto_import)} onAutoImportChange={provider.provider ? (enabled) => handleAutoImport(provider, enabled) : undefined} />
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
                <ul className="source-status" aria-busy={healthLoading}>
                  {quality.source_freshness.map((entry) => (
                    <li key={entry.source_id} className="source-status__item">
                      <span className="source-status__name"><Icon name="sources" />{entry.display_name ?? sourceNames.get(entry.source_id) ?? entry.source_id}</span>
                      <span className={`badge ${entry.state === 'healthy' ? 'badge--success' : INCOMPLETE_STATES.has(entry.state) ? 'badge--warning' : 'badge--neutral'}`}>{entry.state.replaceAll('_', ' ')}</span>
                      <span className="source-status__detail">Latest event: {formatTimestamp(entry.latest_event_at)}</span>
                      {entry.unsupported_records != null ? <span className="source-status__detail" title={UNSUPPORTED_RECORDS_HINT}>Unsupported records: {entry.unsupported_records}</span> : null}
                    </li>
                  ))}
                </ul>
              ) : null}
              {(quality.source_count ?? 0) > 25 ? <div className="ledger-bottom"><span>Sources {healthOffset + 1}–{Math.min(healthOffset + 25, quality.source_count!)} of {quality.source_count}</span><div><button className="button button--secondary" disabled={healthLoading || healthOffset === 0} onClick={() => setHealthOffset(Math.max(0, healthOffset - 25))}>Previous sources</button><button className="button button--secondary" disabled={healthLoading || healthOffset + 25 >= quality.source_count!} onClick={() => setHealthOffset(healthOffset + 25)}>Next sources</button></div></div> : null}
              <div className="quality-panel__note"><Icon name="info" /><p className="source-status__explainer">{explainQuality(quality.state_counts ? Object.keys(quality.state_counts).map((state) => ({ source_id: state, state, latest_event_at: null, unsupported_records: null })) : quality.source_freshness)}</p></div>
              <p className="panel__footnote">{formatQualityCounts(quality.quality_counts)}</p>
            </div>
            {approvedCount === 0 ? <p className="panel__footnote">Rebuilding becomes available after a supported source is approved.</p> : null}
            </section> : null}
          </section>
        ) : null}
        <footer className="app__footer"><span>TokenHub · Local usage observatory</span><span><Icon name="shield" />Observed data. No estimates.</span></footer>
      </main>
      <SyncDetails open={syncOpen} onClose={() => setSyncOpen(false)} collection={collection} quality={quality} discovery={discovery} job={visibleJob} error={error ?? metadataError} onRefresh={handleRefresh} disabled={actionsDisabled} />
    </div>
  );
}
