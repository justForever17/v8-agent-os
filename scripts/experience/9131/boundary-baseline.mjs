// Actual production callbacks; only UI setters and external network/storage are replaced.
// These fixed-baseline adapters must fail loudly if candidate implementation shape changes.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import crypto from 'node:crypto';
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
const args = process.argv.slice(2);
const arg = k => args[args.indexOf(k) + 1];
if (!args.includes('--repo') || !args.includes('--out')) throw new Error('--repo and --out required');
const repo = path.resolve(arg('--repo'));
const require = createRequire(path.join(repo, 'apps/v8-agent-os-web/package.json'));
const ts = require('typescript');
const evidence = [];
const phone = 'apps/v8-agent-os-phone/src/screens/ChatScreen.tsx';
const store = 'apps/v8-agent-os-web/src/app/admin/(dashboard)/extensions/store/page.tsx';
function extract(file, name) {
  const text = fs.readFileSync(path.join(repo, file), 'utf8');
  const ast = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let found;
  function visit(n) {
    if (ts.isVariableDeclaration(n) && n.name.getText(ast) === name && ts.isCallExpression(n.initializer) && n.initializer.expression.getText(ast) === 'useCallback') found = n.initializer.arguments[0];
    if (ts.isFunctionDeclaration(n) && n.name?.text === name) found = n;
    ts.forEachChild(n, visit);
  }
  visit(ast);
  if (!found) throw new Error(`Adapter requires review: ${file}:${name} not found`);
  const original = found.getText(ast).replace(/^export\s+/, '');
  const code = ts.transpileModule(`globalThis.callback = (${original});`, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
  return { code, file, symbol: name, line: ast.getLineAndCharacterOfPosition(found.getStart(ast)).line + 1, sha256: crypto.createHash('sha256').update(text).digest('hex') };
}
function bind(extracted, bindings) {
  const context = vm.createContext({ URLSearchParams, Error, console: { warn() {} }, ...bindings });
  vm.runInContext(extracted.code, context);
  return { invoke: context.callback, context };
}
const noop = () => {};
function setters(names, state) { return Object.fromEntries(names.map(name => [name, value => { state[name] = typeof value === 'function' ? value(state[name]) : value; }])); }
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; }
function response(data) { return { ok: true, json: async () => data }; }
async function scenario(id, source, run) {
  try {
    const result = await run(source);
    evidence.push({ id, level: 'BOUNDARY_EXECUTED', source: { file: source.file, symbol: source.symbol, line: source.line, sha256: source.sha256 }, ...result, status: result.passed ? 'PASS' : 'FAIL' });
  } catch (error) { evidence.push({ id, status: 'HARNESS_ERROR', error: error.message }); }
}
await scenario('P01-same-session-draft', extract(phone, 'handleSelectConversation'), async source => {
  const state = { setInput: '合成未发送草稿 v2', setUploadedFiles: [{ id: 'attachment-a' }], setComposerSelection: { start: 2, end: 6 }, setSelectedPlugins: ['plugin-a'] };
  const before = JSON.stringify(state);
  const names = ['setHistoryOpen','setInput','setComposerSelection','setActiveQueryMode','setActiveQueryText','setUploadedFiles','setSelectedCommand','setSelectedSkills','setSelectedPlugins','setSpecModeEnabled','setWorkspaceChooserVisible','setWorkspaceInfoOpen','setNewProjectPath','setPendingContextSessionRefs'];
  const { invoke } = bind(source, { ...setters(names, state), clearNewConversationIntent: noop, activeConversationIdRef: { current: 'session-1' } });
  await invoke({ id: 'session-1', sessionId: 'session-1' });
  const preserved = JSON.parse(before);
  return { passed: Object.entries(preserved).every(([key, value]) => JSON.stringify(state[key]) === JSON.stringify(value)), expected: preserved, actual: Object.fromEntries(Object.keys(preserved).map(k => [k, state[k]])) };
});
await scenario('X02-detail-install-target-race', extract(store, 'openMcpDetail'), async source => {
  const state = {}; const pending = new Map();
  const { invoke } = bind(source, { ...setters(['setSelectedMcp','setMcpDetail','setMcpDetailError','setSelectedCandidateId','setRequirementValues','setMcpDetailLoading'], state), t: x => x, errorMessage: () => 'fixture error', fetch: url => { const d = deferred(); pending.set(new URL(url, 'http://fixture.invalid').searchParams.get('id'), d); return d.promise; } });
  const a = invoke({ id: 'mcp-A' }); const b = invoke({ id: 'mcp-B' });
  pending.get('mcp-B').resolve(response({ id: 'mcp-B', candidates: [{ id: 'candidate-B', serverName: 'B' }] })); await b;
  pending.get('mcp-A').resolve(response({ id: 'mcp-A', candidates: [{ id: 'candidate-A', serverName: 'A' }] })); await a;
  let submitted;
  const install = bind(extract(store, 'installMcp'), { mcpDetail: state.setMcpDetail, selectedCandidate: state.setMcpDetail.candidates[0], requirementValues: {}, setInstallingMcp: noop, setSelectedMcp: noop, setMcpDetail: noop, loadStore: async () => {}, toast: noop, t: x => x, errorMessage: () => 'fixture error', fetch: async (_url, options) => { submitted = JSON.parse(options.body); return response({}); } });
  await install.invoke();
  return { passed: submitted.id === 'mcp-B', expected: { selected: 'mcp-B', submitted: 'mcp-B' }, actual: { selected: state.setSelectedMcp.id, detail: state.setMcpDetail.id, submitted: submitted.id } };
});
await scenario('X03-search-out-of-order', extract(store, 'loadStore'), async source => {
  const state = {}; const pending = [];
  const bindings = { ...setters(['setLoadError','setRefreshing','setLoading','setSkills','setMcp'], state), activeTab: 'skills', t: x => x, peekAdminJsonCache: () => null, primeAdminJsonCache: noop, fetchAdminJson: () => { const d = deferred(); pending.push(d); return d.promise; } };
  const old = bind(source, { ...bindings, debouncedQuery: 'old' }).invoke();
  const current = bind(source, { ...bindings, debouncedQuery: 'current' }).invoke();
  pending[1].resolve({ query: 'current', items: [] }); await current;
  pending[0].resolve({ query: 'old', items: [] }); await old;
  return { passed: state.setSkills.query === 'current', expected: 'current', actual: state.setSkills.query };
});
await scenario('X04-independent-install-busy', extract(store, 'installSkill'), async source => {
  const state = {}; const pending = []; const completed = [];
  const { invoke } = bind(source, { ...setters(['setInstallingSkillId','setSelectedSkill'], state), loadStore: async () => {}, toast: noop, t: x => x, errorMessage: () => 'fixture error', fetch: () => { const d = deferred(); pending.push(d); return d.promise; } });
  const a = invoke({ id: 'A', source: 'fixture', skillId: 'A', name: 'A' }).then(() => completed.push('A'));
  const b = invoke({ id: 'B', source: 'fixture', skillId: 'B', name: 'B' }).then(() => completed.push('B'));
  pending[0].resolve(response({ installed: ['A'] })); await a;
  const observed = state.setInstallingSkillId; const bPending = !completed.includes('B');
  pending[1].resolve(response({ installed: ['B'] })); await b;
  return { passed: bPending && observed === 'B', expected: { pending: 'B', busy: 'B' }, actual: { pending: bPending ? 'B' : null, busy: observed } };
});
await scenario('P07-secure-storage-rejection', extract('apps/v8-agent-os-phone/src/lib/mobile-storage.ts', 'setStoredValue'), async source => {
  const { invoke } = bind(source, { Platform: { OS: 'android' }, KEYS: { activeAdminConnectionProfileId: 'synthetic-profile-key' }, SecureStore: { setItemAsync: async () => { throw new Error('synthetic native storage failure'); } } });
  let rejected = false;
  try { await invoke('activeAdminConnectionProfileId', 'profile-B'); } catch { rejected = true; }
  return { passed: rejected, expected: 'caller receives rejection and cannot claim saved', actual: rejected ? 'rejected' : 'resolved despite native failure' };
});
const report = { sourceHead: execFileSync('git', ['-C', repo, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(), fixture: 'synthetic-deferred-v1', scope: 'Extracted production callbacks with network/storage boundary doubles; no native device or product API execution.', evidence };
fs.mkdirSync(path.dirname(path.resolve(arg('--out'))), { recursive: true });
fs.writeFileSync(arg('--out'), JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify({ results: evidence.map(({ id, status }) => ({ id, status })), out: arg('--out') }));
process.exitCode = evidence.every(row => row.status === 'PASS') ? 0 : 1;
