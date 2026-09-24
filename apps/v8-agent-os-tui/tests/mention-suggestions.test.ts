import test from 'node:test';
import assert from 'node:assert/strict';
import { editor } from '../src/terminal.js';
import { mentionGroups, mentionMatches, mentionReplacement, mentionSuggestionRows, mentionTokenAt, moveMentionSelection, switchMentionGroup, type MentionSuggestionState } from '../src/mention-suggestions.js';
import { Client } from '../src/client.js';
import { ViewStore } from '../src/persistence.js';
import { Surface } from '../src/surface.js';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const candidates = [
  { group: 'files' as const, value: 'src/index.ts', label: 'src/index.ts', authorized: true },
  { group: 'sessions' as const, value: 'session:s-123456', label: '会话 A', authorized: true },
  { group: 'skills' as const, value: 'skill:review', label: 'review', authorized: true },
  { group: 'agents' as const, value: 'agent:builder', label: 'Builder', authorized: true },
  { group: 'plugins' as const, value: 'plugin:calendar', label: 'Calendar', authorized: true },
  { group: 'mcp' as const, value: 'mcp:docs', label: 'docs', authorized: true },
];
const state = (overrides: Partial<MentionSuggestionState> = {}): MentionSuggestionState => ({
  query: '', group: 'all', selected: 0, candidates, loading: false, triggerStart: 0, triggerEnd: 1,
  originalText: '', originalCursor: 0, sessionId: 's', ...overrides,
});

test('mention token detection requires a whitespace boundary and preserves grapheme cursor', () => {
  assert.deepEqual(mentionTokenAt('请看 @src/🙂', 9), { start: 3, end: 9, query: 'src/🙂', token: '@src/🙂' });
  assert.equal(mentionTokenAt('email@example.com', 17), null);
  assert.equal(mentionTokenAt('@', 1)?.query, '');
});

test('@. enters the workspace file group while an email remains ordinary text', () => {
  const token = mentionTokenAt('look @.', 7);
  assert.equal(token?.query, '.');
  assert.equal(mentionMatches(state({ group: 'files', query: '.' })).length, 1);
  assert.equal(mentionTokenAt('a@b.com', 7), null);
});

test('groups filter authorized structured candidates and switch cyclically', () => {
  assert.deepEqual(mentionGroups, ['all', 'files', 'sessions', 'mcp', 'skills', 'agents', 'plugins']);
  assert.equal(mentionMatches(state({ group: 'files' })).length, 1);
  assert.equal(mentionMatches(state({ query: 'builder' })).at(0)?.value, 'agent:builder');
  assert.equal(switchMentionGroup(state({ group: 'all' }), -1).group, 'plugins');
  assert.equal(switchMentionGroup(state({ group: 'plugins' }), 1).group, 'all');
  assert.equal(moveMentionSelection(state({ group: 'all' }), 99).selected, candidates.length - 1);
});

test('tab/enter rows are bounded and replacement never reads outside the token', () => {
  const rows = mentionSuggestionRows(state({ group: 'skills' }), 32, 3);
  assert.equal(rows.lines.length, 2);
  assert.equal(rows.selectedRow, 1);
  assert.match(rows.lines[1], /@skill:review/);
  assert.deepEqual(mentionReplacement('前缀 @re 后缀', 3, 6, 'skill:review'), { text: '前缀 @skill:review 后缀', cursor: 16 });
  assert.deepEqual(editor('x').text, 'x');
});

test('Surface opens an async @ popup, keeps catalog groups, and Esc restores the original draft', async t => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'v8-mention-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  mkdirSync(path.join(root, 'src')); writeFileSync(path.join(root, 'src', 'index.ts'), 'export {}');
  const api = async (route: string) => route === '/v1/skills/list'
    ? { skills: [{ name: 'review', description: 'Review code' }], subagentFamilies: [{ familyId: 'builder', displayName: 'Builder' }] }
    : route === '/v1/api/plugins/mentions' ? { items: [{ pluginId: 'calendar', displayName: 'Calendar' }] }
      : route === '/v1/mcp/status' ? { servers: [{ name: 'docs', status: 'ready', authorized: true }] } : {};
  const client = new Client(new ViewStore(root, 'mention'), api); client.instance = { instanceId: 'mention' }; client.connection = '已连接'; client.view.workspace = root; client.view.sessionId = '';
  client.sessions = [{ id: 'session-123456', title: '已有会话', workspacePath: root }];
  const ui = new Surface(client); ui.input = editor('草稿 '); client.setDraft(ui.input.text);
  await ui.dispatch({ key: 'text', text: '@' });
  assert.ok(ui.mentionSuggestions);
  // The picker follows Qwen's loading contract: resource discovery is
  // asynchronous and the UI renders a loading state until it settles. Tests
  // observe that state transition instead of relying on a timing guess.
  await ui.waitForMentionSuggestions();
  assert.ok(ui.mentionSuggestions!.candidates.some(item => item.group === 'files'));
  assert.ok(ui.mentionSuggestions!.candidates.some(item => item.group === 'sessions'));
  assert.ok(ui.mentionSuggestions!.candidates.some(item => item.group === 'skills'));
  await ui.dispatch({ key: 'right' }); assert.equal(ui.mentionSuggestions!.group, 'files');
  await ui.dispatch({ key: 'tab' }); assert.match(client.draft.text, /@src\/index\.ts/);
  await ui.dispatch({ key: 'escape' }); assert.equal(client.draft.text, '草稿 '); assert.equal(ui.mentionSuggestions, null);
  client.stop();
});
