const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const verifier = path.join(__dirname, 'scripts', 'verify_desktop_cleanup.mjs');
const repoRoot = path.resolve(__dirname, '..', '..', '..');

function runVerifier(stateRoot) {
  return spawnSync(process.execPath, [
    verifier,
    '--state-root', stateRoot,
    '--ports', '65431,65432,65433',
    '--timeout-ms', '100',
  ], { encoding: 'utf8', windowsHide: true });
}

test('packaged cleanup verifier accepts a fully stopped runtime', (t) => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'v8os-cleanup-stopped-'));
  t.after(() => fs.rmSync(stateRoot, { recursive: true, force: true }));
  const result = runVerifier(stateRoot);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /V8OS_PACKAGED_DESKTOP_CLEANUP_OK/);
});

test('packaged cleanup verifier rejects a live managed descriptor', (t) => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'v8os-cleanup-live-'));
  t.after(() => fs.rmSync(stateRoot, { recursive: true, force: true }));
  const runtimeRoot = path.join(stateRoot, 'runtime');
  fs.mkdirSync(runtimeRoot, { recursive: true });
  fs.writeFileSync(path.join(runtimeRoot, 'shell-control.json'), JSON.stringify({ pid: process.pid }), 'utf8');
  const result = runVerifier(stateRoot);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /managed runtime state alive/);
});

test('packaged cleanup verifier rejects a live desktop pet server when its launcher pid is dead', (t) => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'v8os-cleanup-pet-server-live-'));
  t.after(() => fs.rmSync(stateRoot, { recursive: true, force: true }));
  const runtimeRoot = path.join(stateRoot, 'runtime');
  fs.mkdirSync(runtimeRoot, { recursive: true });
  fs.writeFileSync(path.join(runtimeRoot, 'desktop-pet.json'), JSON.stringify({
    pid: 2_147_483_647,
    serverPid: process.pid,
  }), 'utf8');
  const result = runVerifier(stateRoot);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /serverPid:/);
  assert.match(result.stderr, /managed runtime state alive/);
});

test('Windows cleanup requires the installed CLI and NSIS uninstaller without deleting the install tree', () => {
  const workflow = fs.readFileSync(path.join(repoRoot, '.github', 'workflows', 'desktop-preview.yml'), 'utf8');
  const cleanup = workflow.slice(
    workflow.indexOf('Cleanup Windows desktop smoke processes'),
    workflow.indexOf('Collect Windows desktop smoke diagnostics'),
  );
  assert.match(cleanup, /if \(-not \(Test-Path -LiteralPath \$installedCli -PathType Leaf\)\) \{ throw/);
  assert.match(cleanup, /\$cliExitCode = \$LASTEXITCODE/);
  assert.match(cleanup, /if \(\$cliExitCode -ne 0\) \{ throw/);
  assert.match(cleanup, /if \(\$uninstallers\.Count -ne 1\) \{ throw/);
  assert.match(cleanup, /Windows uninstaller did not remove the installation directory/);
  assert.doesNotMatch(cleanup, /Remove-Item -LiteralPath \$env:V8OS_WINDOWS_INSTALL_DIR -Recurse/);
});
