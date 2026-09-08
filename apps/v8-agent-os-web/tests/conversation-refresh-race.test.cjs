const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const source = fs.readFileSync(path.join(__dirname, '../src/context/ConversationContext.tsx'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
}}).outputText;

function mountContext(initialOwner = 'owner-a') {
  const slots = [];
  const requests = [];
  const cacheWrites = [];
  const errors = [];
  let cursor = 0;
  let effects = [];
  let dirty = false;
  let owner = initialOwner;
  let surface;
  const changed = (a, b) => !a || !b || a.length !== b.length || a.some((value, index) => !Object.is(value, b[index]));
  const react = {
    createContext: () => ({ Provider: 'provider' }),
    useCallback: (fn, deps) => react.useMemo(() => fn, deps),
    useMemo: (fn, deps) => {
      const index = cursor++;
      if (!slots[index] || changed(slots[index].deps, deps)) slots[index] = { value: fn(), deps };
      return slots[index].value;
    },
    useEffect: (fn, deps) => {
      const index = cursor++;
      if (!slots[index] || changed(slots[index].deps, deps)) {
        const previous = slots[index];
        slots[index] = { deps };
        effects.push(() => { previous?.cleanup?.(); slots[index].cleanup = fn(); });
      }
    },
    useRef: (value) => {
      const index = cursor++;
      slots[index] ??= { current: value };
      return slots[index];
    },
    useState: (value) => {
      const index = cursor++;
      slots[index] ??= { value };
      return [slots[index].value, (next) => {
        const result = typeof next === 'function' ? next(slots[index].value) : next;
        if (!Object.is(result, slots[index].value)) { slots[index].value = result; dirty = true; }
      }];
    },
  };
  const imports = (name) => {
    if (name === 'react') return react;
    if (name === 'react/jsx-runtime') return { jsx: (type, props) => ({ type, props }) };
    if (name === 'next-auth/react') return { useSession: () => ({
      status: owner ? 'authenticated' : 'unauthenticated', data: owner ? {user: {id: owner}} : null,
    }) };
    if (name === '@/lib/session-history') return {
      normalizeSessionHistoryList: (items) => items,
      preserveLiveSessionTitles: (_, items) => items,
    };
    if (name === '@/lib/web-conversation-cache') return {
      readWebSessionIndexCache: () => [], clearWebSessionIndexCache: () => {},
      writeWebSessionIndexCache: (key, items) => cacheWrites.push({ key, items: structuredClone(items) }),
    };
    throw new Error(`Unexpected import ${name}`);
  };
  const mod = { exports: {} };
  const fakeBrowser = { addEventListener() {}, removeEventListener() {}, setTimeout() {}, clearTimeout() {} };
  class EventSource { addEventListener() {} close() {} }
  // Let an aborted transport resolve too: the owner fence must reject its body.
  const fetch = (_, options) => new Promise((resolve, reject) => requests.push({resolve, reject, signal: options.signal}));
  new Function('require', 'module', 'exports', 'fetch', 'window', 'document', 'EventSource', 'console', compiled)(
    imports, mod, mod.exports, fetch, fakeBrowser, {...fakeBrowser, visibilityState: 'visible'}, EventSource,
    {error: (...args) => errors.push(args)},
  );
  function render(nextOwner = owner) {
    owner = nextOwner;
    for (let pass = 0; pass < 10; pass++) {
      cursor = 0; effects = []; dirty = false;
      surface = mod.exports.ConversationProvider({children: null}).props.value;
      effects.forEach(effect => effect());
      if (!dirty) return;
    }
    assert.fail('fixture effects failed to settle');
  }
  render();
  return {requests, cacheWrites, errors, render, get surface() {return surface;},
    settle: async () => { await new Promise(resolve => setImmediate(resolve)); render(); }};
}

function resolveRows(request, rows) { request.resolve({ok: true, json: async () => rows}); }

test('terminal activity arriving during a history fetch triggers one trailing reconciliation', async () => {
  const context = mountContext();
  const pending = context.surface.refreshConversations();
  context.surface.refreshConversations();
  assert.equal(context.requests.length, 1, 'concurrent invalidations must not fan out');
  resolveRows(context.requests[0], [{id: 'session', status: 'running'}]);
  await context.settle();
  assert.equal(context.requests.length, 2, 'completion activity must not be swallowed');
  resolveRows(context.requests[1], [{id: 'session', status: 'completed'}]);
  await pending;
  await context.settle();
  assert.equal(context.surface.conversations[0].status, 'completed');
  assert.equal(context.requests.length, 2);
});

test('a pending invalidation survives one failed fetch without an unbounded retry', async () => {
  const context = mountContext();
  const pending = context.surface.refreshConversations();
  context.requests[0].reject(new Error('offline'));
  await context.settle();
  assert.equal(context.requests.length, 2);
  context.surface.refreshConversations();
  context.requests[1].reject(new Error('still offline'));
  await pending;
  await context.settle();
  assert.equal(context.requests.length, 2, 'second failure must stop even if another invalidation arrived');
  assert.equal(context.errors.length, 2);
});

test('one failed request without an invalidation does not retry automatically', async () => {
  const context = mountContext();
  context.requests[0].reject(new Error('offline'));
  await context.settle();
  assert.equal(context.requests.length, 1);
});

test('owner switch aborts old fetch and ignores its late response without clearing the new request', async () => {
  const context = mountContext();
  context.render('owner-b');
  assert.equal(context.requests.length, 2);
  assert.equal(context.requests[0].signal.aborted, true);
  resolveRows(context.requests[0], [{id: 'owner-a-private'}]);
  await context.settle();
  assert.deepEqual(context.surface.conversations, []);
  const pending = context.surface.refreshConversations();
  assert.equal(context.requests.length, 2, 'late A finally must not clear the B in-flight slot');
  resolveRows(context.requests[1], [{id: 'owner-b-current'}]);
  await context.settle();
  assert.equal(context.requests.length, 3);
  resolveRows(context.requests[2], [{id: 'owner-b-current'}]);
  await pending;
  await context.settle();
  assert.equal(context.surface.conversations[0].id, 'owner-b-current');
  assert.ok(context.cacheWrites.filter(write => write.key === 'owner-b')
    .every(write => write.items.every(item => item.id !== 'owner-a-private')));
});

test('cached A list cannot be persisted under B during the owner-change effect commit', async () => {
  const context = mountContext();
  resolveRows(context.requests[0], [{id: 'owner-a-private'}]);
  await context.settle();
  assert.equal(context.surface.conversations[0].id, 'owner-a-private');
  context.render('owner-b');
  assert.deepEqual(context.surface.conversations, []);
  assert.ok(context.cacheWrites.filter(write => write.key === 'owner-b').every(write => write.items.length === 0));
  resolveRows(context.requests[1], [{id: 'owner-b-current'}]);
  await context.settle();
});

test('logout during JSON decode discards the old body and does not restore history', async () => {
  const context = mountContext();
  let finishJson;
  context.requests[0].resolve({ok: true, json: () => new Promise(resolve => {finishJson = resolve;})});
  await context.settle();
  context.render(null);
  assert.equal(context.requests[0].signal.aborted, true);
  finishJson([{id: 'owner-a-private'}]);
  await context.settle();
  assert.deepEqual(context.surface.conversations, []);
  assert.equal(context.surface.isLoading, false);
  assert.equal(context.requests.length, 1);
});
