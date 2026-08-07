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

test('desktop release scripts build native installers for every supported desktop target', () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(shellRoot, 'package.json'), 'utf8'));
  assert.equal(pkg.scripts['dist:win'], 'npm run dist:win:preview');
  assert.match(pkg.scripts['dist:win:preview'], /--win nsis --publish never/);
  assert.doesNotMatch(pkg.scripts['dist:win:preview'], /\bzip\b/);
  assert.match(pkg.scripts['dist:win:stable'], /--win nsis zip --publish never/);
  assert.match(pkg.scripts['dist:mac:preview'], /--mac dmg --publish never/);
  assert.match(pkg.scripts['dist:linux:preview'], /--linux AppImage deb --publish never/);
  assert.match(pkg.repository.url, /v8-agent-os\.git$/);
  assert.equal(pkg.author?.email, 'justforever17@users.noreply.github.com');
  assert.equal(pkg.desktopName, 'v8-agent-os.desktop');

  const config = fs.readFileSync(path.join(shellRoot, 'electron-builder.yml'), 'utf8');
  assert.match(config, /target:\s*\n\s*- target: nsis/);
  assert.match(config, /win:[\s\S]*?arch:\s*\n\s*- x64\s*\n\s*- arm64/);
  assert.doesNotMatch(config, /- target: zip/);
  assert.match(config, /icon: assets\/icon\.ico/);
  assert.match(config, /mac:\s*\n\s+icon: assets\/icon\.icns/);
  assert.match(config, /target: dmg/);
  assert.match(config, /linux:\s*\n\s+icon: assets\/icon\.png/);
  assert.match(config, /maintainer: V8 Agent OS <justforever17@users\.noreply\.github\.com>/);
  assert.match(config, /syncDesktopName: true/);
  assert.match(config, /target: AppImage/);
  assert.match(config, /target: deb/);
  assert.match(config, /at-spi2-core/);
  assert.match(config, /xdotool/);
  assert.match(config, /wmctrl/);
  assert.match(config, /xclip/);
  assert.match(config, /xsel/);
  assert.match(config, /extraResources:/);
  assert.match(config, /to: v8os\/apps\/v8-agent-os-engine/);
  assert.match(config, /to: v8os\/apps\/v8-agent-os-web/);
  assert.match(config, /!\.next\/dev\/\*\*/);
  assert.match(config, /!native\/\*\*\/target\/\*\*/);
});

test('desktop release notes advertise the multi-platform unsigned preview assets', () => {
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
  assert.match(preview, /win-arm64-setup\.exe/);
  assert.match(preview, /macos-x64\.dmg/);
  assert.match(preview, /macos-arm64\.dmg/);
  assert.match(preview, /linux-x64\.AppImage/);
  assert.match(preview, /linux-arm64\.deb/);
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

test('desktop workflow builds every native platform and uploads checksummed build artifacts', () => {
  const workflowPath = path.join(repoRoot, '.github', 'workflows', 'desktop-preview.yml');
  assert.equal(fs.existsSync(workflowPath), true);
  const workflow = fs.readFileSync(workflowPath, 'utf8');
  assert.match(workflow, /packages\/session-realtime\/package-lock\.json/);
  assert.match(workflow, /packages\/product-ui\/\*\*/);
  assert.match(workflow, /packages\/product-ui\/package-lock\.json/);
  assert.match(workflow, /NPM_CONFIG_FETCH_RETRIES: "5"/);
  assert.match(workflow, /NPM_CONFIG_FETCH_TIMEOUT: "300000"/);
  assert.match(workflow, /Build and verify shared Product UI package/);
  assert.match(workflow, /verify-product-ui-package\.mjs --verify-build/);
  assert.match(workflow, /Install shared realtime package dependencies/);
  assert.match(workflow, /Build native engineering sandbox host/);
  assert.match(workflow, /build-sandbox-host\.mjs --force/);
  assert.match(workflow, /working-directory: packages\/session-realtime/);
  assert.match(workflow, /npm exec -- tsc --version/);
  assert.match(workflow, /apps\/v8-agent-os-shell run dist:win:preview/);
  assert.match(workflow, /--\$\{\{ matrix\.arch \}\}/);
  assert.match(workflow, /apps\/v8-agent-os-shell run dist:mac:preview/);
  assert.match(workflow, /apps\/v8-agent-os-shell run dist:linux:preview/);
  assert.match(workflow, /prepare-posix-python-runtime\.mjs/);
  assert.match(workflow, /resolve-desktop-build-matrix\.mjs/);
  assert.match(workflow, /resolve-desktop-platforms:[\s\S]*?uses: actions\/checkout@v4/);
  assert.match(workflow, /build-macos-ax-helper\.mjs/);
  assert.match(workflow, /verify-desktop-package-layout\.mjs/);
  assert.match(workflow, /prepare-desktop-release-assets\.mjs/);
  assert.doesNotMatch(workflow, /dist\/release\/\*\.zip/);
  assert.doesNotMatch(workflow, /desktop-preview-artifacts\/\*\.zip/);
  assert.match(workflow, /SHA256/);

  const resolver = path.join(repoRoot, 'scripts', 'desktop', 'resolve-desktop-build-matrix.mjs');
  const resolverSource = fs.readFileSync(resolver, 'utf8');
  assert.match(resolverSource, /windows-latest/);
  assert.match(resolverSource, /windows-11-arm/);
  assert.match(resolverSource, /pythonArch: "arm64"/);
  assert.match(resolverSource, /macos-15-intel/);
  assert.match(resolverSource, /runner: "macos-15"/);
  assert.match(resolverSource, /ubuntu-24\.04-arm/);
  const selected = JSON.parse(execFileSync(process.execPath, [
    resolver,
    '--event', 'workflow_dispatch',
    '--ref', 'refs/heads/main',
    '--platform', 'linux-arm64',
  ], { encoding: 'utf8' }));
  assert.equal(selected.enabled, true);
  assert.deepEqual(selected.matrix.include.map((target) => target.id), ['linux-arm64']);
  const windowsArm = JSON.parse(execFileSync(process.execPath, [
    resolver,
    '--event', 'workflow_dispatch',
    '--ref', 'refs/heads/main',
    '--platform', 'windows-arm64',
  ], { encoding: 'utf8' }));
  assert.deepEqual(windowsArm.matrix.include.map((target) => target.id), ['windows-arm64']);
  assert.equal(windowsArm.matrix.include[0].runner, 'windows-11-arm');
  assert.equal(windowsArm.matrix.include[0].pythonArch, 'arm64');
  const tag = JSON.parse(execFileSync(process.execPath, [
    resolver,
    '--event', 'push',
    '--ref', 'refs/tags/v8-os-desktop-v2099.01.01.1',
    '--platform', 'windows-x64',
  ], { encoding: 'utf8' }));
  assert.equal(tag.enabled, true);
  assert.equal(tag.matrix.include.length, 6);
});

test('desktop workflow uses fan-in publication with narrowly scoped release permissions', () => {
  const workflow = fs.readFileSync(path.join(repoRoot, '.github', 'workflows', 'desktop-preview.yml'), 'utf8');

  assert.match(workflow, /concurrency:[\s\S]*?group: desktop-preview-/);
  assert.match(workflow, /github\.event\.inputs\.platform \|\| 'all'/);
  assert.match(workflow, /permissions:\s*\n\s+contents: read/);
  assert.match(workflow, /desktop-contract:/);
  assert.match(workflow, /Desktop release contract tests/);
  assert.match(workflow, /node --test apps\/v8-agent-os-shell\/tests\/release-shell\.test\.cjs/);
  assert.match(workflow, /node --test apps\/v8-agent-os-admin\/tests\/feature-pack-ui-contract\.test\.cjs/);
  assert.match(workflow, /desktop-package:[\s\S]*strategy:\s*\n\s+fail-fast: false/);
  assert.match(workflow, /release:\s*\n\s+name: Publish Desktop release[\s\S]*needs:\s*\n\s+- desktop-package/);
  assert.match(workflow, /release:[\s\S]*permissions:\s*\n\s+contents: write/);
  assert.match(workflow, /Create GitHub release[\s\S]*softprops\/action-gh-release@v2/);
  assert.match(workflow, /Download all platform artifacts[\s\S]*actions\/download-artifact@v4/);
  assert.match(workflow, /merge-desktop-release-assets\.mjs/);
  assert.match(workflow, /Upload Windows desktop smoke diagnostics[\s\S]*continue-on-error: true/);
  assert.match(workflow, /retention-days: 7/);
  assert.match(workflow, /compression-level: 0/);
  assert.match(workflow, /Verify Windows runner and Node architecture/);
  assert.match(workflow, /Verify Windows Rust native target/);
  assert.match(workflow, /-Architecture "\$\{\{ matrix\.pythonArch \}\}"/);
  assert.ok(
    workflow.indexOf('Prepare embedded Engine Python runtime on Windows') <
      workflow.indexOf('Install Admin dependencies'),
    'platform Python runtime validation must run before expensive product dependency installs',
  );
  const releaseJob = workflow.slice(workflow.indexOf('\n  release:'));
  assert.doesNotMatch(releaseJob, /desktop-release-assets\/RUNTIME_PROBE-\*\.json/);
  assert.doesNotMatch(releaseJob, /desktop-release-assets\/PACKAGE_LAYOUT-\*\.json/);
});

test('phone workflow honors dispatch inputs while preserving the proven Android tag release path', () => {
  const workflow = fs.readFileSync(path.join(repoRoot, '.github', 'workflows', 'phone-build.yml'), 'utf8');
  assert.match(workflow, /platform:\s*\n\s+description: Target platform[\s\S]*?- android[\s\S]*?- ios[\s\S]*?- all/);
  assert.match(workflow, /EAS_BUILD_PROFILE: \$\{\{ github\.event\.inputs\.profile \|\| 'preview' \}\}/);
  assert.match(workflow, /build-android:[\s\S]*?--platform android[\s\S]*?--profile "\$EAS_BUILD_PROFILE"/);
  assert.match(workflow, /build-ios:[\s\S]*?if: github\.event_name == 'workflow_dispatch' && \(github\.event\.inputs\.platform == 'ios' \|\| github\.event\.inputs\.platform == 'all'\)[\s\S]*?runs-on: macos-latest[\s\S]*?--platform ios[\s\S]*?--profile "\$EAS_BUILD_PROFILE"/);
  assert.match(workflow, /Verify phone release manifest[\s\S]*?verify-release-manifest\.mjs[\s\S]*?--product phone[\s\S]*?--tag "\$GITHUB_REF_NAME"[\s\S]*?--manifest \.\.\/\.\.\/release-manifest\.json/);
  assert.match(workflow, /release:\s*\n\s+name: Publish Phone release\s*\n\s+needs:\s*\n\s+- build-android\s*\n\s+- build-ios/);
  assert.match(workflow, /release:[\s\S]*?needs\.build-android\.result == 'success'[\s\S]*?needs\.build-ios\.result != 'cancelled'/);
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
    env: {
      ...process.env,
      GITHUB_REF_NAME: 'v8-os-desktop-v2026.08.07.2',
    },
  });
  assert.match(notes, /V8OS-Phone-2026\.08\.07\.1-android-preview\.apk/);
  assert.doesNotMatch(notes, /ios-preview\.ipa/);
  assert.match(notes, /不随 Phone tag 发布/);
});

test('desktop preview uses a slim portable Python release profile', () => {
  const workflow = fs.readFileSync(path.join(repoRoot, '.github', 'workflows', 'desktop-preview.yml'), 'utf8');
  assert.match(workflow, /-RequirementsPath apps\/v8-agent-os-engine\/requirements\/desktop-preview\.txt/);
  assert.match(workflow, /-SkipPlaywrightBrowsers/);
  assert.match(workflow, /--skip-playwright-browsers/);

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
  assert.match(runtimeScript, /Unsupported portable Python architecture/);
  assert.match(runtimeScript, /\$expectedMachine/);
  assert.match(runtimeScript, /DEGRADED\.txt/);
  assert.match(runtimeScript, /discovers an installed Edge, Chrome, or Chromium at runtime/);

  const posixRuntimeScript = fs.readFileSync(
    path.join(repoRoot, 'scripts', 'desktop', 'prepare-posix-python-runtime.mjs'),
    'utf8',
  );
  assert.match(posixRuntimeScript, /cpython-3\.11\.15\+20260805-x86_64-apple-darwin-install_only/);
  assert.match(posixRuntimeScript, /sha256/);
  assert.match(posixRuntimeScript, /--skip-playwright-browsers/);
  assert.match(posixRuntimeScript, /V8OS_ENGINE_IMPORT_OK/);
  assert.match(posixRuntimeScript, /process\.platform !== runtime\.platform/);
  assert.match(posixRuntimeScript, /python\/install/);
  assert.match(posixRuntimeScript, /path\.join\(extractDir, "python"\)/);
  assert.match(posixRuntimeScript, /verbatimSymlinks:\s*true/);
  assert.match(posixRuntimeScript, /Portable Python .*resolved outside the packaged runtime/);

  const macHelperBuild = fs.readFileSync(
    path.join(repoRoot, 'scripts', 'desktop', 'build-macos-ax-helper.mjs'),
    'utf8',
  );
  assert.match(macHelperBuild, /swiftc/);
  assert.match(macHelperBuild, /macos-\$\{arch\}/);
  const macDriver = fs.readFileSync(
    path.join(repoRoot, 'apps', 'v8-agent-os-engine', 'runtimes', 'computer_use', 'drivers', 'mac_ax.py'),
    'utf8',
  );
  assert.match(macDriver, /def _packaged_helper_binary_path/);
  assert.match(macDriver, /if packaged_binary\.is_file\(\):/);
  const macHelperSource = fs.readFileSync(
    path.join(repoRoot, 'apps', 'v8-agent-os-engine', 'runtimes', 'computer_use', 'drivers', 'mac_ax_helper.swift'),
    'utf8',
  );
  assert.match(macHelperSource, /var value: CFArray\?/);
  assert.match(macHelperSource, /AXUIElementCopyActionNames\(element, &value\)/);
  assert.match(macHelperSource, /path\.joined\(separator: "\/"\)/);

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
  assert.match(releaseRequirements, /-r platform-macos\.txt/);
  assert.match(releaseRequirements, /-r platform-linux\.txt/);
  const linuxRequirements = fs.readFileSync(
    path.join(repoRoot, 'apps', 'v8-agent-os-engine', 'requirements', 'platform-linux.txt'),
    'utf8',
  );
  assert.match(linuxRequirements, /PyGObject/);
  assert.doesNotMatch(linuxRequirements, /^pyatspi(?:[<=>\\[]|\\s|$)/im);
  assert.match(posixRuntimeScript, /PYATSPI_IMPORT_OK/);
  assert.match(posixRuntimeScript, /f2fb289a9d2e4dac65fca8db0f4d3d65607a0cf2/);
  assert.match(posixRuntimeScript, /200600a819af2733ca43eaadda5bc794c1e0b516799991ca138bb6db184c81b6/);
  assert.match(posixRuntimeScript, /gi\.require_version\('Atspi', '2\.0'\)/);
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
  assert.match(components, /\.python", "bin", "python3"/);
});

test('desktop release uses current desktop tag namespace and keeps runtime probes in CI evidence', () => {
  const workflow = fs.readFileSync(path.join(repoRoot, '.github', 'workflows', 'desktop-preview.yml'), 'utf8');
  assert.match(workflow, /v8-os-desktop-v\*/);
  assert.match(workflow, /startsWith\(github\.ref, 'refs\/tags\/v8-os-desktop-v'\)/);
  assert.match(workflow, /Verify desktop runtime payload/);
  assert.match(workflow, /verify_desktop_release_runtime\.mjs/);
  assert.match(workflow, /Installed Windows desktop smoke/);
  assert.match(workflow, /Print Windows desktop smoke service logs/);
  assert.match(workflow, /\.v8-agent-os\\logs\\cli/);
  assert.match(workflow, /Upload Windows desktop smoke diagnostics/);
  assert.match(workflow, /desktop-smoke-diagnostics/);
  assert.match(workflow, /Normalize platform release assets and checksums/);
  assert.match(workflow, /Upload desktop platform artifacts/);
  assert.doesNotMatch(workflow, /v8-os-desktop-preview-v/);
  const windowsPythonStep = workflow.slice(
    workflow.indexOf('Prepare embedded Engine Python runtime on Windows'),
    workflow.indexOf('Prepare embedded Engine Python runtime on macOS or Linux'),
  );
  assert.match(windowsPythonStep, /timeout-minutes:\s*60/);

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
  assert.match(requiredModulesBlock, /langgraphCheckpointSqlite/);
  assert.match(requiredModulesBlock, /chromaRustNative/);
  assert.match(requiredModulesBlock, /tiktokenNative/);
  assert.match(optionalModulesBlock, /pywinauto/);
  assert.match(optionalModulesBlock, /sqliteVec/);
  assert.match(runtimeProbe, /sqlite-vec does not publish a Windows ARM64 wheel/);

  const baseRequirements = fs.readFileSync(
    path.join(repoRoot, 'apps', 'v8-agent-os-engine', 'requirements', 'base.txt'),
    'utf8',
  );
  assert.match(baseRequirements, /^langgraph-checkpoint-sqlite>=3\.1\.0,<4$/m);
  assert.doesNotMatch(baseRequirements, /langgraph-checkpoint-sqlite[^\r\n]*platform_machine/);

  const windowsPythonRuntime = fs.readFileSync(
    path.join(repoRoot, 'scripts', 'desktop', 'prepare-windows-python-runtime.ps1'),
    'utf8',
  );
  assert.match(windowsPythonRuntime, /langgraph-checkpoint-sqlite==3\.1\.1/);
  assert.match(windowsPythonRuntime, /--no-deps/);
  assert.match(windowsPythonRuntime, /Expected exactly one LangGraph SQLite checkpoint requirement/);
  assert.match(windowsPythonRuntime, /Copy-Item -LiteralPath \$requirementsSourceRoot/);
  assert.match(windowsPythonRuntime, /setuptools-rust==1\.13\.0/);
  assert.match(windowsPythonRuntime, /tiktoken==0\.13\.0/);
  assert.match(windowsPythonRuntime, /Windows ARM64 build support is pinned to Python 3\.11\.9/);
  assert.match(windowsPythonRuntime, /pythonarm64\.\$PythonVersion\.nupkg/);
  assert.match(windowsPythonRuntime, /2F5B3BEE38850FDDE1B44227A23B8130D329839558376D2EB11099CE2B2CC33C/);
  assert.match(windowsPythonRuntime, /Python\.h/);
  assert.match(windowsPythonRuntime, /python311\.lib/);
  assert.match(windowsPythonRuntime, /python3\.lib/);
  assert.match(windowsPythonRuntime, /Expected exactly one native win_arm64 tiktoken wheel/);
  assert.match(windowsPythonRuntime, /"--no-deps", "--no-index"/);
  assert.match(windowsPythonRuntime, /V8OS_ARM64_TIKTOKEN_OK/);
  assert.match(windowsPythonRuntime, /V8OS_ARM64_TIKTOKEN_RUNTIME_OK/);
  assert.match(windowsPythonRuntime, /numpy==2\.4\.6/);
  assert.match(windowsPythonRuntime, /maturin==1\.14\.1/);
  assert.match(windowsPythonRuntime, /Join-Path \$runtimeDir 'Scripts'/);
  assert.match(windowsPythonRuntime, /protoc-\$protocVersion-win64\.zip/);
  assert.match(windowsPythonRuntime, /5D3FF218D7D91EEA95F7569BCB5A98F3030F8996D44151279D9772EDCFF76082/);
  assert.match(windowsPythonRuntime, /PROTOC_INCLUDE/);
  assert.match(windowsPythonRuntime, /chromadb==1\.5\.9/);
  assert.match(windowsPythonRuntime, /5C20E62A455C28BACAC927F26116A73FD8E1799E0D908BE8E8A4F02197A54731/);
  assert.match(windowsPythonRuntime, /Expected exactly one audited generator 0\.8\.8 entry/);
  assert.match(windowsPythonRuntime, /version = \"0\.8\.9\"/);
  assert.match(windowsPythonRuntime, /b3b854b0e584ead1a33f18b2fcad7cf7be18b3875c78816b753639aa501513ae/);
  assert.match(windowsPythonRuntime, /Expected exactly one native win_arm64 Chroma wheel/);
  assert.match(windowsPythonRuntime, /V8OS_ARM64_CHROMA_NATIVE_OK/);
  assert.match(windowsPythonRuntime, /V8OS_ARM64_CHROMA_WRITE_OK/);
  assert.match(windowsPythonRuntime, /V8OS_ARM64_CHROMA_REOPEN_OK/);
  assert.match(windowsPythonRuntime, /build-only Python development files were not removed/);
  assert.match(windowsPythonRuntime, /V8OS_ARM64_PIP_CHECK_EXPECTED_GAP_ONLY/);
  assert.match(windowsPythonRuntime, /V8OS_ARM64_CHECKPOINT_SQLITE_OK/);
  assert.match(windowsPythonRuntime, /await saver\.aput/);
  assert.match(windowsPythonRuntime, /await saver\.aget_tuple/);

  const desktopRequirements = fs.readFileSync(
    path.join(repoRoot, 'apps', 'v8-agent-os-engine', 'requirements', 'desktop-preview.txt'),
    'utf8',
  );
  assert.match(desktopRequirements, /^chromadb==1\.5\.9$/m);
  assert.doesNotMatch(desktopRequirements, /^chromadb$/m);

  const packageLayoutProbe = fs.readFileSync(
    path.join(repoRoot, 'scripts', 'desktop', 'verify-desktop-package-layout.mjs'),
    'utf8',
  );
  assert.match(packageLayoutProbe, /V8OS_PACKAGED_RUNTIME_OK/);
  assert.match(packageLayoutProbe, /engine\.packagedRuntimeImport/);
  assert.match(packageLayoutProbe, /gi\.require_version\('Atspi', '2\.0'\)/);
  assert.match(packageLayoutProbe, /location\.is_relative_to\(runtime\)/);

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
  assert.match(releaseNotes, /win-arm64-setup\.exe/);
  assert.match(releaseNotes, /GitHub Actions artifact/);
  assert.doesNotMatch(releaseNotes, /RUNTIME_PROBE-<platform>\.json/);
  assert.doesNotMatch(releaseNotes, /desktop-preview/);

  const baseline = fs.readFileSync(path.join(repoRoot, 'docs', 'V8OS', 'V8OS_RELEASE_VERSIONING_BASELINE_ZH.md'), 'utf8');
  assert.match(baseline, /v8-os-desktop-vYYYY\.MM\.DD\.N/);
  assert.match(baseline, /Windows x64\/ARM64/);
  assert.match(baseline, /workflow artifact/);
  assert.match(baseline, /Windows ARM64 兼容性技术债登记/);
  assert.match(baseline, /关闭后重开读取/);
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
