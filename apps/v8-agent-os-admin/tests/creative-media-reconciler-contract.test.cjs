const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const adminRoot = path.resolve(__dirname, "..");
const engineRoot = path.resolve(adminRoot, "..", "v8-agent-os-engine");

function readAdmin(...segments) {
  return fs.readFileSync(path.join(adminRoot, ...segments), "utf8");
}

function readEngine(...segments) {
  return fs.readFileSync(path.join(engineRoot, ...segments), "utf8");
}

test("creative media bootstrap fetches the reconciler governance projection", () => {
  const source = readAdmin("src", "app", "api", "creative-media", "bootstrap", "route.ts");
  assert.match(source, /reconcilerStatus/);
  assert.match(source, /"reconciler\/status"/);
  assert.match(source, /requireOk: true/);
  assert.match(source, /reconciler_status_unavailable/);
  assert.match(source, /unavailable: true/);
  assert.doesNotMatch(source, /response\.text\(\)|response\.body/);
  assert.ok(source.indexOf("if (options.requireOk") < source.indexOf("return response.json"));
  assert.match(source, /requireAdminIdentity\(req\)/);
  assert.ok(source.indexOf("requireAdminIdentity(req)") < source.indexOf("fetchEngineJson(engineBaseUrl"));
  assert.match(source, /"governance\/snapshot"/);
  assert.match(source, /resolveCreativeMediaGovernanceSecret\(\)/);
  assert.match(source, /"x-v8-agent-os-admin-governance-secret": governanceSecret/);
  assert.doesNotMatch(source, /x-v8-agent-os-governance/);
});

test("creative media admin global reads use the explicit governance surface", () => {
  const proxy = readAdmin("src", "app", "api", "creative-media", "[...path]", "route.ts");
  const page = readAdmin("src", "app", "admin", "(dashboard)", "creative-media", "page.tsx");
  const runtimeConfig = readAdmin("src", "lib", "server", "runtime-config.ts");
  const engine = readEngine("api", "creative_media_routes.py");
  assert.match(proxy, /path\?\.\[0\] === "governance"/);
  assert.match(proxy, /"x-v8-agent-os-admin-governance-secret"/);
  assert.match(proxy, /resolveCreativeMediaGovernanceSecret\(\)/);
  assert.doesNotMatch(proxy, /x-v8-agent-os-governance/);
  assert.doesNotMatch(proxy, /req\.headers\.get\("x-v8-agent-os-admin-governance-secret"\)/);
  assert.match(runtimeConfig, /creative-media-admin-governance-secret/);
  assert.match(runtimeConfig, /crypto\.randomBytes\(48\)\.toString\("base64url"\)/);
  assert.match(runtimeConfig, /flag: "wx"/);
  assert.match(runtimeConfig, /mode: 0o600/);
  assert.match(page, /\/api\/creative-media\/governance\/snapshot/);
  assert.match(page, /\/api\/creative-media\/governance\/work-orders/);
  assert.match(page, /finiteCount\(quality\.repairCount\)/);
  assert.match(page, /finiteCount\(render\.artifactCount\)/);
  assert.match(page, /text\(event\.eventKind\)/);
  assert.match(engine, /@router\.get\(\s*"\/governance\/snapshot"/);
  assert.match(engine, /require_creative_media_admin_governance/);
  assert.match(engine, /get_creative_media_admin_governance_secret/);
  assert.match(engine, /hmac\.compare_digest/);
  assert.doesNotMatch(engine, /x_v8_agent_os_governance/);
});

test("reconciler route is internal-secret protected and redacts provider internals", () => {
  const source = readEngine("api", "creative_media_routes.py");
  assert.match(source, /@router\.get\("\/reconciler\/status"\)/);
  assert.match(source, /dependencies=\[Depends\(require_creative_media_internal_secret\)\]/);
  assert.match(source, /_SAFE_RECONCILER_CYCLE_KEYS/);
  assert.match(source, /Creative Media reconciler status unavailable/);
  assert.match(source, /"adapterDistribution"/);
  assert.match(source, /"detailCodeDistribution"/);
  assert.match(source, /"quarantineCount"/);
  assert.doesNotMatch(source, /providerHandle|providerTaskId|taskId|externalUrl|sourcePath|responseBody|rawResponse/);
  assert.doesNotMatch(source, /return\s+raw_status/);
});

test("creative media admin surface only renders bounded reconciliation fields", () => {
  const source = readAdmin("src", "app", "admin", "(dashboard)", "creative-media", "page.tsx");
  assert.match(source, /reconcilerStatus/);
  assert.match(source, /reconciler\.uncertain/);
  assert.match(source, /reconciler\.projectionPending/);
  assert.match(source, /reconciler\.adapterDistribution/);
  assert.match(source, /reconciler\.detailCodeDistribution/);
  assert.match(source, /reconciler\.unavailable/);
  assert.match(source, /reconcilerStateUnavailable/);
  assert.doesNotMatch(source, /providerHandle|providerTaskId|taskId|externalUrl|sourcePath|responseBody|rawResponse/);
});
