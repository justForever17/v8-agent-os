import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { parseAtReferences, workspaceReferencePath } from '../src/mentions.js';

test('@ references distinguish files, session/MCP/extension/URL refs and email text', () => {
  const refs = parseAtReferences('请看 @README.md @"docs/design notes.md" @session:old @mcp:server:uri @ext:browser @https://example.com/a.png a@b.test');
  assert.deepEqual(refs.map(ref => [ref.value, ref.kind]), [
    ['README.md', 'file'], ['docs/design notes.md', 'file'], ['session:old', 'session'],
    ['mcp:server:uri', 'mcp'], ['ext:browser', 'extension'], ['https://example.com/a.png', 'url'],
  ]);
});

test('@ file references cannot escape the selected workspace', () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'v8-tui-mentions-'));
  writeFileSync(path.join(root, 'note.md'), 'ok');
  assert.equal(workspaceReferencePath(root, 'note.md'), path.join(root, 'note.md'));
  assert.throws(() => workspaceReferencePath(root, '../secret.txt'), /当前工作区内/);
});
