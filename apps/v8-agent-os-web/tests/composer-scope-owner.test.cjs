const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

function fixture() {
  const filename = path.resolve(__dirname, '../src/app/chat/ChatClient.tsx');
  const source = ts.createSourceFile(filename, fs.readFileSync(filename, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const parts = {};
  function visit(node) {
    if (ts.isVariableDeclaration(node)) {
      const name = node.name.getText(source);
      if (name === 'loadSessionScope') parts.load = node.initializer.arguments[0].getText(source);
      if (name === 'draftWorkspace') parts.workspace = node.initializer.getText(source);
    }
    if (ts.isFunctionDeclaration(node) && node.name?.text === 'normalizeScopeBinding') parts.normalize = node.getText(source);
    ts.forEachChild(node, visit);
  }
  visit(source);
  assert.ok(parts.load && parts.workspace && parts.normalize, 'execute actual scope loader and draft ownership expression');
  const requests = [];
  const state = {
    activeConversationId: 'A', activeConversationIdRef: { current: 'A' },
    scopeOwner: '', scopeBinding: null, scopeCacheRef: { current: new Map() }, scopeRequestSeqRef: { current: 0 },
    setScopeOwner: owner => { state.scopeOwner = owner; },
    setScopeBinding: binding => { state.scopeBinding = binding; },
    error: '', setChatTransportError: value => { state.error = typeof value === 'function' ? value(state.error) : value; },
    t: key => key,
    setScopeLoading: () => {}, console: { warn: () => {} },
    fetch: () => new Promise((resolve, reject) => requests.push({ resolve, reject })),
  };
  vm.runInNewContext(ts.transpileModule(`${parts.normalize}; globalThis.load=${parts.load}; globalThis.workspace=()=>(${parts.workspace});`,
    { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText, state);
  return {
    state, requests, workspace: () => state.workspace(),
    load(id = 'A') { state.activeConversationId = id; state.activeConversationIdRef.current = id; return state.load(id); },
    finish(index, binding, ok = true) { requests[index].resolve({ ok, json: async () => ({ binding }) }); },
  };
}

test('uncached scope cannot publish a temporary global draft before Engine resolves ownership', async () => {
  const f = fixture();
  const pending = f.load();
  assert.equal(f.workspace(), '', 'composer must not accept edits under a guessed global owner');
  f.finish(0, { resolvedScope: 'workspace', workspaceId: 'workspace-A' });
  await pending;
  assert.equal(f.workspace(), 'workspace-A');
});

test('failed cached scope refresh preserves the confirmed owner and its newly typed draft', async () => {
  const f = fixture();
  const binding = { resolvedScope: 'workspace', workspaceId: 'workspace-A' };
  f.state.scopeCacheRef.current.set('A', binding);
  const pending = f.load();
  const drafts = new Map([[f.workspace(), 'typed while refreshing']]);
  f.finish(0, null, false);
  await pending;
  assert.equal(f.workspace(), 'workspace-A');
  assert.equal(drafts.get(f.workspace()), 'typed while refreshing');
  const retry = f.load();
  f.requests[1].reject(new Error('offline'));
  await retry;
  assert.equal(f.workspace(), 'workspace-A');
});

test('late old A response after A to B to A cannot replace the latest workspace owner', async () => {
  const f = fixture();
  const first = f.load('A'), other = f.load('B'), latest = f.load('A');
  f.finish(2, { resolvedScope: 'workspace', workspaceId: 'workspace-new' });
  await latest;
  f.finish(0, { resolvedScope: 'workspace', workspaceId: 'workspace-old' });
  f.finish(1, { resolvedScope: 'workspace', workspaceId: 'workspace-B' });
  await Promise.all([first, other]);
  assert.equal(f.workspace(), 'workspace-new');
  assert.equal(f.state.scopeCacheRef.current.get('A').workspaceId, 'workspace-new');
});

test('confirmed global scope is reusable but an unresolved failed load stays unavailable', async () => {
  const f = fixture();
  const first = f.load();
  f.finish(0, null, false);
  await first;
  assert.equal(f.workspace(), '');
  assert.equal(f.state.error, 'web.chat.scopeLoadFailed');
  const retry = f.load();
  f.finish(1, { resolvedScope: 'global' });
  await retry;
  assert.equal(f.workspace(), '__global__');
  assert.equal(f.state.error, '');
  const refresh = f.load();
  f.requests[2].reject(new Error('offline'));
  await refresh;
  assert.equal(f.workspace(), '__global__');
});

test('a late response cannot clear the active scope failure and successful retry restores ownership', async () => {
  const f = fixture();
  const old = f.load('A'), active = f.load('B');
  f.finish(1, null, false);
  assert.equal(await active, false);
  assert.equal(f.state.error, 'web.chat.scopeLoadFailed');
  f.finish(0, { resolvedScope: 'workspace', workspaceId: 'workspace-A' });
  assert.equal(await old, false);
  assert.equal(f.state.error, 'web.chat.scopeLoadFailed');
  assert.equal(f.workspace(), '');
  const retry = f.load('B');
  f.finish(2, { resolvedScope: 'workspace', workspaceId: 'workspace-B' });
  assert.equal(await retry, true);
  assert.equal(f.state.error, '');
  assert.equal(f.workspace(), 'workspace-B');
});
