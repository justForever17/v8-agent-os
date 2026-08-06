const assert = require('node:assert/strict');
const test = require('node:test');

const {
  classifyProductSurface,
  fetchTextWithTimeout,
  isWebSurfaceReady,
  productSurfaceDomScript,
  validateReadinessResponse,
  verifyProductSurfaceDom,
} = require('../lib/readiness-probe.cjs');

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
  assert.equal(await verifyProductSurfaceDom(async () => false, 'admin-login'), false);
  assert.equal(await verifyProductSurfaceDom(async () => { throw new Error('renderer unavailable'); }, 'admin'), false);
  assert.equal(await verifyProductSurfaceDom(async () => true, 'startup'), false);
  assert.equal(productSurfaceDomScript('startup'), '');
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
