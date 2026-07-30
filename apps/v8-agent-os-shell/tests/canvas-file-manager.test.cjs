const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const shellRoot = path.resolve(__dirname, '..');

test('canvas file reveal stays inside the bound workspace and uses the native file manager', () => {
  const mainSource = fs.readFileSync(path.join(shellRoot, 'electron', 'main.cjs'), 'utf8');
  const preloadSource = fs.readFileSync(path.join(shellRoot, 'electron', 'preload.cjs'), 'utf8');

  assert.match(preloadSource, /revealWorkspaceFile/);
  assert.match(mainSource, /v8os-shell:reveal-workspace-file/);
  assert.match(mainSource, /path\.isAbsolute\(requestedRelativePath\)/);
  assert.match(mainSource, /path\.resolve\(resolvedRoot, requestedRelativePath\)/);
  assert.match(mainSource, /path\.relative\(resolvedRoot, resolvedFile\)/);
  assert.match(mainSource, /relative\.startsWith\('\.\.'\)/);
  assert.match(mainSource, /shell\.showItemInFolder\(resolvedFile\)/);
});
