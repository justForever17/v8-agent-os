const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const petRoot = path.resolve(__dirname, '..');

test('desktop pet auto-connects with bounded retries and loopback-only local server', () => {
  const appSource = fs.readFileSync(path.join(petRoot, 'src', 'App.tsx'), 'utf8');
  const serverSource = fs.readFileSync(path.join(petRoot, 'server.ts'), 'utf8');

  assert.match(appSource, /ensureLocalSession/);
  assert.match(appSource, /const retryDelays = \[500, 1000, 2000, 4000, 5000\]/);
  assert.match(serverSource, /httpServer\.listen\(PORT, "127\.0\.0\.1"/);
  assert.doesNotMatch(serverSource, /httpServer\.listen\(PORT, "0\.0\.0\.0"/);
});

test('desktop pet settings and shutdown use the Shell control contract', () => {
  const mainSource = fs.readFileSync(path.join(petRoot, 'electron', 'main.cjs'), 'utf8');
  const preloadSource = fs.readFileSync(path.join(petRoot, 'electron', 'preload.cjs'), 'utf8');

  assert.match(mainSource, /SHELL_SETTINGS_DEEP_LINK = 'v8os:\/\/open\/admin\/desktop-pet'/);
  assert.match(mainSource, /shellControlClient\?\.send\('open-settings'\)/);
  assert.doesNotMatch(mainSource, /v8-desktop:open-admin'[\s\S]{0,180}shell\.openExternal\(url\)/);
  assert.match(preloadSource, /shutdownReady/);
  assert.match(preloadSource, /onActiveSession/);
});

test('desktop pet event rules use structured event ids and keep legacy text read-only', () => {
  const appSource = fs.readFileSync(path.join(petRoot, 'src', 'App.tsx'), 'utf8');

  assert.match(appSource, /candidate\.event === activity\.event/);
  assert.match(appSource, /expandLegacyDesktopPetEvents/);
  assert.doesNotMatch(appSource, /haystack\.includes\(candidate\.match/);
});
