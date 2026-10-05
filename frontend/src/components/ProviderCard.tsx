import type { ProviderSummary } from '../types';
import { Icon, type IconName } from './Icon';

const EVIDENCE_LABELS: Record<string, string> = {
  executable_on_path: 'provider command on PATH',
  known_root_exists: 'known root directory present',
  configuration_found: 'configuration file present',
  session_source_found: 'session source found',
  state_database_found: 'state database found',
  unsupported_session_format: 'saved sessions in a format TokenHub cannot read',
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
  actionsDisabled?: boolean;
  autoImportEnabled?: boolean;
  requiresReapproval?: boolean;
  onAutoImportChange?: (enabled: boolean) => void;
}

/** One approval per import-capable provider, with a compact source summary. */
export function ProviderCard({ provider, actionsDisabled = false, autoImportEnabled = false, requiresReapproval = false, onAutoImportChange }: ProviderCardProps) {
  const evidence = provider.evidence_codes.map(evidenceLabel);
  const evidenceText = evidence.length > 0 ? evidence.join(', ') : 'none recorded';
  const sourceCount = provider.source_count ?? provider.sources.length;
  const supported = (provider.supported_source_count ?? provider.sources.filter((source) => source.scan_supported).length) > 0;
  const detected = provider.evidence_codes.some((code) => code !== 'discovery_error');
  const unreadableFormat = provider.evidence_codes.includes('unsupported_session_format');
  const detectionFailed = provider.state === 'error' || provider.evidence_codes.includes('discovery_error');
  const symbols: Record<string, IconName> = { codex: 'terminal', claude_code: 'sparkle', hermes: 'hermes', vscode_copilot: 'copilot', antigravity: 'sparkle' };
  const stateLabel = detectionFailed ? 'Detection failed' : supported ? 'Import supported' : detected ? 'Detection only' : 'Not detected';
  const isCopilot = provider.provider === 'vscode_copilot';
  const usageLabel: Record<string, string> = {
    codex: 'Codex sessions', claude_code: 'Claude Code sessions',
    hermes: 'Hermes Agent usage', vscode_copilot: 'Copilot sessions',
    antigravity: 'Antigravity conversations',
  };
  const awaitingApproval = provider.awaiting_approval ?? provider.sources.filter((source) => source.scan_supported && source.state === 'discovered').length;
  const approved = provider.approved_count ?? provider.sources.filter((source) => source.scan_supported && RESCANNABLE_STATES.has(source.state)).length;

  return (
    <article className={`provider-card provider-card--${provider.provider ?? 'unknown'}`} aria-labelledby={`provider-${provider.connector_id}`}>
      <div className="provider-card__header">
        <span className="provider-card__icon"><Icon name={symbols[provider.provider ?? ''] ?? 'sources'} /></span>
        <h3 id={`provider-${provider.connector_id}`}>{provider.display_name}</h3>
        <span className={`badge ${detectionFailed ? 'badge--warning' : supported ? 'badge--success' : 'badge--neutral'}`}>{stateLabel}</span>
      </div>
      <p className="provider-card__description">
        {detectionFailed ? 'This installation could not be checked. Choose Refresh data to retry detection.' : requiresReapproval ? 'Renew folder permission to resume automatic usage imports.' : supported ? 'Approved session usage updates automatically on this device.' : unreadableFormat ? 'Installation detected. Its saved conversations appear to be encrypted, so TokenHub cannot read them.' : detected ? 'Installation detected. Usage imports are not supported yet.' : 'No installation detected on this machine.'}
      </p>

      {(supported || requiresReapproval) && onAutoImportChange ? (
        <div className="provider-card__automation">
          <p className="provider-card__note">{sourceCount} {isCopilot ? (sourceCount === 1 ? 'session' : 'sessions') : (sourceCount === 1 ? 'source' : 'sources')} found · {approved} approved{awaitingApproval ? ` · ${awaitingApproval} awaiting approval` : ''}. {requiresReapproval ? `Your current folder permission needs renewal to import ${provider.provider === 'codex' ? 'existing, new, and archived Codex sessions' : `existing and new ${usageLabel[provider.provider ?? ''] ?? provider.display_name}`}.` : autoImportEnabled ? 'Current and new usage updates automatically.' : 'One approval imports saved usage and includes new sessions automatically.'}</p>
          <button type="button" className={`button ${autoImportEnabled && !requiresReapproval ? 'button--secondary' : 'button--primary'}`} disabled={actionsDisabled} onClick={() => onAutoImportChange(requiresReapproval || !autoImportEnabled)}>
            <Icon name={autoImportEnabled ? 'refresh' : 'check'} />{requiresReapproval ? `Reconnect and import ${usageLabel[provider.provider ?? ''] ?? provider.display_name}` : autoImportEnabled ? 'Stop including new sessions' : `Approve and import ${usageLabel[provider.provider ?? ''] ?? provider.display_name}`}
          </button>
        </div>
      ) : null}

      {sourceCount === 0 ? (
        <p className="provider-card__note">
          {detectionFailed ? 'Source availability is unknown until detection succeeds.' : unreadableFormat ? `${provider.display_name} saved conversations were found, but they are not in a format TokenHub can read. Nothing is imported or estimated.` : `No local source was found for ${provider.display_name}.`}
        </p>
      ) : null}
      <details className="provider-card__details">
        <summary>Detection details <span>{provider.confidence} confidence</span></summary>
        <p className="provider-card__evidence">Evidence: {evidenceText}. Confidence: {provider.confidence}.</p>
      </details>
    </article>
  );
}
