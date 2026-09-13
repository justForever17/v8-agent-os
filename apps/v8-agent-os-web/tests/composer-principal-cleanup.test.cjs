const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

function fixture() {
  const filename = path.resolve(__dirname, '../src/app/chat/ChatClient.tsx');
  const source = ts.createSourceFile(filename, fs.readFileSync(filename, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let effect;
  function visit(node) {
    if (ts.isCallExpression(node) && node.expression.getText(source) === 'useEffect'
      && node.arguments[0]?.getText(source).includes('previousPrincipalRef.current')) effect = node.arguments[0].getText(source);
    ts.forEachChild(node, visit);
  }
  visit(source);
  assert.ok(effect, 'execute the production principal cleanup effect');
  const removed = [], requests = [];
  const ownerA = JSON.stringify(['instance', 'A', 'workspace', 'session']);
  const ownerB = JSON.stringify(['instance', 'B', 'workspace', 'session']);
  const context = {
    previousPrincipalRef: { current: '' }, AbortController,
    removeDrafts: async predicate => removed.push(...[ownerA, ownerB].filter(predicate)),
    fetch: (_url, options) => new Promise((resolve, reject) => requests.push({ resolve, reject, options })),
  };
  vm.runInNewContext(ts.transpileModule(`globalThis.effect=${effect}`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText, context);
  let cleanup;
  return { removed, requests, ownerA, ownerB,
    render(status, id) { cleanup?.(); context.status = status; context.session = id ? { user: { id } } : null; cleanup = context.effect(); },
    finish(index, { ok = true, body = null } = {}) { requests[index].resolve({ ok, json: async () => body }); },
    flush: () => new Promise(resolve => setImmediate(resolve)),
  };
}

test('session initialization, refresh and failed transport do not delete a known principal draft', async () => {
  const f = fixture();
  f.render('loading'); f.render('unauthenticated');
  assert.equal(f.requests.length, 0);
  f.render('authenticated', 'A');
  f.render('loading'); f.render('authenticated', 'A');
  f.render('authenticated'); f.render('authenticated', 'A');
  assert.equal(f.removed.length, 0); assert.equal(f.requests.length, 0);
  f.render('unauthenticated'); f.requests[0].reject(new Error('offline')); await f.flush();
  assert.equal(f.removed.length, 0);
  f.render('authenticated', 'A'); f.render('unauthenticated'); f.finish(1, { ok: false }); await f.flush();
  assert.equal(f.removed.length, 0);
  f.render('authenticated', 'A'); f.render('unauthenticated'); f.finish(2, { body: { user: { id: 'A' } } }); await f.flush();
  assert.equal(f.removed.length, 0);
});

test('confirmed logout clears only the previous principal and cancelled verification cannot clear a replacement', async () => {
  const f = fixture();
  f.render('authenticated', 'A'); f.render('unauthenticated');
  f.render('authenticated', 'A'); f.finish(0); await f.flush();
  assert.equal(f.removed.length, 0, 'ignored abort response must not be treated as logout');
  f.render('unauthenticated'); f.finish(1); await f.flush();
  assert.deepEqual(f.removed, [f.ownerA]);
  f.render('authenticated', 'A'); f.render('loading'); f.render('authenticated', 'B');
  assert.deepEqual(f.removed, [f.ownerA, f.ownerA]);
  f.render('unauthenticated'); f.finish(2, { body: {} }); await f.flush();
  assert.deepEqual(f.removed, [f.ownerA, f.ownerA, f.ownerB]);
});
