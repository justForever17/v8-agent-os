const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const adminRoot = path.resolve(__dirname, "..");

function source(relativePath) {
  return fs.readFileSync(path.join(adminRoot, relativePath), "utf8");
}

test("creative media admin proxies attach the internal service secret", () => {
  const proxyRoute = source("src/app/api/creative-media/[...path]/route.ts");
  const bootstrapRoute = source("src/app/api/creative-media/bootstrap/route.ts");

  for (const route of [proxyRoute, bootstrapRoute]) {
    assert.match(route, /resolveInternalSecret/);
    assert.match(route, /x-v8-agent-os-secret/);
    assert.match(route, /Internal service secret is unavailable/);
  }
  assert.match(proxyRoute, /new Headers\(\{ "x-v8-agent-os-secret": internalSecret \}\)/);
  assert.match(bootstrapRoute, /headers\.set\("x-v8-agent-os-secret", internalSecret\)/);
});
