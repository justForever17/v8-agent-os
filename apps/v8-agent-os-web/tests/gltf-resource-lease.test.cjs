/* eslint-disable @typescript-eslint/no-require-imports, @next/next/no-assign-module-variable */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

function loadLeaseModule() {
  const filename = path.resolve(__dirname, "../src/components/chat/gltf-resource-lease.ts");
  const output = ts.transpileModule(fs.readFileSync(filename, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    fileName: filename,
  }).outputText;
  const module = { exports: {} };
  new Function("module", "exports", "require", output)(module, module.exports, require);
  return module.exports;
}

function sceneWithCounters(counters) {
  const texture = { isTexture: true, dispose: () => { counters.texture += 1; } };
  const material = { map: texture, dispose: () => { counters.material += 1; } };
  const object = {
    geometry: { dispose: () => { counters.geometry += 1; } },
    material,
  };
  return { traverse: (visitor) => visitor(object) };
}

test("GLTF leases keep one hot resource across 30 reopen cycles and release GPU data once", async () => {
  const leases = loadLeaseModule();
  const counters = { clear: 0, geometry: 0, material: 0, texture: 0 };
  const scene = sceneWithCounters(counters);
  for (let cycle = 0; cycle < 30; cycle += 1) {
    const release = leases.acquireGltfResourceLease({
      url: "model.glb",
      scene,
      clear: () => { counters.clear += 1; },
      releaseDelayMs: 20,
    });
    release();
  }
  assert.deepEqual(leases.getGltfResourceLeaseStats(), { active: 0, idle: 1, total: 1 });
  assert.equal(counters.clear, 0);
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.deepEqual(leases.getGltfResourceLeaseStats(), { active: 0, idle: 0, total: 0 });
  assert.deepEqual(counters, { clear: 1, geometry: 1, material: 1, texture: 1 });
});

test("GLTF leases evict idle resources above the GPU cache bound", () => {
  const leases = loadLeaseModule();
  const counters = { clear: 0, geometry: 0, material: 0, texture: 0 };
  for (let index = 0; index < 9; index += 1) {
    leases.acquireGltfResourceLease({
      url: `model-${index}.glb`,
      scene: sceneWithCounters(counters),
      clear: () => { counters.clear += 1; },
      releaseDelayMs: 60_000,
    })();
  }
  assert.deepEqual(leases.getGltfResourceLeaseStats(), { active: 0, idle: 8, total: 8 });
  assert.equal(counters.clear, 1);
  leases.flushIdleGltfResourceLeases();
  assert.deepEqual(leases.getGltfResourceLeaseStats(), { active: 0, idle: 0, total: 0 });
  assert.deepEqual(counters, { clear: 9, geometry: 9, material: 9, texture: 9 });
});
