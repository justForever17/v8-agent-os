const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
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
  assert.match(mainSource, /require\(['"]node:url['"]\)/);
  assert.match(mainSource, /shell_api\.mjs/);
});

test('packaged shell starts core services before waiting for them', () => {
  const mainSource = fs.readFileSync(path.join(shellRoot, 'electron', 'main.cjs'), 'utf8');
  assert.match(mainSource, /ensureCoreServicesStarted/);
  assert.match(mainSource, /shellStart\(\['engine', 'admin', 'web'\], \{ mode: 'start' \}\)/);
  assert.match(mainSource, /await ensureCoreServicesStarted\(\);[\s\S]*await waitForServices\(\);/);
  assert.match(mainSource, /Promise\.all\(\[/);
  assert.match(mainSource, /\$\{engineBaseUrl\}\/readyz/);
  assert.doesNotMatch(mainSource, /\$\{engineBaseUrl\}\/health/);
  assert.match(mainSource, /fetchTextWithTimeout/);
  assert.match(mainSource, /credentials:\s*'omit'/);
  assert.match(mainSource, /validateReadinessResponse/);
  assert.match(mainSource, /kind: 'engine'/);
  assert.match(mainSource, /kind: 'admin'/);
  assert.match(mainSource, /kind: 'web'/);
  assert.match(mainSource, /!\['started', 'already_running'\]\.includes\(item\.status\)/);
  assert.match(mainSource, /核心服务启动失败/);
});

test('shell recovers a failed local surface without an unbounded reload loop', () => {
  const mainSource = fs.readFileSync(path.join(shellRoot, 'electron', 'main.cjs'), 'utf8');
  assert.match(mainSource, /render-process-gone/);
  assert.match(mainSource, /did-fail-load/);
  assert.match(mainSource, /MAX_SURFACE_RECOVERY_ATTEMPTS = 2/);
  assert.match(mainSource, /surfaceRecoveryTimes\.length >= MAX_SURFACE_RECOVERY_ATTEMPTS/);
  assert.match(mainSource, /surfaceStabilityTimer = setTimeout/);
  assert.match(mainSource, /void loadInitialSurface\(\)/);
  assert.match(mainSource, /界面连续恢复失败/);
});

test('shell uses dedicated taskbar and tray icon assets', () => {
  const mainSource = fs.readFileSync(path.join(shellRoot, 'electron', 'main.cjs'), 'utf8');
  assert.match(mainSource, /function taskbarIconPath\(\)/);
  assert.match(mainSource, /function trayIconPath\(\)/);
  assert.match(mainSource, /new Tray\(shellIcon\(\)\)/);
  assert.match(mainSource, /icon: taskbarIconPath\(\) \|\| undefined/);
  assert.equal(fs.existsSync(path.join(shellRoot, 'assets', 'tray-icon.png')), true);
});

test('desktop traffic lights use centered softened vector glyphs', () => {
  const controlsSource = fs.readFileSync(
    path.join(repoRoot, 'packages', 'product-ui', 'src', 'ProductTrafficLightWindowControls.tsx'),
    'utf8',
  );
  const styles = fs.readFileSync(
    path.join(repoRoot, 'packages', 'product-ui', 'src', 'styles.css'),
    'utf8',
  );

  assert.match(controlsSource, /<svg viewBox="0 0 10 10"/);
  assert.doesNotMatch(controlsSource, />[×−+]<\/span>/);
  assert.match(styles, /\.v8-product-traffic-light svg/);
  assert.match(styles, /stroke-linecap:\s*round/);
  assert.match(styles, /opacity:\s*0\.58/);
  assert.match(styles, /place-items:\s*center/);
});

test('desktop traffic lights follow Windows action order and reflect maximize state', () => {
  const controlsSource = fs.readFileSync(
    path.join(repoRoot, 'packages', 'product-ui', 'src', 'ProductTrafficLightWindowControls.tsx'),
    'utf8',
  );
  const styles = fs.readFileSync(
    path.join(repoRoot, 'packages', 'product-ui', 'src', 'styles.css'),
    'utf8',
  );
  const minimizeIndex = controlsSource.indexOf('v8-product-traffic-light--minimize');
  const maximizeIndex = controlsSource.indexOf('v8-product-traffic-light--maximize');
  const closeIndex = controlsSource.indexOf('v8-product-traffic-light--close');

  assert.ok(minimizeIndex >= 0 && minimizeIndex < maximizeIndex && maximizeIndex < closeIndex);
  assert.match(controlsSource, /kind=\{isMaximized \? "restore" : "maximize"\}/);
  assert.match(styles, /traffic-light--minimize \{ background: #28c840; \}/);
  assert.match(styles, /traffic-light--maximize \{ background: #febc2e; \}/);
  assert.match(styles, /traffic-light--close \{ background: #ff5f57; \}/);

  const preloadSource = fs.readFileSync(path.join(shellRoot, 'electron', 'preload.cjs'), 'utf8');
  const mainSource = fs.readFileSync(path.join(shellRoot, 'electron', 'main.cjs'), 'utf8');
  assert.match(preloadSource, /getWindowState/);
  assert.match(preloadSource, /onWindowStateChange/);
  assert.match(preloadSource, /openWorkspaceFolder/);
  assert.match(preloadSource, /selectGodotExecutable/);
  assert.match(preloadSource, /selectGodotProjectDirectory/);
  assert.match(mainSource, /v8os-shell:get-window-state/);
  assert.match(mainSource, /v8os-shell:open-workspace-folder/);
  assert.match(mainSource, /shell\.openPath\(resolvedPath\)/);
  assert.match(mainSource, /v8os-shell:select-godot-executable/);
  assert.match(mainSource, /v8os-shell:select-godot-project-directory/);
  assert.match(mainSource, /properties: \['openDirectory'\]/);
  assert.match(mainSource, /mainWindow\.on\('maximize', emitWindowState\)/);
});

test('electron launcher strips ELECTRON_RUN_AS_NODE before starting child Electron apps', () => {
  const launcherSource = fs.readFileSync(path.join(shellRoot, 'scripts', 'electron-launcher.mjs'), 'utf8');
  assert.match(launcherSource, /delete env\.ELECTRON_RUN_AS_NODE/);
  assert.match(launcherSource, /windowsHide:\s*true/);
});

test('desktop release scripts keep preview installer-only and stable portable output', () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(shellRoot, 'package.json'), 'utf8'));
  assert.equal(pkg.scripts['dist:win'], 'npm run dist:win:preview');
  assert.match(pkg.scripts['dist:win:preview'], /--win nsis --publish never/);
  assert.doesNotMatch(pkg.scripts['dist:win:preview'], /\bzip\b/);
  assert.match(pkg.scripts['dist:win:stable'], /--win nsis zip --publish never/);
  assert.match(pkg.repository.url, /v8-agent-os\.git$/);

  const config = fs.readFileSync(path.join(shellRoot, 'electron-builder.yml'), 'utf8');
  assert.match(config, /target:\s*\n\s*- target: nsis/);
  assert.doesNotMatch(config, /- target: zip/);
  assert.match(config, /icon: assets\/icon\.ico/);
  assert.match(config, /extraResources:/);
  assert.match(config, /to: v8os\/apps\/v8-agent-os-engine/);
  assert.match(config, /to: v8os\/apps\/v8-agent-os-web/);
  assert.match(config, /!\.next\/dev\/\*\*/);
  assert.match(config, /!native\/\*\*\/target\/\*\*/);
});

test('desktop release notes only advertise portable archives on stable', () => {
  const generator = path.join(repoRoot, 'scripts', 'release', 'generate-release-notes.mjs');
  const commonArgs = ['--product', 'desktop', '--version', '2026.07.22.1'];
  const preview = execFileSync(process.execPath, [generator, ...commonArgs, '--channel', 'preview'], {
    cwd: repoRoot,
    encoding: 'utf8',
    env: { ...process.env, GITHUB_REF_NAME: 'main' },
  });
  const stable = execFileSync(process.execPath, [generator, ...commonArgs, '--channel', 'stable'], {
    cwd: repoRoot,
    encoding: 'utf8',
    env: { ...process.env, GITHUB_REF_NAME: 'main' },
  });

  assert.match(preview, /win-x64-setup\.exe/);
  assert.doesNotMatch(preview, /win-x64\.zip/);
  assert.match(stable, /win-x64-setup\.exe/);
  assert.match(stable, /win-x64\.zip/);
});

test('desktop release preparation verifies every packed workspace tarball', () => {
  const prepare = path.join(repoRoot, 'scripts', 'release', 'prepare-release.mjs');
  const output = execFileSync(process.execPath, [
    prepare,
    '--product',
    'desktop',
    '--version',
    '2099.01.01.1',
    '--channel',
    'preview',
  ], {
    cwd: repoRoot,
    encoding: 'utf8',
  });

  assert.match(output, /Desktop local tarball integrity OK/);
  assert.match(output, /@v8\/product-ui/);
  assert.match(output, /@v8\/session-realtime/);
});

test('desktop workflow exists and publishes checksummed Windows artifacts', () => {
  const workflowPath = path.join(repoRoot, '.github', 'workflows', 'desktop-preview.yml');
  assert.equal(fs.existsSync(workflowPath), true);
  const workflow = fs.readFileSync(workflowPath, 'utf8');
  assert.match(workflow, /windows-latest/);
  assert.match(workflow, /packages\/session-realtime\/package-lock\.json/);
  assert.match(workflow, /packages\/product-ui\/\*\*/);
  assert.match(workflow, /packages\/product-ui\/package-lock\.json/);
  assert.match(workflow, /Build and verify shared Product UI package/);
  assert.match(workflow, /verify-product-ui-package\.mjs --verify-build/);
  assert.match(workflow, /Install shared realtime package dependencies/);
  assert.match(workflow, /Build native engineering sandbox host/);
  assert.match(workflow, /build-sandbox-host\.mjs --force/);
  assert.match(workflow, /working-directory: packages\/session-realtime/);
  assert.match(workflow, /npm exec -- tsc --version/);
  assert.match(workflow, /apps\/v8-agent-os-shell run dist:win:preview/);
  assert.doesNotMatch(workflow, /dist\/release\/\*\.zip/);
  assert.doesNotMatch(workflow, /desktop-preview-artifacts\/\*\.zip/);
  assert.match(workflow, /SHA256/);
});

test('desktop workflow uses free-tier guardrails and keeps release permissions scoped', () => {
  const workflow = fs.readFileSync(path.join(repoRoot, '.github', 'workflows', 'desktop-preview.yml'), 'utf8');

  assert.match(workflow, /concurrency:\s*\n\s+group: desktop-preview-/);
  assert.match(workflow, /permissions:\s*\n\s+contents: read/);
  assert.match(workflow, /desktop-contract:/);
  assert.match(workflow, /Desktop release contract tests/);
  assert.match(workflow, /node --test apps\/v8-agent-os-shell\/tests\/release-shell\.test\.cjs/);
  assert.match(workflow, /node --test apps\/v8-agent-os-admin\/tests\/feature-pack-ui-contract\.test\.cjs/);
  assert.match(workflow, /windows-preview:[\s\S]*if: github\.event_name == 'workflow_dispatch' \|\| startsWith\(github\.ref, 'refs\/tags\/v8-os-desktop-v'\)/);
  assert.match(workflow, /windows-preview:[\s\S]*permissions:\s*\n\s+contents: write/);
  assert.match(workflow, /Create GitHub release[\s\S]*softprops\/action-gh-release@v2/);
  assert.match(workflow, /Upload desktop preview artifacts\s*\n\s+if: \$\{\{ !startsWith/);
  assert.match(workflow, /Upload desktop smoke diagnostics[\s\S]*continue-on-error: true/);
  assert.doesNotMatch(workflow, /actions\/download-artifact@v4/);
  assert.match(workflow, /retention-days: 7/);
  assert.match(workflow, /compression-level: 0/);
});

test('phone workflow honors dispatch inputs while preserving the proven Android tag release path', () => {
  const workflow = fs.readFileSync(path.join(repoRoot, '.github', 'workflows', 'phone-build.yml'), 'utf8');
  assert.match(workflow, /platform:\s*\n\s+description: Target platform[\s\S]*?- android[\s\S]*?- ios[\s\S]*?- all/);
  assert.match(workflow, /EAS_BUILD_PROFILE: \$\{\{ github\.event\.inputs\.profile \|\| 'preview' \}\}/);
  assert.match(workflow, /build-android:[\s\S]*?--platform android[\s\S]*?--profile "\$EAS_BUILD_PROFILE"/);
  assert.match(workflow, /build-ios:[\s\S]*?if: github\.event_name == 'workflow_dispatch' && \(github\.event\.inputs\.platform == 'ios' \|\| github\.event\.inputs\.platform == 'all'\)[\s\S]*?runs-on: macos-latest[\s\S]*?--platform ios[\s\S]*?--profile "\$EAS_BUILD_PROFILE"/);
  assert.match(workflow, /release:\s*\n\s+name: Publish Phone release\s*\n\s+needs:\s*\n\s+- build-android\s*\n\s+- build-ios/);
  assert.match(workflow, /release:[\s\S]*?needs\.build-android\.result == 'success'[\s\S]*?needs\.build-ios\.result == 'skipped'/);
  assert.match(workflow, /actions\/download-artifact@v4/);
  assert.match(workflow, /Prepare phone release assets/);
  assert.match(workflow, /Create GitHub release[\s\S]*softprops\/action-gh-release@v2/);
  assert.match(workflow, /apps\/v8-agent-os-phone\/V8OS-Phone-\*-android-preview\.apk/);

  const releaseIndex = workflow.indexOf('\n  release:');
  assert.notEqual(releaseIndex, -1);
  const buildJobs = workflow.slice(0, releaseIndex);
  assert.doesNotMatch(buildJobs, /softprops\/action-gh-release/);
  assert.doesNotMatch(buildJobs, /contents: write/);
});

test('phone release notes retain the Android-only tag distribution contract', () => {
  const generator = path.join(repoRoot, 'scripts', 'release', 'generate-release-notes.mjs');
  const notes = execFileSync(process.execPath, [generator, '--product', 'phone', '--version', '2026.08.07.1'], {
    cwd: repoRoot,
    encoding: 'utf8',
  });
  assert.match(notes, /V8OS-Phone-2026\.08\.07\.1-android-preview\.apk/);
  assert.doesNotMatch(notes, /ios-preview\.ipa/);
  assert.match(notes, /不随 Phone tag 发布/);
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
  assert.match(runtimeScript, /StandardOutput\.ReadToEndAsync\(\)/);
  assert.match(runtimeScript, /StandardError\.ReadToEndAsync\(\)/);
  assert.match(runtimeScript, /Invoke-WebRequestWithRetry/);
  assert.match(runtimeScript, /Invoke-CheckedWithRetry/);
  assert.match(runtimeScript, /Install desktop preview Engine requirements/);
  assert.match(runtimeScript, /--prefer-binary/);
  assert.match(runtimeScript, /DEGRADED\.txt/);
  assert.match(runtimeScript, /discovers an installed Edge, Chrome, or Chromium at runtime/);

  const releaseRequirements = fs.readFileSync(
    path.join(repoRoot, 'apps', 'v8-agent-os-engine', 'requirements', 'desktop-preview.txt'),
    'utf8',
  );
  for (const heavyPackage of [
    'rpaframework',
    'rpaframework-windows',
    'robotframework',
    'mss',
    'pywinauto',
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
  assert.match(runner, /\.next["'], "static"/);
  assert.match(runner, /path\.join\(appDir, "public"\)/);
  assert.match(runner, /fs\.cpSync\(source, target, \{ recursive: true \}\)/);
  assert.match(runner, /windowsHide:\s*true/);
  assert.match(runner, /mode === "build"/);
  assert.match(runner, /"--webpack"/);
  assert.match(runner, /V8_ADMIN_HOSTNAME/);
  assert.match(runner, /\|\| "::"/);
  assert.match(runner, /: "127\.0\.0\.1"/);
  assert.match(runner, /HOSTNAME:\s*runtimeHostname/);
  assert.match(runner, /PORT:\s*port/);
  assert.match(runner, /buildHome/);
  assert.match(runner, /V8_AGENT_OS_HOME:\s*mode === "build" \? buildHome/);
});

test('Phone pairing exposes Admin on LAN without advertising wildcard bind hosts', () => {
  const runtimeConfig = fs.readFileSync(
    path.join(repoRoot, 'apps', 'v8-agent-os-admin', 'src', 'lib', 'server', 'runtime-config.ts'),
    'utf8',
  );
  assert.match(runtimeConfig, /const NON_ROUTABLE_CLIENT_HOSTS = new Set\(\[/);
  assert.match(runtimeConfig, /"0\.0\.0\.0"/);
  assert.match(runtimeConfig, /"\[::\]"/);
  assert.match(runtimeConfig, /!NON_ROUTABLE_CLIENT_HOSTS\.has\(parsed\.hostname \|\| ""\)/);
});

test('desktop pet consumes packaged realtime contract instead of rebuilding workspace package', () => {
  const realtimePkg = JSON.parse(
    fs.readFileSync(path.join(repoRoot, 'packages', 'session-realtime', 'package.json'), 'utf8'),
  );
  const packagedDependency = `file:../../packages/session-realtime/v8-session-realtime-${realtimePkg.version}.tgz`;
  const pkg = JSON.parse(
    fs.readFileSync(path.join(repoRoot, 'apps', 'v8-agent-os-desktop-pet', 'package.json'), 'utf8'),
  );
  assert.equal(
    pkg.dependencies['@v8/session-realtime'],
    packagedDependency,
  );

  const lock = JSON.parse(
    fs.readFileSync(path.join(repoRoot, 'apps', 'v8-agent-os-desktop-pet', 'package-lock.json'), 'utf8'),
  );
  assert.equal(
    lock.packages['']?.dependencies?.['@v8/session-realtime'],
    packagedDependency,
  );
  assert.equal(fs.existsSync(path.join(repoRoot, 'packages', 'session-realtime', `v8-session-realtime-${realtimePkg.version}.tgz`)), true);
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
  assert.match(workflow, /Print desktop smoke service logs/);
  assert.match(workflow, /\.v8-agent-os\\logs\\cli/);
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
  assert.match(runtimeProbe, /installedSystemBrowser/);
  assert.match(runtimeProbe, /agentBrowser\.compatibleBrowser/);
  assert.match(runtimeProbe, /engine\.importMain/);
  assert.match(runtimeProbe, /V8OS_ENGINE_IMPORT_OK/);
  assert.match(runtimeProbe, /V8OS does not download one at runtime/);
  assert.match(runtimeProbe, /minimumFfmpegVersion = \[7, 0\]/);
  assert.match(runtimeProbe, /mediaToolVersion\("ffmpeg"\)/);
  assert.match(runtimeProbe, /mediaToolVersion\("ffprobe"\)/);
  const requiredModulesBlock = runtimeProbe.slice(
    runtimeProbe.indexOf('const requiredModules'),
    runtimeProbe.indexOf('const optionalModules'),
  );
  const optionalModulesBlock = runtimeProbe.slice(
    runtimeProbe.indexOf('const optionalModules'),
    runtimeProbe.indexOf('const moduleResult'),
  );
  assert.doesNotMatch(requiredModulesBlock, /pywinauto/);
  assert.match(optionalModulesBlock, /pywinauto/);

  const installSmoke = fs.readFileSync(
    path.join(repoRoot, 'apps', 'v8-agent-os-shell', 'tests', 'scripts', 'run_desktop_install_smoke.mjs'),
    'utf8',
  );
  assert.match(installSmoke, /featurePackApi/);
  assert.match(installSmoke, /featurePackState/);
  assert.match(installSmoke, /failureStage/);
  assert.doesNotMatch(installSmoke, /stdout|stderr/);

  const portablePython = fs.readFileSync(
    path.join(repoRoot, 'scripts', 'desktop', 'prepare-windows-python-runtime.ps1'),
    'utf8',
  );
  assert.match(portablePython, /\$updatedLines\.Insert\(1, "\.\."\)/);
  assert.match(portablePython, /V8OS_ENGINE_IMPORT_OK/);

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
