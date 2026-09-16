const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');
const { NextRequest, NextResponse } = require('next/server');

function loadRoute(name, session, secret, fetchImpl) {
  const file = path.resolve(__dirname, `../src/app/api/${name}/[[...segments]]/route.ts`);
  const source = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const mocks = {
    'next/server': { NextRequest, NextResponse },
    '@/lib/auth': { auth: async () => session },
    '@/lib/server/runtime-config': { resolveEngineBaseUrl: async () => 'http://engine.invalid/v1', resolveInternalSecret: async () => secret },
  };
  const record = { exports: {} };
  new Function('require', 'exports', 'fetch', source)(name => mocks[name] || require(name), record.exports, fetchImpl);
  return record.exports;
}

for (const [name, segments] of [
  ['workbench', ['sessions', 'fixture-session', 'files', 'read']],
  ['ui-patch', ['sessions', 'fixture-session', 'projects', 'inspect']],
  ['ui-actions', ['fixture-action', 'invoke']],
]) {
  test(`${name} authenticates before body and forwards only to the canonical Engine without redirects`, async () => {
    const calls = [];
    const fetchImpl = async (url, init) => { calls.push({url, init}); return Response.json({accepted: true}); };
    const context = {params: Promise.resolve({segments})};
    const denied = loadRoute(name, null, 'fixture-service', fetchImpl);
    const request = new NextRequest('http://web.invalid/api/test', {method:'POST', body:'{"intent":"once"}'});
    request.text = () => assert.fail('unauthorized body must not be consumed');
    assert.equal((await denied.POST(request, context)).status, 401);
    assert.equal(calls.length, 0);
    const missing = loadRoute(name, {user:{email:'fixture-owner'}}, '', fetchImpl);
    assert.equal((await missing.POST(request, context)).status, 503);
    assert.equal(calls.length, 0);
    const route = loadRoute(name, {user:{email:'fixture-owner'}}, 'fixture-service', fetchImpl);
    const allowed = new NextRequest('http://web.invalid/api/test', {method:'POST', body:'{"intent":"once"}', headers:{'x-v8-agent-os-secret':'forged','Range':'bytes=0-5'}});
    assert.equal((await route.POST(allowed, context)).status, 200);
    assert.equal(calls.length, 1);
    assert.equal(new URL(calls[0].url).origin, 'http://engine.invalid');
    assert.equal(new Headers(calls[0].init.headers).get('x-v8-agent-os-secret'), 'fixture-service');
    assert.equal(calls[0].init.redirect, 'error');
    assert.equal(calls[0].init.signal, allowed.signal);
    assert.equal(calls[0].init.body, '{"intent":"once"}');
  });
}
