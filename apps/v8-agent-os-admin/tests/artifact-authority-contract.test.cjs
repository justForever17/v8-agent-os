const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const repoRoot = path.resolve(__dirname, "../../..");

function read(relativePath) {
  return fs.readFileSync(path.join(repoRoot, relativePath), "utf8");
}

test("Admin artifact proxies require session authority on every read", () => {
  const list = read("apps/v8-agent-os-admin/src/app/api/memory/artifacts/route.ts");
  const detail = read("apps/v8-agent-os-admin/src/app/api/memory/artifacts/[id]/route.ts");
  const content = read("apps/v8-agent-os-admin/src/app/api/memory/artifacts/[id]/content/route.ts");
  const clientDetail = read("apps/v8-agent-os-admin/src/app/api/client/artifacts/[id]/route.ts");
  const clientContent = read("apps/v8-agent-os-admin/src/app/api/client/artifacts/[id]/content/route.ts");

  for (const route of [list, detail, content]) {
    assert.match(route, /resolveAuthorizedUserEmail\(req\)/);
    assert.match(route, /if \(!userEmail\)[\s\S]*?unauthorizedJson\(\)/);
  }
  assert.match(list, /if \(!sessionId\)[\s\S]*?sessionId is required/);
  assert.match(list, /query\.set\("sessionId", sessionId\)/);
  assert.match(detail, /new URLSearchParams\(\{ sessionId \}\)/);
  assert.match(content, /new URLSearchParams\(\{ sessionId \}\)/);
  assert.match(clientDetail, /if \(!sessionId\)[\s\S]*?sessionId is required/);
  assert.match(clientDetail, /new URLSearchParams\(\{ sessionId \}\)/);
  assert.match(clientContent, /new URLSearchParams\(\{ sessionId \}\)/);
  assert.match(clientContent, /verifySignedClientSurfaceRequest\(req\)/);
});

test("signed artifact URLs bind the session query and global Admin prefetch is removed", () => {
  const signing = read("apps/v8-agent-os-admin/src/lib/server/client-surface-resource.ts");
  const surface = read("apps/v8-agent-os-admin/src/lib/server/artifact-surface.ts");
  const cache = read("apps/v8-agent-os-admin/src/lib/admin-client-cache.ts");

  assert.match(signing, /buildVerifiableRequestPath\(req/);
  assert.match(signing, /searchParams\.delete\("v8exp"\)/);
  assert.match(signing, /searchParams\.delete\("v8sig"\)/);
  assert.match(signing, /buildSignature\(normalizedPath, exp\)/);
  assert.match(surface, /deriveAdminResourceRefFromArtifactLike\(next\)/);
  assert.match(surface, /new URLSearchParams\(\{ sessionId \}\)/);
  assert.doesNotMatch(cache, /\/api\/memory\/artifacts\?limit=160/);
});
