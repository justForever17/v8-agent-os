const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

// Exercise the production component and its handlers. Only React scheduling,
// presentation components, and network/cache boundaries are substituted.
function mount(kind = 'admin', mutate = source => source) {
  const slots = [], effects = [], requests = [], notifications = [];
  let cursor = 0, dirty = false, tree;
  const rows = { drafts: [{ id: 'draft-a', name: 'Saved name', updatedAt: 'v1', steps: [{ stepId: 's1', use: 'click', target: { selector: { css: '#before' } } }] }] };
  const changed = (a, b) => !a || !b || a.length !== b.length || a.some((x, i) => !Object.is(x, b[i]));
  const react = {
    useState(initial) {
      const i = cursor++;
      slots[i] ??= { value: typeof initial === 'function' ? initial() : initial };
      return [slots[i].value, next => { const value = typeof next === 'function' ? next(slots[i].value) : next; if (!Object.is(value, slots[i].value)) { slots[i].value = value; dirty = true; } }];
    },
    useRef(value) { const i = cursor++; return slots[i] ??= { current: value }; },
    useMemo(fn, deps) { const i = cursor++; if (!slots[i] || changed(slots[i].deps, deps)) slots[i] = { value: fn(), deps }; return slots[i].value; },
    useCallback(fn, deps) { return react.useMemo(() => fn, deps); },
    useEffect(fn, deps) {
      const i = cursor++;
      if (!slots[i] || changed(slots[i].deps, deps)) { const old = slots[i]; slots[i] = { deps }; effects.push(() => { old?.cleanup?.(); slots[i].cleanup = fn(); }); }
    },
  };
  const translate = key => key;
  const toast = info => notifications.push(info);
  const jsx = (type, props) => ({ type, props });
  const files = {
    admin: '../src/components/rpa/RPAWorkbench.tsx',
    web: '../../v8-agent-os-web/src/components/rpa/RPAQuickPanel.tsx',
  };
  const compile = source => ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const imports = name => {
    if (name === 'react') return react;
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx };
    if (name.endsWith('LocaleProvider')) return { useT: () => translate };
    if (name.endsWith('use-toast')) return { useToast: () => ({ toast }) };
    if (name.endsWith('admin-legacy')) return { tg: (_, key) => key, ti: (_, key) => key, ag: key => key };
    if (name.endsWith('/locale')) return { translateCurrentClient: translate };
    if (name.endsWith('use-runtime-ops')) return { formatRunStatusLabel: status => status, formatWhen: value => value };
    if (name.endsWith('admin-client-cache')) return {
      peekAdminJsonCache: () => undefined,
      fetchAdminJson: async url => structuredClone(url.includes('/drafts') ? rows : url.includes('/availability') ? { robotFramework: true } : {}),
    };
    if (name === './rpa-run-state') {
      const exports = {};
      new Function('exports', compile(fs.readFileSync(path.resolve(__dirname, '../../v8-agent-os-web/src/components/rpa/rpa-run-state.ts'), 'utf8')))(exports);
      return exports;
    }
    return new Proxy({}, { get: (_, key) => key === '__esModule' ? true : String(key) });
  };
  const fetch = (url, options = {}) => new Promise((resolve, reject) => requests.push({ url, options, resolve, reject }));
  const fakeWindow = { confirm: () => true, addEventListener() {}, removeEventListener() {}, setTimeout() {}, clearTimeout() {}, setInterval() {}, clearInterval() {} };
  const exports = {};
  new Function('require', 'exports', 'fetch', 'window', 'console', compile(mutate(fs.readFileSync(path.resolve(__dirname, files[kind]), 'utf8'))))(imports, exports, fetch, fakeWindow, { error() {}, warn() {} });
  function render() {
    for (let pass = 0; pass < 20; pass++) {
      cursor = 0; effects.length = 0; dirty = false;
      tree = kind === 'admin' ? exports.RPAWorkbench() : exports.RPAQuickPanel();
      effects.splice(0).forEach(fn => fn());
      if (!dirty) return;
    }
    assert.fail('effects did not settle');
  }
  function nodes(node) {
    if (Array.isArray(node)) return node.flatMap(nodes);
    if (!node || typeof node !== 'object') return [];
    return [node, ...nodes(node.props?.children)];
  }
  function content(node) {
    if (Array.isArray(node)) return node.map(content).join(' ');
    return node && typeof node === 'object' ? content(node.props?.children) : String(node ?? '');
  }
  const find = predicate => { const found = nodes(tree).find(predicate); assert.ok(found, 'control exists'); return found; };
  render();
  return { rows, requests, notifications, render, find, content, nodes: () => nodes(tree),
    button: label => find(node => node.type === 'Button' && content(node).includes(label)),
    settle: async () => { await new Promise(resolve => setImmediate(resolve)); render(); },
  };
}

function answer(request, data, status = 200) { request.resolve({ ok: status < 400, status, json: async () => data }); }
async function openDraft(ui) {
  await ui.settle();
  ui.find(node => node.type === 'select' && ui.content(node).includes('Saved name')).props.onChange({ target: { value: 'draft-a' } });
  ui.render();
}

test('refresh keeps dirty draft text and a remote version change keeps the original save precondition', async () => {
  const ui = mount(); await openDraft(ui);
  ui.find(n => n.props?.id === 'rpa-flow-name').props.onChange({ target: { value: 'Local edit' } }); ui.render();
  ui.rows.drafts[0] = { ...ui.rows.drafts[0], name: 'Remote edit', updatedAt: 'v2' };
  ui.button('creativeMedia.refresh').props.onClick(); await ui.settle();
  assert.equal(ui.find(n => n.props?.id === 'rpa-flow-name').props.value, 'Local edit');
  assert.ok(ui.nodes().some(n => n.props?.role === 'alert' && ui.content(n).includes('studioDraftConflict')));
  ui.button('studioSaveChanges').props.onClick();
  const save = ui.requests.find(r => r.url.endsWith('/patch'));
  assert.equal(JSON.parse(save.options.body).expectedUpdatedAt, 'v1');
  answer(save, { detail: 'version conflict' }, 409); await ui.settle();
  assert.equal(ui.find(n => n.props?.id === 'rpa-flow-name').props.value, 'Local edit');
});

test('a successful save does not erase edits made while the request was pending', async () => {
  const ui = mount(); await openDraft(ui);
  ui.find(n => n.props?.id === 'rpa-flow-name').props.onChange({ target: { value: 'Submitted' } }); ui.render();
  ui.button('studioSaveChanges').props.onClick();
  const save = ui.requests.find(r => r.url.endsWith('/patch'));
  ui.find(n => n.props?.id === 'rpa-flow-name').props.onChange({ target: { value: 'Newer local edit' } }); ui.render();
  ui.rows.drafts[0] = { ...ui.rows.drafts[0], name: 'Submitted', updatedAt: 'v2' };
  answer(save, ui.rows.drafts[0]); await ui.settle();
  assert.equal(ui.find(n => n.props?.id === 'rpa-flow-name').props.value, 'Newer local edit');
  assert.ok(ui.nodes().some(n => n.type === 'Badge' && ui.content(n).includes('studioDirtyBadge')));
});

test('the dirty-text oracle rejects the old unconditional-refresh mutation', async () => {
  const ui = mount('admin', source => source.replace('loadedDraft.current?.id === selectedDraft.id && currentEditor.current.dirty', 'false'));
  await openDraft(ui);
  ui.find(n => n.props?.id === 'rpa-flow-name').props.onChange({ target: { value: 'Unsaved' } }); ui.render();
  ui.button('creativeMedia.refresh').props.onClick(); await ui.settle();
  assert.throws(() => assert.equal(ui.find(n => n.props?.id === 'rpa-flow-name').props.value, 'Unsaved'), assert.AssertionError);
});

test('the run-state oracle rejects promotion of review-required to success', async () => {
  const ui = mount('web', source => source.replace('status: text(payload?.status)', 'status: "completed"'));
  answer(ui.requests[0], { robotFramework: true }); answer(ui.requests[1], { templates: [{ id: 'a', name: 'A' }] }); await ui.settle();
  ui.button('web.rpa.start').props.onClick();
  answer(ui.requests.at(-1), { status: 'review_required', runId: 'run-a' }); await ui.settle();
  assert.throws(() => assert.ok(ui.content(ui.find(n => n.props?.role === 'status')).includes('waitingApproval')), assert.AssertionError);
});

test('Web refresh and switching templates retain each parameter draft', async () => {
  const ui = mount('web');
  const templates = [{ id: 'a', name: 'A', variables: [{ name: 'recipient' }] }, { id: 'b', name: 'B', variables: [{ name: 'recipient' }] }];
  answer(ui.requests[0], { robotFramework: true }); answer(ui.requests[1], { templates }); await ui.settle();
  ui.find(n => n.props?.id === 'rpa-field-recipient').props.onChange({ target: { value: 'local-a' } }); ui.render();
  ui.button('web.rpa.refresh').props.onClick();
  answer(ui.requests[2], { robotFramework: true }); answer(ui.requests[3], { templates: structuredClone(templates) }); await ui.settle();
  assert.equal(ui.find(n => n.props?.id === 'rpa-field-recipient').props.value, 'local-a');
  ui.find(n => n.type === 'select' && n.props.value === 'a').props.onChange({ target: { value: 'b' } }); ui.render();
  ui.find(n => n.props?.id === 'rpa-field-recipient').props.onChange({ target: { value: 'local-b' } }); ui.render();
  ui.find(n => n.type === 'select' && n.props.value === 'b').props.onChange({ target: { value: 'a' } }); ui.render();
  assert.equal(ui.find(n => n.props?.id === 'rpa-field-recipient').props.value, 'local-a');
});

test('selector validation finishing after a step edit cannot validate the new step', async () => {
  const ui = mount(); await openDraft(ui);
  ui.button('studioValidateSelector').props.onClick();
  const validation = ui.requests.find(r => r.url.endsWith('/validate-step'));
  ui.find(n => n.type === 'Textarea' && n.props['aria-label']?.includes('studioStepJson')).props.onChange({ target: { value: JSON.stringify({ stepId: 's1', use: 'click', target: { selector: { css: '#after' } } }) } }); ui.render();
  answer(validation, { ok: true, summary: 'old-selector-passed' }); await ui.settle();
  assert.ok(!ui.nodes().some(n => ui.content(n).includes('old-selector-passed')));
  assert.ok(!ui.notifications.some(n => n.description === 'old-selector-passed'));
});

test('save and run uses the returned saved version without patching inside the run request', async () => {
  const ui = mount(); await openDraft(ui);
  ui.find(n => n.props?.id === 'rpa-flow-name').props.onChange({ target: { value: 'Run this edit' } }); ui.render();
  ui.find(n => n.type === 'button' && ui.content(n).includes('studioPanelRuns')).props.onClick(); ui.render();
  ui.button('studioDebugRunDraft').props.onClick();
  const save = ui.requests.find(r => r.url.endsWith('/patch'));
  assert.ok(save); assert.equal(ui.requests.filter(r => r.url.endsWith('/run')).length, 0);
  ui.rows.drafts[0] = { ...ui.rows.drafts[0], name: 'Run this edit', updatedAt: 'v2' };
  answer(save, ui.rows.drafts[0]); await ui.settle();
  const run = ui.requests.find(r => r.url.endsWith('/run'));
  assert.ok(run);
  const body = JSON.parse(run.options.body);
  assert.equal(body.expectedUpdatedAt, 'v2');
  assert.equal(body.steps, undefined, 'run does not silently rewrite the saved draft');
});

for (const [status, expected] of [['review_required', 'waitingApproval'], ['cancelled', 'cancelled'], ['unknown', 'unknown'], ['compile_blocked', 'failed'], ['completed', 'completed']]) {
  test(`Web displays ${status} receipt without treating every HTTP 200 as started`, async () => {
    const ui = mount('web');
    answer(ui.requests[0], { robotFramework: true }); answer(ui.requests[1], { templates: [{ id: 'a', name: 'A' }] }); await ui.settle();
    const start = ui.button('web.rpa.start');
    start.props.onClick(); start.props.onClick();
    assert.equal(ui.requests.filter(r => r.url.endsWith('/run')).length, 1, 'double submit is fenced synchronously');
    answer(ui.requests.at(-1), { status, runId: 'run-a' }); await ui.settle();
    const receipt = ui.find(n => n.props?.role === 'status');
    assert.ok(ui.content(receipt).includes(expected));
    assert.ok(!ui.content(receipt).includes('web.rpa.started'));
    assert.ok(ui.content(receipt).includes('web.rpa.runReference'));
  });
}
