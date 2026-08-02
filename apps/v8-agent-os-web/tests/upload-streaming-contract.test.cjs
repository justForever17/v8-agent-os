const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const repoRoot = path.resolve(__dirname, "..", "..", "..");

function read(relativePath) {
  return fs.readFileSync(path.join(repoRoot, relativePath), "utf8");
}

test("large uploads stream through Web and Admin without reparsing multipart bodies", () => {
  const webRoute = read("apps/v8-agent-os-web/src/app/api/upload/route.ts");
  const adminRoute = read("apps/v8-agent-os-admin/src/app/api/client/upload/route.ts");

  for (const source of [webRoute, adminRoute]) {
    assert.doesNotMatch(source, /req\.formData\(\)/);
    assert.match(source, /body: req\.body/);
    assert.match(source, /duplex: "half"/);
    assert.match(source, /content-type/);
    assert.match(source, /content-length/);
  }
  assert.match(webRoute, /x-v8-upload-default-source-kind/);
  assert.match(adminRoute, /x-v8-upload-default-source-kind/);
});
