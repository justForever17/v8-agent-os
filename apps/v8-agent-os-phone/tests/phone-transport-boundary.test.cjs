const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');
const tick = () => new Promise(resolve => setImmediate(resolve));
function loadProduction(mocks = {}) {
  const cache = new Map();
  function load(name) {
    if (Object.hasOwn(mocks, name)) return mocks[name];
    if (name === 'expo/fetch') return { fetch: (...args) => global.fetch(...args) };
    if (name === '@/src/lib/locale') return { translateCurrent: key => key };
    if (!name.startsWith('@/')) return require(name);
    if (cache.has(name)) return cache.get(name);
    const base = path.join(root, name.slice(2));
    const file = fs.existsSync(base + '.ts') ? base + '.ts' : base + '.tsx';
    const module = { exports: {} };
    const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
    vm.runInThisContext(`(function(require,module,exports){${code}\n})`)(load, module, module.exports);
    cache.set(name, module.exports); return module.exports;
  }
  return load;
}
function create(options = {}) {
  const { PhoneTransport } = loadProduction()('@/src/lib/phone-transport');
  return new PhoneTransport({ endpoints: ['https://remote.invalid', 'http://192.168.1.2'], localEndpoints: ['http://192.168.1.2'],
    instanceId: 'paired-instance', principalId: 'owner', native: false, credentials: { accessToken: 'synthetic-access', refreshToken: 'synthetic-refresh' },
    persistRefresh: async () => {}, onEndpoint() {}, onClock() {}, ...options });
}

test('endpoint identity: reassigned fallback gets no bearer, cookies or refresh token', async () => {
  const old = global.fetch, seen = []; const transport = create();
  global.fetch = async (url, init) => {
    seen.push({ url, headers: new Headers(init.headers), body: init.body, credentials: init.credentials, redirect: init.redirect });
    if (url.startsWith('https://remote')) throw new Error('remote offline');
    if (url.endsWith('/instance')) return Response.json({ instanceId: 'different-instance' });
    return Response.json({ stolen: true });
  };
  try {
    await assert.rejects(transport.authorizedFetch('/api/client/conversations'));
    assert.ok(seen.length > 0);
    assert.ok(seen.every(row => row.url.endsWith('/instance') && !row.headers.has('authorization') && !row.body && row.credentials === 'omit' && row.redirect === 'error'));
  } finally { transport.dispose(); global.fetch = old; }
});

test('body backpressure: delayed response bodies retain both permits, queued abort exits', async () => {
  const old = global.fetch, pending = []; const transport = create({ endpoints: ['https://remote.invalid'] });
  global.fetch = async (url) => {
    if (url.endsWith('/instance')) return Response.json({ instanceId: 'paired-instance' });
    return new Response(new ReadableStream({ start(controller) { pending.push(controller); } }));
  };
  try {
    const a = await transport.authorizedFetch('/a'), b = await transport.authorizedFetch('/b');
    const abort = new AbortController();
    const rejected = assert.rejects(transport.authorizedFetch('/cancel-queued', { signal: abort.signal }), { name: 'AbortError' });
    abort.abort(); await rejected;
    let arrived = false;
    const c = transport.authorizedFetch('/c').then(value => { arrived = true; return value; });
    await tick(); assert.equal(pending.length, 2); assert.equal(arrived, false);
    pending[0].enqueue(new TextEncoder().encode('{"ok":true}')); pending[0].close();
    assert.deepEqual(await a.json(), { ok: true });
    const third = await c; assert.equal(pending.length, 3);
    await b.body.cancel(); await third.body.cancel();
    assert.equal(transport.activeReads, 0); assert.equal(transport.controllers.size, 0);
  } finally { transport.dispose(); global.fetch = old; }
});

test('LAN recovery: foreground singleflight validates instance and connection owner before selecting local', async () => {
  const old = global.fetch, seen = [], endpoints = []; let localIdentity = 'wrong', returnedOwner = 'owner';
  const transport = create({ onEndpoint: value => endpoints.push(value) });
  global.fetch = async (url, init) => {
    seen.push({ url, authorized: new Headers(init.headers).has('authorization') });
    if (url.endsWith('/instance')) { await tick(); return Response.json({ instanceId: localIdentity }); }
    return Response.json({ user: { id: returnedOwner }, linkManifest: { instanceId: 'paired-instance' } });
  };
  try {
    transport.setForeground(true);
    await Promise.all(Array.from({ length: 8 }, () => transport.recoverLocalEndpoint()));
    assert.equal(endpoints.length, 0); assert.equal(seen.filter(row => row.authorized).length, 0);
    assert.equal(seen.filter(row => row.url.endsWith('/instance')).length, 1);
    localIdentity = 'paired-instance'; returnedOwner = 'other-owner';
    await transport.recoverLocalEndpoint(); assert.deepEqual(endpoints, []);
    returnedOwner = 'owner'; await transport.recoverLocalEndpoint();
    assert.deepEqual(endpoints, ['http://192.168.1.2']);
    assert.ok(seen.at(-1).url.endsWith('/connection') && seen.at(-1).authorized);
    transport.setForeground(false); const count = seen.length;
    await transport.recoverLocalEndpoint(); assert.equal(seen.length, count);
  } finally { transport.dispose(); global.fetch = old; }
});

test('endpoint identity: refresh rechecks the exact 401 endpoint and shared probes never cross profiles', async () => {
  const old = global.fetch, seen = []; let identity = 'paired-instance';
  const a = create(), b = create({ instanceId: 'B-instance' });
  global.fetch = async (url, init) => {
    seen.push({ url, token: new Headers(init.headers).get('authorization'), body: init.body });
    if (url.endsWith('/instance')) { await tick(); return Response.json({ instanceId: identity }); }
    identity = 'reassigned';
    return new Response('', { status: 401, headers: { 'X-V8-Auth-Stage': 'pre_execution' } });
  };
  try {
    const results = await Promise.allSettled([a.authorizedFetch('/write', { method: 'POST' }), b.authorizedFetch('/write', { method: 'POST' })]);
    assert.ok(results.every(result => result.status === 'rejected'));
    assert.equal(seen.filter(row => row.url.endsWith('/write')).length, 1);
    assert.equal(seen.filter(row => row.url.endsWith('/auth/refresh')).length, 0);
    assert.equal(seen.filter(row => row.url.endsWith('/instance')).length, 3);
  } finally { a.dispose(); b.dispose(); global.fetch = old; }
});

test('endpoint identity: redirects in actual HTTP credential requests cannot reach an unchecked destination', async () => {
  const http = require('node:http');
  let destinationRequests = 0;
  const server = http.createServer((req, res) => {
    if (req.url.endsWith('/instance')) { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify({ instanceId: 'paired-instance' })); }
    else if (req.url === '/redirect') { res.writeHead(307, { Location: '/wrong-destination' }); res.end(); }
    else { destinationRequests++; res.end('must never arrive'); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const endpoint = `http://127.0.0.1:${server.address().port}`;
  const transport = create({ endpoints: [endpoint] });
  try {
    await assert.rejects(transport.authorizedFetch('/redirect', { method: 'POST', body: 'synthetic-secret' }));
    assert.equal(destinationRequests, 0);
  } finally { transport.dispose(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
});

test('body backpressure: reader EOF, cancel, decode error and timeout release permits without turning errors into success', async () => {
  const old = global.fetch, oldTimer = global.setTimeout;
  const transport = create({ endpoints: ['https://remote.invalid'] });
  global.fetch = async url => {
    if (url.endsWith('/instance')) return Response.json({ instanceId: 'paired-instance' });
    if (url.endsWith('/invalid-json')) return new Response('{broken');
    if (url.endsWith('/timeout')) return new Response(new ReadableStream({ start() {} }));
    return new Response('text');
  };
  try {
    const response = await transport.authorizedFetch('/eof'), reader = response.body.getReader();
    assert.equal((await reader.read()).done, false); assert.equal(transport.activeReads, 1);
    assert.equal((await reader.read()).done, true); reader.releaseLock(); assert.equal(transport.activeReads, 0);
    const cancel = await transport.authorizedFetch('/cancel'), cr = cancel.body.getReader();
    await cr.cancel(); cr.releaseLock(); assert.equal(transport.activeReads, 0);
    const invalid = await transport.authorizedFetch('/invalid-json');
    await assert.rejects(invalid.json(), SyntaxError); assert.equal(transport.activeReads, 0);
    global.setTimeout = (fn, ms, ...args) => oldTimer(fn, Math.min(ms, 30), ...args);
    const timeout = await transport.authorizedFetch('/timeout');
    await assert.rejects(timeout.text(), { name: 'AbortError' });
    assert.equal(transport.activeReads, 0); assert.equal(transport.controllers.size, 0);
  } finally { transport.dispose(); global.fetch = old; global.setTimeout = oldTimer; }
});

test('LAN recovery: late result after background/dispose cannot switch route and no timer starts inactive work', async () => {
  const old = global.fetch, oldTimer = global.setTimeout, timers = [];
  let resolveInstance; const endpoints = [], transport = create({ onEndpoint: value => endpoints.push(value) });
  global.setTimeout = (fn, ms, ...args) => { timers.push(ms); return oldTimer(fn, ms, ...args); };
  global.fetch = () => new Promise(resolve => { resolveInstance = resolve; });
  try {
    transport.setForeground(true); const pending = transport.recoverLocalEndpoint(); await tick();
    transport.setForeground(false); resolveInstance(Response.json({ instanceId: 'paired-instance' }));
    assert.equal(await pending, false); assert.deepEqual(endpoints, []);
    assert.ok(!timers.includes(60_000)); assert.equal(transport.activeReads, 0); assert.equal(transport.controllers.size, 0);
  } finally { transport.dispose(); global.fetch = old; global.setTimeout = oldTimer; }
});

test('offline cached identity: actual Provider restores user and draft before failing verification and never signs out', async () => {
  const state = [], refs = [], scheduled = [], effectDeps = [], cleanups = [];
  let stateIndex, refIndex, effectIndex;
  const equal = (a, b) => a && b && a.length === b.length && a.every((value, index) => value === b[index]);
  const react = { createContext: () => ({ Provider: 'provider' }),
    useState(initial) { const i = stateIndex++; if (!(i in state)) state[i] = initial; return [state[i], next => { state[i] = typeof next === 'function' ? next(state[i]) : next; }]; },
    useRef(initial) { const i = refIndex++; return refs[i] ||= { current: initial }; },
    useCallback: fn => fn, useMemo: fn => fn(),
    useEffect(fn, deps) { const i = effectIndex++; if (!equal(effectDeps[i], deps)) { effectDeps[i] = deps; scheduled.push(() => { cleanups[i]?.(); cleanups[i] = fn(); }); } },
  };
  const profile = { id: 'saved-A', instanceId: 'paired-instance', credentialRef: 'synthetic-slot', adminBaseUrl: 'https://offline.invalid',
    user: { id: 'owner', name: 'Cached Owner', role: 'ADMIN' } };
  let cleared = false; const oldFetch = global.fetch;
  global.fetch = async () => { throw new Error('offline'); };
  const load = loadProduction({ react,
    'react/jsx-runtime': { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) },
    'react-native': { AppState: { currentState: 'active', addEventListener: () => ({ remove() {} }) }, Platform: { OS: 'web' }, View: 'View', Text: 'Text', Pressable: 'Pressable' },
    '@/src/lib/admin-connection-profiles': { readAdminConnectionProfiles: async () => [profile], readActiveAdminConnectionProfileId: async () => profile.id,
      readProfileCredentials: async () => ({ accessToken: 'synthetic', refreshToken: 'synthetic' }), orderAdminBaseUrlCandidates: value => value.primary ? [value.primary] : [],
      commitActiveAdminConnectionProfile: async (_id, _ref, publish) => publish(), updateAdminConnectionProfiles: async fn => fn([profile]) },
    '@/src/lib/mobile-storage': { readMetadata: async () => JSON.stringify({ conversationId: 'saved-session', draftId: 'saved-draft' }), clearSessionStorage: async () => { cleared = true; } },
    '@/src/lib/profile-avatar-cache': {}, '@/src/lib/profile-background-cache': {}, '@/src/lib/phone-api': {},
    '@/src/lib/phone-drafts': { phoneDrafts: { flushAll: async () => {}, evictSavedInactive() {} } },
  });
  const { AppSessionProvider } = load('@/src/providers/app-session');
  const render = () => { stateIndex = refIndex = effectIndex = 0; const tree = AppSessionProvider({ children: null }); for (const effect of scheduled.splice(0)) effect(); return tree.props.value; };
  try {
    render(); await tick(); const cached = render();
    assert.equal(cached.status, 'authenticated'); assert.equal(cached.user.name, 'Cached Owner'); assert.equal(cached.activeConversationId, 'saved-session');
    await tick(); await tick(); const offline = render();
    assert.equal(offline.status, 'authenticated'); assert.equal(offline.user.id, 'owner'); assert.equal(offline.newDraftId, 'saved-draft');
    assert.ok(offline.connectionError); assert.equal(cleared, false);
  } finally { cleanups.forEach(cleanup => cleanup?.()); global.fetch = oldFetch; }
});

test('process surface: actual poll preserves stale/failure state, rejects late A and suppresses hidden demand', async () => {
  const file = path.join(root, 'src/screens/ChatScreen.tsx');
  const source = ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let pollEffect, applyCallback;
  function visit(node) {
    if (ts.isCallExpression(node) && node.expression.getText(source) === 'useEffect' && node.arguments[0]?.getText(source).includes('let polling = false')) pollEffect = node.arguments[0].getText(source);
    if (ts.isVariableDeclaration(node) && node.name.getText(source) === 'applySessionProcessSurface') applyCallback = node.initializer.arguments[0].getText(source);
    ts.forEachChild(node, visit);
  }
  visit(source); assert.ok(pollEffect && applyCallback);
  let processes = [{ processId: 'keep-A' }], resolve, reject, calls = 0, timer;
  const context = vm.createContext({ activeConversationId: 'A', activeConversationIdRef: { current: 'A' }, conversationTransitionTokenRef: { current: 1 },
    isFocused: true, appVisible: true, AbortController, Promise, runtimePanelOpen: true, runtimeRef: { current: { status: 'running' } },
    processesRef: { current: processes }, lastProcessSurfaceAtRef: { current: 0 }, isQueueEligibleRunStatus: () => true, authorizedFetch() {},
    setProcesses: fn => { processes = fn(processes); },
    getSessionProcesses: () => { calls++; return new Promise((yes, no) => { resolve = yes; reject = no; }); },
    setInterval: fn => { timer = fn; return 1; }, clearInterval() {},
  });
  vm.runInContext(ts.transpileModule(`this.applySessionProcessSurface=${applyCallback}; this.effect=${pollEffect};`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText, context);
  const cleanup = context.effect(); await tick(); reject(new Error('initial network failure')); await tick();
  assert.equal(processes[0].processId, 'keep-A');
  timer(); resolve({ processes: [], stale: true }); await tick(); assert.equal(processes[0].processId, 'keep-A');
  timer(); cleanup(); context.activeConversationIdRef.current = 'B'; context.conversationTransitionTokenRef.current++;
  processes = [{ processId: 'keep-B' }]; context.processesRef.current = processes;
  resolve({ processes: [], stale: false }); await tick(); assert.equal(processes[0].processId, 'keep-B');
  const count = calls; context.isFocused = false; assert.equal(context.effect(), undefined); await tick(); assert.equal(calls, count);
});

test('endpoint identity: probe overhead is one public read for each concurrent pair and repeats after completion', async () => {
  const old = global.fetch; let probes = 0, business = 0;
  const transport = create({ endpoints: ['https://remote.invalid'] });
  global.fetch = async url => {
    if (url.endsWith('/instance')) { probes++; await tick(); return Response.json({ instanceId: 'paired-instance' }); }
    business++; return Response.json({ ok: true });
  };
  try {
    for (let wave = 0; wave < 5; wave++) {
      await Promise.all([transport.authorizedFetch('/one').then(r => r.json()), transport.authorizedFetch('/two').then(r => r.json())]);
    }
    assert.equal(probes, 5); assert.equal(business, 10);
  } finally { transport.dispose(); global.fetch = old; }
});

test('finite native response: TTS errors consume one body without unsupported clone', async () => {
  const file = path.join(root, 'src/lib/phone-api.ts'), text = fs.readFileSync(file, 'utf8');
  const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const fn = source.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'requestTextToSpeech');
  const context = vm.createContext({ parseTextSafe: response => response.text(), translateCurrent: key => key });
  vm.runInContext(ts.transpileModule(fn.getText(source).replace('export ', ''), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText, context);
  let reads = 0;
  await assert.rejects(context.requestTextToSpeech(async () => ({ ok: false, clone() { throw new Error('clone unsupported'); },
    text: async () => { reads++; return '{"detail":"real native TTS failure"}'; } }), {}), /real native TTS failure/);
  assert.equal(reads, 1);
});

test('finite native response: active Expo body getter stays untouched before text/json consumption', async () => {
  const old = global.fetch; let bodyAccesses = 0;
  const transport = create({ endpoints: ['https://remote.invalid'], native: true });
  global.fetch = async url => ({ ok: true, status: 200, headers: new Headers(),
    get body() { bodyAccesses++; return new ReadableStream(); },
    json: async () => url.endsWith('/instance') ? { instanceId: 'paired-instance' } : { content: 'native json intact' },
  });
  try {
    assert.deepEqual(await (await transport.authorizedFetch('/read')).json(), { content: 'native json intact' });
    assert.equal(bodyAccesses, 0); assert.equal(transport.activeReads, 0);
  } finally { transport.dispose(); global.fetch = old; }
});

test('unmarked 401 mutation stays unknown without refreshing or replaying the side effect', async () => {
  const old = global.fetch; let writes = 0, refreshes = 0;
  const transport = create({ endpoints: ['https://remote.invalid'] });
  global.fetch = async url => {
    if (url.endsWith('/instance')) return Response.json({ instanceId: 'paired-instance' });
    if (url.endsWith('/auth/refresh')) { refreshes++; return Response.json({ accessToken: 'new-access', refreshToken: 'new-refresh', user: { id: 'owner' } }); }
    writes++; return new Response('', { status: 401 });
  };
  try {
    await assert.rejects(transport.authorizedFetch('/api/client/approve', { method: 'POST', body: JSON.stringify({ approvalId: 'approval-1' }) }), error => error.status === 401 && error.acceptanceUnknown === true);
    assert.equal(writes, 1); assert.equal(refreshes, 0);
  } finally { transport.dispose(); global.fetch = old; }
});

// Load the real Admin auth, token signing/expiry/rotation, proxy and routes.
// Only storage/users/config/Next response scaffolding and Engine execution are
// controlled. No user configuration, token store, server or provider is opened.
function adminBoundary() {
  const adminRoot = path.resolve(root, '../v8-agent-os-admin/src');
  const modules = new Map(), store = new Map();
  const user = { id: 'owner', email: 'owner@fixture.invalid', login: 'owner', role: 'ADMIN' };
  class NextResponse extends Response {
    static json(value, init) { return new NextResponse(JSON.stringify(value), { ...init, headers: { 'Content-Type': 'application/json', ...init?.headers } }); }
  }
  const mocks = {
    'next/server': { NextRequest: Request, NextResponse },
    '@/lib/auth': { auth: async () => null },
    '@/lib/service-auth': { verifyServiceAuth: async () => null },
    '@/lib/password': { verifyPassword: async () => false },
    '@/lib/storage': { readJson: (key, fallback) => structuredClone(store.get(key) ?? fallback), writeJson: (key, value) => store.set(key, structuredClone(value)) },
    '@/lib/users': { PERSONAL_OWNER_MODE: true, findUserById: id => id === user.id ? user : null,
      findUserByIdentifier: id => id === user.email || id === user.login ? user : null, getSessionIdentifier: value => value.email },
    '@/lib/server/runtime-config': { resolveEngineBaseUrl: () => 'http://engine.invalid', resolveEngineOrigin: () => 'http://engine.invalid', resolveAdminApiBaseUrl: () => 'http://admin.invalid/api',
      resolveClientSurfaceOriginFromRequest: () => '', resolveInternalSecret: () => 'synthetic-internal-fixture' },
    '@/lib/server/engine-identity': (() => {
      class EngineIdentityError extends Error { constructor(code, status) { super(code); this.code = code; this.status = status; } }
      const pair = (accessToken, refreshToken) => ({ accessToken, accessTokenExpiresAt: new Date(Date.now() + 60_000).toISOString(), refreshToken, refreshTokenExpiresAt: new Date(Date.now() + 86_400_000).toISOString(), user, deviceId: 'synthetic-device' });
      return {
        EngineIdentityError,
        engineClientIdentity: async (path, init = {}) => {
          if (path === '/auth/me') {
            const token = String(new Headers(init.headers).get('authorization') || '').replace(/^Bearer\s+/i, '');
            if (token === 'expired-access') throw new EngineIdentityError('invalid_token', 401);
            if (token !== 'valid-access' && token !== 'new-access') throw new EngineIdentityError('invalid_token', 401);
            return { user };
          }
          if (path === '/auth/refresh') return pair('new-access', 'new-refresh');
          if (path === '/auth/logout') return { revoked: true };
          throw new EngineIdentityError('not_found', 404);
        },
        engineIdentity: async (path) => {
          if (path === '/devices') return { devices: [] };
          throw new EngineIdentityError('not_found', 404);
        },
      };
    })(),
    '@/lib/server/client-perf-metrics': { jsonSizeBytes: () => 0, readEngineElapsedMs: () => 0, recordAdminApiMetric() {} },
  };
  function load(name, from = adminRoot) {
    if (Object.hasOwn(mocks, name)) return mocks[name];
    if (!name.startsWith('@/') && !name.startsWith('.')) return require(name);
    const file = (name.startsWith('@/') ? path.join(adminRoot, name.slice(2)) : path.resolve(from, name)) + '.ts';
    if (modules.has(file)) return modules.get(file);
    const module = { exports: {} };
    const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } }).outputText;
    vm.runInThisContext(`(function(require,module,exports,console){${code}\n})`)((next) => load(next, path.dirname(file)), module, module.exports, { error() {}, warn() {} });
    modules.set(file, module.exports); return module.exports;
  }
  const mobile = load('@/lib/mobile-auth');
  return {
    load,
    expired: { accessToken: 'expired-access', refreshToken: 'expired-refresh', accessTokenExpiresAt: new Date(Date.now() - 60_000).toISOString(), refreshTokenExpiresAt: new Date(Date.now() + 86_400_000).toISOString(), user, deviceId: 'synthetic-device' },
    valid: { accessToken: 'valid-access', refreshToken: 'valid-refresh', accessTokenExpiresAt: new Date(Date.now() + 60_000).toISOString(), refreshTokenExpiresAt: new Date(Date.now() + 86_400_000).toISOString(), user, deviceId: 'synthetic-valid' },
    user, mobile, NextResponse,
  };
}

test('real BFF auth rejects before Engine and expired-token send plus approval recover with one refresh', async () => {
  const old = global.fetch, boundary = adminBoundary(), attempts = [], executed = [];
  const chat = boundary.load('@/app/api/client/chat-submit/route');
  const approval = boundary.load('@/app/api/client/approvals/[id]/approve/route');
  const refresh = boundary.load('@/app/api/client/auth/refresh/route');
  const context = { params: Promise.resolve({ id: 'approval-1' }) };
  const chatBody = JSON.stringify({ clientMessageId: 'intent-1', messages: [{ role: 'user', content: 'continue once' }], data: { conversationId: 'session-A', clientMessageId: 'intent-1' } });
  const approvalBody = JSON.stringify({ response: { answer: 'yes', approved: true } });
  let refreshes = 0, persisted = 0;
  const transport = create({ endpoints: ['https://remote.invalid'], credentials: boundary.expired, persistRefresh: async () => { persisted++; } });
  global.fetch = async (url, init) => {
    if (url.endsWith('/instance')) return Response.json({ instanceId: 'paired-instance' });
    if (url.startsWith('http://engine.invalid')) { executed.push({ url, body: JSON.parse(init.body) }); return Response.json({ accepted: true, ok: true }); }
    const request = new Request(url, init);
    if (url.endsWith('/auth/refresh')) { refreshes++; await tick(); return refresh.POST(request); }
    attempts.push({ url, body: init.body });
    return url.endsWith('/chat-submit') ? chat.POST(request) : approval.POST(request, context);
  };
  try {
    for (const [route, routeUrl] of [[chat, '/api/client/chat-submit'], [approval, '/api/client/approvals/approval-1/approve']]) {
      const request = new Request(`https://remote.invalid${routeUrl}`, { method: 'POST', headers: { Authorization: `Bearer ${boundary.expired.accessToken}` } });
      request.json = () => assert.fail('expired auth must reject before reading action body');
      const rejected = await route.POST(request, context);
      assert.equal(rejected.status, 401); assert.equal(rejected.headers.get('x-v8-auth-stage'), 'pre_execution');
      assert.equal((await rejected.json()).code, 'auth_pre_execution'); assert.equal(executed.length, 0);
    }
    const results = await Promise.all([
      transport.authorizedFetch('/api/client/chat-submit', { method: 'POST', body: chatBody }).then(r => r.json()),
      transport.authorizedFetch('/api/client/approvals/approval-1/approve', { method: 'POST', body: approvalBody }).then(r => r.json()),
    ]);
    assert.ok(results.every(result => result.accepted)); assert.equal(refreshes, 1); assert.equal(persisted, 1);
    assert.equal(attempts.length, 4); assert.equal(executed.length, 2);
    assert.equal(executed.filter(row => row.url.endsWith('/chat/submit')).length, 1);
    assert.equal(executed.filter(row => row.url.endsWith('/approvals/approval-1/approve')).length, 1);
    const forwardedChat = executed.find(row => row.url.endsWith('/chat/submit')).body;
    assert.equal(forwardedChat.clientMessageId, 'intent-1');
    assert.equal(forwardedChat.data.clientMessageId, 'intent-1');
    assert.equal(forwardedChat.session_id, 'session-A');
    for (const item of attempts) assert.equal(item.body, item.url.endsWith('/chat-submit') ? chatBody : approvalBody);
  } finally { transport.dispose(); global.fetch = old; }
});

test('real BFF forwarding does not label downstream 401 or lost responses as pre-execution; no write replay', async () => {
  const old = global.fetch, boundary = adminBoundary();
  const credentials = boundary.valid;
  let executed = 0, refreshes = 0, mode = '401';
  const chat = boundary.load('@/app/api/client/chat-submit/route');
  const approval = boundary.load('@/app/api/client/approvals/[id]/approve/route');
  const transport = create({ endpoints: ['https://remote.invalid'], credentials });
  global.fetch = async (url, init) => {
    if (url.endsWith('/instance')) return Response.json({ instanceId: 'paired-instance' });
    if (url.endsWith('/auth/refresh')) { refreshes++; assert.fail('unknown action outcome must not trigger token recovery'); }
    if (url.startsWith('http://engine.invalid')) {
      executed++;
      if (mode === 'lost') throw new Error('synthetic response loss after execution');
      // Downstream fault injection, NOT a claim that token expiry causes this.
      return Response.json({ error: 'downstream failure' }, { status: 401, headers: { 'X-V8-Auth-Stage': 'pre_execution' } });
    }
    const request = new Request(url, init);
    const response = url.endsWith('/chat-submit') ? await chat.POST(request) : await approval.POST(request, { params: Promise.resolve({ id: 'approval-1' }) });
    assert.equal(response.headers.get('x-v8-auth-stage'), null);
    return response;
  };
  try {
    for (const route of ['/api/client/chat-submit', '/api/client/approvals/approval-1/approve']) {
      await assert.rejects(transport.authorizedFetch(route, { method: 'POST', body: '{}' }), error => error.acceptanceUnknown === true);
    }
    assert.equal(executed, 2); assert.equal(refreshes, 0);
    mode = 'lost';
    const failed = await transport.authorizedFetch('/api/client/chat-submit', { method: 'POST', body: '{}' });
    assert.equal(failed.status, 500); await failed.text(); assert.equal(executed, 3);
  } finally { transport.dispose(); global.fetch = old; }
});

test('pre-execution retry stays bounded for repeated rejection; a retry becoming unknown is not replayed', async () => {
  const old = global.fetch;
  for (const markedRetry of [true, false]) {
    let requests = 0, refreshes = 0;
    const transport = create({ endpoints: ['https://remote.invalid'] });
    global.fetch = async url => {
      if (url.endsWith('/instance')) return Response.json({ instanceId: 'paired-instance' });
      if (url.endsWith('/auth/refresh')) { refreshes++; return Response.json({ accessToken: 'new', refreshToken: 'new-r', user: { id: 'owner' } }); }
      requests++; return Response.json({ error: 'denied' }, { status: 401,
        headers: requests === 1 || markedRetry ? { 'X-V8-Auth-Stage': 'pre_execution' } : {} });
    };
    try {
      const operation = transport.authorizedFetch('/write', { method: 'POST', body: '{}' });
      if (markedRetry) { const response = await operation; assert.equal(response.status, 401); await response.text(); }
      else await assert.rejects(operation, error => error.acceptanceUnknown === true);
      assert.equal(requests, 2); assert.equal(refreshes, 1);
    } finally { transport.dispose(); global.fetch = old; }
  }
});

test('read auth recovery retries exactly once and consumes no orphan response', async () => {
  const old = global.fetch; let reads = 0;
  const transport = create({ endpoints: ['https://remote.invalid'] });
  global.fetch = async url => {
    if (url.endsWith('/instance')) return Response.json({ instanceId: 'paired-instance' });
    if (url.endsWith('/auth/refresh')) return Response.json({ accessToken: 'new', refreshToken: 'new-r', user: { id: 'owner' } });
    return ++reads === 1 ? new Response('', { status: 401 }) : Response.json({ ok: true });
  };
  try {
    assert.deepEqual(await (await transport.authorizedFetch('/read')).json(), { ok: true });
    assert.equal(reads, 2); assert.equal(transport.activeReads, 0); assert.equal(transport.controllers.size, 0);
  } finally { transport.dispose(); global.fetch = old; }
});

test('refresh response loss retries the persisted rotation id and does not replay a write', async () => {
  const old = global.fetch, attempts = [], persisted = [], writes = [];
  const transport = create({ endpoints: ['https://remote.invalid'],
    persistRefreshAttempt: async (token, id) => persisted.push({ token, id }),
  });
  global.fetch = async (url, init) => {
    if (url.endsWith('/instance')) return Response.json({ instanceId: 'paired-instance' });
    if (url.endsWith('/auth/refresh')) {
      const payload = JSON.parse(init.body); attempts.push(payload);
      assert.equal(persisted.at(-1).id, payload.rotationId, 'attempt must be durable before dispatch');
      if (attempts.length === 1) throw new Error('response lost after rotation');
      return Response.json({ accessToken: 'new', refreshToken: 'new-r', user: { id: 'owner' } });
    }
    if (new Headers(init.headers).get('authorization') === 'Bearer new') { writes.push(init.body); return Response.json({ ok: true }); }
    return new Response('', { status: 401, headers: { 'X-V8-Auth-Stage': 'pre_execution' } });
  };
  try {
    await assert.rejects(transport.authorizedFetch('/write', { method: 'POST', body: 'intent-A' }), /response lost/);
    assert.deepEqual(writes, []);
    assert.deepEqual(await (await transport.authorizedFetch('/write', { method: 'POST', body: 'intent-A' })).json(), { ok: true });
    assert.equal(attempts.length, 2);
    assert.ok(attempts[0].rotationId);
    assert.deepEqual(attempts[0], attempts[1]);
    assert.deepEqual(writes, ['intent-A']);
  } finally { transport.dispose(); global.fetch = old; }
});

test('SecureStore failure retries the received pair without rotating again or publishing unsaved tokens', async () => {
  const old = global.fetch; let refreshes = 0, saves = 0;
  const transport = create({ endpoints: ['https://remote.invalid'], persistRefresh: async () => {
    if (++saves === 1) throw new Error('SecureStore unavailable');
  } });
  global.fetch = async (url, init) => {
    if (url.endsWith('/instance')) return Response.json({ instanceId: 'paired-instance' });
    if (url.endsWith('/auth/refresh')) {
      refreshes++; return Response.json({ accessToken: 'new', refreshToken: 'new-r', user: { id: 'owner' } });
    }
    return new Headers(init.headers).get('authorization') === 'Bearer new'
      ? Response.json({ ok: true }) : new Response('', { status: 401 });
  };
  try {
    await assert.rejects(transport.authorizedFetch('/read'), /SecureStore/);
    assert.equal(transport.credentials.accessToken, 'synthetic-access');
    assert.deepEqual(await (await transport.authorizedFetch('/read')).json(), { ok: true });
    assert.equal(refreshes, 1); assert.equal(saves, 2);
    assert.equal(transport.refreshRotationId, undefined);
  } finally { transport.dispose(); global.fetch = old; }
});

test('cancelling the first view leaves the shared refresh available to another view', async () => {
  const old = global.fetch; let releaseRefresh, refreshStarted;
  const started = new Promise(resolve => { refreshStarted = resolve; });
  const transport = create({ endpoints: ['https://remote.invalid'] });
  global.fetch = async (url, init) => {
    if (url.endsWith('/instance')) return Response.json({ instanceId: 'paired-instance' });
    if (url.endsWith('/auth/refresh')) {
      refreshStarted(); return new Promise(resolve => { releaseRefresh = () => resolve(Response.json({ accessToken: 'new', refreshToken: 'new-r', user: { id: 'owner' } })); });
    }
    return new Headers(init.headers).get('authorization') === 'Bearer new'
      ? Response.json({ ok: true }) : new Response('', { status: 401 });
  };
  try {
    const cancel = new AbortController();
    const first = assert.rejects(transport.authorizedFetch('/read-A', { signal: cancel.signal }), { name: 'AbortError' });
    await started;
    const second = transport.authorizedFetch('/read-B');
    await tick(); cancel.abort(); await first;
    releaseRefresh();
    assert.deepEqual(await (await second).json(), { ok: true });
    assert.equal(transport.controllers.size, 0);
  } finally { transport.dispose(); global.fetch = old; }
});

test('approval resolution owner deduplicates a double tap and drops UI cleanup after session switch', async () => {
  const file = path.join(root, 'src/screens/ChatScreen.tsx');
  const source = ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let arrow;
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(source) === 'handleApprovalResolve') {
      arrow = node.initializer.arguments?.[0]?.getText(source);
    }
    ts.forEachChild(node, visit);
  }
  visit(source); assert.ok(arrow);
  const effects = []; let resolve;
  const context = vm.createContext({
    useCallback: fn => fn, activeConversationIdRef: { current: 'A' }, conversationTransitionTokenRef: { current: 1 },
    approvalResolutionInFlightRef: { current: new Set() },
    isSpecStageApproval: approval => approval.approval_kind === 'spec_stage_approval',
    setAskUserInteractions: fn => effects.push(['ask', fn]), setApprovals: fn => effects.push(['approval', fn]),
    authorizedFetch: {},
    respondAskUser: async () => {}, approvePendingItem: async () => { await new Promise(done => { resolve = done; }); effects.push('side-effect'); },
    recentlyResolvedApprovalIdsRef: { current: new Set() }, setTimeout, String, Boolean, Promise,
  });
  vm.runInContext(ts.transpileModule(`this.resolveApproval = ${arrow};`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText, context);
  const approval = { id: 'approval-1', approval_id: 'approval-1' };
  const first = context.resolveApproval(approval, 'yes', true);
  const second = context.resolveApproval(approval, 'yes', true);
  assert.equal(context.approvalResolutionInFlightRef.current.size, 1);
  context.activeConversationIdRef.current = 'B'; context.conversationTransitionTokenRef.current = 2;
  resolve(); await Promise.all([first, second]);
  assert.deepEqual(effects.filter(value => value === 'side-effect'), ['side-effect']);
  assert.equal(effects.filter(value => Array.isArray(value) && value[0] === 'approval').length, 0);
});
