const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const shellRoot = path.resolve(__dirname, '..');
const repoRoot = path.resolve(shellRoot, '..', '..');

test('shell main uses embedded CLI API instead of source-tree v8os wrappers', () => {
  const mainSource = fs.readFileSync(path.join(shellRoot, 'electron', 'main.cjs'), 'utf8');
  assert.doesNotMatch(mainSource, /v8os\.cmd/);
  assert.doesNotMatch(mainSource, /spawnSync\(['"]cmd['"]/);
  assert.doesNotMatch(mainSource, /spawn\(['"]cmd['"]/);
  assert.match(mainSource, /shell_api\.mjs/);
});

test('packaged shell starts core services before waiting for them', () => {
  const mainSource = fs.readFileSync(path.join(shellRoot, 'electron', 'main.cjs'), 'utf8');
  assert.match(mainSource, /ensureCoreServicesStarted/);
  assert.match(mainSource, /shellStart\(\['engine', 'admin', 'web'\], \{ mode: 'start' \}\)/);
  assert.match(mainSource, /await ensureCoreServicesStarted\(\);\s*await waitForServices\(\);/);
});

test('electron launcher strips ELECTRON_RUN_AS_NODE before starting child Electron apps', () => {
  const launcherSource = fs.readFileSync(path.join(shellRoot, 'scripts', 'electron-launcher.mjs'), 'utf8');
  assert.match(launcherSource, /delete env\.ELECTRON_RUN_AS_NODE/);
  assert.match(launcherSource, /windowsHide:\s*true/);
});

test('desktop release config emits unsigned Windows installer and zip artifacts', () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(shellRoot, 'package.json'), 'utf8'));
  assert.match(pkg.scripts['dist:win'], /--publish never/);
  assert.match(pkg.repository.url, /v8-agent-os\.git$/);

  const config = fs.readFileSync(path.join(shellRoot, 'electron-builder.yml'), 'utf8');
  assert.match(config, /target:\s*\n\s*- target: nsis/);
  assert.match(config, /- target: zip/);
  assert.match(config, /icon: assets\/icon\.ico/);
  assert.match(config, /extraResources:/);
  assert.match(config, /to: v8os\/apps\/v8-agent-os-engine/);
  assert.match(config, /to: v8os\/apps\/v8-agent-os-web/);
  assert.match(config, /!\.next\/dev\/\*\*/);
});

test('desktop workflow exists and publishes checksummed Windows artifacts', () => {
  const workflowPath = path.join(repoRoot, '.github', 'workflows', 'desktop-preview.yml');
  assert.equal(fs.existsSync(workflowPath), true);
  const workflow = fs.readFileSync(workflowPath, 'utf8');
  assert.match(workflow, /windows-latest/);
  assert.match(workflow, /packages\/session-realtime\/package-lock\.json/);
  assert.match(workflow, /Install shared realtime package dependencies/);
  assert.match(workflow, /working-directory: packages\/session-realtime/);
  assert.match(workflow, /npm exec -- tsc --version/);
  assert.match(workflow, /apps\/v8-agent-os-shell run dist:win/);
  assert.match(workflow, /SHA256/);
});

test('desktop preview uses a slim portable Python release profile', () => {
  const workflow = fs.readFileSync(path.join(repoRoot, '.github', 'workflows', 'desktop-preview.yml'), 'utf8');
  assert.match(workflow, /-RequirementsPath apps\/v8-agent-os-engine\/requirements\/desktop-preview\.txt/);
  assert.match(workflow, /-SkipPlaywrightBrowsers/);

  const runtimeScript = fs.readFileSync(
    path.join(repoRoot, 'scripts', 'desktop', 'prepare-windows-python-runtime.ps1'),
    'utf8',
  );
  assert.match(runtimeScript, /\[string\]\$RequirementsPath/);
  assert.match(runtimeScript, /\[switch\]\$SkipPlaywrightBrowsers/);
  assert.match(runtimeScript, /BeginOutputReadLine/);
  assert.match(runtimeScript, /BeginErrorReadLine/);
  assert.match(runtimeScript, /--prefer-binary/);
  assert.match(runtimeScript, /DEGRADED\.txt/);

  const releaseRequirements = fs.readFileSync(
    path.join(repoRoot, 'apps', 'v8-agent-os-engine', 'requirements', 'desktop-preview.txt'),
    'utf8',
  );
  for (const heavyPackage of [
    'rpaframework',
    'rpaframework-windows',
    'robotframework',
    'aiortc',
    'av',
    'soundcard',
    'patchright',
  ]) {
    assert.doesNotMatch(releaseRequirements, new RegExp(`^${heavyPackage}(?:[<=>\\[]|\\s|$)`, 'im'));
  }
});

test('Admin and Web release builds use Next standalone servers', () => {
  for (const app of ['admin', 'web']) {
    const config = fs.readFileSync(
      path.join(repoRoot, 'apps', `v8-agent-os-${app}`, 'next.config.ts'),
      'utf8',
    );
    assert.match(config, /output:\s*["']standalone["']/);
  }

  const runner = fs.readFileSync(path.join(repoRoot, 'scripts', 'run-next-with-managed-auth.mjs'), 'utf8');
  assert.match(runner, /findStandaloneServer/);
  assert.match(runner, /\.next["'], "standalone"/);
  assert.match(runner, /mode === "build"/);
  assert.match(runner, /"--webpack"/);
  assert.match(runner, /HOSTNAME:\s*"127\.0\.0\.1"/);
  assert.match(runner, /PORT:\s*port/);
  assert.match(runner, /buildHome/);
  assert.match(runner, /V8_AGENT_OS_HOME:\s*mode === "build" \? buildHome/);
});

test('desktop pet consumes packaged realtime contract instead of rebuilding workspace package', () => {
  const pkg = JSON.parse(
    fs.readFileSync(path.join(repoRoot, 'apps', 'v8-agent-os-desktop-pet', 'package.json'), 'utf8'),
  );
  assert.equal(
    pkg.dependencies['@v8/session-realtime'],
    'file:../../packages/session-realtime/v8-session-realtime-0.0.2.tgz',
  );

  const lock = JSON.parse(
    fs.readFileSync(path.join(repoRoot, 'apps', 'v8-agent-os-desktop-pet', 'package-lock.json'), 'utf8'),
  );
  assert.equal(
    lock.packages['']?.dependencies?.['@v8/session-realtime'],
    'file:../../packages/session-realtime/v8-session-realtime-0.0.2.tgz',
  );
  assert.equal(lock.packages['../../packages/session-realtime'], undefined);
});

test('Engine release process forces UTF-8 output on Windows runners', () => {
  const components = fs.readFileSync(
    path.join(repoRoot, 'apps', 'v8-agent-os-cli', 'src', 'components.mjs'),
    'utf8',
  );
  assert.match(components, /PYTHONIOENCODING:\s*"utf-8"/);
  assert.match(components, /PYTHONUTF8:\s*"1"/);
});

test('desktop release uses current desktop tag namespace and runtime probes', () => {
  const workflow = fs.readFileSync(path.join(repoRoot, '.github', 'workflows', 'desktop-preview.yml'), 'utf8');
  assert.match(workflow, /v8-os-desktop-v\*/);
  assert.match(workflow, /\^v8-os-desktop-v\(\.\+\)\$/);
  assert.match(workflow, /Verify desktop runtime payload/);
  assert.match(workflow, /verify_desktop_release_runtime\.mjs/);
  assert.match(workflow, /Installed desktop smoke/);
  assert.match(workflow, /Upload desktop smoke diagnostics/);
  assert.match(workflow, /desktop-smoke-diagnostics/);
  assert.match(workflow, /RUNTIME_PROBE\.json/);
  assert.doesNotMatch(workflow, /v8-os-desktop-preview-v/);

  const runtimeProbe = fs.readFileSync(
    path.join(repoRoot, 'apps', 'v8-agent-os-shell', 'tests', 'scripts', 'verify_desktop_release_runtime.mjs'),
    'utf8',
  );
  assert.match(runtimeProbe, /standaloneServerFor/);
  assert.match(runtimeProbe, /admin\.standaloneServer/);
  assert.match(runtimeProbe, /web\.standaloneServer/);

  const prepareRelease = fs.readFileSync(path.join(repoRoot, 'scripts', 'release', 'prepare-release.mjs'), 'utf8');
  assert.match(prepareRelease, /v8-os-\$\{product\}-v\$\{version\}/);
  assert.doesNotMatch(prepareRelease, /desktop-preview/);

  const releaseNotes = fs.readFileSync(path.join(repoRoot, 'scripts', 'release', 'generate-release-notes.mjs'), 'utf8');
  assert.match(releaseNotes, /\^v8-os-\(phone\|desktop\)-v/);
  assert.match(releaseNotes, /RUNTIME_PROBE\.json/);
  assert.doesNotMatch(releaseNotes, /desktop-preview/);

  const baseline = fs.readFileSync(path.join(repoRoot, 'docs', 'V8OS', 'V8OS_RELEASE_VERSIONING_BASELINE_ZH.md'), 'utf8');
  assert.match(baseline, /v8-os-desktop-vYYYY\.MM\.DD\.N/);
  assert.match(baseline, /RUNTIME_PROBE\.json/);
});

test('memory knowledge graph stays visible without advanced mode', () => {
  const navSource = fs.readFileSync(
    path.join(repoRoot, 'apps', 'v8-agent-os-admin', 'src', 'components', 'memory', 'MemorySectionNav.tsx'),
    'utf8',
  );
  assert.match(navSource, /!\["logs", "runtime", "config"\]\.includes\(item\.key\)/);
  assert.match(navSource, /key: "graph"/);
  assert.doesNotMatch(navSource, /"logs", "runtime", "config", "graph"/);
});
