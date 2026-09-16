const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

// Execute the production component with deferred HTTP receipts. No Engine or
// browser is needed to reproduce the pre-registration stop race.
function mount() {
  let cursor = 0, dirty = false, tree;
  const slots = [], effects = [], requests = [], timers = [];
  const changed = (a, b) => !a || a.length !== b.length || a.some((x, i) => !Object.is(x, b[i]));
  const react = {
    useState(initial) {
      const i = cursor++;
      slots[i] ??= { value: initial };
      return [slots[i].value, next => { slots[i].value = typeof next === 'function' ? next(slots[i].value) : next; dirty = true; }];
    },
    useRef(value) { return slots[cursor++] ??= { current: value }; },
    useMemo(fn, deps) {
      const i = cursor++;
      if (!slots[i] || changed(slots[i].deps, deps)) slots[i] = { value: fn(), deps };
      return slots[i].value;
    },
    useCallback(fn, deps) { return react.useMemo(() => fn, deps); },
    useEffect(fn, deps) {
      const i = cursor++;
      if (!slots[i] || changed(slots[i].deps, deps)) { slots[i] = { deps }; effects.push(fn); }
    },
  };
  const translate = key => key;
  const jsx = (type, props) => ({ type, props });
  const compile = name => ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/components/rpa', name), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const state = {};
  new Function('exports', compile('rpa-run-state.ts'))(state);
  const imports = name => {
    if (name === 'react') return react;
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx };
    if (name.endsWith('LocaleProvider')) return { useT: () => translate };
    if (name === './rpa-run-state') return state;
    return new Proxy({}, { get: (_, key) => String(key) });
  };
  const fetch = (url, options = {}) => new Promise(resolve => requests.push({ url, options, resolve }));
  const exports = {};
  new Function('require', 'exports', 'fetch', 'window', compile('RPAQuickPanel.tsx'))(imports, exports, fetch, {
    setTimeout: fn => timers.push(fn), clearTimeout() {},
  });
  function render() {
    for (let i = 0; i < 10; i++) {
      cursor = 0; dirty = false;
      tree = exports.RPAQuickPanel(); effects.splice(0).forEach(fn => fn());
      if (!dirty) return;
    }
    assert.fail('render did not settle');
  }
  const nodes = node => Array.isArray(node) ? node.flatMap(nodes)
    : node && typeof node === 'object' ? [node, ...nodes(node.props?.children)] : [];
  const content = node => Array.isArray(node) ? node.map(content).join(' ')
    : node && typeof node === 'object' ? content(node.props?.children) : String(node ?? '');
  const button = key => {
    const result = nodes(tree).find(node => node.type === 'Button' && content(node).includes(key));
    assert.ok(result, `button ${key} exists`); return result;
  };
  render();
  return { requests, timers, render, button, content: () => content(tree),
    settle: async () => { await new Promise(resolve => setImmediate(resolve)); render(); },
  };
}

function answer(request, payload, status = 200) {
  request.resolve({ ok: status < 400, status, json: async () => payload });
}

async function start() {
  const ui = mount();
  answer(ui.requests.find(r => r.url.includes('/availability')), { robotFramework: true });
  answer(ui.requests.find(r => r.url.includes('/templates')), { templates: [{ id: 'owned', name: 'Owned' }] });
  await ui.settle();
  ui.button('web.rpa.start').props.onClick(); ui.render();
  const run = ui.requests.find(r => r.url.endsWith('/run'));
  return { ui, runId: JSON.parse(run.options.body).runId };
}

test('a generated run ID cannot send stop before Engine confirms that same run', async () => {
  const { ui, runId } = await start();
  assert.match(ui.content(), /web\.rpa\.preparing/);
  assert.equal(ui.button('web.rpa.stop').props.disabled, true);
  ui.button('web.rpa.stop').props.onClick();
  assert.equal(ui.requests.filter(r => r.url.endsWith('/cancel')).length, 0);
  answer(ui.requests.find(r => r.url.startsWith('/api/runs?')), { runs: [{ id: 'other-run', run_type: 'rpa', status: 'running' }] });
  await ui.settle();
  assert.equal(ui.button('web.rpa.stop').props.disabled, true, 'another running task does not acknowledge this request');
  ui.timers.shift()();
  answer(ui.requests.at(-1), { runs: [{ id: runId, run_type: 'rpa', status: 'running', metadata: { executionState: 'running_robot' } }] });
  await ui.settle();
  assert.equal(ui.button('web.rpa.stop').props.disabled, false);
  ui.button('web.rpa.stop').props.onClick();
  const stop = ui.requests.at(-1);
  assert.equal(stop.url, `/api/runs/${runId}/commands/cancel`);
  answer(stop, { run: { id: runId, status: 'cancelled' } }); await ui.settle();
});

test('a failed confirmed stop stays visible and never claims cancellation', async () => {
  const { ui, runId } = await start();
  answer(ui.requests.find(r => r.url.startsWith('/api/runs?')), { runs: [{ id: runId, run_type: 'rpa', status: 'queued' }] });
  await ui.settle();
  ui.button('web.rpa.stop').props.onClick();
  answer(ui.requests.at(-1), { detail: 'resource_not_found' }, 404); await ui.settle();
  assert.match(ui.content(), /web\.rpa\.stopFailed/);
  assert.doesNotMatch(ui.content(), /web\.runControl\.status\.cancelled/);
  assert.equal(ui.button('web.rpa.stop').props.disabled, false);
});
