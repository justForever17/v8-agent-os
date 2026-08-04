const assert = require('node:assert/strict');
const test = require('node:test');
const {
  STABLE_RENDERER_ENTRY_URL,
  createDevelopmentTransport,
  createVerifiedTransport,
  developmentRendererContentSecurityPolicy,
  forwardStableRendererRequest,
  isTrustedRendererUrl,
  mapStableRequestUrl,
  registerStableRendererScheme,
  rendererTransportView,
} = require('../lib/stable-renderer-transport.cjs');

function productionTransport(port = 43123) {
  return createVerifiedTransport({
    baseUrl: `http://127.0.0.1:${port}`,
    instanceId: 'transport-test',
    serverPid: 321,
  });
}

test('stable renderer scheme is registered as a standard secure streaming fetch origin', () => {
  let registrations = null;
  registerStableRendererScheme({
    registerSchemesAsPrivileged(value) {
      registrations = value;
    },
  });
  assert.deepEqual(registrations, [{
    scheme: 'v8-desktop',
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      stream: true,
      corsEnabled: true,
    },
  }]);
});

test('stable renderer URL mapping cannot change the verified loopback origin', () => {
  const transport = productionTransport();
  assert.equal(
    mapStableRequestUrl(`${STABLE_RENDERER_ENTRY_URL}assets/app.js?v=1`, transport),
    'http://127.0.0.1:43123/assets/app.js?v=1',
  );
  assert.equal(
    mapStableRequestUrl('v8-desktop://app//example.com/escape', transport),
    'http://127.0.0.1:43123//example.com/escape',
  );
  assert.throws(
    () => mapStableRequestUrl('v8-desktop://other/assets/app.js', transport),
    /Untrusted desktop pet renderer request/,
  );
  assert.throws(
    () => createVerifiedTransport({
      baseUrl: 'http://127.0.0.1:43123/redirect?target=https://example.com',
      instanceId: 'bad',
      serverPid: 12,
    }),
    /exact loopback HTTP origin/,
  );
});

test('stable renderer forwarding preserves POST bodies and streaming responses', async () => {
  const transport = productionTransport();
  let forwarded = null;
  const request = new Request('v8-desktop://app/api/echo?mode=post', {
    method: 'POST',
    headers: {
      'content-type': 'text/plain',
      host: 'attacker.invalid',
      referer: 'https://attacker.invalid/private',
    },
    body: 'payload',
  });
  const response = await forwardStableRendererRequest(request, transport, async (url, init) => {
    forwarded = {
      url,
      method: init.method,
      host: init.headers.get('host'),
      referer: init.headers.get('referer'),
      body: await new Response(init.body).text(),
    };
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('event: ready\ndata: {}\n\n'));
        controller.close();
      },
    });
    return new Response(stream, { headers: { 'content-type': 'text/event-stream' } });
  });
  assert.deepEqual(forwarded, {
    url: 'http://127.0.0.1:43123/api/echo?mode=post',
    method: 'POST',
    host: null,
    referer: null,
    body: 'payload',
  });
  assert.equal(response.headers.get('content-type'), 'text/event-stream');
  const csp = response.headers.get('content-security-policy');
  assert.match(csp, /connect-src 'self' ws:\/\/127\.0\.0\.1:43123/);
  assert.doesNotMatch(csp, /127\.0\.0\.1:\*/);
  assert.equal(await response.text(), 'event: ready\ndata: {}\n\n');
});

test('stable renderer forwarding rejects cross-origin redirects', async () => {
  const transport = productionTransport();
  const response = await forwardStableRendererRequest(
    new Request('v8-desktop://app/redirect'),
    transport,
    async () => new Response(null, {
      status: 302,
      headers: { location: 'https://example.com/escape' },
    }),
  );
  assert.equal(response.status, 502);
  assert.match(await response.text(), /cross-origin redirect/);
});

test('production permissions only trust the stable origin and development uses the explicit origin', () => {
  assert.equal(isTrustedRendererUrl('v8-desktop://app/index.html'), true);
  assert.equal(isTrustedRendererUrl('http://127.0.0.1:3000/'), false);
  assert.equal(isTrustedRendererUrl('about:blank'), false);

  const dev = createDevelopmentTransport('http://localhost:5173/app');
  assert.equal(isTrustedRendererUrl('http://localhost:5173/other', dev), true);
  assert.equal(isTrustedRendererUrl('http://127.0.0.1:5173/other', dev), false);
  assert.equal(isTrustedRendererUrl('v8-desktop://app/', dev), false);
  assert.deepEqual(rendererTransportView(dev), {
    engineWebSocketUrl: 'ws://localhost:5173/api/v8/engine-ws',
  });
  const devCsp = developmentRendererContentSecurityPolicy(dev);
  assert.match(devCsp, /connect-src 'self' ws:\/\/localhost:5173/);
  assert.doesNotMatch(devCsp, /127\.0\.0\.1:\*/);
});
