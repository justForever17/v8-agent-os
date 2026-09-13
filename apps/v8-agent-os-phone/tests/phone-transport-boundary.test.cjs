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
    return new Response('', { status: 401 });
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
