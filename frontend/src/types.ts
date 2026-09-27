/**
 * Wire types for the TokenHub `/api/v1` routes.
 *
 * These mirror the JSON shapes the local FastAPI app actually returns. Fields
 * the API reports as unknown are `null` on purpose: the client must never turn
 * an unknown value into a zero.
 */

export interface SourceSummary {
  source_id: string;
  connector_id: string;
  provider: string;
  display_name: string;
  source_type: string;
  path_fingerprint: string;
  state: string;
  evidence_codes: string[];
  scan_supported: boolean;
  parser_version: string | null;
}

export interface ProviderSummary {
  connector_id: string;
  display_name: string;
  provider: string | null;
  state: string;
  /** How sure discovery is that this provider is present. Reported by the API. */
  confidence: string;
  evidence_codes: string[];
  sources: SourceSummary[];
}

export interface DiscoveryResponse {
  providers: ProviderSummary[];
}

export interface ApprovalResult {
  source_id: string;
  provider: string;
  display_name: string;
  state: string;
}

export interface ImportOutcome {
  inserted_events: number;
  duplicate_events: number;
  partial_final_record: boolean;
  unsupported_records: number;
}

export interface RebuildOutcome {
  inserted_events: number;
  failed_source_ids: string[];
}

export interface SourceFreshness {
  source_id: string;
  state: string;
  latest_event_at: string | null;
  unsupported_records: number | null;
}

export interface DashboardSummary {
  workload_tokens: number | null;
  input_total_tokens: number | null;
  output_total_tokens: number | null;
  cache_read_tokens: number | null;
  cache_write_tokens: number | null;
  reasoning_tokens: number | null;
  event_count: number;
  quality_counts: Record<string, number>;
  source_freshness: SourceFreshness[];
}

export interface DataQualityResponse {
  quality_counts: Record<string, number>;
  source_freshness: SourceFreshness[];
}

export interface CollectionStatus {
  scan_interval_seconds: number;
  codex_auto_import: boolean;
  last_scan_at: string | null;
  failed_source_count: number;
}
