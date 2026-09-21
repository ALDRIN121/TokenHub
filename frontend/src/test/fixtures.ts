import type { DashboardSummary, DataQualityResponse, DiscoveryResponse } from '../types';

/**
 * Synthetic fixtures only: no real home directory, no real provider data, no
 * absolute paths (the API returns fingerprints, and the UI shows none of them).
 */

export const discoveryFixture: DiscoveryResponse = {
  providers: [
    {
      connector_id: 'claude',
      display_name: 'Claude Code',
      provider: 'anthropic',
      state: 'discovered',
      evidence_codes: ['directory_present'],
      sources: [
        {
          source_id: 'claude-code:config',
          connector_id: 'claude',
          provider: 'anthropic',
          display_name: 'Claude Code configuration directory',
          source_type: 'directory',
          path_fingerprint: 'fp-0000-claude',
          state: 'discovered',
          evidence_codes: ['directory_present'],
          scan_supported: false,
          parser_version: null,
        },
      ],
    },
    {
      connector_id: 'codex',
      display_name: 'OpenAI Codex',
      provider: 'openai',
      state: 'discovered',
      evidence_codes: ['directory_present', 'sessions_present'],
      sources: [
        {
          source_id: 'codex:sessions',
          connector_id: 'codex',
          provider: 'openai',
          display_name: 'OpenAI Codex sessions',
          source_type: 'jsonl_sessions',
          path_fingerprint: 'fp-0000-codex',
          state: 'discovered',
          evidence_codes: ['directory_present', 'sessions_present'],
          scan_supported: true,
          parser_version: 'codex-token-usage/1',
        },
      ],
    },
    {
      connector_id: 'hermes',
      display_name: 'Hermes',
      provider: 'nousresearch',
      state: 'discovered',
      evidence_codes: ['database_present'],
      sources: [
        {
          source_id: 'hermes:state',
          connector_id: 'hermes',
          provider: 'nousresearch',
          display_name: 'Hermes state database',
          source_type: 'sqlite_database',
          path_fingerprint: 'fp-0000-hermes',
          state: 'discovered',
          evidence_codes: ['database_present'],
          scan_supported: false,
          parser_version: null,
        },
      ],
    },
  ],
};

/** Same discovery payload with the Codex source moved to a different state. */
export function discoveryWithCodexState(state: string): DiscoveryResponse {
  return {
    providers: discoveryFixture.providers.map((provider) =>
      provider.connector_id === 'codex'
        ? {
            ...provider,
            state,
            sources: provider.sources.map((source) => ({ ...source, state })),
          }
        : provider,
    ),
  };
}

export const dashboardFixture: DashboardSummary = {
  workload_tokens: 125,
  input_total_tokens: 100,
  output_total_tokens: 25,
  cache_read_tokens: 40,
  cache_write_tokens: 10,
  reasoning_tokens: 8,
  event_count: 12,
  quality_counts: { exact: 11, partial: 1 },
  source_freshness: [
    {
      source_id: 'codex:sessions',
      state: 'healthy',
      latest_event_at: '2026-09-20T12:00:00Z',
      unsupported_records: 0,
    },
    {
      source_id: 'claude-code:config',
      state: 'approved',
      latest_event_at: null,
      unsupported_records: null,
    },
  ],
};

export const dataQualityFixture: DataQualityResponse = {
  quality_counts: { exact: 11, partial: 1 },
  source_freshness: dashboardFixture.source_freshness,
};

export const importOutcomeFixture = {
  inserted_events: 3,
  duplicate_events: 1,
  partial_final_record: true,
  unsupported_records: 0,
};
