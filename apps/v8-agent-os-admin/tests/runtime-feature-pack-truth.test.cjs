const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

const adminRoot = path.resolve(__dirname, "..");
const sourcePath = path.join(adminRoot, "src", "lib", "server", "runtime-feature-pack-truth.ts");
const compiled = ts.transpileModule(fs.readFileSync(sourcePath, "utf8"), {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
    esModuleInterop: true,
  },
  fileName: sourcePath,
}).outputText;

const target = { exports: {} };
new Function("require", "module", "exports", compiled)(require, target, target.exports);
const { engineFeaturePackSnapshotIsAuthoritative, mergeFeaturePackTruth } = target.exports;

function pack(overrides = {}) {
  return {
    id: "rpa_automation",
    recommendedOrder: 2,
    status: "not_installed",
    installed: false,
    restartRequired: false,
    logRef: null,
    lastError: null,
    updatedAt: null,
    ...overrides,
  };
}

test("fresh Engine readiness wins even when config carries a newer pack timestamp", () => {
  const config = pack({
    status: "installed",
    installed: true,
    restartRequired: true,
    logRef: "install.log",
    lastError: "stale install error",
    updatedAt: "2026-08-11T00:00:02.000Z",
  });
  const engine = pack({
    status: "installed",
    installed: true,
    restartRequired: false,
    updatedAt: "2026-08-11T00:00:00.000Z",
  });

  const [merged] = mergeFeaturePackTruth(
    [config],
    [engine],
    Date.parse("2026-08-11T00:00:03.000Z"),
  );

  assert.equal(merged.status, "installed");
  assert.equal(merged.installed, true);
  assert.equal(merged.restartRequired, false);
  assert.equal(merged.logRef, "install.log", "config may fill missing diagnostic log metadata");
  assert.equal(merged.lastError, null, "a ready Engine must clear stale config failure text");
});

test("Engine failure remains authoritative and keeps missing config diagnostics", () => {
  const config = pack({
    status: "installed",
    installed: true,
    restartRequired: false,
    logRef: "failed-install.log",
    lastError: "dependency probe failed",
    updatedAt: "2099-01-01T00:00:00.000Z",
  });
  const engine = pack({
    status: "failed",
    installed: false,
    restartRequired: true,
    updatedAt: "2026-08-11T00:00:00.000Z",
  });

  const [merged] = mergeFeaturePackTruth(
    [config],
    [engine],
    Date.parse("2099-01-01T00:00:01.000Z"),
  );

  assert.equal(merged.status, "failed");
  assert.equal(merged.installed, false);
  assert.equal(merged.restartRequired, true);
  assert.equal(merged.logRef, "failed-install.log");
  assert.equal(merged.lastError, "dependency probe failed");
});

test("a config failure newer than the server snapshot is not overwritten", () => {
  const config = pack({
    status: "failed",
    installed: false,
    restartRequired: true,
    lastError: "install failed",
    updatedAt: "2026-08-11T00:00:02.000Z",
  });
  const cachedEngine = pack({
    status: "installed",
    installed: true,
    restartRequired: false,
    updatedAt: "2026-08-11T00:00:00.000Z",
  });

  const [merged] = mergeFeaturePackTruth(
    [config],
    [cachedEngine],
    Date.parse("2026-08-11T00:00:01.000Z"),
  );

  assert.equal(merged.status, "failed");
  assert.equal(merged.installed, false);
  assert.equal(merged.restartRequired, true);
  assert.equal(merged.lastError, "install failed");
});

test("only a fresh available server snapshot may replace config fallback state", () => {
  assert.equal(engineFeaturePackSnapshotIsAuthoritative({ available: true, stale: false, updatedAt: 1 }), true);
  assert.equal(engineFeaturePackSnapshotIsAuthoritative({ available: true, stale: true, updatedAt: 1 }), false);
  assert.equal(engineFeaturePackSnapshotIsAuthoritative({ available: false, stale: false, updatedAt: 1 }), false);
  assert.equal(engineFeaturePackSnapshotIsAuthoritative({ available: true, stale: false, updatedAt: 0 }), false);

  const failedConfig = pack({ status: "failed", lastError: "install failed" });
  assert.deepEqual(mergeFeaturePackTruth([failedConfig], null, 0), [failedConfig]);
});
