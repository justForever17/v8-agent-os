const assert = require('node:assert/strict');
const test = require('node:test');
const { ensureLocalEngineIdentity } = require('../lib/engine-identity.cjs');

test('shell local identity uses loopback Engine management and returns only public identity', async () => {
  let request;
  const result = await ensureLocalEngineIdentity(async (url, init) => {
    request = { url, init };
    return { ok: true, status: 200, async json() { return { ok: true, instanceId: 'v8i_fixture', user: { id: 'owner-fixture' }, accessToken: 'secret-token' }; } };
  }, 'http://127.0.0.1:23930/v1', () => 'fixture-internal-secret');
  assert.deepEqual(result, { instanceId: 'v8i_fixture', userId: 'owner-fixture' });
  assert.equal(request.url, 'http://127.0.0.1:23930/v1/client-identity/local-session');
  assert.equal(request.init.headers['x-v8-agent-os-secret'], 'fixture-internal-secret');
  assert.equal(request.init.body, JSON.stringify({ surface: 'shell', deviceName: 'V8OS Desktop' }));
});

test('shell identity rejects remote and traversal targets before a request', async () => {
  await assert.rejects(() => ensureLocalEngineIdentity(async () => { throw new Error('must_not_fetch'); }, 'https://remote.invalid/v1', () => 'secret'), /local_engine_origin_required/);
  await assert.rejects(() => ensureLocalEngineIdentity(async () => { throw new Error('must_not_fetch'); }, 'http://127.0.0.1:23930/v1/../api', () => 'secret'), /local_engine_origin_required/);
});
