import type { ProviderSummary } from '../types';

const EVIDENCE_LABELS: Record<string, string> = {
  directory_present: 'provider directory present',
  sessions_present: 'session directory present',
  database_present: 'state database present',
  file_present: 'expected file present',
  env_var_set: 'home override configured',
};

/** Sources in these states can be scanned again, but only if the connector scans. */
const RESCANNABLE_STATES = new Set(['approved', 'healthy', 'partial', 'error']);

function evidenceLabel(code: string): string {
  return EVIDENCE_LABELS[code] ?? code.split('_').join(' ');
}

function confidenceFor(evidenceCount: number): string {
  if (evidenceCount >= 2) {
    return 'high';
  }
  return evidenceCount === 1 ? 'medium' : 'low';
}

interface ProviderCardProps {
  provider: ProviderSummary;
  busySourceId: string | null;
  onApprove: (sourceId: string) => void;
  onRescan: (sourceId: string) => void;
}

/**
 * One provider and its local sources. Actions are rendered only when the API
 * itself allows them: the connector must support scanning and the source state
 * must permit the action. Otherwise the card is read-only information.
 */
export function ProviderCard({ provider, busySourceId, onApprove, onRescan }: ProviderCardProps) {
  const evidence = provider.evidence_codes.map(evidenceLabel);
  const evidenceText = evidence.length > 0 ? evidence.join(', ') : 'none recorded';

  return (
    <article className="provider-card" aria-labelledby={`provider-${provider.connector_id}`}>
      <h3 id={`provider-${provider.connector_id}`}>{provider.display_name}</h3>
      <p className="provider-card__evidence">
        Evidence: {evidenceText}. Confidence: {confidenceFor(evidence.length)}.
      </p>

      {provider.sources.length === 0 ? (
        <p className="provider-card__note">
          No local source was found for {provider.display_name}.
        </p>
      ) : (
        <ul className="provider-card__sources">
          {provider.sources.map((source) => {
            const descriptionId = `source-summary-${source.source_id.replace(/[^a-zA-Z0-9_-]+/g, '-')}`;
            const canApprove = source.scan_supported && source.state === 'discovered';
            const canRescan = source.scan_supported && RESCANNABLE_STATES.has(source.state);
            const busy = busySourceId === source.source_id;

            return (
              <li key={source.source_id}>
                <p className="provider-card__source" id={descriptionId}>
                  {source.display_name}: state {source.state}.{' '}
                  {source.scan_supported ? 'Import supported' : 'Discovery only'}.
                </p>
                {canApprove ? (
                  <button
                    type="button"
                    aria-describedby={descriptionId}
                    disabled={busy}
                    onClick={() => onApprove(source.source_id)}
                  >
                    Approve source
                  </button>
                ) : null}
                {canRescan ? (
                  <button
                    type="button"
                    aria-describedby={descriptionId}
                    disabled={busy}
                    onClick={() => onRescan(source.source_id)}
                  >
                    Rescan source
                  </button>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </article>
  );
}
