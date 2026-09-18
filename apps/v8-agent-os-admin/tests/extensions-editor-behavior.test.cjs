const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

// Execute the production page and event handlers with deterministic React
// scheduling. Substitute presentation and network boundaries, not editor logic.
function mount() {
  const slots = [], effects = [], requests = [], saves = [], notifications = [];
  let cursor = 0, dirty = false, tree;
  const changed = (a, b) => !a || !b || a.length !== b.length || a.some((x, i) => !Object.is(x, b[i]));
  const react = {
    useState(initial) {
      const i = cursor++;
      slots[i] ??= { value: typeof initial === 'function' ? initial() : initial };
      return [slots[i].value, next => {
        const value = typeof next === 'function' ? next(slots[i].value) : next;
        if (!Object.is(value, slots[i].value)) { slots[i].value = value; dirty = true; }
      }];
    },
    useRef(value) { const i = cursor++; return slots[i] ??= { current: value }; },
    useMemo(fn, deps) { const i = cursor++; if (!slots[i] || changed(slots[i].deps, deps)) slots[i] = { value: fn(), deps }; return slots[i].value; },
    useCallback(fn, deps) { return react.useMemo(() => fn, deps); },
    useEffect(fn, deps) {
      const i = cursor++;
      if (!slots[i] || changed(slots[i].deps, deps)) {
        const old = slots[i]; slots[i] = { deps };
        effects.push(() => { old?.cleanup?.(); slots[i].cleanup = fn(); });
      }
    },
  };
  const fixture = {
    config: { domain: 'extensions', data: { prefilterPolicy: { enabled: true, skills: { stage1TopK: 20 }, mcp: { stage2TopK: 2 } }, modelBindings: { prefilterModel: 'fixture::saved' }, future: { keep: false } } },
    catalog: { summary: { skillCount: 0, mcpServerCount: 1, connectedMcpServerCount: 0, mcpToolCount: 0 }, mcp: { servers: [{ name: 'synthetic', status: 'disabled', tools: [] }] } },
    health: { mcp: { statusBreakdown: {} } },
    failRefresh: false,
  };
  const translate = key => key;
  const toast = info => notifications.push(info);
  const jsx = (type, props) => ({ type, props });
  const read = url => structuredClone(url.includes('/catalog') ? fixture.catalog : url.includes('/health') ? fixture.health : url === '/api/models' ? [] : { items: [] });
  const imports = name => {
    if (name === 'react') return react;
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'Fragment' };
    if (name.endsWith('LocaleProvider')) return { useT: () => translate };
    if (name.endsWith('use-toast')) return { useToast: () => ({ toast }) };
    if (name.endsWith('admin-legacy')) return { tg: (_, key) => key };
    if (name.endsWith('admin-client-cache')) return {
      peekAdminJsonCache: read,
      fetchAdminJson: async url => { if (fixture.failRefresh) throw new Error('refresh unavailable'); return read(url); },
    };
    if (name.endsWith('config-registry')) return {
      peekConfigDomain: () => structuredClone(fixture.config),
      fetchConfigDomain: async () => { if (fixture.failRefresh) throw new Error('refresh unavailable'); return structuredClone(fixture.config); },
      saveConfigDomain: (domain, payload) => new Promise((resolve, reject) => saves.push({ domain, payload, resolve, reject })),
    };
    return new Proxy({}, { get: (_, key) => key === '__esModule' ? true : String(key) });
  };
  const sourcePath = path.resolve(__dirname, '../src/app/admin/(dashboard)/extensions/page.tsx');
  const source = ts.transpileModule(fs.readFileSync(sourcePath, 'utf8'), {
    fileName: sourcePath,
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  const fetch = (url, options = {}) => new Promise((resolve, reject) => requests.push({ url, options, resolve, reject }));
  new Function('require', 'exports', 'fetch', 'window', source)(imports, exports, fetch, { setTimeout() {}, confirm: () => true });
  function render() {
    for (let pass = 0; pass < 20; pass++) {
      cursor = 0; effects.length = 0; dirty = false;
      tree = exports.default();
      effects.splice(0).forEach(fn => fn());
      if (!dirty) return;
    }
    assert.fail('effects did not settle');
  }
  function nodes(node) {
    if (Array.isArray(node)) return node.flatMap(nodes);
    if (!node || typeof node !== 'object') return [];
    return [node, ...nodes(node.props?.children), ...nodes(node.props?.actions), ...nodes(node.props?.footer)];
  }
  function content(node) {
    if (Array.isArray(node)) return node.map(content).join(' ');
    return node && typeof node === 'object' ? content(node.props?.children) : String(node ?? '');
  }
  const find = predicate => { const found = nodes(tree).find(predicate); assert.ok(found, 'control exists'); return found; };
  const ui = {
    fixture, requests, saves, notifications, render, find, content,
    nodes: () => nodes(tree),
    button: suffix => find(n => n.type === 'Button' && content(n).endsWith(`.${suffix}`)),
    input: placeholder => find(n => n.props?.placeholder === placeholder),
    jsonInput: () => find(n => n.type === 'Textarea' && n.props?.placeholder?.includes('mcpServers')),
    dialog: suffix => find(n => n.type === 'Dialog' && content(n).includes(`.${suffix}`)),
    set: (node, value) => { node.props.onChange({ target: { value } }); render(); },
    settle: async () => { await new Promise(resolve => setImmediate(resolve)); render(); },
  };
  render();
  return ui;
}

function answer(request, data, status = 200) { request.resolve({ ok: status < 400, status, json: async () => data }); }
function inlineSaveStateText(ui) {
  const sourcePath = path.resolve(__dirname, '../src/components/admin-shell/InlineSaveState.tsx');
  const source = ts.transpileModule(fs.readFileSync(sourcePath, 'utf8'), {
    fileName: sourcePath,
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const locale = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../src/i18n/locales/zh-CN.json'), 'utf8'));
  const translate = key => locale[key] || key;
  const jsx = (type, props) => ({ type, props });
  const imports = name => {
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx };
    if (name.endsWith('LocaleProvider')) return { useT: () => translate, useResolveText: () => translate };
    return new Proxy({}, { get: (_, key) => String(key) });
  };
  const exports = {};
  new Function('require', 'exports', source)(imports, exports);
  return ui.content(exports.InlineSaveState(ui.find(n => n.type === 'InlineSaveState').props));
}
async function openPolicy(ui) {
  await ui.settle();
  ui.find(n => n.type === 'details').props.onToggle({ currentTarget: { open: true } });
  await ui.settle();
}
function setModel(ui, value) { ui.find(n => n.type === 'ModelSelect').props.onValueChange(value); ui.render(); }
function model(ui) { return ui.find(n => n.type === 'ModelSelect').props.value; }
function openMcp(ui, kind) {
  if (kind === 'form') {
    ui.button('mcpFormInstall').props.onClick(); ui.render();
    ui.set(ui.input('context7'), 'synthetic');
    ui.set(ui.input('npx -y @modelcontextprotocol/server-filesystem'), 'fixture-command');
  } else {
    ui.dialog('k061b2335').props.onOpenChange(true); ui.render();
    ui.set(ui.jsonInput(), JSON.stringify({ mcpServers: { synthetic: { type: 'stdio', command: 'fixture-command', future: { keep: false } } } }));
  }
}
function saveMcp(ui, kind) { ui.button(kind === 'form' ? 'mcpSaveServer' : 'k836f3c8b').props.onClick(); }

test('refresh updates the server snapshot without overwriting a policy draft', async () => {
  const ui = mount(); await openPolicy(ui);
  setModel(ui, 'fixture::draft');
  ui.fixture.config.data.modelBindings.prefilterModel = 'fixture::remote';
  ui.button('k286cb634').props.onClick(); await ui.settle();
  assert.equal(model(ui), 'fixture::draft');
  ui.button('k6010e1ed').props.onClick();
  assert.equal(ui.saves[0].payload.data.modelBindings.prefilterModel, 'fixture::draft');
  assert.deepEqual(ui.saves[0].payload.data.future, { keep: false });
  ui.saves[0].resolve({ ...ui.fixture.config, data: ui.saves[0].payload.data }); await ui.settle();
  ui.button('k286cb634').props.onClick(); await ui.settle();
  assert.equal(model(ui), 'fixture::remote', 'successful save releases the draft for subsequent reads');
});

test('policy save failure is retryable; a late success keeps newer edits visibly unsaved', async () => {
  const ui = mount(); await openPolicy(ui);
  assert.match(inlineSaveStateText(ui), /未变更/);
  setModel(ui, 'fixture::submitted');
  assert.match(inlineSaveStateText(ui), /未保存/);
  ui.button('k6010e1ed').props.onClick();
  ui.saves[0].reject(new Error('permission denied')); await ui.settle();
  assert.equal(model(ui), 'fixture::submitted');
  assert.equal(ui.notifications.at(-1).description, 'permission denied');
  ui.button('k6010e1ed').props.onClick();
  setModel(ui, 'fixture::newer');
  ui.saves[1].resolve({ ...ui.fixture.config, data: ui.saves[1].payload.data }); await ui.settle();
  assert.equal(model(ui), 'fixture::newer');
  assert.equal(ui.find(n => n.type === 'InlineSaveState').props.saved, false);
  assert.match(inlineSaveStateText(ui), /未保存/);
  ui.button('k6010e1ed').props.onClick();
  assert.equal(ui.saves[2].payload.data.modelBindings.prefilterModel, 'fixture::newer');
  ui.saves[2].resolve({ ...ui.fixture.config, data: ui.saves[2].payload.data }); await ui.settle();
  assert.match(inlineSaveStateText(ui), /已保存/);
});

for (const kind of ['form', 'json']) {
  test(`${kind} MCP late success keeps newer editable text and permits its next save`, async () => {
    const ui = mount(); await ui.settle(); openMcp(ui, kind); saveMcp(ui, kind); ui.render();
    const input = kind === 'form' ? ui.input('npx -y @modelcontextprotocol/server-filesystem') : ui.jsonInput();
    assert.ok(!input.props.disabled);
    const newer = kind === 'form' ? 'fixture-newer-command' : JSON.stringify({ mcpServers: { synthetic: { type: 'stdio', command: 'fixture-newer-command' } } });
    // Do not render yet: the change and the response can land in the same batch.
    input.props.onChange({ target: { value: newer } });
    assert.equal(JSON.parse(ui.requests[0].options.body).mcpServers.synthetic.command, 'fixture-command');
    answer(ui.requests[0], { status: 'success' }); await ui.settle();
    assert.equal((kind === 'form' ? ui.input('npx -y @modelcontextprotocol/server-filesystem') : ui.jsonInput()).props.value, newer);
    assert.equal(ui.dialog(kind === 'form' ? 'mcpFormInstall' : 'k061b2335').props.open, true);
    saveMcp(ui, kind);
    assert.equal(JSON.parse(ui.requests[1].options.body).mcpServers.synthetic.command, 'fixture-newer-command');
    answer(ui.requests[1], { status: 'success' }); await ui.settle();
    assert.equal(ui.dialog(kind === 'form' ? 'mcpFormInstall' : 'k061b2335').props.open, false);
  });

  test(`${kind} MCP late success cannot close a reopened editor even with identical content`, async () => {
    const ui = mount(); await ui.settle(); openMcp(ui, kind); saveMcp(ui, kind); ui.render();
    const suffix = kind === 'form' ? 'mcpFormInstall' : 'k061b2335';
    ui.dialog(suffix).props.onOpenChange(false); ui.render();
    openMcp(ui, kind);
    const before = (kind === 'form' ? ui.input('npx -y @modelcontextprotocol/server-filesystem') : ui.jsonInput()).props.value;
    answer(ui.requests[0], { status: 'success' }); await ui.settle();
    assert.equal(ui.dialog(suffix).props.open, true);
    assert.equal((kind === 'form' ? ui.input('npx -y @modelcontextprotocol/server-filesystem') : ui.jsonInput()).props.value, before);
  });

  test(`${kind} MCP late failure does not contaminate another editor and releases the save lock`, async () => {
    const ui = mount(); await ui.settle(); openMcp(ui, kind); saveMcp(ui, kind); ui.render();
    ui.dialog(kind === 'form' ? 'mcpFormInstall' : 'k061b2335').props.onOpenChange(false); ui.render();
    const next = kind === 'form' ? 'json' : 'form';
    openMcp(ui, next);
    // The same synchronous lock covers both input modes until the old POST settles.
    ui.nodes().filter(n => n.type === 'Button' && ui.content(n).endsWith('.kfc8f3cfd')).forEach(n => n.props.onClick());
    assert.equal(ui.requests.length, 1);
    answer(ui.requests[0], { detail: 'old save denied' }, 403); await ui.settle();
    assert.ok(!ui.content(ui.dialog(next === 'form' ? 'mcpFormInstall' : 'k061b2335')).includes('old save denied'));
    assert.equal(ui.notifications.at(-1).description, 'old save denied', 'the completed request still reports its failure');
    saveMcp(ui, next);
    assert.equal(ui.requests.length, 2);
    answer(ui.requests[1], { status: 'success' }); await ui.settle();
  });

  test(`${kind} MCP editor can be dismissed without sending a write`, async () => {
    const ui = mount(); await ui.settle(); openMcp(ui, kind);
    const dialog = ui.dialog(kind === 'form' ? 'mcpFormInstall' : 'k061b2335');
    dialog.props.onOpenChange(false); ui.render();
    assert.equal(ui.dialog(kind === 'form' ? 'mcpFormInstall' : 'k061b2335').props.open, false);
    assert.equal(ui.requests.length, 0);
  });

  test(`${kind} MCP editor preserves denied drafts and retries through the same endpoint`, async () => {
    const ui = mount(); await ui.settle(); openMcp(ui, kind); saveMcp(ui, kind);
    assert.equal(ui.requests[0].url, '/api/mcp/config');
    assert.equal(ui.requests[0].options.method, 'POST');
    const body = JSON.parse(ui.requests[0].options.body);
    assert.equal(body.mcpServers.synthetic.command, 'fixture-command');
    answer(ui.requests[0], { detail: 'Permission denied' }, 403); await ui.settle();
    assert.equal(ui.notifications.at(-1).variant, 'destructive');
    assert.equal(ui.notifications.at(-1).description, 'Permission denied');
    assert.equal(ui.dialog(kind === 'form' ? 'mcpFormInstall' : 'k061b2335').props.open, true);
    saveMcp(ui, kind);
    assert.deepEqual(JSON.parse(ui.requests[1].options.body), body);
    answer(ui.requests[1], { status: 'success' }); await ui.settle();
    assert.equal(ui.dialog(kind === 'form' ? 'mcpFormInstall' : 'k061b2335').props.open, false);
    assert.equal(ui.notifications.at(-1).variant, undefined);
  });

  test(`${kind} MCP editor rejects invalid inputs locally and suppresses same-tick double submission`, async () => {
    const ui = mount(); await ui.settle(); openMcp(ui, kind);
    const input = kind === 'form' ? ui.input('npx -y @modelcontextprotocol/server-filesystem') : ui.jsonInput();
    const value = input.props.value;
    ui.set(input, kind === 'form' ? '' : '{'); saveMcp(ui, kind); await ui.settle();
    assert.equal(ui.requests.length, 0);
    assert.equal(ui.notifications.at(-1).variant, 'destructive');
    ui.set(kind === 'form' ? ui.input('npx -y @modelcontextprotocol/server-filesystem') : ui.jsonInput(), value);
    const submit = ui.button(kind === 'form' ? 'mcpSaveServer' : 'k836f3c8b').props.onClick;
    submit(); submit();
    assert.equal(ui.requests.length, 1);
    answer(ui.requests[0], { status: 'success' }); await ui.settle();
  });
}

test('a committed MCP write with failed inventory refresh stays a success with a retryable read error', async () => {
  const ui = mount(); await openPolicy(ui); setModel(ui, 'fixture::draft');
  openMcp(ui, 'form'); saveMcp(ui, 'form');
  ui.fixture.failRefresh = true;
  answer(ui.requests[0], { status: 'success' }); await ui.settle();
  assert.equal(ui.notifications.at(-1).variant, undefined);
  assert.ok(ui.nodes().some(n => n.props?.role === 'alert'));
  assert.equal(model(ui), 'fixture::draft');
  ui.fixture.failRefresh = false;
  ui.button('k286cb634').props.onClick(); await ui.settle();
  assert.ok(!ui.nodes().some(n => n.props?.role === 'alert'));
  assert.equal(ui.requests.length, 1, 'read recovery does not repeat the committed write');
  assert.equal(model(ui), 'fixture::draft');
});

test('MCP form editing keeps unknown fields, exact argv, credential refs and the Engine edit base', async () => {
  const ui = mount(); await ui.settle();
  ui.find(n => n.type === 'Button' && n.props.title?.endsWith('.mcpEditServer')).props.onClick();
  const base = { type: 'stdio', command: 'fixture-command', args: ['', ' spaced ', 'embedded\nnewline'], env: { KEEP: 'value' }, future: { keep: false }, 'x-v8-credential-refs': { key: { secretRef: 'synthetic-ref' } } };
  answer(ui.requests[0], { mcpServers: { synthetic: base } }); await ui.settle();
  ui.button('mcpUpdateServer').props.onClick();
  const saved = JSON.parse(ui.requests[1].options.body).mcpServers.synthetic;
  assert.deepEqual(saved['x-v8-edit-base'], base);
  delete saved['x-v8-edit-base'];
  assert.deepEqual(saved, base);
  answer(ui.requests[1], { status: 'success' }); await ui.settle();
});

test('advanced MCP fields and credential choices also invalidate a pending save cleanup', async () => {
  const cases = [
    { name: 'arguments', input: '-y\n@example/server', value: '["", "new argument"]' },
    { name: 'environment', input: 'API_KEY=...\nDEBUG=false', value: 'KEEP=new-value' },
    { name: 'endpoint', type: 'http', input: 'https://example.com/mcp', value: 'https://new.fixture.invalid/mcp' },
    { name: 'headers', type: 'http', input: 'Authorization=Bearer ...\nX-Client=V8OS', value: 'X-New=1' },
    { name: 'transport', type: 'http' },
    { name: 'credential', type: 'http' },
  ];
  for (const row of cases) {
    const ui = mount(); await ui.settle();
    ui.find(n => n.type === 'Button' && n.props.title?.endsWith('.mcpEditServer')).props.onClick();
    const base = { type: row.type || 'stdio', command: 'fixture-command', args: [], endpointRef: 'synthetic-endpoint', 'x-v8-credential-refs': { key: { secretRef: 'synthetic-ref' } }, future: { keep: false } };
    answer(ui.requests[0], { mcpServers: { synthetic: base } }); await ui.settle();
    ui.button('mcpUpdateServer').props.onClick(); ui.render();
    if (row.input) ui.set(ui.input(row.input), row.value);
    else if (row.name === 'transport') {
      ui.find(n => n.type === 'Select' && n.props.value === 'http').props.onValueChange('sse'); ui.render();
    } else {
      ui.find(n => n.type === 'input' && n.props.type === 'checkbox').props.onChange({ target: { checked: true } }); ui.render();
    }
    answer(ui.requests[1], { status: 'success' }); await ui.settle();
    assert.equal(ui.dialog('mcpFormEdit').props.open, true, row.name);
    if (row.input) assert.equal(ui.input(row.input).props.value, row.value, row.name);
    else if (row.name === 'transport') assert.ok(ui.nodes().some(n => n.type === 'Select' && n.props.value === 'sse'));
    else assert.equal(ui.find(n => n.type === 'input' && n.props.type === 'checkbox').props.checked, true);
    assert.deepEqual(JSON.parse(ui.requests[1].options.body).mcpServers.synthetic['x-v8-edit-base'], base);
  }
});

test('a late MCP edit load cannot replace a newly opened create editor', async () => {
  const ui = mount(); await ui.settle();
  ui.find(n => n.type === 'Button' && n.props.title?.endsWith('.mcpEditServer')).props.onClick();
  openMcp(ui, 'form');
  answer(ui.requests[0], { mcpServers: { synthetic: { type: 'stdio', command: 'old-edit-command' } } }); await ui.settle();
  assert.equal(ui.dialog('mcpFormInstall').props.open, true);
  assert.equal(ui.input('npx -y @modelcontextprotocol/server-filesystem').props.value, 'fixture-command');
  assert.ok(!ui.input('context7').props.disabled);
});
