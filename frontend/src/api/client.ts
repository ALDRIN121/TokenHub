import type {
  ApprovalResult,
  CollectionStatus,
  DashboardSummary,
  DataQualityResponse,
  DiscoveryResponse,
  ImportOutcome,
  RebuildOutcome,
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
  return request<DiscoveryResponse>('/discovery');
}

export function getDashboard(): Promise<DashboardSummary> {
  return request<DashboardSummary>('/dashboard');
}

export function getDataQuality(): Promise<DataQualityResponse> {
  return request<DataQualityResponse>('/data-quality');
}

export function getCollectionStatus(): Promise<CollectionStatus> {
  return request<CollectionStatus>('/collection');
}

export function setCodexAutoImport(enabled: boolean): Promise<CollectionStatus> {
  return request<CollectionStatus>(`/collection/codex/${enabled ? 'enable' : 'disable'}`, { method: 'POST' });
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

export function rebuildIndex(): Promise<RebuildOutcome> {
  return request<RebuildOutcome>('/rebuild', { method: 'POST' });
}
