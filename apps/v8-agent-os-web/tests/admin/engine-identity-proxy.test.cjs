const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const { NextRequest, NextResponse } = require('next/server');

// These are real HTTP adapter tests. Identity transaction behavior lives in
// Engine test_client_identity.py and is deliberately not reimplemented here.
async function fixture(t) {
    const calls = [];
    const state = { user: { id: 'u1', login: 'owner', sessionIdentifier: 'stable-owner', email: 'display@example.invalid', role: 'ADMIN', appearance: {} }, needsSetup: false };
    const server = http.createServer(async (req, res) => {
        let text = ''; for await (const chunk of req) text += chunk;
        calls.push({ path: req.url, method: req.method, headers: req.headers, body: text ? JSON.parse(text) : null });
        res.setHeader('content-type', 'application/json');
        if (req.url.startsWith('/v1/') && req.headers['x-v8-agent-os-secret'] !== 'synthetic-local') { res.writeHead(401); res.end('{"error":"denied"}'); return; }
        const payload = req.url.endsWith('/owner') ? { initialized: true, needsSetup: state.needsSetup, user: state.user }
            : req.url.endsWith('/users') ? { users: [state.user] }
            : req.url.endsWith('/verify-credentials') ? { ok: true, user: state.user }
            : req.url.endsWith('/profile') ? { user: { ...state.user, ...JSON.parse(text) } }
            : req.url.endsWith('/auth/refresh') ? { accessToken: 'synthetic-access', refreshToken: 'synthetic-next', user: state.user }
            : { user: state.user };
        if (req.headers.authorization === 'Bearer revoked') { res.writeHead(401); res.end('{"error":"device_revoked"}'); return; }
        res.end(JSON.stringify(payload));
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    t.after(() => new Promise(resolve => server.close(resolve)));
    const origin = `http://127.0.0.1:${server.address().port}`;
    const cache = new Map();
    const mocks = {
        'next/server': { NextRequest, NextResponse },
        '@admin/lib/auth': { auth: async () => null },
        '@admin/lib/server/runtime-config': { resolveEngineBaseUrl: () => origin + '/v1', resolveInternalSecret: () => 'synthetic-local' },
    };
    function load(name) {
        if (mocks[name]) return mocks[name];
        if (!name.startsWith('@admin/')) return require(name);
        if (cache.has(name)) return cache.get(name).exports;
        const relative = name.slice(7);
        const sourceRoot = relative.startsWith('app/') ? '../../src' : '../../src/admin';
        const file = path.resolve(__dirname, sourceRoot, relative + '.ts');
        const mod = { exports: {} }; cache.set(name, mod);
        const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } }).outputText;
        new Function('require', 'module', 'exports', code)(load, mod, mod.exports);
        return mod.exports;
    }
    return { calls, state, load };
}

test('owner/profile reads and writes use Engine and preserve stable identity', async t => {
    const f = await fixture(t), users = f.load('@admin/lib/users');
    assert.equal((await users.listUsers())[0].id, 'u1');
    assert.equal(users.getSessionIdentifier(await users.findUserByIdentifier('stable-owner')), 'stable-owner');
    const saved = await users.updateUserRecord('u1', { name: 'Renamed', appearance: { lightBackgroundEnabled: false } });
    assert.equal(saved.name, 'Renamed');
    assert.deepEqual(f.calls.at(-1).body, { name: 'Renamed', appearance: { lightBackgroundEnabled: false } });
    assert.equal(f.calls.at(-1).path, '/v1/client-identity/profile');
    const before = f.calls.length;
    await assert.rejects(users.updateUserRecord('other-owner', { name: 'Bad' }), /owner_unavailable/);
    assert.equal(f.calls.length, before + 1, 'mismatched identity must never reach PATCH');
    f.state.needsSetup = true;
    assert.equal(await users.hasOwner(), false, 'local bootstrap still offers first Admin password setup');
});

test('refresh forwards the same rotation ID and a revoked bearer never gets service credentials', async t => {
    const f = await fixture(t), mobile = f.load('@admin/lib/mobile-auth');
    await mobile.rotateMobileSession('synthetic-refresh', 'Phone', 'retry-1');
    await mobile.rotateMobileSession('synthetic-refresh', 'Phone', 'retry-1');
    assert.deepEqual(f.calls.map(row => row.body.rotationId), ['retry-1', 'retry-1']);
    assert.ok(f.calls.every(row => row.headers['x-v8-agent-os-secret'] === undefined));
    assert.equal(await mobile.resolveMobileAccessUser(new Request('http://admin.invalid', { headers: { authorization: 'Bearer revoked' } })), null);
    assert.equal(f.calls.at(-1).headers['x-v8-agent-os-secret'], undefined);
});

test('service proof resolves Engine owner and ignores attacker supplied actor', async t => {
    const f = await fixture(t), service = f.load('@admin/lib/service-auth');
    assert.equal(await service.verifyServiceAuth(new Request('http://admin.invalid', { headers: { 'x-v8-agent-os-secret': 'synthetic-local', 'x-v8-agent-os-user-email': 'attacker' } })), 'stable-owner');
    const before = f.calls.length;
    assert.equal(await service.verifyServiceAuth(new Request('http://admin.invalid', { headers: { 'x-v8-agent-os-secret': 'wrong' } })), null);
    assert.equal(f.calls.length, before);
});

test('credential proxy rejects missing proof before Engine invocation', async t => {
    const f = await fixture(t), route = f.load('@admin/app/api/admin/auth/verify-credentials/route');
    const request = headers => new NextRequest('http://admin.invalid/api/auth/verify-credentials', { method: 'POST', headers, body: JSON.stringify({ login: 'owner', password: 'synthetic-password' }) });
    assert.equal((await route.POST(request({}))).status, 401);
    assert.equal(f.calls.length, 0);
    assert.equal((await route.POST(request({ 'x-v8-agent-os-secret': 'synthetic-local' }))).status, 200);
    assert.equal(f.calls[0].path, '/v1/client-identity/verify-credentials');
});

test('a Phone bearer cannot use Admin BFF to create a management pairing ticket', async t => {
    const f = await fixture(t), route = f.load('@admin/app/api/admin/client/pairing/tickets/route');
    const response = await route.POST(new NextRequest('http://admin.invalid/api/admin/client/pairing/tickets', { method: 'POST', headers: { authorization: 'Bearer phone-token' }, body: '{}' }));
    assert.equal(response.status, 403);
    assert.equal(f.calls.length, 0);
});
