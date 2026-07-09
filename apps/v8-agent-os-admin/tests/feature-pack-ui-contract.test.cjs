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
  assert.doesNotMatch(itemBlock("/admin/plugin-host"), /tone: "beta"/);
  assert.doesNotMatch(itemBlock("/admin/network-supervisor-runtime"), /tone: "beta"/);
  assert.doesNotMatch(itemBlock("/admin/creative-media"), /tone: "beta"/);

  const desktopIndex = runtimeGroup.indexOf('href: "/admin/desktop-automation"');
  const rpaIndex = runtimeGroup.indexOf('href: "/admin/rpa"');
  const creativeIndex = runtimeGroup.indexOf('href: "/admin/creative-media"');
  const networkIndex = runtimeGroup.indexOf('href: "/admin/network-supervisor-runtime"');
  const pluginIndex = runtimeGroup.indexOf('href: "/admin/plugin-host"');

  assert.ok(creativeIndex > -1 && networkIndex > -1 && pluginIndex > -1);
  assert.ok(desktopIndex > creativeIndex);
  assert.ok(desktopIndex > networkIndex);
  assert.ok(desktopIndex > pluginIndex);
  assert.ok(rpaIndex > desktopIndex);
});
