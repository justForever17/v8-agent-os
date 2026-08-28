const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const adminRoot = path.resolve(__dirname, "..");

test("Topbar uses feature pack API instead of legacy runtime install API", () => {
  const topbarSource = fs.readFileSync(path.join(adminRoot, "src", "components", "layout", "Topbar.tsx"), "utf8");
  const installerSource = fs.readFileSync(path.join(adminRoot, "src", "lib", "server", "runtime-feature-packs.ts"), "utf8");
  const featurePackSnapshotSource = fs.readFileSync(path.join(adminRoot, "src", "lib", "server", "engine-feature-pack-snapshot.ts"), "utf8");

  assert.match(topbarSource, /\/api\/runtime-feature-packs/);
  assert.doesNotMatch(topbarSource, /\/api\/runtime-install/);
  assert.match(topbarSource, /v8os:open-feature-packs/);
  assert.match(topbarSource, /loadInstallState = useCallback\(async \(force = false, silent = false, refreshHealth = false\)/);
  assert.match(topbarSource, /loadInstallState\(false, installState !== null\)/);
  assert.match(topbarSource, /loadInstallState\(true, true, true\)/);
  assert.match(topbarSource, /\/api\/runtime-feature-packs\?refresh=1/);
  assert.match(topbarSource, /installLoading && !installState/);
  assert.match(topbarSource, /loadInstallState\(true, true, healthRefreshing \|\| packInstalling\)/);
  assert.match(topbarSource, /healthRefreshing \? installState\?\.retryAfterMs \|\| 1_500 : 5_000/);
  assert.match(installerSource, /productName: "可选本地识别包"/);
  assert.match(installerSource, /readEngineFeaturePackSnapshot/);
  assert.doesNotMatch(installerSource, /readEngineHealthSnapshot/);
  assert.match(installerSource, /engineFeaturePackSnapshotIsAuthoritative/);
  assert.match(installerSource, /mergeFeaturePackTruth\(configPacks, enginePacks, engineHealth\.updatedAt\)/);
  const truthSource = fs.readFileSync(path.join(adminRoot, "src", "lib", "server", "runtime-feature-pack-truth.ts"), "utf8");
  assert.match(truthSource, /return \{\s*\.\.\.configPack,\s*\.\.\.enginePack/);
  assert.match(truthSource, /restartRequired: boolean/);
  assert.doesNotMatch(installerSource, /fetch\(`\$\{resolveEngineOrigin\(\)\}\/health`/);
  assert.match(featurePackSnapshotSource, /\/v1\/runtime-feature-packs\/status/);
  assert.match(featurePackSnapshotSource, /"x-v8-agent-os-secret": input\.internalSecret/);
  assert.match(featurePackSnapshotSource, /SNAPSHOT_REQUEST_TIMEOUT_MS = 3_000/);
  assert.match(featurePackSnapshotSource, /snapshotRequests = new Map<string, Promise<SnapshotCache>>/);
});

test("feature pack dropdown keeps actions inside a compact bounded scroll surface", () => {
  const topbarSource = fs.readFileSync(path.join(adminRoot, "src", "components", "layout", "Topbar.tsx"), "utf8");

  assert.match(topbarSource, /fixed left-2 right-2 top-12[^\n]*max-h-\[40rem\][^\n]*overflow-hidden/);
  assert.match(topbarSource, /sm:absolute sm:left-auto sm:right-0 sm:top-full sm:h-\[calc\(100dvh-5\.5rem\)\]/);
  assert.match(topbarSource, /className="h-full min-w-0 max-w-full overflow-x-hidden"/);
  assert.match(topbarSource, /visibleFeaturePacks = .*pack\.installable \|\| pack\.installed/);
  assert.match(topbarSource, /rounded-lg[^\n]*sm:w-\[22rem\]/);
  assert.match(topbarSource, /className="h-7 shrink-0 rounded-md px-2\.5"/);
  assert.doesNotMatch(topbarSource, /const description = t\(`/);
  assert.doesNotMatch(topbarSource, /pack\.runtimeFamilies\.map/);
  assert.match(topbarSource, /scrollbarClassName="w-2 /);
});

test("feature pack API exposes GET/POST and legacy runtime install is deprecated", () => {
  const routeSource = fs.readFileSync(path.join(adminRoot, "src", "app", "api", "runtime-feature-packs", "route.ts"), "utf8");
  const legacySource = fs.readFileSync(path.join(adminRoot, "src", "lib", "server", "runtime-install.ts"), "utf8");

  assert.match(routeSource, /export async function GET/);
  assert.match(routeSource, /export async function POST/);
  assert.match(routeSource, /dryRun/);
  assert.match(routeSource, /triggerFeaturePackInstall/);
  assert.match(routeSource, /forceHealthRefresh: req\.nextUrl\.searchParams\.get\("refresh"\) === "1"/);
  assert.match(routeSource, /feature_pack_install_failed/);
  assert.match(routeSource, /feature_pack_lock_unavailable/);
  assert.match(routeSource, /feature_pack_python_runtime_unavailable/);
  assert.doesNotMatch(routeSource, /\{ error: error instanceof Error \? error\.message/);
  assert.match(routeSource, /function publicFeaturePackState/);
  assert.match(routeSource, /function publicFeaturePackInstallResult/);
  assert.match(routeSource, /logName: compactLogName\(pack\.logRef\)/);
  assert.match(routeSource, /hasError: Boolean\(pack\.lastError\)/);
  assert.doesNotMatch(routeSource, /commandSummary:/);
  assert.doesNotMatch(routeSource, /targetDir:/);
  assert.doesNotMatch(routeSource, /requirementsFile:/);
  assert.match(legacySource, /deprecated:\s*true/);
  assert.match(legacySource, /replacement:\s*"\/api\/runtime-feature-packs"/);
  assert.doesNotMatch(legacySource, /bootstrap\.ps1/);
  assert.doesNotMatch(legacySource, /desktop dependencies/i);
});

test("feature pack installer retries official PyPI through trusted zh mirrors without exposing raw pip logs", () => {
  const installerSource = fs.readFileSync(path.join(adminRoot, "src", "lib", "server", "runtime-feature-packs.ts"), "utf8");
  const topbarSource = fs.readFileSync(path.join(adminRoot, "src", "components", "layout", "Topbar.tsx"), "utf8");

  const officialIndex = installerSource.indexOf('id: "official"');
  const tunaIndex = installerSource.indexOf('id: "tuna"');
  const ustcIndex = installerSource.indexOf('id: "ustc"');
  const aliyunIndex = installerSource.indexOf('id: "aliyun"');
  assert.ok(officialIndex > -1, "official source is missing");
  assert.ok(tunaIndex > officialIndex, "TUNA mirror must be tried after official PyPI");
  assert.ok(ustcIndex > tunaIndex, "USTC mirror must be tried after TUNA");
  assert.ok(aliyunIndex > ustcIndex, "Aliyun mirror must be tried after USTC");

  assert.match(installerSource, /https:\/\/pypi\.tuna\.tsinghua\.edu\.cn\/simple/);
  assert.match(installerSource, /https:\/\/pypi\.mirrors\.ustc\.edu\.cn\/simple/);
  assert.match(installerSource, /https:\/\/mirrors\.aliyun\.com\/pypi\/simple/);
  assert.match(installerSource, /sourceStrategy/);
  assert.match(installerSource, /function pipSourceStrategy\(locale: string\)/);
  assert.match(installerSource, /normalized\.startsWith\("zh"\)/);
  assert.match(installerSource, /source\.id !== "official"/);
  assert.match(installerSource, /No matching distribution found/);
  assert.match(topbarSource, /JSON\.stringify\(\{ packId, locale \}\)/);
  assert.match(installerSource, /Could not fetch URL/);
  assert.match(installerSource, /No matching distribution found/);
  assert.match(installerSource, /runFeaturePackInstallSequence/);
  assert.ok(installerSource.indexOf("if (dryRun)") < installerSource.indexOf("fs.mkdirSync(featurePackLogRoot()"));
  const triggerSource = installerSource.slice(installerSource.indexOf("export async function triggerFeaturePackInstall"));
  assert.ok(
    triggerSource.indexOf("if (dryRun)") < triggerSource.indexOf("if (await reconcileInterruptedFeaturePackInstalls(config))"),
    "dry-run must return before interrupted-install reconciliation",
  );

  assert.doesNotMatch(topbarSource, /commandSummary/);
  assert.doesNotMatch(topbarSource, /stdout|stderr/);
});

test("feature pack installation detects hardware but trusts only a format-specific validated provider", () => {
  const installerSource = fs.readFileSync(path.join(adminRoot, "src", "lib", "server", "runtime-feature-packs.ts"), "utf8");
  const routeSource = fs.readFileSync(path.join(adminRoot, "src", "app", "api", "runtime-feature-packs", "route.ts"), "utf8");
  const motionManifest = JSON.parse(fs.readFileSync(path.join(adminRoot, "..", "v8-agent-os-engine", "requirements", "feature-packs", "creative-media-motion-capture.manifest.json"), "utf8"));
  const imageManifest = JSON.parse(fs.readFileSync(path.join(adminRoot, "..", "v8-agent-os-engine", "requirements", "feature-packs", "creative-media-image-analysis.manifest.json"), "utf8"));

  assert.deepEqual(motionManifest.smokeCheck, { kind: "mediapipe_task", task: "holistic_landmarker", preferGpu: true });
  assert.deepEqual(imageManifest.smokeCheck, { kind: "onnx", preferGpu: true });
  assert.match(installerSource, /Get-CimInstance Win32_VideoController/);
  assert.match(installerSource, /hardwareGpuAdapters/);
  assert.match(installerSource, /BaseOptions\.Delegate\.GPU/);
  assert.match(installerSource, /\[GPU fallback\]/);
  assert.match(installerSource, /validating CPU in a fresh process/);
  assert.match(installerSource, /selectedExecutionProvider/);
  assert.match(installerSource, /environment,/);
  assert.match(installerSource, /smokeCheck: smokeResult/);
  assert.match(routeSource, /LOCALE_COOKIE_NAME/);
});

test("feature pack installation targets the managed Engine Python before a development venv", () => {
  const installerSource = fs.readFileSync(path.join(adminRoot, "src", "lib", "server", "runtime-feature-packs.ts"), "utf8");
  const managedRuntimeIndex = installerSource.indexOf('path.join(engineRoot, ".python", "python.exe")');
  const configuredRuntimeIndex = installerSource.indexOf("if (configured && fs.existsSync(configured)) return configured");
  const developmentVenvIndex = installerSource.indexOf('path.join(engineRoot, ".venv", "Scripts", "python.exe")');

  assert.ok(managedRuntimeIndex > -1, "managed Engine Python candidate is missing");
  assert.ok(configuredRuntimeIndex > managedRuntimeIndex, "configured fallback must not override an installed managed runtime");
  assert.ok(developmentVenvIndex > configuredRuntimeIndex, "development venv must remain the final source-tree fallback");
  assert.match(installerSource, /V8_ENGINE_PYTHON/);
});

test("feature pack cards localize metadata and do not expose raw installer errors", () => {
  const topbarSource = fs.readFileSync(path.join(adminRoot, "src", "components", "layout", "Topbar.tsx"), "utf8");
  const en = JSON.parse(fs.readFileSync(path.join(adminRoot, "src", "i18n", "locales", "en.json"), "utf8"));
  const zh = JSON.parse(fs.readFileSync(path.join(adminRoot, "src", "i18n", "locales", "zh-CN.json"), "utf8"));

  for (const packId of ["document_ingestion", "computer_use_desktop", "rpa_automation", "local_asr_ocr", "creative_media_image_analysis", "creative_media_motion_capture"]) {
    for (const field of ["name", "description", "hover"]) {
      const key = `components.layout.Topbar.featurePack.${packId}.${field}`;
      assert.ok(en[key], `missing English ${key}`);
      assert.ok(zh[key], `missing Chinese ${key}`);
    }
  }
  assert.match(topbarSource, /featurePackInstallFailedDetail/);
  assert.match(topbarSource, /const anotherPackInstalling = Boolean/);
  assert.match(topbarSource, /const canInstall = showInstall && !isInstalling && !anotherPackInstalling/);
  assert.match(topbarSource, /description: t\("components\.layout\.Topbar\.featurePackInstallFailedDescription"\)/);
  assert.doesNotMatch(topbarSource, /description: error instanceof Error \? error\.message/);
  assert.doesNotMatch(topbarSource, /pack\.lastError|pack\.logRef/);
  assert.match(topbarSource, /pack\.hasError/);
  assert.match(topbarSource, /pack\.logName/);
});

test("image analysis feature pack uses a pinned asset transaction and never a silent runtime download", () => {
  const installerSource = fs.readFileSync(path.join(adminRoot, "src", "lib", "server", "runtime-feature-packs.ts"), "utf8");
  const managedNextSource = fs.readFileSync(path.join(adminRoot, "..", "..", "scripts", "run-next-with-managed-auth.mjs"), "utf8");
  const engineSource = fs.readFileSync(path.join(adminRoot, "..", "v8-agent-os-engine", "core", "runtime", "feature_packs.py"), "utf8");
  const manifest = JSON.parse(fs.readFileSync(path.join(adminRoot, "..", "v8-agent-os-engine", "requirements", "feature-packs", "creative-media-image-analysis.manifest.json"), "utf8"));
  const assetInstallSource = installerSource.slice(
    installerSource.indexOf("async function runTransactionalAssetPackInstall"),
    installerSource.indexOf("async function runFeaturePackInstallSequence"),
  );

  assert.equal(manifest.id, "creative_media_image_analysis");
  assert.equal(manifest.assets[0].size, 178648008);
  assert.equal(String(manifest.assets[0].sha256).toLowerCase(), "60920e99c45464f2ba57bee2ad08c919a52bbf852739e96947fbb4358c0d964a");
  assert.match(installerSource, /runTransactionalAssetPackInstall/);
  assert.match(installerSource, /staging/);
  assert.match(installerSource, /receipt\.json/);
  assert.match(installerSource, /sha256File/);
  assert.match(installerSource, /reuseVerifiedFeaturePackAsset/);
  assert.match(installerSource, /\[Asset reused\]/);
  assert.match(assetInstallSource, /const lockFile = lockPathFor\(definition, environment\)/);
  assert.match(assetInstallSource, /lockFile \|\| requirementsFile/);
  assert.match(assetInstallSource, /Boolean\(lockFile\)/);
  assert.match(assetInstallSource, /lockSha256: lockFile \? sha256File\(lockFile\) : null/);
  assert.match(installerSource, /fs\.linkSync\(existingPath, stagingPath\)/);
  assert.match(installerSource, /backup/);
  assert.match(installerSource, /FEATURE_PACK_ASSET_HOSTS/);
  assert.match(installerSource, /FEATURE_PACK_ASSET_MAX_REDIRECTS = 3/);
  assert.match(installerSource, /redirect: "manual"/);
  assert.match(installerSource, /new URL\(location, currentUrl\)/);
  assert.match(installerSource, /feature_pack_asset_redirect_limit/);
  assert.match(installerSource, /new Transform/);
  assert.match(installerSource, /attemptOffset \+ received > asset\.size/);
  assert.match(installerSource, /feature_pack_asset_size_exceeded/);
  assert.match(installerSource, /featurePackAssetSources/);
  assert.match(installerSource, /function featurePackAssetSources\(asset: FeaturePackAsset, locale = "en"\)/);
  assert.match(installerSource, /host === "hf-mirror\.com"/);
  assert.match(installerSource, /fetchFeaturePackAssetOverIpv4/);
  assert.match(installerSource, /family: 4/);
  assert.match(installerSource, /x-v8-asset-transport/);
  assert.match(installerSource, /feature_pack_asset_sources_exhausted/);
  assert.match(installerSource, /hostname\.endsWith\("\.hf\.co"\)/);
  assert.match(installerSource, /\[Asset source recovered\]/);
  assert.match(installerSource, /normalizeStatus\(existing\.status\) === "installing"/);
  assert.match(installerSource, /本次请求未重复启动下载/);
  assert.match(managedNextSource, /V8_AGENT_OS_REPO_ROOT:\s*repoRoot/);
  assert.match(managedNextSource, /V8_ENGINE_DIR:\s*path\.join\(repoRoot, "apps", "v8-agent-os-engine"\)/);
  assert.doesNotMatch(engineSource, /requests\.(get|post)|urlopen|httpx\.(get|post)/);
});

test("dependency-gated runtime pages stay unmounted until the installed runtime is ready", () => {
  const desktopSource = fs.readFileSync(path.join(adminRoot, "src", "app", "admin", "(dashboard)", "desktop-automation", "page.tsx"), "utf8");
  const rpaSource = fs.readFileSync(path.join(adminRoot, "src", "app", "admin", "(dashboard)", "rpa", "page.tsx"), "utf8");

  assert.match(desktopSource, /computer_use_desktop/);
  assert.match(desktopSource, /v8os:open-feature-packs/);
  assert.match(desktopSource, /type FeaturePackGateState = "unknown" \| "missing" \| "restart_required" \| "ready"/);
  assert.match(desktopSource, /featurePack\.restartRequired !== false/);
  assert.match(desktopSource, /runtimeCapabilityCanBeEnabled\(runtimeCapability\)/);
  assert.match(desktopSource, /capability\?\.availability === "disabled_by_policy"/);
  assert.match(desktopSource, /availability\?\.available === true/);
  assert.match(desktopSource, /availability\.environmentProbe\?\.state === "fresh"/);
  assert.match(desktopSource, /!availabilityChecked \|\| !computerUseProbeReady\(availability\)/);
  assert.match(desktopSource, /availabilityChecked \? "failed" : "refreshing"/);
  assert.match(desktopSource, /const retryAvailability = \(\) =>/);
  assert.match(desktopSource, /setAvailabilityRefreshKey\(\(current\) => current \+ 1\)/);
  assert.match(desktopSource, /if \(enabled && featurePackState !== "ready"\)/);
  assert.match(desktopSource, /disabled=\{runtimeSaving \|\| runtimeCapability\?\.policy\?\.enabled === false\}/);
  assert.ok(
    desktopSource.indexOf('if (enabled && featurePackState !== "ready")') < desktopSource.indexOf('fetch("/api/runtime-capabilities/computer_use/policy"'),
    "Computer Use policy writes must remain unreachable until every readiness gate passes",
  );
  assert.match(rpaSource, /rpa_automation/);
  assert.match(rpaSource, /v8os:open-feature-packs/);
  assert.match(rpaSource, /type FeaturePackGateState = "unknown" \| "missing" \| "restart_required" \| "ready"/);
  assert.match(rpaSource, /restartRequired\?: boolean/);
  assert.match(rpaSource, /runtimeCapabilityCanBeEnabled\(rpaCapability\)/);
  assert.match(rpaSource, /capability\?\.availability === "disabled_by_policy"/);
  assert.match(rpaSource, /\/api\/rpa\/availability/);
  assert.match(rpaSource, /availability\?\.robotFramework === true/);
  assert.match(rpaSource, /availability\?\.rpaFramework === true/);
  assert.match(rpaSource, /availability\?\.libraries\?\.\["RPA\.Browser\.Selenium"\] === true/);
  assert.match(rpaSource, /availability\?\.libraries\?\.\["RPA\.Excel\.Files"\] === true/);
  assert.match(rpaSource, /featurePackState !== "ready"/);
  assert.match(rpaSource, /featurePackState === "missing"/);
  assert.ok(
    rpaSource.indexOf('{featurePackState !== "ready" ?') < rpaSource.lastIndexOf("<RPAWorkbench />"),
    "RPA Workbench must remain unmounted until the Engine route and dependency probes are ready",
  );
});

test("feature packs publish immutable version targets with journal recovery and RPA dry-run smoke", () => {
  const installerSource = fs.readFileSync(path.join(adminRoot, "src", "lib", "server", "runtime-feature-packs.ts"), "utf8");
  const journalSource = fs.readFileSync(path.join(adminRoot, "src", "lib", "server", "feature-pack-transaction-journal.ts"), "utf8");
  const triggerStart = installerSource.indexOf("export async function triggerFeaturePackInstall");
  const triggerSource = installerSource.slice(triggerStart);

  assert.match(installerSource, /smokeModules: \["robot", "RPA", "RPA\.Browser\.Selenium", "RPA\.Excel\.Files"\]/);
  assert.match(installerSource, /smokeModulesByPlatform: \{ win32: \["RPA\.Windows"\] \}/);
  assert.match(installerSource, /function smokeModulesFor\(definition: FeaturePackDefinition\)/);
  assert.match(installerSource, /runTransactionalPythonPackInstall/);
  assert.match(installerSource, /runPythonImportSmokeCheck/);
  assert.match(installerSource, /runFeaturePackDependencyCompatibilityCheck/);
  assert.match(installerSource, /verify_feature_pack_dependencies\.py/);
  assert.match(installerSource, /Feature pack dependency compatibility validation failed/);
  assert.match(installerSource, /module_not_loaded_from_staging/);
  assert.match(installerSource, /smokeCheck: smokeResult/);
  assert.match(installerSource, /environment,/);
  assert.match(installerSource, /runpy\.run_module\('robot',run_name='__main__'\)/);
  assert.match(installerSource, /const libraries = \["RPA\.Browser\.Selenium", "RPA\.Excel\.Files"\]/);
  assert.match(installerSource, /libraries\.map\(\(library\) => `Library    \$\{library\}`\)/);
  assert.match(installerSource, /if \(process\.platform === "win32"\) libraries\.push\("RPA\.Windows"\)/);
  assert.match(installerSource, /Open Available Browser/);
  assert.match(installerSource, /Create Workbook/);
  assert.match(installerSource, /Windows Run/);
  assert.match(installerSource, /"-I",\s*"-S"/);
  assert.match(installerSource, /"--only-binary=:all:",\s*"--require-hashes",\s*"--no-deps"/);
  assert.match(installerSource, /normalized === "x64"/);
  assert.match(installerSource, /fs\.renameSync\(journal\.paths\.stagingRoot, journal\.paths\.versionRoot\)/);
  assert.match(installerSource, /transitionFeaturePackInstallJournal\(featurePackRoot\(\), journal, "staged"/);
  assert.match(installerSource, /transitionFeaturePackInstallJournal\(featurePackRoot\(\), journal, "published"/);
  assert.match(installerSource, /transitionFeaturePackInstallJournal\(featurePackRoot\(\), journal, "commit_pending"/);
  assert.match(journalSource, /path\.join\(canonicalRoot, packId, "versions", operationId\)/);
  assert.match(journalSource, /\.journal/);
  assert.doesNotMatch(installerSource, /fs\.renameSync\(stagingRoot, packRoot\)/);
  assert.doesNotMatch(installerSource, /backupRoot/);
  assert.doesNotMatch(installerSource, /remove_new_pack|restore_previous_pack/);
  assert.match(installerSource, /journal\.backup\.state\.assetRoot/);
  assert.match(installerSource, /previousModelRoots\(journal\)/);
  assert.match(installerSource, /fs\.linkSync\(existingPath, stagingPath\)/);
  assert.match(installerSource, /fs\.copyFileSync\(existingPath, stagingPath\)/);
  assert.doesNotMatch(triggerSource, /fs\.mkdirSync\(targetDir/);
});

test("feature pack transactions use Engine authority, bounded workers, and compatible receipts", () => {
  const installerSource = fs.readFileSync(path.join(adminRoot, "src", "lib", "server", "runtime-feature-packs.ts"), "utf8");

  assert.doesNotMatch(installerSource, /writeCanonicalConfig/);
  assert.match(installerSource, /\/v1\/runtime-feature-packs\/\$\{encodeURIComponent\(packId\)\}\/state/);
  assert.match(installerSource, /"x-v8-agent-os-secret": internalSecret/);
  assert.match(installerSource, /expectedOperationId/);
  assert.match(installerSource, /crypto\.randomUUID\(\)/);
  assert.match(installerSource, /ACTIVE_FEATURE_PACK_INSTALLS/);
  assert.match(installerSource, /PENDING_FEATURE_PACK_INSTALLS/);
  assert.match(installerSource, /FEATURE_PACK_INSTALL_RESERVATION/);
  assert.match(installerSource, /feature_pack_install_busy/);
  assert.match(installerSource, /feature_pack_install_interrupted/);
  assert.match(installerSource, /FEATURE_PACK_STALE_INSTALL_MS = 90 \* 60_000/);
  assert.match(installerSource, /featurePackInstallIsStale/);
  assert.match(installerSource, /cleanupInterruptedStaging/);
  const journalSource = fs.readFileSync(path.join(adminRoot, "src", "lib", "server", "feature-pack-transaction-journal.ts"), "utf8");
  assert.match(journalSource, /`\$\{packId\}-\$\{operationId\}`/);
  assert.doesNotMatch(installerSource, /`\$\{definition\.id\}-\$\{Date\.now\(\)\}`/);
  const reconcileStart = installerSource.indexOf("async function reconcileInterruptedFeaturePackInstalls");
  const reconcileEnd = installerSource.indexOf("function resolvePythonExecutable", reconcileStart);
  const reconcileSource = installerSource.slice(reconcileStart, reconcileEnd);
  assert.ok(
    reconcileSource.indexOf("await updateFeaturePackConfig") < reconcileSource.indexOf("cleanupInterruptedStaging"),
    "stale install ownership must be committed before staging cleanup",
  );
  const triggerStart = installerSource.indexOf("export async function triggerFeaturePackInstall");
  const triggerSource = installerSource.slice(triggerStart);
  assert.ok(
    triggerSource.indexOf("await updateFeaturePackConfig")
      < triggerSource.indexOf("ACTIVE_FEATURE_PACK_INSTALLS.set"),
    "an install must acquire the Engine CAS lease before it is marked active locally",
  );
  assert.match(
    reconcileSource,
    /cleanupInterruptedStaging\(definition\.id, operationId\)/,
  );
  assert.match(installerSource, /FEATURE_PACK_PIP_TIMEOUT_MS/);
  assert.match(installerSource, /const pipDeadline = Date\.now\(\) \+ FEATURE_PACK_PIP_TIMEOUT_MS/);
  assert.match(installerSource, /runPipAttempt\(pythonExe, args, output, remainingMs\)/);
  assert.match(installerSource, /"--ignore-installed"/);
  assert.match(installerSource, /"--report"/);
  assert.match(installerSource, /function readPipResolutionReport/);
  assert.match(installerSource, /resolvedPackages,/);
  assert.match(installerSource, /dependencyCompatibility,/);
  assert.match(installerSource, /FEATURE_PACK_ASSET_TIMEOUT_MS/);
  assert.match(installerSource, /FEATURE_PACK_ASSET_SOURCE_CONNECT_TIMEOUT_MS/);
  assert.match(installerSource, /FEATURE_PACK_ASSET_STREAM_RETRY_LIMIT = 3/);
  assert.match(installerSource, /content-range/);
  assert.match(installerSource, /feature_pack_asset_resume_contract_invalid/);
  assert.match(installerSource, /\[Asset stream retry\]/);
  assert.match(installerSource, /const sourceController = new AbortController\(\)/);
  assert.match(installerSource, /removeEventListener\("abort", abortSource\)/);
  assert.match(installerSource, /Boolean\(lockFile\)/);
  assert.match(installerSource, /"--allow-missing"/);
  assert.match(installerSource, /terminateFeaturePackChild/);
  assert.match(installerSource, /waitForFeaturePackChildExit/);
  assert.match(installerSource, /process\.kill\(-child\.pid, "SIGKILL"\)/);
  assert.match(installerSource, /await waitForFeaturePackChildExit\(child, 5_000\)/);
  assert.match(installerSource, /feature_pack_worker_termination_unconfirmed/);
  assert.match(installerSource, /provider === "GPU" \? 20_000 : 180_000/);
  assert.match(installerSource, /void terminateFeaturePackChild\(child\)/);
  assert.match(installerSource, /preserveStaging: workerTerminationUnconfirmed/);
  assert.doesNotMatch(installerSource, /\[Backup cleanup warning\]/);
  assert.match(installerSource, /function createGovernedFeaturePackLog/);
  assert.match(installerSource, /fs\.openSync\(logRef, "a"\)/);
  assert.match(installerSource, /FEATURE_PACK_LOG_ERRORS\.set\(output, error\)/);
  assert.match(installerSource, /assertFeaturePackLogHealthy\(output\)/);
  assert.match(installerSource, /\.catch\(async \(error\) =>/);
  assert.match(installerSource, /const patchMatches = Object\.entries\(patch\)\.every/);
  assert.match(installerSource, /if \(responseReceived\) throw error/);
  assert.match(installerSource, /FEATURE_PACK_COMMIT_BLOCKING_STATUSES = new Set\(\[401, 409, 422\]\)/);
  assert.match(installerSource, /isBlockingFeaturePackCommitError\(error\) \? "commit_blocked" : "commit_pending"/);
  assert.match(installerSource, /listFeaturePackInstallJournals\(featurePackRoot\(\)\)/);
  assert.match(installerSource, /publishStagedFeaturePackJournal/);
  assert.match(installerSource, /commitPublishedFeaturePackJournal/);
  assert.ok(
    triggerSource.indexOf("createFeaturePackInstallJournal")
      < triggerSource.indexOf("await updateFeaturePackConfig"),
    "the durable operation journal must exist before the Engine CAS lease is acquired",
  );
  assert.ok(
    triggerSource.indexOf("PENDING_FEATURE_PACK_INSTALLS.set")
      < triggerSource.indexOf("await updateFeaturePackConfig"),
    "reconciliation must not reclaim the CAS handoff window before local active ownership is visible",
  );
  const leasePatchStart = triggerSource.indexOf("await updateFeaturePackConfig");
  const leasePatchEnd = triggerSource.indexOf("}, String(existing.operationId", leasePatchStart);
  assert.doesNotMatch(
    triggerSource.slice(leasePatchStart, leasePatchEnd),
    /targetDir|assetRoot|receiptRef/,
    "the installing lease must retain the previous compatible target until immutable publish commits",
  );
  assert.match(installerSource, /requirements:\s*\{[\s\S]*?sha256:/);
  assert.match(installerSource, /previousEnvironment\.platform \|\| ""\) !== environment\.platform/);
  assert.match(installerSource, /pythonMinor\(previousEnvironment\.pythonVersion\)/);
  assert.match(installerSource, /normalizedArchitecture\(previousEnvironment\.architecture\)/);
  assert.match(installerSource, /String\(requirements\.sha256/);
  assert.match(installerSource, /!manifest \|\| String\(receipt\.packVersion \|\| ""\) === manifest\.version/);
  assert.match(installerSource, /sys\.path\.insert\(0,root\)/);
  assert.match(installerSource, /'win32','win32\/lib','Pythonwin'/);
  assert.match(installerSource, /import pywin32_bootstrap/);
  assert.match(installerSource, /module_not_loaded_from_staging/);
});

test("RPA platform lock files are complete, hashed, and curated", () => {
  const lockRoot = path.join(adminRoot, "..", "..", "apps", "v8-agent-os-engine", "requirements", "feature-packs", "locks");
  for (const [platformName, architectures, expectedCount] of [
    ["windows", ["x64", "arm64"], 49],
    ["linux", ["x64", "arm64"], 39],
    ["macos", ["x64", "arm64"], 39],
  ]) {
    for (const architecture of architectures) {
      const file = path.join(lockRoot, `rpa-automation-cp311-${platformName}-${architecture}.txt`);
      const lines = fs.readFileSync(file, "utf8").split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
      assert.equal(lines.length, expectedCount, file);
      assert.ok(lines.every((line) => /^[A-Za-z0-9][A-Za-z0-9_.-]*==[^\s]+ --hash=sha256:[0-9a-f]{64}$/.test(line)), file);
      const names = lines.map((line) => line.split("==", 1)[0].toLowerCase().replace(/[-_.]+/g, "-"));
      assert.equal(new Set(names).size, names.length, file);
      assert.ok(names.includes("robotframework"), file);
      assert.ok(names.includes("rpaframework"), file);
      assert.ok(names.includes("robotframework-seleniumlibrary"), file);
      assert.ok(!names.includes("rpaframework-recognition"), file);
      assert.ok(!names.includes("robotframework-sapguilibrary"), file);
      assert.equal(names.includes("rpaframework-windows"), platformName === "windows", file);
    }
  }
});

test("image analysis platform lock files are complete and fail closed on unsupported macOS x64", () => {
  const lockRoot = path.join(adminRoot, "..", "..", "apps", "v8-agent-os-engine", "requirements", "feature-packs", "locks");
  for (const [platformName, architectures] of [
    ["windows", ["x64", "arm64"]],
    ["linux", ["x64", "arm64"]],
    ["macos", ["arm64"]],
  ]) {
    for (const architecture of architectures) {
      const file = path.join(lockRoot, `creative-media-image-analysis-cp311-${platformName}-${architecture}.txt`);
      const lines = fs.readFileSync(file, "utf8").split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
      assert.equal(lines.length, 5, file);
      assert.ok(lines.every((line) => /^[A-Za-z0-9][A-Za-z0-9_.-]*==[^\s]+ --hash=sha256:[0-9a-f]{64}$/.test(line)), file);
      assert.deepEqual(lines.map((line) => line.split("==", 1)[0]), ["flatbuffers", "numpy", "onnxruntime", "packaging", "protobuf"]);
      if (platformName === "linux" && architecture === "x64") {
        assert.equal(
          lines[1],
          "numpy==2.3.5 --hash=sha256:8cba086a43d54ca804ce711b2a940b16e452807acebe7852ff327f1ecd49b0d4",
          file,
        );
      } else {
        assert.match(lines[1], /^numpy==2\.4\.6 --hash=sha256:[0-9a-f]{64}$/, file);
      }
    }
  }
  assert.equal(
    fs.existsSync(path.join(lockRoot, "creative-media-image-analysis-cp311-macos-x64.txt")),
    false,
  );
  const manifest = JSON.parse(fs.readFileSync(
    path.join(adminRoot, "..", "..", "apps", "v8-agent-os-engine", "requirements", "feature-packs", "creative-media-image-analysis.manifest.json"),
    "utf8",
  ));
  assert.match(manifest.assets[0].url, /^https:\/\/huggingface\.co\//);
  assert.ok(manifest.assets[0].mirrors.some((url) => url.startsWith("https://hf-mirror.com/")));
  assert.equal(manifest.assets[0].sha256, "60920e99c45464f2ba57bee2ad08c919a52bbf852739e96947fbb4358c0d964a");
});

test("feature pack child processes receive an explicit non-secret environment and empty packs are disabled", () => {
  const installerSource = fs.readFileSync(path.join(adminRoot, "src", "lib", "server", "runtime-feature-packs.ts"), "utf8");

  assert.match(installerSource, /const FEATURE_PACK_CHILD_ENV_KEYS = new Set/);
  assert.match(installerSource, /function featurePackChildEnv/);
  assert.match(installerSource, /env: featurePackChildEnv\(/);
  assert.doesNotMatch(installerSource, /env:\s*\{\s*\.\.\.process\.env/);
  for (const sensitiveName of ["AUTH_SECRET", "NEXTAUTH_SECRET", "EXPO_TOKEN", "OPENAI_API_KEY"]) {
    assert.doesNotMatch(installerSource.slice(
      installerSource.indexOf("const FEATURE_PACK_CHILD_ENV_KEYS"),
      installerSource.indexOf("export const PIP_SOURCE_STRATEGY"),
    ), new RegExp(sensitiveName));
  }
  assert.match(installerSource, /function requirementsEntries\(requirementsFile: string\)/);
  assert.match(installerSource, /id: "local_asr_ocr"[\s\S]*?enabled: false/);
  assert.match(installerSource, /smokeModulesByPlatform: \{ win32: \["pywinauto", "pycaw"\] \}/);
  assert.match(installerSource, /id: "document_ingestion"[\s\S]*?requirementsFile: "document-ingestion\.txt"[\s\S]*?smokeModules: \["openpyxl", "xlrd", "docx", "pptx", "pymupdf", "tabulate"\]/);
  assert.match(installerSource, /if \(definition\.enabled === false\) return false/);
  assert.match(installerSource, /requirementsEntries\(requirementsFile\)\.length > 0/);
  assert.match(installerSource, /throw new Error\("feature_pack_not_available"\)/);
});

test("feature pack smoke loads staging first and treats cross-volume origins as outside", () => {
  const installerSource = fs.readFileSync(path.join(adminRoot, "src", "lib", "server", "runtime-feature-packs.ts"), "utf8");

  assert.match(installerSource, /sys\.path\.insert\(0,root\)/);
  assert.match(installerSource, /def is_from_staging\(root,item\):/);
  assert.match(installerSource, /except ValueError:[\s\S]*?return False/);
  assert.doesNotMatch(installerSource, /sys\.path\.append\(root\)/);
  assert.doesNotMatch(installerSource, /os\.path\.commonpath\(\[root,(?:origin|item)\]\)/);
});

test("runtime navigation keeps beta only on dependency-gated runtimes and places them last", () => {
  const navSource = fs.readFileSync(path.join(adminRoot, "src", "lib", "admin-navigation.ts"), "utf8");
  const runtimeGroup = navSource.slice(navSource.indexOf('id: "runtimes"'), navSource.indexOf('id: "capabilities"'));

  const itemBlock = (href) => {
    const start = runtimeGroup.indexOf(`href: "${href}"`);
    assert.ok(start > -1, `missing nav item ${href}`);
    const next = runtimeGroup.indexOf('href: "', start + 1);
    return runtimeGroup.slice(start, next > -1 ? next : runtimeGroup.length);
  };

  assert.match(itemBlock("/admin/desktop-automation"), /badge: \{ label: "lib\.admin\.navigation\.kdb4add74", tone: "beta" \}/);
  assert.match(itemBlock("/admin/rpa"), /badge: \{ label: "lib\.admin\.navigation\.kdb4add74", tone: "beta" \}/);
  assert.doesNotMatch(itemBlock("/admin/plugins"), /tone: "beta"/);
  assert.doesNotMatch(itemBlock("/admin/network-supervisor-runtime"), /tone: "beta"/);
  assert.doesNotMatch(itemBlock("/admin/creative-media"), /tone: "beta"/);

  const desktopIndex = runtimeGroup.indexOf('href: "/admin/desktop-automation"');
  const rpaIndex = runtimeGroup.indexOf('href: "/admin/rpa"');
  const creativeIndex = runtimeGroup.indexOf('href: "/admin/creative-media"');
  const networkIndex = runtimeGroup.indexOf('href: "/admin/network-supervisor-runtime"');
  const pluginIndex = runtimeGroup.indexOf('href: "/admin/plugins"');

  assert.ok(creativeIndex > -1 && networkIndex > -1 && pluginIndex > -1);
  assert.ok(desktopIndex > creativeIndex);
  assert.ok(desktopIndex > networkIndex);
  assert.ok(desktopIndex > pluginIndex);
  assert.ok(rpaIndex > desktopIndex);
});
