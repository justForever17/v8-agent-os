const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

const root = path.resolve(__dirname, "..");
const sourcePath = path.join(root, "src", "lib", "artifacts.ts");
const compiled = ts.transpileModule(fs.readFileSync(sourcePath, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  fileName: sourcePath,
}).outputText;
const testModule = { exports: {} };
new Function("require", "module", "exports", compiled)(require, testModule, testModule.exports);

const {
  dedupeArtifactItemsForPresentation,
  prioritizeArtifactItems,
} = testModule.exports;

test("artifact presentation keeps the latest path revision and ranks media then documents", () => {
  const artifacts = [
    { id: "readme-old", canonicalPath: "README.md", title: "README.md", kind: "document" },
    { id: "app", canonicalPath: "src/App.jsx", title: "App.jsx", kind: "code" },
    { id: "image", canonicalPath: "preview.png", title: "preview.png", kind: "image" },
    { id: "readme-new", workspaceRelativePath: "README.md", title: "README.md", kind: "document" },
    { id: "report", canonicalPath: "report.pdf", title: "report.pdf", kind: "document" },
  ];

  const deduped = dedupeArtifactItemsForPresentation(artifacts, (item) => item);
  const prioritized = prioritizeArtifactItems(deduped, (item) => item);

  assert.equal(deduped.length, 4);
  assert.equal(deduped.find((item) => item.title === "README.md").id, "readme-new");
  assert.deepEqual(prioritized.map((item) => item.id), [
    "image",
    "readme-new",
    "report",
    "app",
  ]);
});
