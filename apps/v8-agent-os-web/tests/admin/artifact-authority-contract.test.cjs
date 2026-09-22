const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const repoRoot = path.resolve(__dirname, "../../../..");
const read = relativePath => fs.readFileSync(path.join(repoRoot, relativePath), "utf8");

test("Admin artifact proxies require session authority and Engine signs resource reads", () => {
  const list = read("apps/v8-agent-os-web/src/app/api/admin/memory/artifacts/route.ts");
  const detail = read("apps/v8-agent-os-web/src/app/api/admin/memory/artifacts/[id]/route.ts");
  const content = read("apps/v8-agent-os-web/src/app/api/admin/memory/artifacts/[id]/content/route.ts");
  const clientContent = read("apps/v8-agent-os-web/src/app/api/admin/client/artifacts/[id]/content/route.ts");
  for (const route of [list, detail, content]) {
    assert.match(route, /resolveAuthorizedUserEmail\(req\)/);
    assert.match(route, /sessionId is required/);
  }
  assert.match(list, /query\.set\("sessionId", sessionId\)/);
  assert.match(detail, /new URLSearchParams\(\{ sessionId \}\)/);
  assert.match(content, /new URLSearchParams\(\{ sessionId \}\)/);
  assert.match(clientContent, /searchParams\.has\("v8sig"\)/);
  assert.match(clientContent, /fetchEngineClientIdentity/);
});

test("resource links delegate signing and keep global Admin prefetch removed", () => {
  const signing = read("apps/v8-agent-os-web/src/admin/lib/server/client-surface-resource.ts");
  const surface = read("apps/v8-agent-os-web/src/admin/lib/server/artifact-surface.ts");
  const cache = read("apps/v8-agent-os-web/src/admin/lib/admin-client-cache.ts");
  assert.match(signing, /engineIdentity/);
  assert.match(signing, /resource-link/);
  assert.doesNotMatch(signing, /createHmac|buildSignature|resolveInternalSecret/);
  assert.match(surface, /deriveAdminResourceRefFromArtifactLike\(next\)/);
  assert.match(surface, /await buildSignedClientSurfaceUrl/);
  assert.doesNotMatch(cache, /\/api\/memory\/artifacts\?limit=160/);
});
