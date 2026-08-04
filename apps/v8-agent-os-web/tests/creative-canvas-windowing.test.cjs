/* eslint-disable @typescript-eslint/no-require-imports, @next/next/no-assign-module-variable */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

const canvasPath = path.resolve(
  __dirname,
  "../src/components/workbench/CreativeArtifactCanvas.tsx",
);

function loadAssetWindowFunction() {
  const canvas = fs.readFileSync(canvasPath, "utf8");
  const constantsStart = canvas.indexOf("const DRAWER_COLUMN_COUNT");
  const constantsEnd = canvas.indexOf("const CATALOG_CHANNELS", constantsStart);
  const functionStart = canvas.indexOf("function getCanvasAssetWindow", constantsEnd);
  const functionEnd = canvas.indexOf("\ninterface CanvasGraphSaveMeta", functionStart);
  assert.notEqual(constantsStart, -1);
  assert.notEqual(constantsEnd, -1);
  assert.notEqual(functionStart, -1);
  assert.notEqual(functionEnd, -1);
  const isolatedSource = [
    canvas.slice(constantsStart, constantsEnd),
    canvas.slice(functionStart, functionEnd),
    "export { getCanvasAssetWindow };",
  ].join("\n");
  const output = ts.transpileModule(isolatedSource, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
    fileName: canvasPath,
  }).outputText;
  const module = { exports: {} };
  new Function("module", "exports", output)(module, module.exports);
  return module.exports.getCanvasAssetWindow;
}

test("500 canvas assets keep a bounded mounted window across the full scroll range", () => {
  const getCanvasAssetWindow = loadAssetWindowFunction();
  const top = getCanvasAssetWindow(500, 0, 620);
  const middle = getCanvasAssetWindow(500, 10_000, 620);
  const bottom = getCanvasAssetWindow(500, Number.MAX_SAFE_INTEGER, 620);

  assert.deepEqual(top, {
    startIndex: 0,
    endIndex: 24,
    offsetY: 0,
    totalHeight: 19_372,
    safeScrollTop: 0,
  });
  assert.equal(middle.endIndex - middle.startIndex, 30);
  assert.equal(bottom.endIndex, 500);
  assert.ok(bottom.startIndex > 0);
  assert.ok([top, middle, bottom].every((window) => window.endIndex - window.startIndex <= 30));
});

test("asset filtering clamps a stale scroll position and never yields an empty visible window", () => {
  const getCanvasAssetWindow = loadAssetWindowFunction();
  const filtered = getCanvasAssetWindow(7, 10_000, 620);
  const empty = getCanvasAssetWindow(0, 10_000, 620);

  assert.deepEqual(filtered, {
    startIndex: 0,
    endIndex: 7,
    offsetY: 0,
    totalHeight: 348,
    safeScrollTop: 0,
  });
  assert.deepEqual(empty, {
    startIndex: 0,
    endIndex: 0,
    offsetY: 0,
    totalHeight: 0,
    safeScrollTop: 0,
  });
});
