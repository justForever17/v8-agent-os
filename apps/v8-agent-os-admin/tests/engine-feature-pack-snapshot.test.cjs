const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

const adminRoot = path.resolve(__dirname, "..");
const sourcePath = path.join(adminRoot, "src", "lib", "server", "engine-feature-pack-snapshot.ts");
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
const {
  readEngineFeaturePackSnapshot,
  resetEngineFeaturePackSnapshotForTests,
} = target.exports;

test("feature-pack snapshot uses the authenticated lightweight endpoint and a short cache", async () => {
  resetEngineFeaturePackSnapshotForTests();
  let currentTime = 1_000;
  const requests = [];
  const fetchImpl = async (url, init) => {
    requests.push({ url, init });
    return {
      ok: true,
      status: 200,
      json: async () => ({ sampledAt: "1970-01-01T00:00:01.000Z", featurePacks: [{ id: "rpa_automation", restartRequired: false }] }),
    };
  };

  const first = await readEngineFeaturePackSnapshot({
    origin: "http://127.0.0.1:9530/",
    internalSecret: "internal-secret",
    fetchImpl,
    now: () => currentTime,
  });
  assert.equal(first.available, true);
  assert.equal(first.stale, false);
  assert.equal(first.updatedAt, 1_000);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "http://127.0.0.1:9530/v1/runtime-feature-packs/status");
  assert.equal(requests[0].init.headers["x-v8-agent-os-secret"], "internal-secret");

  currentTime = 2_000;
  const cached = await readEngineFeaturePackSnapshot({
    origin: "http://127.0.0.1:9530",
    internalSecret: "internal-secret",
    fetchImpl,
    now: () => currentTime,
  });
  assert.equal(cached.available, true);
  assert.equal(requests.length, 1, "a fresh snapshot must not trigger another Engine request");

  currentTime = 2_001;
  await readEngineFeaturePackSnapshot({
    origin: "http://127.0.0.1:9530",
    internalSecret: "internal-secret",
    force: true,
    fetchImpl,
    now: () => currentTime,
  });
  assert.equal(requests.length, 2, "an explicit refresh must bypass the short cache");
});

test("snapshot authority uses Engine sampledAt rather than response completion time", async () => {
  resetEngineFeaturePackSnapshotForTests();
  const snapshot = await readEngineFeaturePackSnapshot({
    origin: "http://127.0.0.1:9532",
    internalSecret: "internal-secret",
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        sampledAt: "1970-01-01T00:00:05.000Z",
        featurePacks: [{ id: "rpa_automation", status: "installed" }],
      }),
    }),
    now: () => 10_000,
  });
  assert.equal(snapshot.updatedAt, 5_000);
  assert.equal(snapshot.updatedAt < 10_000, true);
});

test("feature-pack snapshot fails closed without credentials or a valid payload", async () => {
  resetEngineFeaturePackSnapshotForTests();
  let calls = 0;
  const missingSecret = await readEngineFeaturePackSnapshot({
    origin: "http://127.0.0.1:9530",
    internalSecret: "",
    fetchImpl: async () => {
      calls += 1;
      throw new Error("must not run");
    },
  });
  assert.equal(missingSecret.available, false);
  assert.equal(calls, 0);

  const invalid = await readEngineFeaturePackSnapshot({
    origin: "http://127.0.0.1:9531",
    internalSecret: "internal-secret",
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      json: async () => ({ sampledAt: "1970-01-01T00:00:01.000Z", featurePacks: "not-an-array" }),
    }),
  });
  assert.equal(invalid.available, false);
  assert.equal(invalid.data, null);
  assert.equal(invalid.stale, false);
  assert.equal(invalid.error, "engine_feature_pack_status_invalid");
});
