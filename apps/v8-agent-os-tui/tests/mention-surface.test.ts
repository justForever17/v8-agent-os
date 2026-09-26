import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Client } from '../src/client.js';
import { Surface } from '../src/surface.js';
import { ViewStore } from '../src/persistence.js';

test('@ opens a grouped picker, keeps workspace files bounded, and Enter confirms only the mention', async t => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'v8-tui-mention-surface-'));
  mkdirSync(path.join(root, 'nested'), { recursive: true });
  writeFileSync(path.join(root, 'notes.md'), 'ok');
  writeFileSync(path.join(root, 'nested', 'plan.md'), 'ok');
  mkdirSync(path.join(root, 'node_modules'), { recursive: true });
  writeFileSync(path.join(root, 'node_modules', 'secret.js'), 'must not appear');
  const client = new Client(new ViewStore(root, 'mention-surface'), async (route: string) => {
    if (route === '/v1/skills/list') return { skills: [{ name: 'review', description: 'Review code' }] };
    if (route === '/v1/mcp/status') return { servers: [{ name: 'docs', enabled: true, authorized: true }] };
    if (route === '/v1/api/plugins/mentions') return { plugins: [{ id: 'calendar', authorized: true }] };
    return {};
  });
  client.instance = { instanceId: 'mention-surface' }; client.view.sessionId = ''; client.view.workspace = root;
  const surface = new Surface(client);
  t.after(() => { client.stop(); rmSync(root, { recursive: true, force: true }); });

  await surface.dispatch({ key: 'text', text: '@' });
  assert.ok(surface.mentionSuggestions);
  for (let i = 0; i < 50; i++) {
    if (surface.mentionRows(80, 10).lines.some(l => l.includes('@notes.md'))) break;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  const rows = surface.mentionRows(80, 10);
  assert.match(rows.lines.join('\n'), /@notes\.md/);
  assert.doesNotMatch(rows.lines.join('\n'), /secret\.js/);
  await surface.dispatch({ key: 'enter' });
  assert.equal(surface.input.text, '@notes.md');
  assert.equal(surface.mentionSuggestions, null);
});
