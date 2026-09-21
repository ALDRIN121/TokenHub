import { http, HttpResponse } from 'msw';

import {
  dashboardFixture,
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
  http.get('/api/v1/discovery', () => HttpResponse.json(discoveryFixture)),
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
