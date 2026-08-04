import assert from 'node:assert/strict';
import test from 'node:test';

import { fetchWithTimeout, V8DesktopClientAdapter } from '../src/lib/v8DesktopClient';

function installLocalStorage() {
  const values = new Map<string, string>();
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, String(value)),
      removeItem: (key: string) => values.delete(key),
    },
  });
  return values;
}

function jsonResponse(status: number, payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

test('ensureLocalSession signs in immediately when no token exists', async () => {
  installLocalStorage();
  const calls: string[] = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    calls.push(String(input));
    return jsonResponse(200, { accessToken: 'access-1', refreshToken: 'refresh-1' });
  }) as typeof fetch;
  try {
    const session = await new V8DesktopClientAdapter().ensureLocalSession();
    assert.equal(session.accessToken, 'access-1');
    assert.deepEqual(calls, ['/api/v8/api/client/auth/local-session']);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('ensureLocalSession refreshes an expired access token before issuing a new local session', async () => {
  const storage = installLocalStorage();
  storage.set('v8.desktopPet.auth', JSON.stringify({
    adminBaseUrl: 'http://127.0.0.1:9528',
    accessToken: 'expired-access',
    refreshToken: 'refresh-1',
  }));
  const calls: string[] = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = String(input);
    calls.push(url);
    if (url.endsWith('/auth/me') && calls.length === 1) return jsonResponse(401, { error: 'Unauthorized' });
    if (url.endsWith('/auth/refresh')) return jsonResponse(200, { accessToken: 'access-2', refreshToken: 'refresh-2' });
    if (url.endsWith('/auth/me')) return jsonResponse(200, { user: { name: 'owner' } });
    return jsonResponse(500, { error: 'unexpected' });
  }) as typeof fetch;
  try {
    const session = await new V8DesktopClientAdapter().ensureLocalSession();
    assert.equal(session.accessToken, 'access-2');
    assert.equal(calls.some((url) => url.endsWith('/auth/local-session')), false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('ensureLocalSession falls back to a trusted local session after refresh rejection', async () => {
  const storage = installLocalStorage();
  storage.set('v8.desktopPet.auth', JSON.stringify({
    adminBaseUrl: 'http://127.0.0.1:9528',
    accessToken: 'expired-access',
    refreshToken: 'expired-refresh',
  }));
  const calls: string[] = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = String(input);
    calls.push(url);
    if (url.endsWith('/auth/me')) return jsonResponse(401, { error: 'Unauthorized' });
    if (url.endsWith('/auth/refresh')) return jsonResponse(401, { error: 'Invalid refresh token' });
    if (url.endsWith('/auth/local-session')) return jsonResponse(200, { accessToken: 'access-3', refreshToken: 'refresh-3' });
    return jsonResponse(500, { error: 'unexpected' });
  }) as typeof fetch;
  try {
    const session = await new V8DesktopClientAdapter().ensureLocalSession();
    assert.equal(session.accessToken, 'access-3');
    assert.equal(calls.filter((url) => url.endsWith('/auth/local-session')).length, 1);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('ensureLocalSession reissues a trusted local session after a non-auth refresh failure', async () => {
  const storage = installLocalStorage();
  storage.set('v8.desktopPet.auth', JSON.stringify({
    adminBaseUrl: 'http://127.0.0.1:9528',
    accessToken: 'expired-access',
    refreshToken: 'refresh-1',
  }));
  storage.set('v8.desktopPet.activeConversationId', 'session-live-preserved');
  const calls: string[] = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = String(input);
    calls.push(url);
    if (url.endsWith('/auth/me')) return jsonResponse(401, { error: 'Unauthorized' });
    if (url.endsWith('/auth/refresh')) return jsonResponse(500, { error: 'Refresh temporarily unavailable' });
    if (url.endsWith('/auth/local-session')) return jsonResponse(200, { accessToken: 'access-4', refreshToken: 'refresh-4' });
    return jsonResponse(500, { error: 'unexpected' });
  }) as typeof fetch;
  try {
    const client = new V8DesktopClientAdapter();
    const session = await client.ensureLocalSession();
    assert.equal(session.accessToken, 'access-4');
    assert.equal(client.getActiveConversationId(), 'session-live-preserved');
    assert.equal(calls.filter((url) => url.endsWith('/auth/local-session')).length, 1);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('bounded requests expose a stable timeout and do not poison the next request', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
    return new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
    });
  }) as typeof fetch;
  try {
    await assert.rejects(
      fetchWithTimeout('/api/v8/slow', {}, 15),
      /V8OS 本机服务响应超时/,
    );
    globalThis.fetch = (async () => jsonResponse(200, { ok: true })) as typeof fetch;
    const response = await fetchWithTimeout('/api/v8/healthy', {}, 50);
    assert.equal(response.ok, true);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
