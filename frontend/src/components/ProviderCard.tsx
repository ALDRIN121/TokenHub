import type { ProviderSummary } from '../types';
import { Icon, type IconName } from './Icon';

const EVIDENCE_LABELS: Record<string, string> = {
  executable_on_path: 'provider command on PATH',
  known_root_exists: 'known root directory present',
  configuration_found: 'configuration file present',
  session_source_found: 'session source found',
  state_database_found: 'state database found',
};

/**
 * Sources in these states keep their approval, so a retry is allowed — the same
 * set the backend's `APPROVED_SOURCE_STATES` uses.
 */
export const RESCANNABLE_STATES = new Set([
  'approved',
  'healthy',
  'partial',
  'error',
  'permission_denied',
  'source_missing',
]);

function evidenceLabel(code: string): string {
  return EVIDENCE_LABELS[code] ?? code.split('_').join(' ');
}

interface ProviderCardProps {
  provider: ProviderSummary;
  busySourceId: string | null;
  actionsDisabled?: boolean;
  onApprove: (sourceId: string) => void;
  onRescan: (sourceId: string) => void;
  autoImportEnabled?: boolean;
  onAutoImportChange?: (enabled: boolean) => void;
}

/**
 * One provider and its local sources. Actions are rendered only when the API
 * itself allows them: the connector must support scanning and the source state
 * must permit the action. Otherwise the card is read-only information.
 */
export function ProviderCard({ provider, busySourceId, actionsDisabled = false, onApprove, onRescan, autoImportEnabled = false, onAutoImportChange }: ProviderCardProps) {
  const evidence = provider.evidence_codes.map(evidenceLabel);
  const evidenceText = evidence.length > 0 ? evidence.join(', ') : 'none recorded';
  const supported = provider.sources.some((source) => source.scan_supported);
  const detected = provider.evidence_codes.some((code) => code !== 'discovery_error');
  const detectionFailed = provider.state === 'error' || provider.evidence_codes.includes('discovery_error');
  const symbols: Record<string, IconName> = { codex: 'terminal', claude_code: 'sparkle', hermes: 'hermes' };
  const stateLabel = detectionFailed ? 'Detection failed' : supported ? 'Import supported' : detected ? 'Detection only' : 'Not detected';

  return (
    <article className={`provider-card provider-card--${provider.provider ?? 'unknown'}`} aria-labelledby={`provider-${provider.connector_id}`}>
      <div className="provider-card__header">
        <span className="provider-card__icon"><Icon name={symbols[provider.provider ?? ''] ?? 'sources'} /></span>
        <h3 id={`provider-${provider.connector_id}`}>{provider.display_name}</h3>
        <span className={`badge ${detectionFailed ? 'badge--warning' : supported ? 'badge--success' : 'badge--neutral'}`}>{stateLabel}</span>
      </div>
      <p className="provider-card__description">
        {detectionFailed ? 'This installation could not be checked. Choose Refresh data to retry detection.' : supported ? 'Approved session usage updates automatically on this device.' : detected ? 'Installation detected. Usage imports are not supported yet.' : 'No installation detected on this machine.'}
      </p>

      {provider.connector_id === 'codex-local' && supported && onAutoImportChange ? (
        <div className="provider-card__automation">
          <p className="provider-card__note">{autoImportEnabled ? 'Existing and new Codex sessions are included automatically.' : 'Approve this session folder once to automatically include new Codex sessions.'}</p>
          <button type="button" className="button button--secondary" disabled={actionsDisabled} onClick={() => onAutoImportChange(!autoImportEnabled)}>
            <Icon name="refresh" />{autoImportEnabled ? 'Stop including new sessions' : 'Include new sessions automatically'}
          </button>
        </div>
      ) : null}

      {provider.sources.length === 0 ? (
        <p className="provider-card__note">
          {detectionFailed ? 'Source availability is unknown until detection succeeds.' : `No local source was found for ${provider.display_name}.`}
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
                  <span>{source.display_name}: state {source.state}.</span>{' '}
                  {source.scan_supported ? null : 'Discovery only.'}
                </p>
                {canApprove ? (
                  <button
                    type="button"
                    className="button button--primary"
                    aria-describedby={descriptionId}
                    aria-busy={busy}
                    disabled={busy || actionsDisabled}
                    onClick={() => onApprove(source.source_id)}
                  >
                    <Icon name={busy ? 'refresh' : 'check'} className={busy ? 'is-spinning' : ''} />Approve source
                  </button>
                ) : null}
                {canRescan ? (
                  <button
                    type="button"
                    className="button button--primary"
                    aria-describedby={descriptionId}
                    aria-busy={busy}
                    disabled={busy || actionsDisabled}
                    onClick={() => onRescan(source.source_id)}
                  >
                    <Icon name="refresh" className={busy ? 'is-spinning' : ''} />Rescan source
                  </button>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
      <details className="provider-card__details">
        <summary>Detection details <span>{provider.confidence} confidence</span></summary>
        <p className="provider-card__evidence">Evidence: {evidenceText}. Confidence: {provider.confidence}.</p>
      </details>
    </article>
  );
}
