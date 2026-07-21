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
