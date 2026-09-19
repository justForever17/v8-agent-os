const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

// Deterministic hook scheduler, not a DOM renderer. Memo/effect dependencies,
// cleanup, stable dispatch and functional updates follow React contracts.
// Only HTTP, presentation primitives and browser FormData are substituted.
function createHarness(cached, { realCache = false } = {}) {
  let active;
  const bootstrapRequests = [], requests = [], toasts = [];
  const same = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
  const React = {
    useState(initial) {
      const host = active, index = host.cursor++;
      if (!host.slots[index]) {
        const slot = { value: typeof initial === 'function' ? initial() : initial };
        slot.set = next => {
          if (host.unmounted) host.lateWrites++;
          const value = typeof next === 'function' ? next(slot.value) : next;
          if (!Object.is(value, slot.value)) { slot.value = value; host.dirty = true; }
        };
        host.slots[index] = slot;
      }
      const slot = host.slots[index];
      return [slot.value, slot.set];
    },
    useRef(initial) { return active.slots[active.cursor++] ??= { current: initial }; },
    useMemo(factory, deps) {
      const index = active.cursor++, previous = active.slots[index];
      if (!previous || !same(previous.deps, deps)) active.slots[index] = { value: factory(), deps };
      return active.slots[index].value;
    },
    useCallback(callback, deps) { return React.useMemo(() => callback, deps); },
    useEffect(effect, deps) {
      const host = active, index = host.cursor++, previous = host.slots[index];
      if (!previous || !same(previous.deps, deps)) {
        host.effects.push(() => {
          previous?.cleanup?.();
          host.slots[index] = { deps, cleanup: effect() };
        });
      }
    },
  };
  function mount(render) {
    const host = { slots: [], cursor: 0, effects: [], dirty: false, unmounted: false, lateWrites: 0 };
    host.render = () => {
      let value, rounds = 0;
      do {
        if (++rounds > 30) throw new Error('render/effect loop');
        host.cursor = 0; host.dirty = false;
        active = host;
        try { value = render(); } finally { active = undefined; }
        host.effects.splice(0).forEach(effect => effect());
      } while (host.dirty);
      return value;
    };
    host.unmount = () => { host.slots.forEach(slot => slot?.cleanup?.()); host.unmounted = true; };
    return host;
  }
  const jsx = (type, props) => ({ type, props });
  const primitives = new Proxy({}, { get: (_target, name) => name === '__esModule' ? false : String(name) });
  const modules = new Map();
  const root = path.resolve(__dirname, '../src');
  function load(name, parent = root) {
    if (name === 'react') return React;
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'Fragment' };
    if (name === '@/lib/admin-client-cache' && !realCache) return {
      peekAdminJsonCache: () => cached,
      primeAdminJsonCache: (_url, data) => { cached = data; },
      fetchAdminJson: (url, options) => {
        const pending = { ...deferred(), url, options };
        bootstrapRequests.push(pending);
        return pending.promise.then(value => { cached = value; return value; });
      },
    };
    if (name.endsWith('/LocaleProvider')) return { useT: () => key => key };
    if (name.endsWith('/use-toast')) return { useToast: () => ({ toast: item => {
      toasts.push(item); return { id: 'test', update: next => toasts.push(next) };
    } }) };
    if ((name.startsWith('@/components/') && !name.startsWith('@/components/model-hub/')) || name === 'lucide-react' || name === 'next/image') return primitives;
    let filename = name.startsWith('@/') ? path.join(root, name.slice(2)) : path.resolve(parent, name);
    if (!fs.existsSync(filename)) filename = ['.ts', '.tsx', '.json'].map(ext => filename + ext).find(fs.existsSync);
    if (!filename || !fs.existsSync(filename)) throw new Error('Unexpected import: ' + name);
    if (modules.has(filename)) return modules.get(filename).exports;
    const record = { exports: {} }; modules.set(filename, record);
    if (filename.endsWith('.json')) record.exports = JSON.parse(fs.readFileSync(filename, 'utf8'));
    else {
      const source = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
        fileName: filename,
        compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
      }).outputText;
      const fetch = (url, options) => {
        const pending = { ...deferred(), url, options };
        requests.push(pending); return pending.promise;
      };
      class FormData { constructor(fields) { this.fields = fields; } entries() { return Object.entries(this.fields); } }
      new Function('require', 'module', 'exports', 'fetch', 'FormData', source)(
        dependency => load(dependency, path.dirname(filename)), record, record.exports, fetch, FormData,
      );
    }
    return record.exports;
  }
  if (realCache && cached) load('@/lib/admin-client-cache').primeAdminJsonCache('/api/model-hub/bootstrap', cached);
  return { load, mount, bootstrapRequests, requests, toasts };
}

function nodes(tree, predicate) {
  const result = [];
  function visit(node) {
    if (Array.isArray(node)) node.forEach(visit);
    else if (node && typeof node === 'object' && 'type' in node && 'props' in node) {
      if (predicate(node)) result.push(node);
      Object.values(node.props || {}).forEach(visit);
    }
  }
  visit(tree); return result;
}
const named = (tree, name) => nodes(tree, node => node.type === name || node.type?.name === name);
const byId = (tree, id) => nodes(tree, node => node.props.id === id)[0];
const selectById = (tree, id) => named(tree, 'Select').find(node => byId(node, id));
const flush = () => new Promise(resolve => setImmediate(resolve));
function payload(id, audioConfig = { tts: { edge_tts: { voice: id } } }) {
  return {
    providers: [{ id, code: id, name: id, type: 'API', isEnabled: true, models: [] }],
    models: [{ id, providerId: id, modelId: id, type: 'TEXT', isEnabled: true }],
    hubEnvelope: { data: { models: [], providersOverview: [] } },
    audioConfig, defaultModel: { modelRef: id + '::default' }, catalog: { providers: [] },
  };
}
module.exports = { createHarness, nodes, named, byId, selectById, deferred, flush, payload };
