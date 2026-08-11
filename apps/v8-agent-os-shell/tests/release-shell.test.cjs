const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
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

test('packaged shell checks only governed unified releases without installing updates', () => {
  const mainSource = fs.readFileSync(path.join(shellRoot, 'electron', 'main.cjs'), 'utf8');
  const updateSource = fs.readFileSync(path.join(shellRoot, 'lib', 'update-check.cjs'), 'utf8');
  const config = fs.readFileSync(path.join(shellRoot, 'electron-builder.yml'), 'utf8');
  assert.match(config, /from: \.\.\/\.\.\/release-manifest\.json\s+to: v8os\/release-manifest\.json/);
  assert.match(mainSource, /app\.isPackaged && process\.env\.V8OS_DISABLE_UPDATE_CHECK !== '1'/);
  assert.match(mainSource, /scheduleAutomaticUpdateCheck\(\)/);
  assert.match(mainSource, /if \(!surfaceReady\) \{[\s\S]*?return;[\s\S]*?\}\s+scheduleAutomaticUpdateCheck\(\)/);
  assert.doesNotMatch(mainSource, /product_navigation_completed[^\n]*\n\s*scheduleAutomaticUpdateCheck\(\)/);
  assert.match(mainSource, /AUTOMATIC_UPDATE_CHECK_DELAY_MS = 20_000/);
  assert.match(mainSource, /requestDesktopUpdateCheck\(\{ manual: true \}\)/);
  assert.match(mainSource, /async function showUpdateCheckResult[\s\S]*showMainWindow\(\);[\s\S]*mainWindow\.isVisible\(\)/);
  assert.match(mainSource, /shell\.openExternal\(controlledUrl\)/);
  assert.doesNotMatch(mainSource, /quitAndInstall|checkForUpdatesAndNotify|electron-updater/);
  assert.match(updateSource, /repos\/justForever17\/v8-agent-os\/releases\?per_page=20/);
  assert.doesNotMatch(updateSource, /releases\/latest|Authorization/);
  assert.match(updateSource, /credentials: 'omit'/);
  assert.match(updateSource, /redirect: 'error'/);
  assert.match(updateSource, /DEFAULT_UPDATE_TIMEOUT_MS = 7000/);
  assert.match(updateSource, /UNIFIED_TAG_RE/);
  assert.match(updateSource, /SHA256SUMS\.txt/);
  const preloadSource = fs.readFileSync(path.join(shellRoot, 'electron', 'preload.cjs'), 'utf8');
  assert.match(preloadSource, /getUpdateStatus/);
  assert.match(preloadSource, /checkForUpdates/);
  assert.match(preloadSource, /openUpdateRelease/);
  assert.match(preloadSource, /v8os-shell:update-status/);
  assert.match(mainSource, /currentVersion/);
  assert.match(mainSource, /v8os-shell:get-update-status/);
  assert.match(mainSource, /v8os-shell:check-for-updates/);
  assert.match(mainSource, /v8os-shell:open-update-release/);
  const workflow = fs.readFileSync(path.join(repoRoot, '.github', 'workflows', 'desktop-preview.yml'), 'utf8');
  const disabledUpdateChecks = workflow.match(/V8OS_DISABLE_UPDATE_CHECK:\s*['"]?1['"]?/g) || [];
  assert.equal(disabledUpdateChecks.length, 4);
  assert.match(workflow, /Installed Linux desktop smoke/);
  assert.match(workflow, /Read-only AppImage desktop smoke/);
  assert.match(workflow, /Installed Windows desktop smoke/);
  assert.match(workflow, /Packaged macOS desktop smoke/);
});

test('packaged shell starts core services before waiting for them', () => {
  const mainSource = fs.readFileSync(path.join(shellRoot, 'electron', 'main.cjs'), 'utf8');
  assert.match(mainSource, /ensureCoreServicesStarted/);
  assert.match(mainSource, /const CORE_SERVICE_IDS = \['engine', 'admin', 'web'\]/);
  assert.match(mainSource, /shellStart\(CORE_SERVICE_IDS, \{ mode: 'start' \}\)/);
  assert.match(mainSource, /const startResults = await ensureCoreServicesStarted\(\);[\s\S]*await waitForServices\(startResults\);/);
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
  assert.match(mainSource, /monitorCoreServiceLiveness/);
  assert.match(mainSource, /process\.kill\(pid, 0\)/);
  assert.match(mainSource, /const statuses = await shellStatus\(exitedIds\)/);
  assert.doesNotMatch(mainSource, /while \(!isComplete\(\)\) \{[\s\S]{0,220}shellStatus\(CORE_SERVICE_IDS\)/);
  assert.match(mainSource, /isCancelled: \(\) => complete/);
  assert.match(mainSource, /if \(options\.isCancelled\?\.\(\)\) return false/);
  assert.match(mainSource, /coreServicesStartPromise === currentAttempt/);
  assert.match(mainSource, /coreServiceStartupError/);
  assert.match(mainSource, /logErr/);
  assert.match(mainSource, /typeof error\?\.userFacingMessage === 'string'/);
  assert.match(mainSource, /restartRetryableCoreServices/);
  assert.match(mainSource, /v8os-shell:retry-startup/);
  assert.match(mainSource, /replaceAll\('\\\\', '\/'\)/);
  assert.doesNotMatch(mainSource, /isAdminLoggedIn/);
  assert.match(mainSource, /api\/client\/instance/);
  assert.match(mainSource, /payload\?\.kind !== 'v8_instance_manifest'/);
  assert.match(mainSource, /typeof payload\?\.initialized !== 'boolean'/);
  assert.match(mainSource, /initialProductSurfaceUrl\(\{/);
  assert.match(mainSource, /async function openWeb\(\)[\s\S]*?return loadInMainWindow\(chatUrl\)/);
  assert.match(mainSource, /mainWindow\.once\('ready-to-show',[\s\S]*?showMainWindow\(\)/);
  const preloadSource = fs.readFileSync(path.join(shellRoot, 'electron', 'preload.cjs'), 'utf8');
  assert.match(preloadSource, /retryStartup/);
  const installSmokeSource = fs.readFileSync(path.join(shellRoot, 'tests', 'scripts', 'run_desktop_install_smoke.mjs'), 'utf8');
  assert.match(installSmokeSource, /api\/client\/instance/);
  assert.match(installSmokeSource, /rawInitialInstanceManifest\.payload\?\.initialized === false/);
  assert.match(installSmokeSource, /initialShellSurface\.surfaceKind === "admin-login"/);
  assert.match(installSmokeSource, /\/api\/auth\/bootstrap/);
  assert.match(installSmokeSource, /stop", "--only", "shell"/);
  assert.match(installSmokeSource, /shellSurface\.surfaceKind === "web"/);
  assert.match(installSmokeSource, /initial_bootstrap_surface_mismatch/);
  assert.match(installSmokeSource, /trusted_web_surface_mismatch/);
  assert.match(installSmokeSource, /typeof value\.checked !== "boolean"/);
  assert.match(installSmokeSource, /permittedBooleans\.some\(\(key\) => typeof value\[key\] !== "boolean"\)/);
  assert.match(installSmokeSource, /function appImageRuntimeEnvironment\(appImageRoot, noSandbox\)/);
  assert.match(installSmokeSource, /APPIMAGE: path\.join\(appDir, "AppRun"\)/);
  assert.match(installSmokeSource, /V8OS_ELECTRON_NO_SANDBOX: noSandbox \? "1" : "0"/);
  assert.match(installSmokeSource, /const shellArgs = shellNoSandbox \? \["--no-sandbox"\] : \[\]/);
  const packagedShellSpawnSource = installSmokeSource.slice(
    installSmokeSource.indexOf('function spawnPackagedShell'),
    installSmokeSource.indexOf('async function waitForPidExit'),
  );
  const packagedCliSource = installSmokeSource.slice(
    installSmokeSource.indexOf('async function runPackagedCli'),
    installSmokeSource.indexOf('async function waitForDesktopPet'),
  );
  assert.match(packagedShellSpawnSource, /spawn\(shellExecutable, shellArgs/);
  assert.match(packagedShellSpawnSource, /\.\.\.runtimeEnvironment/);
  assert.match(packagedCliSource, /spawn\(shellExecutable, \[cliPath, \.\.\.args\]/);
  assert.match(packagedCliSource, /\.\.\.runtimeEnvironment/);
  assert.doesNotMatch(packagedCliSource, /--no-sandbox/);
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

test('packaged desktop pet reuses the Shell Electron runtime without packaged pet node_modules', async () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(shellRoot, 'package.json'), 'utf8'));
  const bootstrapSource = fs.readFileSync(path.join(shellRoot, 'electron', 'bootstrap.cjs'), 'utf8');
  const launcherSource = fs.readFileSync(path.join(shellRoot, 'scripts', 'electron-launcher.mjs'), 'utf8');
  const detachedSource = fs.readFileSync(path.join(shellRoot, 'scripts', 'spawn-detached-electron.mjs'), 'utf8');
  const petMainSource = fs.readFileSync(
    path.join(repoRoot, 'apps', 'v8-agent-os-desktop-pet', 'electron', 'main.cjs'),
    'utf8',
  );

  assert.equal(pkg.main, 'electron/bootstrap.cjs');
  assert.match(bootstrapSource, /V8OS_DESKTOP_RUNTIME_MODE/);
  assert.match(bootstrapSource, /app\.commandLine\.hasSwitch\('no-sandbox'\)/);
  assert.match(bootstrapSource, /app\.setName\('V8 Agent OS Desktop Pet'\)/);
  assert.match(bootstrapSource, /app\.setPath\('userData', desktopPetUserData\)/);
  assert.match(bootstrapSource, /app\.setPath\('sessionData'/);
  assert.match(bootstrapSource, /require\(desktopPetMain\)/);
  assert.match(launcherSource, /delete env\.ELECTRON_RUN_AS_NODE/);
  assert.match(launcherSource, /V8OS_SHELL_EXECUTABLE/);
  assert.match(launcherSource, /V8OS_DESKTOP_RUNTIME_MODE = "desktop-pet"/);
  assert.match(launcherSource, /V8_DESKTOP_NODE_IS_ELECTRON = "1"/);
  assert.doesNotMatch(detachedSource, /electronCliPath/);
  assert.match(detachedSource, /desktopRuntimeSpawnSpec\(target\)/);
  assert.match(petMainSource, /serverRuntimeIsElectron/);
  assert.match(petMainSource, /serverEnv\.ELECTRON_RUN_AS_NODE = '1'/);
  assert.match(launcherSource, /windowsHide:\s*true/);

  const launcher = await import(`${pathToFileURL(path.join(shellRoot, 'scripts', 'electron-launcher.mjs')).href}?test=${Date.now()}`);
  const target = path.join(repoRoot, 'apps', 'v8-agent-os-desktop-pet', 'electron', 'main.cjs');
  const spec = launcher.desktopRuntimeSpawnSpec(target, {
    ELECTRON_RUN_AS_NODE: '1',
    V8OS_SHELL_PACKAGED: '1',
    V8OS_SHELL_EXECUTABLE: process.execPath,
  });
  assert.equal(spec.command, process.execPath);
  assert.deepEqual(spec.args, [target]);
  assert.equal(spec.env.ELECTRON_RUN_AS_NODE, undefined);
  assert.equal(spec.env.V8OS_DESKTOP_RUNTIME_MODE, 'desktop-pet');
  assert.equal(spec.env.V8_DESKTOP_NODE, process.execPath);
  assert.equal(spec.env.V8_DESKTOP_NODE_IS_ELECTRON, '1');

  const noSandboxPetSpec = launcher.desktopRuntimeSpawnSpec(target, {
    V8OS_SHELL_PACKAGED: '1',
    V8OS_SHELL_EXECUTABLE: process.execPath,
    V8OS_ELECTRON_NO_SANDBOX: '1',
  });
  assert.deepEqual(noSandboxPetSpec.args, ['--no-sandbox', target]);

  const shellSpec = launcher.shellRuntimeSpawnSpec(shellRoot, {
    ELECTRON_RUN_AS_NODE: '1',
    V8OS_DESKTOP_RUNTIME_MODE: 'desktop-pet',
    V8OS_SHELL_PACKAGED: '1',
    V8OS_SHELL_EXECUTABLE: process.execPath,
  });
  assert.equal(shellSpec.command, process.execPath);
  assert.deepEqual(shellSpec.args, []);
  assert.equal(shellSpec.env.ELECTRON_RUN_AS_NODE, undefined);
  assert.equal(shellSpec.env.V8OS_DESKTOP_RUNTIME_MODE, undefined);

  const noSandboxShellSpec = launcher.shellRuntimeSpawnSpec(shellRoot, {
    V8OS_SHELL_PACKAGED: '1',
    V8OS_SHELL_EXECUTABLE: process.execPath,
    V8OS_ELECTRON_NO_SANDBOX: '1',
  });
  assert.deepEqual(noSandboxShellSpec.args, ['--no-sandbox']);
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
  assert.match(config, /to: v8os\/apps\/v8-agent-os-shell\/scripts/);
  assert.match(config, /electron-launcher\.mjs/);
  assert.match(config, /feature_pack_runtime_probe\.py/);
  assert.match(config, /launch-desktop-pet\.mjs/);
  assert.match(config, /launch-shell\.mjs/);
  assert.match(config, /spawn-detached-electron\.mjs/);
  assert.match(
    config,
    /from: \.\.\/\.\.\/apps\/v8-agent-os-shell\/scripts[\s\S]*?feature_pack_runtime_probe\.py/,
  );
  assert.doesNotMatch(config, /from: \.\.\/\.\.\/apps\/v8-agent-os-shell\/tests\/scripts/);
  assert.match(config, /!\.next\/dev\/\*\*/);
  assert.match(config, /!native\/\*\*\/target\/\*\*/);
  assert.match(config, /to: v8os\/apps\/v8-agent-os-shell\/scripts/);
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

test('schema 2 release validation verifies every enabled product tarball without writing', () => {
  const prepare = path.join(repoRoot, 'scripts', 'release', 'prepare-release.mjs');
  const output = execFileSync(process.execPath, [prepare, '--from-manifest'], {
    cwd: repoRoot,
    encoding: 'utf8',
  });

  assert.match(output, /Desktop local tarball integrity OK/);
  assert.match(output, /Phone local tarball integrity OK/);
  assert.match(output, /@v8\/product-ui/);
  assert.match(output, /@v8\/session-realtime/);
  assert.match(output, /Validation complete\. No files changed\./);
});

test('desktop reusable workflow builds explicit native targets and only uploads build artifacts', () => {
  const workflowPath = path.join(repoRoot, '.github', 'workflows', 'desktop-preview.yml');
  assert.equal(fs.existsSync(workflowPath), true);
  const workflow = fs.readFileSync(workflowPath, 'utf8');
  assert.match(workflow, /workflow_call:/);
  assert.match(workflow, /targets_json:/);
  assert.doesNotMatch(workflow, /pull_request:/);
  assert.doesNotMatch(workflow, /push:/);
  assert.doesNotMatch(workflow, /softprops\/action-gh-release/);
  assert.doesNotMatch(workflow, /contents: write/);
  assert.match(workflow, /packages\/session-realtime\/package-lock\.json/);
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
  assert.match(
    workflow,
    /resolve-desktop-platforms:[\s\S]*?uses: actions\/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1/,
  );
  assert.match(workflow, /build-macos-ax-helper\.mjs/);
  assert.match(workflow, /verify-desktop-package-layout\.mjs/);
  assert.match(workflow, /prepare-desktop-release-assets\.mjs/);
  assert.match(workflow, /--release-version "\$\{\{ inputs\.release_version \}\}"/);
  assert.match(workflow, /name: v8os-desktop-\$\{\{ matrix\.id \}\}/);
  assert.doesNotMatch(workflow, /dist\/release\/\*\.zip/);
  assert.doesNotMatch(workflow, /desktop-preview-artifacts\/\*\.zip/);
  const assetPreparer = fs.readFileSync(
    path.join(repoRoot, 'scripts', 'desktop', 'prepare-desktop-release-assets.mjs'),
    'utf8',
  );
  assert.match(assetPreparer, /SHA256SUMS-\$\{platform\}\.txt/);
  assert.match(assetPreparer, /Desktop release output directory must be empty/);
  assert.doesNotMatch(assetPreparer, /fs\.rmSync\(outputDir/);

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
  const explicit = JSON.parse(execFileSync(process.execPath, [
    resolver,
    '--event', 'workflow_call',
    '--targets-json', '["windows-x64","macos-arm64"]',
  ], { encoding: 'utf8' }));
  assert.equal(explicit.enabled, true);
  assert.deepEqual(explicit.matrix.include.map((target) => target.id), ['windows-x64', 'macos-arm64']);
  const tagPushCaller = JSON.parse(execFileSync(process.execPath, [
    resolver,
    '--event', 'push',
    '--targets-json', '["linux-x64"]',
  ], { encoding: 'utf8' }));
  assert.equal(tagPushCaller.enabled, true);
  assert.deepEqual(tagPushCaller.matrix.include.map((target) => target.id), ['linux-x64']);
});

test('root release workflow is the only fan-in publisher and enforces required products', () => {
  const desktop = fs.readFileSync(path.join(repoRoot, '.github', 'workflows', 'desktop-preview.yml'), 'utf8');
  const phone = fs.readFileSync(path.join(repoRoot, '.github', 'workflows', 'phone-build.yml'), 'utf8');
  const workflow = fs.readFileSync(path.join(repoRoot, '.github', 'workflows', 'release.yml'), 'utf8');

  assert.match(workflow, /"v8-os-v\*"/);
  assert.match(workflow, /"v8-os-desktop-v\*"/);
  assert.match(workflow, /"v8-os-phone-v\*"/);
  assert.match(workflow, /uses: \.\/\.github\/workflows\/desktop-preview\.yml/);
  assert.doesNotMatch(workflow, /uses: \.\/\.github\/workflows\/phone-build\.yml/);
  assert.match(workflow, /phone-android-build:[\s\S]*?environment: release[\s\S]*?uses: \.\/\.github\/actions\/build-phone-package/);
  assert.match(workflow, /phone-ios-build:[\s\S]*?environment: release[\s\S]*?uses: \.\/\.github\/actions\/build-phone-package/);
  assert.match(workflow, /phone-build-contract:[\s\S]*?Requested Phone target \$target ended with \$result/);
  assert.match(workflow, /PHONE_RESULT: \$\{\{ needs\.phone-build-contract\.result \}\}/);
  assert.match(workflow, /release-gate:[\s\S]*always\(\) && !cancelled\(\)/);
  assert.match(workflow, /Required product \$product ended with \$result/);
  assert.match(
    workflow,
    /publish:[\s\S]*?if: >-[\s\S]*?!cancelled\(\)[\s\S]*?needs\.plan\.result == 'success'[\s\S]*?needs\.release-gate\.result == 'success'[\s\S]*?needs\.release-gate\.outputs\.publish_ready == 'true'/,
  );
  assert.match(workflow, /prepare-unified-release-assets\.mjs/);
  assert.match(workflow, /pattern: v8os-desktop-\*/);
  assert.match(workflow, /name: phone-android-package/);
  assert.match(workflow, /prerelease: \$\{\{ needs\.plan\.outputs\.prerelease \}\}/);
  assert.match(workflow, /fail_on_unmatched_files: true/);
  assert.equal((workflow.match(/contents: write/g) || []).length, 1);
  assert.equal(
    (workflow.match(/softprops\/action-gh-release@3d0d9888cb7fd7b750713d6e236d1fcb99157228/g) || []).length,
    1,
  );
  assert.equal((workflow.match(/environment: release/g) || []).length, 2);
  assert.equal((workflow.match(/secrets\.EXPO_TOKEN/g) || []).length, 2);
  assert.doesNotMatch(workflow, /secrets: inherit/);
  assert.doesNotMatch(desktop, /softprops\/action-gh-release|contents: write/);
  assert.doesNotMatch(phone, /softprops\/action-gh-release|contents: write/);
  assert.match(desktop, /Upload Windows desktop smoke diagnostics[\s\S]*continue-on-error: true/);
  assert.match(desktop, /retention-days: 7/);
  assert.match(desktop, /compression-level: 0/);
  assert.match(desktop, /Verify Windows runner and Node architecture/);
  assert.match(desktop, /Verify Windows Rust native target/);
  assert.match(desktop, /-Architecture "\$\{\{ matrix\.pythonArch \}\}"/);
  assert.ok(
    desktop.indexOf('Prepare embedded Engine Python runtime on Windows') <
      desktop.indexOf('Install Admin dependencies'),
    'platform Python runtime validation must run before expensive product dependency installs',
  );
  assert.doesNotMatch(workflow, /RUNTIME_PROBE-\*\.json|PACKAGE_LAYOUT-\*\.json/);
});

test('every external GitHub Action is pinned to an immutable commit', () => {
  const githubRoot = path.join(repoRoot, '.github');
  const pending = [githubRoot];
  const actionFiles = [];

  while (pending.length > 0) {
    const current = pending.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const entryPath = path.join(current, entry.name);
      if (entry.isDirectory()) {
        pending.push(entryPath);
      } else if (/\.ya?ml$/i.test(entry.name)) {
        actionFiles.push(entryPath);
      }
    }
  }

  const unpinned = [];
  for (const actionFile of actionFiles) {
    const source = fs.readFileSync(actionFile, 'utf8');
    for (const match of source.matchAll(/^\s*uses:\s*([^\s#]+)(?:\s+#.*)?$/gm)) {
      const reference = match[1];
      if (reference.startsWith('./') || reference.startsWith('docker://')) continue;
      const separator = reference.lastIndexOf('@');
      const revision = separator >= 0 ? reference.slice(separator + 1) : '';
      if (!/^[0-9a-f]{40}$/i.test(revision)) {
        unpinned.push(`${path.relative(repoRoot, actionFile)}: ${reference}`);
      }
    }
  }

  assert.deepEqual(unpinned, []);
});

test('phone manual workflow and root release share one governed package action', () => {
  const workflow = fs.readFileSync(path.join(repoRoot, '.github', 'workflows', 'phone-build.yml'), 'utf8');
  const phoneAction = fs.readFileSync(
    path.join(repoRoot, '.github', 'actions', 'build-phone-package', 'action.yml'),
    'utf8',
  );
  assert.doesNotMatch(workflow, /workflow_call:|enforce_requested_targets|build_android|build_ios/);
  assert.match(workflow, /platform:\s*\n\s+description: Target platform[\s\S]*?- android[\s\S]*?- ios[\s\S]*?- all/);
  assert.match(workflow, /inputs\.platform == 'android' \|\| inputs\.platform == 'all'/);
  assert.match(workflow, /inputs\.platform == 'ios' \|\| inputs\.platform == 'all'/);
  assert.match(workflow, /build-android:[\s\S]*?uses: \.\/\.github\/actions\/build-phone-package[\s\S]*?platform: android/);
  assert.match(workflow, /build-ios:[\s\S]*?runs-on: macos-latest[\s\S]*?uses: \.\/\.github\/actions\/build-phone-package[\s\S]*?platform: ios/);
  assert.equal((workflow.match(/environment: release/g) || []).length, 2);
  assert.equal((workflow.match(/secrets\.EXPO_TOKEN/g) || []).length, 2);
  assert.doesNotMatch(workflow, /secrets: inherit|pull_request:|push:|contents: write|softprops\/action-gh-release/);
  assert.match(phoneAction, /using: composite/);
  assert.match(phoneAction, /Validate Phone package request/);
  assert.match(phoneAction, /android\|ios/);
  assert.match(phoneAction, /development\|preview\|production/);
  assert.match(phoneAction, /Verify EAS release credential availability/);
  assert.match(phoneAction, /eas-version: 21\.7\.0/);
  assert.match(phoneAction, /token: \$\{\{ inputs\.expo-token \}\}/);
  assert.ok(
    phoneAction.indexOf('name: Typecheck') < phoneAction.indexOf('name: Verify EAS release credential availability'),
    'dependency install and typecheck must complete before the credential enters a third-party action',
  );
  assert.match(phoneAction, /--platform android[\s\S]*?--profile "\$EAS_BUILD_PROFILE"/);
  assert.match(phoneAction, /--platform ios[\s\S]*?--profile "\$EAS_BUILD_PROFILE"/);
  assert.match(phoneAction, /name: phone-android-package/);
  assert.match(phoneAction, /name: phone-ios-package/);
  assert.equal((phoneAction.match(/EXPO_TOKEN is unavailable to the release environment job/g) || []).length, 1);
  assert.doesNotMatch(phoneAction, /secrets\./);
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
  assert.match(posixRuntimeScript, /https:\/\/download\.gnome\.org\/sources\/pyatspi\/2\.58\/pyatspi-2\.58\.2\.tar\.xz/);
  assert.match(posixRuntimeScript, /24590e5b60fec8dfb59fcd27d2a90de7034060be318ca3f7770e0f984f1f94e2/);
  assert.match(posixRuntimeScript, /"-xJf"/);
  assert.match(posixRuntimeScript, /pyatspi source archive/);
  assert.doesNotMatch(posixRuntimeScript, /gitlab\.gnome\.org\/api\/v4\/projects/);
  assert.match(posixRuntimeScript, /gi\.require_version\('Atspi', '2\.0'\)/);
  assert.match(workflow, /xz-utils/);
  const packageLayoutScript = fs.readFileSync(
    path.join(repoRoot, 'scripts', 'desktop', 'verify-desktop-package-layout.mjs'),
    'utf8',
  );
  assert.match(packageLayoutScript, /THIRD_PARTY_NOTICES", "pyatspi2-COPYING/);
  assert.match(packageLayoutScript, /v8-agent-os-shell", "scripts", "launch-desktop-pet\.mjs/);
  assert.match(packageLayoutScript, /v8-agent-os-shell", "scripts", "launch-shell\.mjs/);
  assert.match(packageLayoutScript, /v8-agent-os-shell", "scripts", "spawn-detached-electron\.mjs/);
  assert.match(packageLayoutScript, /verifyShellBootstrap\(appAsar\)/);
  assert.match(packageLayoutScript, /verifyDesktopPetServerBundle\(desktopPetServerBundle\)/);
});

test('desktop pet production server bundles runtime dependencies and keeps Vite development-only', () => {
  const petRoot = path.join(repoRoot, 'apps', 'v8-agent-os-desktop-pet');
  const pkg = JSON.parse(fs.readFileSync(path.join(petRoot, 'package.json'), 'utf8'));
  const serverSource = fs.readFileSync(path.join(petRoot, 'server.ts'), 'utf8');

  assert.match(pkg.scripts.build, /--bundle/);
  assert.match(pkg.scripts.build, /--external:vite/);
  assert.doesNotMatch(pkg.scripts.build, /--packages=external/);
  assert.doesNotMatch(serverSource, /^import .* from ["']vite["'];?$/m);
  assert.match(serverSource, /process\.env\.NODE_ENV !== "production"[\s\S]{0,180}await import\("vite"\)/);
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
  assert.match(runner, /assertStandaloneAssetsReady/);
  assert.match(runner, /stageStandaloneAssets\(appDir, builtStandaloneServer\)/);
  assert.doesNotMatch(runner, /stageStandaloneAssets\(appDir, standaloneServer\)/);
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

test('unified release keeps desktop runtime probes in CI evidence', () => {
  const workflow = fs.readFileSync(path.join(repoRoot, '.github', 'workflows', 'desktop-preview.yml'), 'utf8');
  const releaseWorkflow = fs.readFileSync(path.join(repoRoot, '.github', 'workflows', 'release.yml'), 'utf8');
  assert.match(releaseWorkflow, /"v8-os-v\*"/);
  assert.match(releaseWorkflow, /"v8-os-desktop-v\*"/);
  assert.match(releaseWorkflow, /"v8-os-phone-v\*"/);
  assert.doesNotMatch(workflow, /v8-os-(?:desktop|phone)-v\*/);
  assert.match(workflow, /Verify desktop runtime payload/);
  assert.match(workflow, /verify_desktop_release_runtime\.mjs/);
  assert.match(workflow, /Installed Windows desktop smoke/);
  assert.match(workflow, /Print Windows desktop smoke service logs/);
  assert.match(workflow, /Join-Path \$env:V8_AGENT_OS_HOME "logs\\cli"/);
  assert.match(workflow, /Upload Windows desktop smoke diagnostics/);
  const installedWindowsSmoke = workflow.slice(
    workflow.indexOf('Installed Windows desktop smoke'),
    workflow.indexOf('Print Windows desktop smoke service logs'),
  );
  assert.match(installedWindowsSmoke, /\$env:V8OS_WINDOWS_INSTALL_DIR = \[IO\.Path\]::GetFullPath\(\$env:V8OS_WINDOWS_INSTALL_DIR\)/);
  assert.match(installedWindowsSmoke, /\.Contains\("\/"\)/);
  assert.match(installedWindowsSmoke, /Start-Process -FilePath \$installer\.FullName -ArgumentList @\("\/S", "\/D=\$env:V8OS_WINDOWS_INSTALL_DIR"\)/);
  assert.match(installedWindowsSmoke, /resources\\v8os/);
  assert.match(installedWindowsSmoke, /\.python\\python\.exe/);
  assert.match(installedWindowsSmoke, /WindowsCredentialBackend/);
  assert.match(installedWindowsSmoke, /secrets\.token_hex\(12\)/);
  assert.match(installedWindowsSmoke, /store\.put\(value, reference=reference, namespace="system"\)/);
  assert.match(installedWindowsSmoke, /store\.resolve\(reference\) == value/);
  assert.match(installedWindowsSmoke, /store\.delete\(reference\) is True/);
  assert.match(installedWindowsSmoke, /store\.status\(reference\)\.configured is False/);
  assert.match(installedWindowsSmoke, /V8OS_WINDOWS_CREDENTIAL_MANAGER_OK/);
  assert.match(workflow, /Read-only AppImage desktop smoke/);
  assert.match(workflow, /chmod -R a-w "\$package_root"/);
  const appImageSmoke = workflow.slice(
    workflow.indexOf('Read-only AppImage desktop smoke'),
    workflow.indexOf('Collect Linux desktop smoke diagnostics'),
  );
  assert.doesNotMatch(appImageSmoke, /app_run="\$package_root\/AppRun"/);
  assert.match(appImageSmoke, /"\$shell_exe" "\$installed_cli" stop --all/);
  assert.match(appImageSmoke, /--shell-exe "\$shell_exe"/);
  assert.match(appImageSmoke, /--resource-root "\$resource_root"/);
  assert.match(appImageSmoke, /--appimage-root "\$package_root"/);
  assert.match(appImageSmoke, /--shell-no-sandbox "\$shell_no_sandbox"/);
  assert.match(appImageSmoke, /if ! unshare -Ur true 2>\/dev\/null/);
  assert.match(appImageSmoke, /x64\) appimage_arch="x86_64"/);
  assert.match(appImageSmoke, /arm64\) appimage_arch="arm64"/);
  assert.match(appImageSmoke, /mapfile -t appimage_paths/);
  assert.match(appImageSmoke, /test "\$\{#appimage_paths\[@\]\}" -eq 1/);
  assert.match(appImageSmoke, /shell_exe="\$package_root\/v8-agent-os-shell"/);
  assert.match(appImageSmoke, /APPDIR="\$package_root"/);
  assert.match(appImageSmoke, /APPIMAGE="\$package_root\/AppRun"/);
  assert.match(appImageSmoke, /LD_LIBRARY_PATH="\$package_root\/usr\/lib/);
  assert.match(appImageSmoke, /GSETTINGS_SCHEMA_DIR="\$package_root\/usr\/share\/glib-2\.0\/schemas/);
  assert.doesNotMatch(appImageSmoke, /export LD_LIBRARY_PATH/);
  assert.doesNotMatch(appImageSmoke, /--shell-exe "\$appimage"/);
  assert.match(workflow, /Packaged macOS desktop smoke/);
  assert.match(workflow, /hdiutil attach "\$dmg_path" -mountpoint "\$mount_point" -nobrowse -readonly/);
  assert.match(workflow, /Install Linux desktop preview package/);
  assert.match(workflow, /mapfile -t deb_paths/);
  assert.match(workflow, /test "\$\{#deb_paths\[@\]\}" -eq 1/);
  assert.match(workflow, /x64\) expected_deb_arch="amd64"/);
  assert.match(workflow, /dpkg-deb -f "\$deb_path" Architecture/);
  assert.match(workflow, /sudo dpkg -i "\$deb_path"/);
  assert.match(workflow, /\/opt\/V8 Agent OS\/v8-agent-os-shell/);
  assert.match(workflow, /Installed Linux desktop smoke/);
  assert.match(workflow, /dbus-run-session/);
  assert.match(workflow, /gnome-keyring-daemon --unlock --components=secrets/);
  assert.match(workflow, /V8OS_LINUX_SECRET_SERVICE_OK/);
  assert.match(workflow, /LinuxSecretServiceCredentialBackend/);
  assert.match(workflow, /xvfb-run -a node/);
  assert.match(workflow, /--startup-budget-ms 90000/);
  assert.match(workflow, /Print Linux desktop smoke service logs/);
  assert.match(workflow, /Upload Linux desktop smoke diagnostics/);
  assert.match(workflow, /resource_root="\/opt\/V8 Agent OS\/resources\/v8os"/);
  assert.match(workflow, /installed_cli="\$resource_root\/apps\/v8-agent-os-cli\/bin\/v8os\.mjs"/);
  const linuxDebCleanup = workflow.slice(
    workflow.indexOf('Cleanup Linux desktop smoke processes'),
    workflow.indexOf('Read-only AppImage desktop smoke'),
  );
  assert.match(linuxDebCleanup, /ELECTRON_RUN_AS_NODE=1/);
  assert.match(linuxDebCleanup, /V8OS_SHELL_PACKAGED=1/);
  assert.match(linuxDebCleanup, /V8_REPO_ROOT="\$resource_root"/);
  assert.match(linuxDebCleanup, /"\$shell_exe" "\$installed_cli" stop --all/);
  assert.doesNotMatch(linuxDebCleanup, /node "\$installed_cli" stop --all/);
  assert.equal(
    (workflow.match(/verify_desktop_cleanup\.mjs/g) || []).length,
    4,
    'DEB, AppImage, Windows, and macOS smoke paths must share the strict cleanup verifier',
  );
  const cleanupVerifier = fs.readFileSync(
    path.join(
      repoRoot,
      'apps',
      'v8-agent-os-shell',
      'tests',
      'scripts',
      'verify_desktop_cleanup.mjs',
    ),
    'utf8',
  );
  assert.match(cleanupVerifier, /V8OS_PACKAGED_DESKTOP_CLEANUP_OK/);
  assert.match(workflow, /desktop-smoke-diagnostics/);
  assert.match(workflow, /Normalize platform release assets and checksums/);
  assert.match(workflow, /Upload desktop platform artifacts/);
  assert.doesNotMatch(workflow, /v8-os-desktop-preview-v/);
  assert.match(workflow, /toolchain: \$\{\{ matrix\.id == 'windows-arm64' && '1\.92\.0' \|\| 'stable' \}\}/);
  assert.match(workflow, /timeout-minutes:\s*\$\{\{ matrix\.id == 'windows-arm64' && 180 \|\| 120 \}\}/);
  const windowsPythonStep = workflow.slice(
    workflow.indexOf('Prepare embedded Engine Python runtime on Windows'),
    workflow.indexOf('Prepare embedded Engine Python runtime on macOS or Linux'),
  );
  assert.match(windowsPythonStep, /timeout-minutes:\s*\$\{\{ matrix\.id == 'windows-arm64' && 110 \|\| 60 \}\}/);

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
  assert.match(requiredModulesBlock, /grpcNative/);
  assert.match(requiredModulesBlock, /httptoolsNative/);
  assert.match(requiredModulesBlock, /yaml/);
  assert.match(requiredModulesBlock, /windowsCredentialManager/);
  assert.match(requiredModulesBlock, /macOSKeychainApi/);
  assert.match(requiredModulesBlock, /secretStorage/);
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
  assert.match(windowsPythonRuntime, /VC\\Tools\\Llvm\\ARM64\\bin\\clang\.exe/);
  assert.match(windowsPythonRuntime, /CFLAGS_aarch64_pc_windows_msvc = "-D_ARM64_=1"/);
  assert.match(windowsPythonRuntime, /chromadb==1\.5\.9/);
  assert.match(windowsPythonRuntime, /5C20E62A455C28BACAC927F26116A73FD8E1799E0D908BE8E8A4F02197A54731/);
  assert.match(windowsPythonRuntime, /Expected exactly one audited generator 0\.8\.8 entry/);
  assert.match(windowsPythonRuntime, /version = \"0\.8\.9\"/);
  assert.match(windowsPythonRuntime, /b3b854b0e584ead1a33f18b2fcad7cf7be18b3875c78816b753639aa501513ae/);
  assert.match(windowsPythonRuntime, /Expected exactly one native win_arm64 Chroma wheel/);
  assert.match(windowsPythonRuntime, /V8OS_ARM64_CHROMA_NATIVE_OK/);
  assert.match(windowsPythonRuntime, /V8OS_ARM64_CHROMA_WRITE_OK/);
  assert.match(windowsPythonRuntime, /V8OS_ARM64_CHROMA_REOPEN_OK/);
  assert.match(windowsPythonRuntime, /setuptools==82\.0\.1/);
  assert.match(windowsPythonRuntime, /wheel==0\.46\.3/);
  assert.match(windowsPythonRuntime, /pyyaml-6\.0\.3\.tar\.gz/);
  assert.match(windowsPythonRuntime, /D76623373421DF22FB4CF8817020CBB7EF15C725B9D5E45F17E189BFC384190F/);
  assert.match(windowsPythonRuntime, /PYYAML_FORCE_LIBYAML = "0"/);
  assert.match(windowsPythonRuntime, /pyyaml-6\.0\.3-py3-none-any\.whl/);
  assert.match(windowsPythonRuntime, /httptools-0\.8\.0\.tar\.gz/);
  assert.match(windowsPythonRuntime, /6B2A32F18D97E16E90827D7A819FFA8DBD8CC245FC4E1FA9D1095B54EF4BD999/);
  assert.match(windowsPythonRuntime, /httptools-0\.8\.0-cp311-cp311-win_arm64\.whl/);
  assert.match(windowsPythonRuntime, /grpcio-1\.83\.0\.tar\.gz/);
  assert.match(windowsPythonRuntime, /7674587248FBBB2AC6E4EECF83A8A0F3D91A928F941DE571ACFD3A2F007FBC24/);
  assert.match(windowsPythonRuntime, /GRPC_PYTHON_BUILD_WITH_CYTHON = "0"/);
  assert.match(windowsPythonRuntime, /GRPC_PYTHON_BUILD_USE_SHORT_TEMP_DIR_NAME = "1"/);
  assert.match(windowsPythonRuntime, /-WorkingDirectory \$grpcSourceRoot/);
  assert.match(windowsPythonRuntime, /grpcio-1\.83\.0-cp311-cp311-win_arm64\.whl/);
  assert.match(windowsPythonRuntime, /grpcio build directories were not removed/);
  assert.match(windowsPythonRuntime, /--only-binary=grpcio,PyYAML,httptools/);
  assert.match(windowsPythonRuntime, /V8OS_ARM64_COMPATIBILITY_VERSIONS_OK/);
  assert.match(windowsPythonRuntime, /machine == 0xAA64/);
  assert.match(windowsPythonRuntime, /V8OS_ARM64_COMPATIBILITY_PE_OK/);
  assert.match(windowsPythonRuntime, /V8OS_ARM64_PYYAML_OK/);
  assert.match(windowsPythonRuntime, /V8OS_ARM64_HTTPTOOLS_OK/);
  assert.match(windowsPythonRuntime, /V8OS_ARM64_GRPC_LOOPBACK_OK/);
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
  assert.match(packageLayoutProbe, /nextStandaloneRequired/);
  assert.match(packageLayoutProbe, /server\.js/);
  assert.match(packageLayoutProbe, /standaloneAppRoot, "public"/);
  assert.match(packageLayoutProbe, /release-manifest\.json/);
  assert.match(packageLayoutProbe, /feature_pack_runtime_probe\.py/);
  assert.match(packageLayoutProbe, /rpa-automation-cp311-/);
  assert.match(packageLayoutProbe, /creative-media-image-analysis-cp311-/);
  assert.match(packageLayoutProbe, /platform !== "macos-x64"/);
  assert.match(packageLayoutProbe, /argValue\("--resource-root"\)/);
  assert.match(packageLayoutProbe, /argValue\("--output"\)/);
  assert.match(packageLayoutProbe, /spawnSync\(python, \["-I", "-X", "utf8"/);
  assert.match(packageLayoutProbe, /PYTHONNOUSERSITE/);

  const featurePackProbe = fs.readFileSync(
    path.join(repoRoot, 'apps', 'v8-agent-os-shell', 'scripts', 'feature_pack_runtime_probe.py'),
    'utf8',
  );
  assert.match(featurePackProbe, /RPA\.Excel\.Files/);
  assert.match(featurePackProbe, /Create Workbook/);
  assert.match(featurePackProbe, /_probe_onnx_runtime/);
  assert.match(featurePackProbe, /moduleOriginsVerified/);
  assert.match(featurePackProbe, /modelShaVerified/);
  assert.doesNotMatch(featurePackProbe, /\b(?:pip|urlopen|requests|httpx)\b/);

  const installSmoke = fs.readFileSync(
    path.join(repoRoot, 'apps', 'v8-agent-os-shell', 'tests', 'scripts', 'run_desktop_install_smoke.mjs'),
    'utf8',
  );
  assert.match(installSmoke, /featurePackApi/);
  assert.match(installSmoke, /featurePackEngineStatus/);
  assert.match(installSmoke, /featurePackRuntime/);
  assert.match(installSmoke, /feature_pack_runtime_probe\.py/);
  assert.match(
    installSmoke,
    /resourceRoot,[\s\S]*?"apps",[\s\S]*?"v8-agent-os-engine"[\s\S]*?"\.python"/,
  );
  assert.match(
    installSmoke,
    /"v8-agent-os-shell",[\s\S]*?"scripts",[\s\S]*?"feature_pack_runtime_probe\.py"/,
  );
  assert.match(installSmoke, /v1\/runtime-feature-packs\/status/);
  assert.doesNotMatch(installSmoke, /127\.0\.0\.1:9530\/health/);
  assert.match(installSmoke, /timeoutMs: 3_000/);
  assert.match(installSmoke, /--feature-pack-smoke/);
  assert.match(installSmoke, /--feature-pack-probe-timeout-ms/);
  assert.match(installSmoke, /Math\.min\(120_000/);
  assert.match(installSmoke, /\["-I", "-B", probePath\]/);
  assert.match(installSmoke, /PYTHONPATH/);
  assert.match(installSmoke, /PYTHONHOME/);
  assert.match(installSmoke, /windowsHide: true/);
  assert.match(installSmoke, /taskkill/);
  assert.match(installSmoke, /maxOutputBytes/);
  assert.match(installSmoke, /rpa\.available && rpa\.isolated && rpa\.dryRunPassed/);
  assert.match(
    installSmoke,
    /image\.assetResolved && image\.cpuSessionLoaded && image\.isolated[\s\S]*?image\.moduleOriginsVerified && image\.modelShaVerified/,
  );
  assert.match(installSmoke, /!rpa\.failClosed && rpa\.available/);
  assert.match(installSmoke, /!image\.failClosed[\s\S]*?image\.assetResolved/);
  assert.match(installSmoke, /seenIds/);
  assert.match(installSmoke, /hasFeaturePackPayloadSchema/);
  assert.match(installSmoke, /Array\.isArray\(payload\?\.packs\)/);
  assert.match(installSmoke, /requiredAdminPackIds/);
  assert.match(installSmoke, /"rpa_automation", "creative_media_image_analysis"/);
  assert.match(installSmoke, /typeof pack\.installable !== "boolean"/);
  assert.match(installSmoke, /featurePackApiMatchesEngine/);
  assert.match(installSmoke, /adminPack\.status !== enginePack\.status/);
  assert.match(installSmoke, /adminPack\.installed !== enginePack\.installed/);
  assert.match(installSmoke, /adminPack\.restartRequired !== enginePack\.restartRequired/);
  assert.match(installSmoke, /feature_pack_truth_mismatch/);
  assert.match(installSmoke, /"total", "installed", "missing", "installing", "failed"/);
  assert.match(installSmoke, /feature_pack_schema_invalid/);
  assert.match(installSmoke, /rawFeaturePackApi\.ok\s*&& hasFeaturePackPayloadSchema\(rawFeaturePackApi\.payload\)/);
  assert.match(installSmoke, /argValue\("--resource-root"\)/);
  assert.match(installSmoke, /Packaged resource root is not a directory/);
  assert.match(installSmoke, /failureStage/);
  assert.match(installSmoke, /V8_AGENT_OS_HOME/);
  assert.match(installSmoke, /Promise\.all/);
  assert.match(installSmoke, /startupDurationMs/);
  assert.match(installSmoke, /startupBudgetMs/);
  assert.match(installSmoke, /validateReadinessResponse/);
  assert.match(installSmoke, /shell-control\.json/);
  assert.match(installSmoke, /surfaceReady/);
  assert.match(installSmoke, /isPidAlive/);
  assert.match(installSmoke, /desktop-pet\.json/);
  assert.match(installSmoke, /\/api\/pet\/health/);
  assert.match(installSmoke, /controlConnected/);
  assert.match(installSmoke, /desktopPetProcessRunning/);
  assert.match(installSmoke, /Object\.values\(checks\)\.every\(\(item\) => item\.ok\)/);
  const publicSmokePayload = installSmoke.slice(
    installSmoke.indexOf('const payload = {'),
    installSmoke.indexOf('const output = reportPath()'),
  );
  assert.doesNotMatch(publicSmokePayload, /stdout|stderr|desktopPetLaunch|stateRoot|shellExe|resourceRoot|logRef|lastError/);

  const portablePython = fs.readFileSync(
    path.join(repoRoot, 'scripts', 'desktop', 'prepare-windows-python-runtime.ps1'),
    'utf8',
  );
  assert.match(portablePython, /\$updatedLines\.Insert\(1, "\.\."\)/);
  assert.match(portablePython, /V8OS_ENGINE_IMPORT_OK/);

  const prepareRelease = fs.readFileSync(path.join(repoRoot, 'scripts', 'release', 'prepare-release.mjs'), 'utf8');
  assert.match(prepareRelease, /toUnifiedTag\(version\)/);
  assert.match(prepareRelease, /updates every enabled product/);
  assert.doesNotMatch(prepareRelease, /desktop-preview/);

  const releaseNotes = fs.readFileSync(path.join(repoRoot, 'scripts', 'release', 'generate-release-notes.mjs'), 'utf8');
  assert.match(releaseNotes, /UNIFIED_TAG_RE/);
  assert.match(releaseNotes, /LEGACY_PRODUCT_TAG_RE as LEGACY_TAG_RE/);
  assert.match(releaseNotes, /win-arm64-setup\.exe/);
  assert.match(releaseNotes, /GitHub Actions artifact/);
  assert.doesNotMatch(releaseNotes, /RUNTIME_PROBE-<platform>\.json/);
  assert.doesNotMatch(releaseNotes, /desktop-preview/);

  const releaseManifest = fs.readFileSync(path.join(repoRoot, 'scripts', 'release', 'release-manifest.mjs'), 'utf8');
  assert.match(releaseManifest, /export const UNIFIED_TAG_RE = \/\^v8-os-v/);
  assert.match(releaseManifest, /export const LEGACY_PRODUCT_TAG_RE = \/\^v8-os-\(phone\|desktop\)-v/);

  const baseline = fs.readFileSync(path.join(repoRoot, 'docs', 'V8OS', 'V8OS_RELEASE_VERSIONING_BASELINE_ZH.md'), 'utf8');
  assert.match(baseline, /v8-os-vYYYY\.MM\.DD\.N/);
  assert.match(baseline, /v8-os-desktop-vYYYY\.MM\.DD\.N/);
  assert.match(baseline, /Windows x64\/ARM64/);
  assert.match(baseline, /Actions artifact/);
  assert.match(baseline, /Windows ARM64 兼容性技术债登记/);
  assert.match(baseline, /关闭后重开读取/);
  assert.match(baseline, /Linux DEB 非 root 启动门禁/);
  assert.match(baseline, /Secret Service/);
  assert.match(baseline, /90 秒/);
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
