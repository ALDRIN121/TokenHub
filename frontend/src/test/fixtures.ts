import type { DashboardSummary, DataQualityResponse, DiscoveryResponse } from '../types';

/**
 * Synthetic fixtures only: no real home directory, no real provider data, no
 * absolute paths (the API returns fingerprints, and the UI shows none of them).
 *
 * Field values mirror what the real connectors emit, so the tests exercise the
 * shapes the app will actually receive: connector ids are `*-local`, providers
 * are the `Provider` enum values, and evidence codes are the codes the
 * connectors append (see backend/tokenhub/connectors, one module per provider).
 */
export const discoveryFixture: DiscoveryResponse = {
  providers: [
    {
      connector_id: 'claude-code-local',
      display_name: 'Claude Code',
      provider: 'claude_code',
      state: 'discovered',
      // Two independent signals → high (see confidence_from_evidence in the backend).
      confidence: 'high',
      evidence_codes: ['known_root_exists', 'configuration_found'],
      // This baseline has no Claude session files; import-capable variants are tested separately.
      sources: [],
    },
    {
      connector_id: 'codex-local',
      display_name: 'OpenAI Codex',
      provider: 'codex',
      state: 'discovered',
      confidence: 'high',
      evidence_codes: ['known_root_exists', 'session_source_found'],
      sources: [
        {
          source_id: 'codex-local:3f9a1c47d2b8e05f6a1c93d47e0b25a8c6f1d93e47b2a05c8d1f63e9a472c8b05',
          connector_id: 'codex-local',
          provider: 'codex',
          display_name: 'Codex session',
          source_type: 'jsonl',
          path_fingerprint: '3f9a1c47d2b8e05f6a1c93d47e0b25a8c6f1d93e47b2a05c8d1f63e9a472c8b05',
          state: 'discovered',
          evidence_codes: ['session_source_found'],
          scan_supported: true,
          parser_version: 'codex-jsonl-v2',
        },
      ],
    },
    {
      connector_id: 'hermes-local',
      display_name: 'Hermes Agent',
      provider: 'hermes',
      // Exercise compatibility with a discovery-only response from an older server.
      state: 'unsupported',
      confidence: 'high',
      evidence_codes: ['known_root_exists', 'state_database_found'],
      sources: [
        {
          source_id: 'hermes-local:a1d7f3c95b2e84016f9c3a75d0b8e142c6f3d91a7b2e50834c9d1f6a3b8e2074',
          connector_id: 'hermes-local',
          provider: 'hermes',
          display_name: 'Hermes state database',
          source_type: 'sqlite',
          path_fingerprint: 'a1d7f3c95b2e84016f9c3a75d0b8e142c6f3d91a7b2e50834c9d1f6a3b8e2074',
          state: 'unsupported',
          evidence_codes: ['state_database_found'],
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
      provider.connector_id === 'codex-local'
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
      source_id: 'codex-local:3f9a1c47d2b8e05f6a1c93d47e0b25a8c6f1d93e47b2a05c8d1f63e9a472c8b05',
      state: 'healthy',
      latest_event_at: '2026-09-20T12:00:00Z',
      unsupported_records: 0,
    },
    {
      source_id: 'hermes-local:a1d7f3c95b2e84016f9c3a75d0b8e142c6f3d91a7b2e50834c9d1f6a3b8e2074',
      state: 'unsupported',
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

const usageTotals = {
  workload_tokens: 125, input_total_tokens: 100, output_total_tokens: 25,
  cache_read_tokens: 40, cache_write_tokens: 10, reasoning_tokens: 8,
  event_count: 12, session_count: 1, model_count: 1, incomplete_event_count: 0,
  first_seen: '2026-09-20T12:00:00Z', last_seen: '2026-09-20T12:00:00Z',
};

export const usageFixture: import('../types').UsageBreakdown = {
  totals: usageTotals,
  providers: [{ ...usageTotals, provider: 'codex' }],
  models: [{ ...usageTotals, provider: 'codex', model_name: 'gpt-test', attribution: 'turn' }],
  sessions: [{ ...usageTotals, provider: 'codex', session_key: 'opaque-session-one', models: [{ ...usageTotals, model_name: 'gpt-test' }] }],
};
