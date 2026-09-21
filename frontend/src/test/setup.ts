import '@testing-library/jest-dom/vitest';

import { cleanup } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, beforeEach } from 'vitest';

import { server } from './server';

const pageOrigin = window.location.origin;

/**
 * Browser emulation shim, test-environment only.
 *
 * A real browser attaches `Origin` to same-origin state-changing requests
 * automatically, which is exactly what the local server's guard requires. jsdom
 * does not, so the shim adds it here — application code never sets `Origin`
 * itself, and no test asserts a hard-coded header value.
 *
 * Installed *after* `server.listen()` so the mock layer wraps this shim: that
 * way the request the handlers observe is the same request a browser would send.
 */
function installBrowserFetchShim(): void {
  const mswFetch = globalThis.fetch;

  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? new URL(input, pageOrigin) : input;
    const method = (
      init?.method ?? (input instanceof Request ? input.method : 'GET')
    ).toUpperCase();

    if (method === 'GET' || method === 'HEAD') {
      return mswFetch(url, init);
    }

    const headers = new Headers(init?.headers);
    if (!headers.has('origin')) {
      headers.set('origin', pageOrigin);
    }
    return mswFetch(url, { ...init, method, headers });
  }) as typeof globalThis.fetch;
}

beforeAll(() => {
  server.listen({ onUnhandledRequest: 'error' });
  installBrowserFetchShim();
});

beforeEach(() => {
  window.history.replaceState({}, '', '/');
});

afterEach(() => {
  server.resetHandlers();
  cleanup();
  window.localStorage.clear();
});

afterAll(() => {
  server.close();
});
