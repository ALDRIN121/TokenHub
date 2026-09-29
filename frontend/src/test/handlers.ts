import { http, HttpResponse } from 'msw';

import {
  dashboardFixture,
  usageFixture,
  dataQualityFixture,
  discoveryFixture,
  importOutcomeFixture,
} from './fixtures';

/**
 * Default handlers for the local `/api/v1` contract. Tests override individual
 * routes with `server.use(...)` when they need a different payload.
 *
 * State-changing POSTs mirror the server's own guard: without a same-origin
 * `Origin` header the request is refused with 403.
 */
function refusesWithoutOrigin(request: Request): Response | null {
  if (request.headers.get('origin') === null) {
    return HttpResponse.json({ detail: 'Invalid Origin header' }, { status: 403 });
  }
  return null;
}

export const handlers = [
  http.get('/api/v1/collection', () => HttpResponse.json({ scan_interval_seconds: 30, codex_auto_import: false, last_scan_at: null, failed_source_count: 0, data_version: 1 })),
  http.post('/api/v1/collection/refresh', ({ request }) => {
    const refusal = refusesWithoutOrigin(request);
    if (refusal !== null) return refusal;
    return HttpResponse.json({ scan_interval_seconds: 30, codex_auto_import: false, auto_import_connectors: [], last_scan_at: '2026-09-29T08:30:00+00:00', failed_source_count: 0, data_version: 1 });
  }),
  http.post('/api/v1/collection/:provider/:action', ({ request, params }) => {
    const refusal = refusesWithoutOrigin(request);
    if (refusal !== null) return refusal;
    const connectors: Record<string, string> = { codex: 'codex-local', claude_code: 'claude-code-local', hermes: 'hermes-local' };
    return HttpResponse.json({ scan_interval_seconds: 30, codex_auto_import: params.provider === 'codex' && params.action === 'enable', auto_import_connectors: params.action === 'enable' ? [connectors[String(params.provider)]] : [], last_scan_at: null, failed_source_count: 0, data_version: 1 });
  }),
  http.get('/api/v1/discovery', () => HttpResponse.json(discoveryFixture)),
  http.get('/api/v1/usage', () => HttpResponse.json(usageFixture)),
  http.get('/api/v1/dashboard', () => HttpResponse.json(dashboardFixture)),
  http.get('/api/v1/data-quality', () => HttpResponse.json(dataQualityFixture)),

  http.post('/api/v1/sources/:sourceId/approve', ({ request, params }) => {
    const refusal = refusesWithoutOrigin(request);
    if (refusal !== null) {
      return refusal;
    }
    return HttpResponse.json({
      source_id: String(params.sourceId),
      provider: 'codex',
      display_name: 'Codex session',
      state: 'approved',
    });
  }),

  http.post('/api/v1/sources/:sourceId/rescan', ({ request }) => {
    const refusal = refusesWithoutOrigin(request);
    if (refusal !== null) {
      return refusal;
    }
    return HttpResponse.json(importOutcomeFixture);
  }),

  http.post('/api/v1/rebuild', ({ request }) => {
    const refusal = refusesWithoutOrigin(request);
    if (refusal !== null) {
      return refusal;
    }
    return HttpResponse.json({ inserted_events: 125, failed_source_ids: [] });
  }),
];
