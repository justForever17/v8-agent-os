const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const adminRoot = path.resolve(__dirname, "..");

test("Topbar uses feature pack API instead of legacy runtime install API", () => {
  const topbarSource = fs.readFileSync(path.join(adminRoot, "src", "components", "layout", "Topbar.tsx"), "utf8");

  assert.match(topbarSource, /\/api\/runtime-feature-packs/);
  assert.doesNotMatch(topbarSource, /\/api\/runtime-install/);
  assert.match(topbarSource, /v8os:open-feature-packs/);
});

test("feature pack API exposes GET/POST and legacy runtime install is deprecated", () => {
  const routeSource = fs.readFileSync(path.join(adminRoot, "src", "app", "api", "runtime-feature-packs", "route.ts"), "utf8");
  const legacySource = fs.readFileSync(path.join(adminRoot, "src", "lib", "server", "runtime-install.ts"), "utf8");

  assert.match(routeSource, /export async function GET/);
  assert.match(routeSource, /export async function POST/);
  assert.match(routeSource, /dryRun/);
  assert.match(routeSource, /triggerFeaturePackInstall/);
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
  assert.match(installerSource, /Could not fetch URL/);
  assert.match(installerSource, /No matching distribution found/);
  assert.match(installerSource, /runFeaturePackInstallSequence/);
  assert.ok(installerSource.indexOf("if (dryRun)") < installerSource.indexOf("fs.mkdirSync(targetDir"));

  assert.doesNotMatch(topbarSource, /commandSummary/);
  assert.doesNotMatch(topbarSource, /stdout|stderr/);
});

test("image analysis feature pack uses a pinned asset transaction and never a silent runtime download", () => {
  const installerSource = fs.readFileSync(path.join(adminRoot, "src", "lib", "server", "runtime-feature-packs.ts"), "utf8");
  const managedNextSource = fs.readFileSync(path.join(adminRoot, "..", "..", "scripts", "run-next-with-managed-auth.mjs"), "utf8");
  const engineSource = fs.readFileSync(path.join(adminRoot, "..", "v8-agent-os-engine", "core", "runtime", "feature_packs.py"), "utf8");
  const manifest = JSON.parse(fs.readFileSync(path.join(adminRoot, "..", "v8-agent-os-engine", "requirements", "feature-packs", "creative-media-image-analysis.manifest.json"), "utf8"));

  assert.equal(manifest.id, "creative_media_image_analysis");
  assert.equal(manifest.assets[0].size, 178648008);
  assert.equal(String(manifest.assets[0].sha256).toLowerCase(), "60920e99c45464f2ba57bee2ad08c919a52bbf852739e96947fbb4358c0d964a");
  assert.match(installerSource, /runTransactionalAssetPackInstall/);
  assert.match(installerSource, /staging/);
  assert.match(installerSource, /receipt\.json/);
  assert.match(installerSource, /sha256File/);
  assert.match(installerSource, /backup/);
  assert.match(installerSource, /FEATURE_PACK_ASSET_HOSTS/);
  assert.match(installerSource, /assertTrustedFeaturePackAssetUrl\(response\.url\)/);
  assert.match(installerSource, /normalizeStatus\(existing\.status\) === "installing"/);
  assert.match(installerSource, /本次请求未重复启动下载/);
  assert.match(managedNextSource, /V8_AGENT_OS_REPO_ROOT:\s*repoRoot/);
  assert.match(managedNextSource, /V8_ENGINE_DIR:\s*path\.join\(repoRoot, "apps", "v8-agent-os-engine"\)/);
  assert.doesNotMatch(engineSource, /requests\.(get|post)|urlopen|httpx\.(get|post)/);
});

test("dependency-gated runtime pages open the feature pack panel when pack is missing", () => {
  const desktopSource = fs.readFileSync(path.join(adminRoot, "src", "app", "admin", "(dashboard)", "desktop-automation", "page.tsx"), "utf8");
  const rpaSource = fs.readFileSync(path.join(adminRoot, "src", "app", "admin", "(dashboard)", "rpa", "page.tsx"), "utf8");

  assert.match(desktopSource, /computer_use_desktop/);
  assert.match(desktopSource, /v8os:open-feature-packs/);
  assert.match(desktopSource, /featurePackMissing/);
  assert.match(rpaSource, /rpa_automation/);
  assert.match(rpaSource, /v8os:open-feature-packs/);
  assert.match(rpaSource, /featurePackMissing/);
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
