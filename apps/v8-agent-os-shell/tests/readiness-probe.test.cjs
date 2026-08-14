const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');

const {
  classifyProductSurface,
  fetchTextWithTimeout,
  initialProductSurfaceUrl,
  isWebSurfaceReady,
  productSurfaceDomScript,
  resolveAdminSurfaceAuthentication,
  validateAdminSessionResponse,
  validateReadinessResponse,
  verifyProductSurfaceDom,
  waitForProductSurfaceDom,
} = require('../lib/readiness-probe.cjs');

test('initial Shell surface uses local Web after setup and reserves Admin login for bootstrap', () => {
  const options = {
    defaultChatUrl: 'http://127.0.0.1:9527/chat',
    adminBaseUrl: 'http://127.0.0.1:9528',
  };
  assert.equal(initialProductSurfaceUrl({ ...options, initialized: true, adminAuthenticated: true }), options.defaultChatUrl);
  assert.equal(initialProductSurfaceUrl({ ...options, initialized: false, adminAuthenticated: false }), 'http://127.0.0.1:9528/login');
  assert.equal(initialProductSurfaceUrl({ ...options, initialized: true, adminAuthenticated: false }), 'http://127.0.0.1:9528/login');
  assert.equal(initialProductSurfaceUrl({
    ...options,
    initialized: true,
    adminAuthenticated: true,
    pendingSurfaceUrl: 'http://127.0.0.1:9528/admin/models',
  }), 'http://127.0.0.1:9528/admin/models');
  assert.equal(initialProductSurfaceUrl({
    ...options,
    initialized: true,
    adminAuthenticated: false,
    pendingSurfaceUrl: 'http://127.0.0.1:9527/chat',
  }), 'http://127.0.0.1:9528/login');
  assert.throws(() => initialProductSurfaceUrl(options), /initialized must be a boolean/);
  assert.throws(() => initialProductSurfaceUrl({ ...options, initialized: true }), /adminAuthenticated must be a boolean/);
});

test('Admin session probe accepts only an authenticated administrator', () => {
  assert.equal(validateAdminSessionResponse({
    ok: true,
    status: 200,
    body: JSON.stringify({ user: { role: 'ADMIN' } }),
  }), true);
  assert.equal(validateAdminSessionResponse({ ok: true, status: 200, body: '{}' }), false);
  assert.equal(validateAdminSessionResponse({ ok: true, status: 200, body: '{invalid' }), false);
  assert.equal(validateAdminSessionResponse({ ok: false, status: 500, body: '{}' }), false);
});

test('Admin surfaces unlock only for a current authenticated administrator document', async () => {
  const base = {
    coreServicesReady: true,
    webBaseUrl: 'http://127.0.0.1:9527',
    adminBaseUrl: 'http://127.0.0.1:9528',
  };
  for (const loadedUrl of [
    'http://127.0.0.1:9528/admin/verify',
    'http://127.0.0.1:9528/admin/404',
  ]) {
    const surfaceKind = classifyProductSurface({ ...base, loadedUrl });
    assert.equal(surfaceKind, 'admin');
    assert.equal(await resolveAdminSurfaceAuthentication({
      surfaceKind,
      probeAuthenticated: async () => false,
      isCurrent: () => true,
    }), 'unauthenticated');
  }

  const modelsSurfaceKind = classifyProductSurface({
    ...base,
    loadedUrl: 'http://127.0.0.1:9528/admin/models',
  });
  assert.equal(await resolveAdminSurfaceAuthentication({
    surfaceKind: modelsSurfaceKind,
    probeAuthenticated: async () => true,
    isCurrent: () => true,
  }), 'authenticated');
});

test('a stale Admin session probe result cannot unlock a newer document', async () => {
  let finishProbe;
  let current = true;
  const resolution = resolveAdminSurfaceAuthentication({
    surfaceKind: 'admin',
    probeAuthenticated: () => new Promise((resolve) => { finishProbe = resolve; }),
    isCurrent: () => current,
  });

  current = false;
  finishProbe(true);
  assert.equal(await resolution, 'stale');
});

test('Engine readiness requires the canonical JSON identity and ready=true', () => {
  const urlContract = {
    responseUrl: 'http://127.0.0.1:9530/readyz',
    expectedOrigin: 'http://127.0.0.1:9530',
  };
  assert.deepEqual(validateReadinessResponse('engine', {
    ok: true,
    status: 200,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify({ status: 'ok', service: 'v8-agent-os-engine', ready: true }),
    ...urlContract,
  }), {
    ok: true,
    marker: 'v8-agent-os-engine:ready',
  });
  assert.equal(validateReadinessResponse('engine', {
    ok: true,
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ status: 'ok', service: 'v8-agent-os-engine', ready: false }),
    ...urlContract,
  }).ok, false);
  assert.equal(validateReadinessResponse('engine', {
    ok: true,
    status: 200,
    contentType: 'text/html',
    body: '"service":"v8-agent-os-engine"',
    ...urlContract,
  }).ok, false);
  assert.equal(validateReadinessResponse('engine', {
    ok: true,
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ status: 'ok', service: 'v8-agent-os-engine', ready: true }),
    responseUrl: 'http://127.0.0.1:9530/docs',
    expectedOrigin: 'http://127.0.0.1:9530',
  }).reason, 'engine_response_path_mismatch');
});

test('Admin and Web readiness reject generic successful pages', () => {
  assert.equal(validateReadinessResponse('admin', {
    ok: true,
    status: 200,
    body: '<input id="login">',
    responseUrl: 'http://127.0.0.1:9528/login',
    expectedOrigin: 'http://127.0.0.1:9528',
  }).ok, true);
  assert.deepEqual(validateReadinessResponse('admin', {
    ok: true,
    status: 200,
    body: '<title>V8 Agent OS</title><main>Admin</main>',
    responseUrl: 'http://127.0.0.1:9528/admin',
    expectedOrigin: 'http://127.0.0.1:9528',
  }), {
    ok: true,
    marker: '<title>V8 Agent OS</title>',
    surface: 'admin',
  });
  assert.equal(validateReadinessResponse('admin', {
    ok: true,
    status: 200,
    body: '<title>V8 Agent OS</title>',
    responseUrl: 'https://example.com/admin',
    expectedOrigin: 'http://127.0.0.1:9528',
  }).ok, false);
  assert.equal(validateReadinessResponse('admin', {
    ok: true,
    status: 200,
    body: '<title>V8 Agent OS</title>',
    responseUrl: 'http://127.0.0.1:9528/error',
    expectedOrigin: 'http://127.0.0.1:9528',
  }).ok, false);
  assert.equal(validateReadinessResponse('admin', {
    ok: true,
    status: 200,
    body: 'loading',
    responseUrl: 'http://127.0.0.1:9528/login',
    expectedOrigin: 'http://127.0.0.1:9528',
  }).ok, false);
  assert.equal(validateReadinessResponse('web', {
    ok: true,
    status: 200,
    body: '<title>V8 Agent OS - AI Assistant</title>',
    responseUrl: 'http://127.0.0.1:9527/chat',
    expectedOrigin: 'http://127.0.0.1:9527',
  }).ok, true);
  assert.equal(validateReadinessResponse('web', {
    ok: true,
    status: 200,
    body: '<title>V8 Agent OS - AI Assistant</title>',
    responseUrl: 'https://example.com/chat',
    expectedOrigin: 'http://127.0.0.1:9527',
  }).reason, 'web_redirect_origin_mismatch');
  assert.equal(validateReadinessResponse('web', {
    ok: true,
    status: 200,
    body: '<title>V8 Agent OS - AI Assistant</title>',
    responseUrl: 'http://127.0.0.1:9527/error',
    expectedOrigin: 'http://127.0.0.1:9527',
  }).reason, 'web_response_path_mismatch');
  assert.equal(validateReadinessResponse('web', { ok: true, status: 503, body: '' }).ok, false);
});

test('response body consumption remains inside the request timeout', async () => {
  const fetchImpl = async (_url, options) => ({
    ok: true,
    status: 200,
    text: () => new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => {
        const error = new Error('body aborted');
        error.name = 'AbortError';
        reject(error);
      }, { once: true });
    }),
  });

  await assert.rejects(
    fetchTextWithTimeout(fetchImpl, 'http://127.0.0.1/slow-body', 20),
    (error) => error?.name === 'AbortError',
  );
});

test('fetch readiness returns the final response URL after redirects', async () => {
  const fetchImpl = async () => ({
    ok: true,
    status: 200,
    url: 'http://127.0.0.1:9528/admin',
    text: async () => '<title>V8 Agent OS</title>',
  });

  const result = await fetchTextWithTimeout(fetchImpl, 'http://127.0.0.1:9528/login', 100);
  assert.equal(result.responseUrl, 'http://127.0.0.1:9528/admin');
});

test('product surface DOM readiness requires a nonblank interactive product document', async () => {
  const observed = [];
  assert.equal(await verifyProductSurfaceDom(async (script) => {
    observed.push(script);
    return true;
  }, 'web'), true);
  assert.match(observed[0], /V8 Agent OS - AI Assistant/);
  assert.match(observed[0], /querySelector/);
  assert.match(observed[0], /data-v8os-hydration/);
  assert.match(observed[0], /getComputedStyle/);
  assert.match(observed[0], /textarea\[data-v8os-chat-composer=/);
  assert.match(observed[0], /button\[data-v8os-start-task=/);
  assert.doesNotMatch(observed[0], /, button:not\(\[disabled\]\)/);
  assert.match(observed[0], /elementFromPoint/);
  assert.match(observed[0], /pointerEvents !== 'none'/);
  assert.doesNotMatch(observed[0], /\.focus\(|\.blur\(/);
  assert.equal(await verifyProductSurfaceDom(async () => false, 'admin-login'), false);
  assert.equal(await verifyProductSurfaceDom(async () => { throw new Error('renderer unavailable'); }, 'admin'), false);
  assert.equal(await verifyProductSurfaceDom(async () => true, 'startup'), false);
  assert.equal(productSurfaceDomScript('startup'), '');
});

test('Admin login readiness probe never steals or clears the active input focus', () => {
  let focusCalls = 0;
  let blurCalls = 0;
  const input = {
    focus() { focusCalls += 1; },
    blur() { blurCalls += 1; },
    contains(target) { return target === this; },
    getBoundingClientRect() { return { left: 10, top: 10, width: 200, height: 40 }; },
  };
  const marker = { getAttribute: () => 'ready' };
  const document = {
    title: 'V8 Agent OS',
    body: { innerText: 'Admin sign in' },
    activeElement: input,
    querySelector(selector) {
      if (selector === '[data-v8os-style-probe="true"]') return marker;
      return input;
    },
    elementFromPoint: () => input,
  };
  const ready = vm.runInNewContext(productSurfaceDomScript('admin-login'), {
    document,
    getComputedStyle(target) {
      return target === marker ? { display: 'none' } : { pointerEvents: 'auto' };
    },
  });
  assert.equal(ready, true);
  assert.equal(focusCalls, 0);
  assert.equal(blurCalls, 0);
});

test('product surface DOM readiness waits for client hydration without relaxing the contract', async () => {
  let elapsedMs = 0;
  let attempts = 0;
  const ready = await waitForProductSurfaceDom(async () => {
    attempts += 1;
    return attempts >= 3;
  }, 'web', {
    timeoutMs: 500,
    intervalMs: 100,
    now: () => elapsedMs,
    sleep: async (delayMs) => { elapsedMs += delayMs; },
  });
  assert.equal(ready, true);
  assert.equal(attempts, 3);
  assert.equal(elapsedMs, 200);

  elapsedMs = 0;
  attempts = 0;
  assert.equal(await waitForProductSurfaceDom(async () => {
    attempts += 1;
    return false;
  }, 'admin', {
    timeoutMs: 250,
    intervalMs: 100,
    now: () => elapsedMs,
    sleep: async (delayMs) => { elapsedMs += delayMs; },
  }), false);
  assert.equal(attempts, 4);
  assert.equal(elapsedMs, 250);
});

test('Shell classifies only loaded same-origin product surfaces as ready', () => {
  const base = {
    coreServicesReady: true,
    webBaseUrl: 'http://127.0.0.1:9527',
    adminBaseUrl: 'http://127.0.0.1:9528',
  };
  assert.equal(isWebSurfaceReady({ ...base, loadedUrl: 'http://127.0.0.1:9527/chat' }), true);
  assert.equal(isWebSurfaceReady({ ...base, loadedUrl: 'http://127.0.0.1:9527/chat/session-1?view=live' }), true);
  assert.equal(isWebSurfaceReady({ ...base, coreServicesReady: false, loadedUrl: 'http://127.0.0.1:9527/chat' }), false);
  assert.equal(isWebSurfaceReady({ ...base, loadedUrl: 'http://127.0.0.1:9527/admin' }), false);
  assert.equal(isWebSurfaceReady({ ...base, loadedUrl: 'http://127.0.0.1:9527.example.com/chat' }), false);
  assert.equal(classifyProductSurface({ ...base, loadedUrl: 'http://127.0.0.1:9528/login' }), 'admin-login');
  assert.equal(classifyProductSurface({ ...base, loadedUrl: 'http://127.0.0.1:9528/admin/models' }), 'admin');
  assert.equal(classifyProductSurface({ ...base, loadedUrl: 'http://127.0.0.1:9528/' }), null);
  assert.equal(classifyProductSurface({ ...base, loadedUrl: 'http://127.0.0.1:9528.example.com/login' }), null);
});
