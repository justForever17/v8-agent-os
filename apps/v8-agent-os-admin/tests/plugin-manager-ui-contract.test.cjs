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
  assert.match(source, /interactionHint === "device_code_clipboard"/);
  assert.match(source, /force: requirement\.configured/);
  assert.match(source, /PluginManagerWorkbench\.configuration\.reauthorize/);
  assert.match(source, /selectedHasEditableConfiguration/);
  assert.match(source, /Boolean\(\s*requirements\s*&&\s*selected\s*&&\s*requirements\.pluginId === selected\.id/);
});

test("plugin component summary omits zero counts, counts Skills, and pins curated bundles", () => {
  assert.match(source, /function ComponentVersionStrip/);
  assert.match(source, /count: plugin\.componentCounts\.skills/);
  assert.match(source, /\.filter\(\(item\) => Number\(item\.count\) > 0\)/);
  assert.match(source, /FEATURED_PLUGIN_ORDER = \["office-suite", "godot"\]/);
  assert.match(source, /FEATURED_PLUGIN_ORDER\.map\(\(id\) =>/);
  assert.match(source, /\.sort\(\(left, right\) =>/);
});

test("CLI, Skill, and MCP summaries expose member details and reviewed updates", () => {
  assert.match(source, /components: discovery\?\.cli \|\| \[\]/);
  assert.match(source, /components: discovery\?\.skills \|\| \[\]/);
  assert.match(source, /components: discovery\?\.mcp \|\| \[\]/);
  assert.match(source, /component\.members\?\.length/);
  assert.match(source, /group-hover:visible/);
  assert.match(source, /max-h-64[^\n]*overflow-y-auto/);
  assert.match(source, /item\.action === "update" && item\.updateSupported/);
  assert.match(source, /PluginManagerWorkbench\.machine\.version\.updateTo/);
  assert.match(source, /selectedUpdatesAvailable/);
  assert.match(source, /selectedNeedsUpdate/);
});

test("Godot setup unlocks installation only after native paths, scenario, and live MCP verification", () => {
  assert.match(source, /function GodotSetupPanel/);
  assert.match(source, /https:\/\/godotengine\.org\/download\/windows\//);
  assert.match(source, /selectGodotExecutable/);
  assert.match(source, /selectGodotProjectDirectory/);
  assert.match(source, /godotSetup\.status\.readyForInstall/);
  assert.match(source, /selectedInstallActive \|\| !selectedSetupReady/);
  assert.match(source, /setup\/refresh/);
  assert.match(source, /setup\.status\.editorOnline/);
  assert.match(source, /setup\.status\.offlinePrerequisitesReady/);
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
  assert.match(source, /selectedNeedsCompletion \? "components\.plugins\.PluginManagerWorkbench\.complete" : selectedNeedsUpdate \? "components\.plugins\.PluginManagerWorkbench\.update" : "components\.plugins\.PluginManagerWorkbench\.install"/);
});
