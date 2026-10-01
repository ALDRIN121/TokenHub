import type {
  AcceptedJob,
  CollectionJob,
  ModelUsage,
  SessionUsage,
  UsagePage,
  ApprovalResult,
  CollectionStatus,
  DashboardSummary,
  DataQualityResponse,
  DiscoveryResponse,
  ImportOutcome,
  RebuildOutcome,
  UsageBreakdown,
  UsageTrendsResponse,
} from '../types';

/**
 * Every call is a same-origin relative path under `/api/v1`: the app is served
 * by the local FastAPI process itself, so the browser supplies the Origin
 * header on state-changing requests. The client never fabricates headers.
 */
const API_BASE = '/api/v1';

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, path: string) {
    super(`TokenHub could not complete the request to ${path} (status ${status}).`);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { Accept: 'application/json', ...(init?.headers ?? {}) },
  });

  if (!response.ok) {
    throw new ApiError(response.status, path);
  }

  return (await response.json()) as T;
}

export function getDiscovery(): Promise<DiscoveryResponse> {
  return request<DiscoveryResponse>('/discovery?include_sources=false');
}

export function getDashboard(): Promise<DashboardSummary> {
  return request<DashboardSummary>('/dashboard');
}

export function getDataQuality(offset = 0): Promise<DataQualityResponse> {
  return request<DataQualityResponse>(`/data-quality?lightweight=true&limit=25&offset=${offset}`);
}

export function getCollectionStatus(): Promise<CollectionStatus> {
  return request<CollectionStatus>('/collection');
}

export function refreshCollection(): Promise<AcceptedJob | CollectionStatus> {
  return request<AcceptedJob | CollectionStatus>('/collection/refresh?background=true', { method: 'POST' });
}

export function setProviderAutoImport(provider: string, enabled: boolean): Promise<AcceptedJob | CollectionStatus> {
  return request<AcceptedJob | CollectionStatus>(`/collection/${encodeURIComponent(provider)}/${enabled ? 'enable?background=true' : 'disable'}`, { method: 'POST' });
}

export function approveSource(sourceId: string): Promise<ApprovalResult> {
  return request<ApprovalResult>(`/sources/${encodeURIComponent(sourceId)}/approve`, {
    method: 'POST',
  });
}

export function rescanSource(sourceId: string): Promise<ImportOutcome> {
  return request<ImportOutcome>(`/sources/${encodeURIComponent(sourceId)}/rescan`, {
    method: 'POST',
  });
}

export function rebuildIndex(): Promise<AcceptedJob | RebuildOutcome> {
  return request<AcceptedJob | RebuildOutcome>('/rebuild?background=true', { method: 'POST' });
}

export function getUsageBreakdown(range?: { from: string; to: string }): Promise<UsageBreakdown> {
  const query = `?${new URLSearchParams({ ...range, summary_only: 'true' }).toString()}`;
  return request<UsageBreakdown>(`/usage${query}`);
}

export function getCollectionJob(id: string): Promise<CollectionJob> {
  return request<CollectionJob>(`/jobs/${encodeURIComponent(id)}`);
}

export function getUsagePage(mode: 'models' | 'sessions', options: Record<string, string>, signal?: AbortSignal): Promise<UsagePage<ModelUsage | SessionUsage>> {
  return request(`/usage/${mode}?${new URLSearchParams({ ...options, limit: '25' })}`, { signal });
}

export function getSessionModels(key: string, options: Record<string, string>, signal?: AbortSignal): Promise<UsagePage<ModelUsage>> {
  return request(`/usage/sessions/${encodeURIComponent(key)}/models?${new URLSearchParams({ ...options, limit: '25' })}`, { signal });
}

export function getUsageTrends(options: Record<string, string>, signal?: AbortSignal): Promise<UsageTrendsResponse> {
  return request(`/usage/trends?${new URLSearchParams({ ...options, limit: '25' })}`, { signal });
}
