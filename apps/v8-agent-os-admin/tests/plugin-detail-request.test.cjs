const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

const sourcePath = path.join(__dirname, "..", "src", "components", "plugins", "plugin-detail-request.ts");

function loadCoordinatorModule() {
  const output = ts.transpileModule(fs.readFileSync(sourcePath, "utf8"), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
    fileName: sourcePath,
  }).outputText;
  const moduleRecord = { exports: {} };
  new Function("require", "module", "exports", output)(require, moduleRecord, moduleRecord.exports);
  return moduleRecord.exports;
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, resolve, reject };
}

test("late plugin A details cannot overwrite plugin B discovery, requirements, Godot, loading, or error", async () => {
  const { createPluginDetailRequestCoordinator } = loadCoordinatorModule();
  const coordinator = createPluginDetailRequestCoordinator();
  const state = {
    discovery: null,
    requirements: null,
    godot: null,
    loading: "",
    error: "",
  };

  const run = (request, requests) => {
    state.loading = request.pluginId;
    state.error = "";
    return Promise.all([
      requests.discovery.promise.then((value) => coordinator.commit(request, () => { state.discovery = value; })),
      requests.requirements.promise.then((value) => coordinator.commit(request, () => { state.requirements = value; })),
      requests.godot.promise.then((value) => coordinator.commit(request, () => { state.godot = value; })),
    ]).catch((error) => {
      coordinator.commit(request, () => { state.error = error.message; });
    }).finally(() => {
      coordinator.commit(request, () => { state.loading = ""; });
    });
  };

  const aRequests = { discovery: deferred(), requirements: deferred(), godot: deferred() };
  const a = coordinator.begin("plugin-a");
  const aRun = run(a, aRequests);

  const bRequests = { discovery: deferred(), requirements: deferred(), godot: deferred() };
  const b = coordinator.begin("plugin-b");
  const bRun = run(b, bRequests);
  assert.equal(a.signal.aborted, true);
  assert.equal(b.signal.aborted, false);

  bRequests.discovery.resolve({ pluginId: "plugin-b", source: "discovery-b" });
  bRequests.requirements.resolve({ pluginId: "plugin-b", source: "requirements-b" });
  bRequests.godot.resolve({ pluginId: "plugin-b", source: "godot-b" });
  await bRun;

  aRequests.discovery.resolve({ pluginId: "plugin-a", source: "discovery-a" });
  aRequests.godot.resolve({ pluginId: "plugin-a", source: "godot-a" });
  aRequests.requirements.reject(new Error("late-a-error"));
  await aRun;

  assert.deepEqual(state.discovery, { pluginId: "plugin-b", source: "discovery-b" });
  assert.deepEqual(state.requirements, { pluginId: "plugin-b", source: "requirements-b" });
  assert.deepEqual(state.godot, { pluginId: "plugin-b", source: "godot-b" });
  assert.equal(state.loading, "");
  assert.equal(state.error, "");
});

test("cancelling a plugin detail request prevents every later commit", () => {
  const { createPluginDetailRequestCoordinator } = loadCoordinatorModule();
  const coordinator = createPluginDetailRequestCoordinator();
  const request = coordinator.begin("plugin-a");
  let commits = 0;

  assert.equal(coordinator.cancel(request), true);
  assert.equal(request.signal.aborted, true);
  assert.equal(coordinator.commit(request, () => { commits += 1; }), false);
  assert.equal(commits, 0);
});
