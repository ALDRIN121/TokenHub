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
  source_count?: number;
  supported_source_count?: number;
  approved_count?: number;
  awaiting_approval?: number;
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
  display_name?: string;
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
  source_count?: number;
  approved_source_count?: number;
  state_counts?: Record<string, number>;
}

export interface CollectionStatus {
  scan_interval_seconds: number;
  codex_auto_import: boolean;
  auto_import_connectors?: string[];
  requires_reapproval_connectors?: string[];
  last_scan_at: string | null;
  failed_source_count: number;
  /** Changes only when discovery, source states, or usage may have changed. */
  data_version: number;
  active_job?: CollectionJob | null;
  latest_job?: CollectionJob | null;
  queued_jobs?: number;
}

export interface CollectionJob {
  job_id: string;
  kind: string;
  state: 'queued' | 'running' | 'completed' | 'completed_with_errors' | 'failed' | 'interrupted';
  stage: string;
  provider: string | null;
  files_total: number | null;
  files_completed: number;
  bytes_read: number;
  bytes_total: number | null;
  records_read: number;
  records_saved: number;
  records_total: number | null;
  skipped_files: number;
  inserted_events: number;
  duplicate_events: number;
  unsupported_records: number;
  elapsed_seconds: number;
  error: string | null;
  result: ImportOutcome | RebuildOutcome | null;
}

export interface AcceptedJob { job_id: string }
export interface UsagePage<T> { items: T[]; total: number; offset: number; limit: number; data_version?: number }

export interface UsageTotals {
  workload_tokens: number | null;
  input_total_tokens: number | null;
  output_total_tokens: number | null;
  cache_read_tokens: number | null;
  cache_write_tokens: number | null;
  reasoning_tokens: number | null;
  event_count: number;
  session_count: number;
  model_count: number;
  incomplete_event_count: number;
  first_seen: string | null;
  last_seen: string | null;
}

export interface ModelUsage extends UsageTotals {
  provider: string;
  model_name: string | null;
  attribution: string;
}

export interface SessionUsage extends UsageTotals {
  provider: string;
  session_key: string;
  models: (UsageTotals & { model_name: string | null })[];
  contribution?: UsageTotals;
}

export interface UsageBreakdown {
  totals: UsageTotals;
  providers: (UsageTotals & { provider: string })[];
  models: ModelUsage[];
  sessions: SessionUsage[];
  paging?: boolean;
  unknown_model_events?: Record<string, number>;
  data_version?: number;
}

export type TrendMetric = 'workload_tokens' | 'input_total_tokens' | 'output_total_tokens' | 'cache_read_tokens' | 'cache_write_tokens' | 'reasoning_tokens';
export interface TrendTotals extends UsageTotals { reported_counts: Record<TrendMetric, number> }
export interface TrendChange {
  difference: number | null;
  percent_change: number | null;
  state: 'compared' | 'no_baseline' | 'incomplete' | 'all_time';
}
export interface TrendBucket {
  from: string; to: string;
  previous_from: string | null; previous_to: string | null;
  current: TrendTotals; previous: TrendTotals | null;
}
export interface UsageTrendsResponse {
  period: { from: string | null; to: string | null; previous_from: string | null; previous_to: string | null;
    days: number; time_zone: string; granularity: 'day' | 'week'; all_time: boolean; includes_today: boolean };
  current: TrendTotals; previous: TrendTotals | null;
  comparison: Record<TrendMetric, TrendChange>;
  buckets: TrendBucket[];
  series_limited: boolean;
  breakdown: { dimension: 'providers' | 'models'; items: { provider: string; model_name: string | null;
    current: TrendTotals; previous: TrendTotals | null; comparison: Record<TrendMetric, TrendChange> }[];
    total: number; offset: number; limit: number };
  data_version: number;
}
