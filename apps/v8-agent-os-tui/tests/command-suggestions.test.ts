import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import stringWidth from 'string-width';
import { Client } from '../src/client.js';
import { ViewStore } from '../src/persistence.js';
import { Surface } from '../src/surface.js';
import { editor, InputDecoder } from '../src/terminal.js';
import { commandMatches, suggestionRows, visibleCommandMatches } from '../src/command-suggestions.js';

function make(t: any, transport: any = async () => ({})) {
  const root = mkdtempSync(path.join(os.tmpdir(), 'v8-command-test-'));
  const client = new Client(new ViewStore(root, 'fixture'), transport);
  client.instance = { instanceId: 'fixture' }; client.view.sessionId = 'original';
  client.sessions = [{ id: 'original' }, { id: 'other' }]; client.syncCursor = 'cursor';
  t.after(() => { client.stop(); rmSync(root, { recursive: true, force: true }); });
  return { client, ui: new Surface(client) };
}
async function query(ui: Surface, text: string) {
  await ui.dispatch({ key: 'ctrl-p' }); await ui.dispatch({ key: 'text', text });
}

test('suggestions project the canonical actions, never Send; query by alias, label and purpose', t => {
  const { ui } = make(t), commands = ui.commands();
  const matches = commandMatches(commands, '');
  assert.equal(new Set(matches.map(action => action.command)).size, matches.length);
  assert.ok(matches.every(action => action.description && commands.includes(action)));
  assert.ok(!matches.some(action => action.label === '发送'));
  assert.equal(commandMatches(commands, '/EDITOR')[0].command, 'editor');
  assert.equal(commandMatches(commands, '编辑器')[0].command, 'editor');
  assert.equal(commandMatches(commands, '快捷键')[0].command, 'help');
  assert.deepEqual(commandMatches(commands, 'not-a-command'), []);
});

test('command search accepts a fuzzy subsequence without changing empty-query order', () => {
  const actions = [
    { command: 'settings', label: '设置', description: '模型与上下文' },
    { command: 'sessions', label: '会话列表', description: '搜索并恢复' },
    { command: 'stop', label: '停止', description: '查看任务' },
  ];
  assert.deepEqual(commandMatches(actions, '').map(item => item.command), ['settings', 'sessions', 'stop']);
  assert.equal(commandMatches(actions, 'stngs')[0].command, 'settings');
  assert.equal(commandMatches(actions, '/会话')[0].command, 'sessions');
});

test('visible window follows selection and reserves rows even in a 24-column terminal', t => {
  const { ui } = make(t), commands = ui.commands(), last = commandMatches(commands, '').length - 1;
  for (const width of [128, 80, 59, 40, 24]) for (const rows of [1, 2, 3, 5, 8, 20]) {
    const view = suggestionRows(commands, '', last, width, rows);
    assert.ok(view.lines.length <= rows);
    assert.ok(view.lines.filter(line => /^[› ] \//.test(line)).length <= 6);
    assert.match(view.lines[view.selectedRow], /\/help/);
    assert.ok(view.lines.slice(1).every(line => stringWidth(line) <= width));
  }
  const empty = suggestionRows(commands, 'missing', 0, 40, 5);
  assert.equal(empty.selectedRow, -1); assert.match(empty.lines.join(''), /没有匹配/);
});

test('Escape restores exact draft editor and scroll anchor; query is never persisted', async t => {
  const { client, ui } = make(t);
  client.setDraft('草稿👩‍🚀\npath/to/file'); ui.input = { ...editor(client.draft.text), cursor: 2, preferredColumn: 4, pasted: true };
  const before = { ...ui.input };
  client.view.scroll.original = { messageId: 'm4', offset: 28, lineBreaks: 2, following: false };
  ui.following = false; ui.scrollDelta = -6;
  const anchor = { ...client.view.scroll.original };
  await query(ui, 'help'); await ui.dispatch({ key: 'down' }); await ui.dispatch({ key: 'escape' });
  assert.equal(ui.suggestions, null); assert.deepEqual(ui.input, before);
  assert.deepEqual(client.view.scroll.original, anchor); assert.equal(ui.scrollDelta, -6); assert.equal(ui.following, false);
  client.save(true); const saved = readFileSync(client.store.file, 'utf8');
  assert.ok(!saved.includes('help')); assert.equal(client.draft.text, before.text);
});

test('Tab completes only; Enter runs the explicit selected catalog action', async t => {
  const { client, ui } = make(t); let invoked = 0;
  ui.help = () => { invoked++; };
  await query(ui, 'he'); await ui.dispatch({ key: 'tab' });
  assert.equal(ui.suggestions!.query.text, 'help'); assert.equal(invoked, 0);
  await ui.dispatch({ key: 'enter' }); assert.equal(invoked, 1); assert.equal(ui.suggestions, null);
  await ui.dispatch({ key: 'text', text: '/' });
  const count = visibleCommandMatches(ui.commands(), '', {
    active: client.active,
    hasAttachments: client.draft.attachments.length > 0,
    configured: client.ownerReady && Boolean(client.workspace),
  }).length;
  for (let i = 0; i < count + 2; i++) await ui.dispatch({ key: 'down' });
  assert.equal(ui.suggestions!.selected, count - 1);
  await ui.dispatch({ key: 'enter' }); assert.equal(invoked, 2);
});

test('unknown query, disabled command and F9 never fall through into send or stop', async t => {
  const { client, ui } = make(t); let sent = 0, stopped = 0;
  ui.submit = async () => { sent++; }; ui.stopRun = () => { stopped++; };
  client.setDraft('must stay unsent'); ui.input = editor(client.draft.text);
  await query(ui, 'no-matches');
  for (const key of ['enter', 'enter', 'f9', 'tab']) await ui.dispatch({ key });
  assert.equal(sent, 0); assert.equal(stopped, 0); assert.ok(ui.suggestions);
  await ui.dispatch({ key: 'escape' }); await query(ui, 'stop');
  await ui.dispatch({ key: 'enter' }); assert.equal(stopped, 0); assert.ok(ui.suggestions);
  assert.equal(client.draft.text, 'must stay unsent');
});

test('repeated Enter through a command page cannot submit the underlying draft', async t => {
  const { client, ui } = make(t); let sent = 0;
  ui.submit = async () => { sent++; };
  client.setDraft('protected draft'); ui.input = editor(client.draft.text);
  await query(ui, 'help');
  await Promise.all([ui.dispatch({ key: 'enter' }), ui.dispatch({ key: 'enter' })]);
  if (ui.page) await ui.dispatch({ key: 'enter' });
  assert.equal(ui.page, null);
  await ui.dispatch({ key: 'enter' }); await ui.dispatch({ key: 'enter' }); assert.equal(sent, 0);
  await ui.dispatch({ key: 'text', text: '!' }); await ui.dispatch({ key: 'enter' }); assert.equal(sent, 1);
  await query(ui, 'sidebar'); await ui.dispatch({ key: 'enter' });
  await ui.dispatch({ key: 'f9' }); assert.equal(sent, 2);
});

test('slashes inside text and bracketed/unbracketed paste remain exact inert draft text', async t => {
  const { ui, client } = make(t); let stopped = 0, sent = 0;
  ui.stopRun = () => { stopped++; }; ui.submit = async () => { sent++; };
  await ui.dispatch({ key: 'text', text: 'path' }); await ui.dispatch({ key: 'text', text: '/' });
  assert.equal(ui.suggestions, null); assert.equal(client.draft.text, 'path/');
  await query(ui, 'stop');
  const decoder = new InputDecoder(), payload = '/stop\n/approval\x1b[B\r\x1b[20~';
  for (const byte of Buffer.from('\x1b[200~' + payload + '\x1b[201~')) for (const event of decoder.push(Buffer.from([byte]))) await ui.dispatch(event);
  assert.equal(ui.suggestions, null); assert.equal(client.draft.text, 'path/' + payload.replace(/\r/g, '\n'));
  await ui.dispatch({ key: 'enter' }); assert.equal(sent, 0); assert.equal(stopped, 0);
  await ui.dispatch({ key: 'ctrl-c' });
  for (const event of decoder.push(Buffer.from('/stop\r/approval\r'))) await ui.dispatch(event);
  await ui.dispatch({ key: 'enter' }); assert.equal(sent, 0); assert.equal(stopped, 0);
  assert.equal(client.draft.text, '/stop\n/approval\n');
});

test('approval command opens default return; repeated Enter never changes approval mode', async t => {
  const { client, ui } = make(t); let mutations = 0;
  ui.submit = async () => { mutations++; };
  await query(ui, 'approval'); await ui.dispatch({ key: 'enter' });
  assert.equal(ui.page!.actions[ui.page!.selected].label, '返回');
  for (let i = 0; i < 3; i++) await ui.dispatch({ key: 'enter' });
  assert.equal(client.approvalMode, ''); assert.equal(mutations, 0);
});

test('busy writes permit local search/navigation but block a second mutation', async t => {
  const { ui, client } = make(t); let release: () => void = () => {}, mutations = 0;
  const pending = ui.execute(() => new Promise<void>(resolve => { release = resolve; mutations++; }));
  await query(ui, 'new'); await ui.dispatch({ key: 'enter' }); assert.ok(ui.suggestions); assert.equal(mutations, 1);
  await ui.dispatch({ key: 'escape' }); await query(ui, 'sidebar');
  const before = client.view.sidebar; await ui.dispatch({ key: 'enter' }); assert.equal(client.view.sidebar, !before);
  await query(ui, 'help'); await ui.dispatch({ key: 'enter' }); assert.equal(ui.page!.title, '帮助 / 首次安装');
  release(); await pending;
});

test('old async read cannot replace an open overlay or a subsequently selected page', async t => {
  let release: (value: any) => void = () => {};
  const { ui } = make(t, async () => new Promise(resolve => { release = resolve; }));
  const loading = ui.dispatch({ key: 'ctrl-t' });
  await query(ui, 'help'); const menu = ui.suggestions;
  release({ currentRun: { id: 'r', status: 'completed' } }); await loading;
  assert.equal(ui.page, null); assert.equal(ui.suggestions, menu);
  await ui.dispatch({ key: 'enter' }); assert.equal(ui.page!.title, '帮助 / 首次安装');
});

test('new session leaves original draft attached to its session, with no query transfer', async t => {
  const { ui, client } = make(t); client.setDraft('original-only'); ui.input = editor(client.draft.text);
  client.view.drafts.new = { text: 'new-only', attachments: [] };
  await query(ui, 'new'); await ui.dispatch({ key: 'enter' });
  assert.equal(client.view.sessionId, ''); assert.equal(client.view.drafts.original.text, 'original-only');
  assert.equal(client.draft.text, 'new-only'); assert.equal(ui.input.text, 'new-only'); assert.equal(ui.suggestions, null);
});

test('complete menu and existing shortcuts remain discoverable; query has no authority', async t => {
  const { ui } = make(t);
  await ui.dispatch({ key: 'ctrl-p' }); assert.ok(ui.suggestions);
  await ui.dispatch({ key: 'ctrl-p' }); assert.equal(ui.page!.title, '操作菜单');
  assert.ok(ui.page!.actions.some(action => action.label === '发送'));
  await ui.dispatch({ key: 'escape' }); await query(ui, 'editor'); await ui.dispatch({ key: 'f1' });
  assert.equal(ui.page!.title, '帮助 / 首次安装'); assert.equal(ui.suggestions, null);
  assert.match(ui.page!.lines.join(''), /Tab 补全/);
});

test('full command palette owns its editor, including Delete and readline controls', async t => {
  const { ui } = make(t);
  ui.palette('hel');
  assert.equal(ui.page?.title, '操作菜单');
  assert.equal(ui.paletteEditor.text, 'hel');
  ui.paletteEditor.cursor = 2;
  await ui.dispatch({ key: 'delete' });
  assert.equal(ui.paletteEditor.text, 'he');
  assert.equal(ui.page?.title, '操作菜单');
  await ui.dispatch({ key: 'ctrl-u' });
  assert.equal(ui.paletteEditor.text, '');
  await ui.dispatch({ key: 'text', text: 'help' });
  await ui.dispatch({ key: 'escape' });
  assert.equal(ui.page, null);
  assert.equal(ui.input.text, '');
});

test('language preference is persisted without entering Engine configuration', t => {
  const { client, ui } = make(t);
  ui.setLocale('en-US');
  assert.equal(client.view.locale, 'en-US');
  assert.equal(JSON.parse(readFileSync(client.store.file, 'utf8')).locale, 'en-US');
});

test('Ctrl-X delegates the active draft to the existing external editor owner', async t => {
  const { ui } = make(t);
  let opened = 0;
  ui.externalEditor = async () => { opened++; };
  await ui.dispatch({ key: 'text', text: 'draft' });
  await ui.dispatch({ key: 'ctrl-x' });
  assert.equal(opened, 1);
});

test('Backspace on an empty command query returns to the untouched draft', async t => {
  const { client, ui } = make(t);
  client.setDraft('keep this draft'); ui.input = editor(client.draft.text);
  await ui.dispatch({ key: 'ctrl-p' });
  assert.ok(ui.suggestions);
  await ui.dispatch({ key: 'backspace' });
  assert.equal(ui.suggestions, null);
  assert.equal(ui.input.text, 'keep this draft');
});

test('Backspace on an empty full palette returns to the draft instead of trapping focus', async t => {
  const { client, ui } = make(t);
  client.setDraft('keep this draft'); ui.input = editor(client.draft.text);
  ui.palette();
  await ui.dispatch({ key: 'backspace' });
  assert.equal(ui.page, null);
  assert.equal(ui.input.text, 'keep this draft');
});
