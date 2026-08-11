import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  assertStandaloneAssetsReady,
  findStandaloneServer,
  stageStandaloneAssets,
} from "./run-next-with-managed-auth.mjs";

function createStandaloneFixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-next-assets-"));
  const appDir = path.join(root, "apps", "v8-agent-os-admin");
  const serverPath = path.join(appDir, ".next", "standalone", "server.js");
  fs.mkdirSync(path.dirname(serverPath), { recursive: true });
  fs.writeFileSync(serverPath, "// standalone server\n", "utf8");
  fs.mkdirSync(path.join(appDir, ".next", "static"), { recursive: true });
  fs.writeFileSync(path.join(appDir, ".next", "static", "chunk.js"), "static", "utf8");
  fs.mkdirSync(path.join(appDir, "public"), { recursive: true });
  fs.writeFileSync(path.join(appDir, "public", "product-mark.png"), "public", "utf8");
  return { root, appDir, serverPath };
}

test("standalone assets are staged during build and start validation is read-only", (t) => {
  const fixture = createStandaloneFixture();
  t.after(() => fs.rmSync(fixture.root, { recursive: true, force: true }));

  assert.equal(findStandaloneServer(fixture.appDir, "admin"), fixture.serverPath);
  stageStandaloneAssets(fixture.appDir, fixture.serverPath);

  const standaloneRoot = path.dirname(fixture.serverPath);
  const staticTarget = path.join(standaloneRoot, ".next", "static", "chunk.js");
  const publicTarget = path.join(standaloneRoot, "public", "product-mark.png");
  const before = [staticTarget, publicTarget].map((target) => ({
    target,
    content: fs.readFileSync(target, "utf8"),
    modifiedAt: fs.statSync(target).mtimeMs,
  }));

  assert.doesNotThrow(() => assertStandaloneAssetsReady(fixture.appDir, fixture.serverPath));
  assert.deepEqual(
    before,
    [staticTarget, publicTarget].map((target) => ({
      target,
      content: fs.readFileSync(target, "utf8"),
      modifiedAt: fs.statSync(target).mtimeMs,
    })),
  );
});

test("start validation fails before launch when staged assets are missing", (t) => {
  const fixture = createStandaloneFixture();
  t.after(() => fs.rmSync(fixture.root, { recursive: true, force: true }));

  stageStandaloneAssets(fixture.appDir, fixture.serverPath);
  fs.rmSync(path.join(path.dirname(fixture.serverPath), ".next", "static"), {
    recursive: true,
    force: true,
  });

  assert.throws(
    () => assertStandaloneAssetsReady(fixture.appDir, fixture.serverPath),
    /Standalone assets are incomplete\. Rebuild before starting/,
  );
});

test("start validation fails before launch when the standalone server is missing", () => {
  assert.throws(
    () => assertStandaloneAssetsReady("unused", ""),
    /Standalone server is missing\. Rebuild the production bundle before starting it/,
  );
});
