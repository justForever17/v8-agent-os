const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');

// Execute the actual callback/effect together; only network and browser clocks
// are controlled. There is no second polling implementation in this fixture.
function fixture() {
  const filename = path.resolve(__dirname, '../src/app/chat/ChatClient.tsx');
  const source = ts.createSourceFile(filename, fs.readFileSync(filename, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let callback, effect;
  function visit(node) {
    if (ts.isVariableStatement(node) && node.declarationList.declarations.some(item => item.name.getText(source) === 'loadSupervisorDisplayProfile')) callback = node.getText(source);
    if (ts.isCallExpression(node) && node.expression.getText(source) === 'useEffect'
      && node.arguments[1]?.getText(source) === '[loadSupervisorDisplayProfile, status]') effect = node.getText(source);
    ts.forEachChild(node, visit);
  }
  visit(source);
  assert.ok(callback && effect, 'canonical profile refresh callback and effect must be present');
  const requests = [], updates = [], timers = new Set();
  const window = new EventTarget(), document = new EventTarget();
  document.visibilityState = 'visible';
  window.setInterval = fn => { timers.add(fn); return fn; };
  window.clearInterval = fn => timers.delete(fn);
  const module = { exports: {} };
  const compiled = ts.transpileModule(`
    function mount(status = 'authenticated') {
      let cleanup;
      const useCallback = fn => fn;
      const useEffect = fn => { cleanup = fn(); };
      ${callback}
      ${effect};
      return () => cleanup?.();
    }
    module.exports = { mount };
  `, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
  vm.runInNewContext(compiled, {
    module, window, document, AbortController,
    readString: value => typeof value === 'string' ? value : '', resolveProfileAvatarSrc: value => value,
    setSupervisorDisplayProfile: update => updates.push(update({ name: 'before', roleLabel: 'before', avatar: '' })),
    fetch: (_url, options) => new Promise((resolve, reject) => requests.push({ options, resolve, reject })),
  });
  const finish = (index, name) => requests[index].resolve({ ok: true, json: async () => ({ name, roleLabel: 'fixture', avatar: '' }) });
  const flush = () => new Promise(resolve => setImmediate(resolve));
  return { ...module.exports, requests, updates, window, document, finish, flush,
    tick() { for (const fn of timers) fn(); }, timers };
}

test('slow profile requests merge timer, focus and visibility refreshes until completion', async () => {
  const f = fixture();
  const cleanup = f.mount();
  for (let i = 0; i < 8; i++) {
    f.tick(); f.window.dispatchEvent(new Event('focus')); f.document.dispatchEvent(new Event('visibilitychange'));
  }
  assert.equal(f.requests.length, 1);
  f.finish(0, 'current'); await f.flush();
  assert.equal(f.updates.at(-1).name, 'current');
  f.window.dispatchEvent(new Event('focus'));
  assert.equal(f.requests.length, 2);
  cleanup();
});

test('unmount aborts the real request and a late response cannot overwrite a replacement owner', async () => {
  const f = fixture();
  const stopOld = f.mount();
  stopOld();
  assert.equal(f.requests[0].options.signal?.aborted, true);
  const stopNew = f.mount();
  f.finish(1, 'new owner'); await f.flush();
  // A backend/fetch implementation can finish despite cancellation.
  f.finish(0, 'old owner'); await f.flush();
  assert.deepEqual(f.updates.map(update => update.name), ['new owner']);
  stopNew();
  const count = f.requests.length;
  f.window.dispatchEvent(new Event('focus')); f.tick();
  assert.equal(f.requests.length, count);
  assert.equal(f.timers.size, 0);
});

test('failure releases the request slot and hidden pages resume on visibility restoration', async () => {
  const f = fixture();
  f.document.visibilityState = 'hidden';
  const cleanup = f.mount();
  f.tick(); assert.equal(f.requests.length, 0);
  f.document.visibilityState = 'visible';
  f.document.dispatchEvent(new Event('visibilitychange'));
  f.requests[0].reject(new Error('temporary failure')); await f.flush();
  f.tick(); assert.equal(f.requests.length, 2);
  f.finish(1, 'recovered'); await f.flush();
  assert.equal(f.updates.at(-1).name, 'recovered');
  cleanup();
});
