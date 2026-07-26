const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const source = fs.readFileSync(
  path.join(__dirname, "..", "src", "components", "plugins", "PluginManagerWorkbench.tsx"),
  "utf8",
);

test("plugin manager routes CLI login separately from MCP OAuth", () => {
  assert.match(source, /requirement\.kind === "cli_login" \? onCliLogin : onOAuth/);
  assert.match(source, /\/cli-login\/start/);
  assert.match(source, /authorization\.flow === "cli_login" \? "cli-login" : "oauth"/);
  assert.match(source, /authorizationForField\?\.authorizationUrl/);
  assert.match(source, /PluginManagerWorkbench\.configuration\.openAuthorizationPage/);
  assert.match(source, /PluginManagerWorkbench\.configuration\.deviceCodeClipboard/);
});

test("plugin component summary omits zero counts, counts Skills, and pins the daily bundle", () => {
  assert.match(source, /\.filter\(\(\[, count\]\) => Number\(count\) > 0\)/);
  assert.match(source, /\["Skill", selected\.componentCounts\.skills\]/);
  assert.match(source, /FEATURED_PLUGIN_ORDER = \["office-suite"\]/);
  assert.match(source, /FEATURED_PLUGIN_ORDER\.map\(\(id\) =>/);
  assert.match(source, /\.sort\(\(left, right\) =>/);
});

test("plugin installs expose targeted progress instead of silently reloading the full catalog", () => {
  assert.match(source, /PLUGIN_JOBS_URL}\/\${installJob\.jobId}/);
  assert.match(source, /<InstallProgressCard job={selectedInstallJob}/);
  assert.match(source, /progress\?\.currentComponent/);
  assert.match(source, /aria-live="polite"/);
  assert.doesNotMatch(source, /setInterval\(\(\) => void load\(false, true\)/);
});

test("plugin catalog and details remain bounded with internal scrolling", () => {
  assert.match(source, /lg:h-\[calc\(100vh-230px\)\]/);
  assert.match(source, /max-h-\[420px\][^\n]*overflow-y-auto/);
  assert.match(source, /<aside[^>]*lg:overflow-y-auto/);
});

test("MediaKit presents local and cloud configuration as separate concise surfaces", () => {
  assert.match(source, /function MediaKitConfigurationPanel/);
  assert.match(source, /MEDIAKIT_API_KEY/);
  assert.match(source, /MEDIAKIT_OUTPUT_PATH/);
  assert.match(source, /console\.volcengine\.com\/imp\/ai-mediakit\/settings/);
  assert.match(source, /selectedNeedsCompletion \? "components\.plugins\.PluginManagerWorkbench\.complete" : "components\.plugins\.PluginManagerWorkbench\.install"/);
});
