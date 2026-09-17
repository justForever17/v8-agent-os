const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

// Run the actual Provider callbacks, directory transactions and PhoneTransport.
// Only hook scheduling, native storage and HTTP are in-memory boundaries. No
// Phone device, real credentials, persistent configuration or server is used.
async function harness() {
  const metadata = new Map(), secure = new Map(), states = [], refs = [], transports = [];
  let stateIndex = 0, refIndex = 0;
  const react = {
    createContext: () => ({ Provider: 'provider' }),
    useState(initial) { const index = stateIndex++; if (!(index in states)) states[index] = initial;
      return [states[index], next => { states[index] = typeof next === 'function' ? next(states[index]) : next; }]; },
    useRef(initial) { const index = refIndex++; return refs[index] ||= { current: initial }; },
    useCallback: fn => fn, useMemo: fn => fn(), useEffect() {},
  };
  const storage = {
    readMetadata: async key => metadata.get(key) ?? null,
    writeMetadata: async (key, value) => metadata.set(key, value),
    readSecureItem: async key => secure.get(key) ?? null,
    writeSecureItem: async (key, value) => secure.set(key, value),
    deleteSecureItem: async key => secure.delete(key),
    getStoredValue: async key => metadata.get('v8.phone.' + key) ?? null,
    clearSessionStorage: async () => {},
  };
  const server = { required: { A: 0, B: 0 }, refreshes: { A: 0, B: 0 }, accepted: [], refreshUser: 'owner', gate: null, started: null };
  const fetch = async (url, init) => {
    const parsed = new URL(url), id = parsed.hostname[0].toUpperCase();
    if (parsed.pathname.endsWith('/instance')) return Response.json({ instanceId: 'instance-' + id });
    if (parsed.pathname.endsWith('/auth/refresh')) {
      const version = ++server.refreshes[id];
      server.started?.resolve();
      if (server.gate) await server.gate.promise;
      return Response.json({ accessToken: `synthetic-${id}-a${version}`, refreshToken: `synthetic-${id}-r${version}`, user: { id: server.refreshUser } });
    }
    const authorized = new Headers(init.headers).get('Authorization') === `Bearer synthetic-${id}-a${server.required[id]}`;
    if (authorized) server.accepted.push({ id, path: parsed.pathname, version: server.required[id] });
    return authorized ? Response.json({ accepted: true, instance: id }) : Response.json({ error: 'expired' }, { status: 401 });
  };
  const mocks = {
    react, 'react/jsx-runtime': { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) },
    'react-native': { AppState: { currentState: 'active', addEventListener: () => ({ remove() {} }) }, Platform: { OS: 'android' }, View: 'View', Text: 'Text', Pressable: 'Pressable' },
    'expo/fetch': { fetch }, '@/src/lib/mobile-storage': storage, '@/src/lib/locale': {},
    '@/src/lib/profile-avatar-cache': {}, '@/src/lib/profile-background-cache': {}, '@/src/lib/phone-api': {},
    '@/src/lib/phone-drafts': { phoneDrafts: { flushAll: async () => {}, evictSavedInactive() {} } },
    '@/src/lib/device-executor': { deviceExecutor: {} },
  };
  const cache = new Map();
  function load(name) {
    if (Object.hasOwn(mocks, name)) return mocks[name];
    if (!name.startsWith('@/')) return require(name);
    if (cache.has(name)) return cache.get(name);
    const base = path.join(root, name.slice(2));
    const file = base + (fs.existsSync(base + '.ts') ? '.ts' : '.tsx');
    const module = { exports: {} };
    const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
    } }).outputText;
    vm.runInThisContext(`(function(require,module,exports){${code}\n})`, { filename: file })(load, module, module.exports);
    cache.set(name, module.exports); return module.exports;
  }
  const transport = load('@/src/lib/phone-transport');
  mocks['@/src/lib/phone-transport'] = { ...transport, PhoneTransport: class extends transport.PhoneTransport {
    constructor(options) { super(options); transports.push({ transport: this, options }); }
  } };
  const profiles = load('@/src/lib/admin-connection-profiles');
  await profiles.updateAdminConnectionProfiles(() => ['A', 'B'].map(id => ({
    id, label: id, instanceId: 'instance-' + id, adminBaseUrl: `https://${id.toLowerCase()}.invalid`, lastUsedAt: '1',
    accessToken: `synthetic-${id}-a0`, refreshToken: `synthetic-${id}-r0`, user: { id: 'owner' },
  })));
  const { AppSessionProvider } = load('@/src/providers/app-session');
  const session = () => { stateIndex = refIndex = 0; return AppSessionProvider({ children: null }).props.value; };
  await session().activateProfile('A');
  return { profiles, session, server, transports, storage,
    profile: async id => (await profiles.readAdminConnectionProfiles()).find(item => item.id === id),
    read: async path => (await session().authorizedFetch(path)).json(),
    close: () => { server.gate?.resolve(); transports.forEach(item => item.transport.dispose()); },
  };
}

test('same active session refreshes twice and authenticates a subsequent request with its published slot', async t => {
  const h = await harness(); t.after(h.close);
  const original = (await h.profile('A')).credentialRef;
  h.server.required.A = 1;
  assert.deepEqual(await h.read('/first'), { accepted: true, instance: 'A' });
  const first = (await h.profile('A')).credentialRef;
  assert.notEqual(first, original);
  h.server.required.A = 2;
  assert.deepEqual(await h.read('/second'), { accepted: true, instance: 'A' });
  const second = (await h.profile('A')).credentialRef;
  assert.notEqual(second, first);
  assert.deepEqual(await h.read('/subsequent'), { accepted: true, instance: 'A' });
  assert.equal(h.server.refreshes.A, 2);
  assert.deepEqual(h.server.accepted.map(item => item.version), [1, 2, 2]);
  assert.equal(h.session().activeProfileId, 'A');
  assert.equal(h.transports.length, 1);
});

test('external slot replacement cannot be adopted by an old transport before refresh', async t => {
  const h = await harness(); t.after(h.close);
  await h.profiles.updateAdminConnectionProfiles(current => current.map(item => item.id === 'A'
    ? { ...item, accessToken: 'external-access', refreshToken: 'external-refresh' } : item));
  const replacement = await h.profile('A');
  h.server.required.A = 1;
  await assert.rejects(h.read('/old-session'), /Connection changed while refreshing/);
  assert.equal(h.server.refreshes.A, 0);
  assert.equal((await h.profile('A')).credentialRef, replacement.credentialRef);
});

test('external slot replacement during refresh is preserved instead of overwritten by its late result', async t => {
  const h = await harness(); t.after(h.close);
  h.server.required.A = 1; h.server.gate = deferred(); h.server.started = deferred();
  const pending = h.read('/old-session');
  const rejected = assert.rejects(pending, { name: 'AbortError' });
  await h.server.started.promise;
  await h.profiles.updateAdminConnectionProfiles(current => current.map(item => item.id === 'A'
    ? { ...item, accessToken: 'external-access', refreshToken: 'external-refresh' } : item));
  const replacement = await h.profile('A');
  h.server.gate.resolve(); await rejected;
  assert.equal((await h.profile('A')).credentialRef, replacement.credentialRef);
  assert.equal((await h.profiles.readProfileCredentials(replacement)).refreshToken, 'external-refresh');
  assert.equal(h.server.accepted.length, 0);
});

test('expected refresh token remains fenced even when the slot name is unchanged', async t => {
  const h = await harness(); t.after(h.close);
  const current = await h.profile('A');
  await h.storage.writeSecureItem(current.credentialRef, JSON.stringify({ accessToken: 'external-access', refreshToken: 'external-refresh' }));
  h.server.required.A = 1;
  await assert.rejects(h.read('/old-session'), /Connection credentials changed while refreshing/);
  assert.equal(h.server.refreshes.A, 0);
});

test('switching during refresh waits for persistence and fences all callbacks from the replaced transport', async t => {
  const h = await harness(); t.after(h.close);
  h.server.required.A = 1; h.server.gate = deferred(); h.server.started = deferred();
  const old = h.transports[0];
  const request = h.read('/A-before-switch').catch(error => { assert.equal(error.name, 'AbortError'); });
  await h.server.started.promise;
  let switched = false;
  const activation = h.session().activateProfile('B').then(() => { switched = true; });
  await tick(); assert.equal(switched, false); assert.equal(h.session().activeProfileId, 'A');
  h.server.gate.resolve(); await Promise.all([activation, request]);
  assert.equal(h.session().activeProfileId, 'B');
  const b = (await h.profile('B')).credentialRef;
  await assert.rejects(old.options.persistRefreshAttempt('synthetic-A-r1', 'late-attempt'), { name: 'AbortError' });
  await assert.rejects(old.options.persistRefresh({ accessToken: 'late', refreshToken: 'late-r' }, { id: 'owner' }), { name: 'AbortError' });
  await assert.rejects(old.transport.authorizedFetch('/late-A'), { name: 'AbortError' });
  assert.equal((await h.profile('B')).credentialRef, b);
  assert.deepEqual(await h.read('/B-after-switch'), { accepted: true, instance: 'B' });
});

test('a refresh response for another owner never publishes its credentials', async t => {
  const h = await harness(); t.after(h.close);
  const original = (await h.profile('A')).credentialRef;
  h.server.required.A = 1; h.server.refreshUser = 'other-owner';
  await assert.rejects(h.read('/wrong-owner'), /The paired account changed/);
  assert.equal((await h.profile('A')).credentialRef, original);
  assert.equal(h.session().user.id, 'owner');
  assert.equal(h.server.accepted.length, 0);
});

test('directory owner drift during refresh cannot be overwritten by a late same-slot result', async t => {
  const h = await harness(); t.after(h.close);
  h.server.required.A = 1; h.server.gate = deferred(); h.server.started = deferred();
  const rejected = assert.rejects(h.read('/owner-drift'), { name: 'AbortError' });
  await h.server.started.promise;
  await h.profiles.updateAdminConnectionProfiles(current => current.map(item => item.id === 'A'
    ? { ...item, user: { id: 'other-owner' }, principalId: 'other-owner' } : item));
  h.server.gate.resolve(); await rejected;
  assert.equal((await h.profile('A')).user.id, 'other-owner');
  assert.equal(h.server.accepted.length, 0);
});
