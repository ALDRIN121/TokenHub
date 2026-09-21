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
import { ProviderCard } from './components/ProviderCard';
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
    return `TokenHub could not complete that request. ${cause.message}`;
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
  return parsed.toISOString().replace('T', ' ').replace('Z', ' UTC');
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

function explainQuality(incomplete: SourceFreshness[]): string {
  if (incomplete.length === 0) {
    return 'Every discovered source is fully readable right now.';
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

  const incomplete = (quality?.source_freshness ?? []).filter((entry) =>
    INCOMPLETE_STATES.has(entry.state),
  );
  const loading = discovery === null && dashboard === null && quality === null && error === null;

  return (
    <div className="app">
      <header className="app__header">
        <h1>TokenHub</h1>
        <p className="app__tagline">Local usage observatory</p>
        <p className="app__privacy">
          Everything here is read from this machine. TokenHub never reads prompts, transcripts,
          credentials, or provider accounts, and it shows no paths.
        </p>
      </header>

      {error !== null ? (
        <p className="app__error" role="alert">
          {error}
        </p>
      ) : null}

      {status !== null ? (
        <p className="app__status" role="status">
          {status}
        </p>
      ) : null}

      {loading ? <p className="app__loading">Reading local provider data…</p> : null}

      {discovery !== null ? (
        <section className="panel" aria-labelledby="local-sources-heading">
          <h2 id="local-sources-heading">Local sources</h2>
          <p className="panel__intro">
            Detected provider roots and whether TokenHub may read them. Nothing is imported until a
            source is approved.
          </p>
          <div className="provider-grid">
            {discovery.providers.map((provider) => (
              <ProviderCard
                key={provider.connector_id}
                provider={provider}
                busySourceId={busySourceId}
                onApprove={handleApprove}
                onRescan={handleRescan}
              />
            ))}
          </div>
        </section>
      ) : null}

      {dashboard !== null ? (
        <section className="panel" aria-labelledby="observed-workload-heading">
          <h2 id="observed-workload-heading">Observed workload</h2>
          <p className="panel__intro">
            Workload is imported input plus output. Cache and reasoning are breakdowns of that
            total, never extra usage. Unknown values are left blank.
          </p>
          <dl className="metric-grid">
            <MetricCard label="Workload tokens" value={dashboard.workload_tokens} />
            <MetricCard label="Input tokens" value={dashboard.input_total_tokens} />
            <MetricCard label="Output tokens" value={dashboard.output_total_tokens} />
            <MetricCard label="Cache read tokens" value={dashboard.cache_read_tokens} />
            <MetricCard label="Reasoning tokens" value={dashboard.reasoning_tokens} />
          </dl>
          <p className="panel__footnote">
            Events imported: {dashboard.event_count}. {formatQualityCounts(dashboard.quality_counts)}
          </p>
        </section>
      ) : null}

      {quality !== null ? (
        <section className="panel" aria-labelledby="data-quality-heading">
          <h2 id="data-quality-heading">Data quality</h2>
          <ul className="source-status">
            {quality.source_freshness.map((entry) => (
              <li key={entry.source_id} className="source-status__item">
                <span className="source-status__name">
                  {displayNameForSource(discovery, entry.source_id)}
                </span>
                <span className="source-status__state">{entry.state}</span>
                <span className="source-status__detail">
                  Latest event: {formatTimestamp(entry.latest_event_at)}
                </span>
                {entry.unsupported_records != null ? (
                  <span className="source-status__detail">
                    Unsupported records: {entry.unsupported_records}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
          <p className="source-status__explainer">{explainQuality(incomplete)}</p>
          <p className="panel__footnote">{formatQualityCounts(quality.quality_counts)}</p>
          <button type="button" onClick={handleRebuild} disabled={rebuilding}>
            Rebuild index
          </button>
        </section>
      ) : null}
    </div>
  );
}
