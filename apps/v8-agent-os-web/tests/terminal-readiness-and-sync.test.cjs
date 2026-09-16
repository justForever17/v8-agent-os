const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');
const source = fs.readFileSync(path.join(__dirname, '../src/lib/terminal-instance.ts'), 'utf8');
const mod = { exports: {} };
new Function('module', 'exports', ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText)(mod, mod.exports);
const { readTerminalInstance } = mod.exports;

test('first readiness failure recovers without retrying a mutation', async () => {
  const urls = [];
  const id = await readTerminalInstance(new AbortController().signal, async url => {
    urls.push(url);
    return urls.length === 1 ? new Response('{}', { status: 503 }) : Response.json({ instanceId: 'fixture' });
  });
  assert.equal(id, 'fixture');
  assert.deepEqual(urls, ['/api/client/instance', '/api/client/instance']);
});

test('identity rejection and invalid success contract are not hidden by retry', async () => {
  for (const response of [new Response('{}', {status: 401}), new Response('{}', {status: 403}), Response.json({})]) {
    let attempts = 0;
    await assert.rejects(readTerminalInstance(new AbortController().signal, async () => { attempts++; return response; }), /identityFailed/);
    assert.equal(attempts, 1);
  }
});

test('leaving the terminal during readiness backoff cancels further requests', async () => {
  const controller = new AbortController();
  let attempts = 0;
  const pending = readTerminalInstance(controller.signal, async () => { attempts++; return new Response('{}', {status:503}); });
  setTimeout(() => controller.abort(), 10);
  await assert.rejects(pending, error => error.name === 'AbortError');
  assert.equal(attempts, 1);
});

test('late restore list cannot delete the terminal just created in the same conversation', async () => {
  const client = fs.readFileSync(path.join(__dirname, '../src/app/chat/ChatClient.tsx'), 'utf8');
  const start = 'const loadManualTerminalSessions = useCallback(async () => {';
  const callback = client.slice(client.indexOf(start) + start.length).split('}, [activeConversationId]);')[0];
  assert.match(client, /terminalListRequestRef\.current \+= 1;\s*upsertManualTerminalSession\(payload, true\)/);
  const compiled = ts.transpileModule(`export async function restore(){${callback}}`, {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
  }).outputText;
  let release;
  let sessions = [{sessionId:'created'}];
  const epoch = {current:0};
  const loaded = {exports:{}};
  new Function('module', 'exports', 'fetch', 'activeConversationId', 'activeConversationIdRef', 'terminalListRequestRef', 'setManualTerminalSessions', 'setActiveTerminalTabId', 'terminalTabIdForManualSession', compiled)(
    loaded, loaded.exports, () => new Promise(resolve => {release=resolve;}), 'mine', {current:'mine'}, epoch,
    value => {sessions=value;}, () => {}, id => id,
  );
  const restore = loaded.exports.restore();
  epoch.current += 1; // successful POST invalidates the pre-create snapshot
  release(Response.json({sessions:[]}));
  await restore;
  assert.deepEqual(sessions, [{sessionId:'created'}]);
});
