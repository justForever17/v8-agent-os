const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const ts = require("typescript");

function load(relative, suffix = "") {
    const source = fs.readFileSync(path.join(__dirname, "../src", relative), "utf8") + suffix;
    const compiled = ts.transpileModule(source, { compilerOptions: {
        module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
    } }).outputText;
    const sandbox = { exports: {}, require: (name) => name === "@/lib/models/media-capabilities"
        ? load("lib/models/media-capabilities.ts") : {} };
    vm.runInNewContext(compiled, sandbox);
    return sandbox.exports;
}
const admin = load("lib/models/model-admin.ts");
const select = load("components/models/ModelSelect.tsx", "\nexport { contextWindowInvalidReason };\n");

test("auto roundtrip does not emit the retained legacy value as a custom cap", () => {
    const row = admin.mapEngineModel("fixture", { models: { model: { type: "TEXT", contextWindow: 500000,
        maxTokens: 4096, outputTokenMode: "auto" } } }, "model");
    assert.equal(row.outputTokenMode, "auto");
    const payload = admin.buildModelMutationPayload({ ...row, maxTokens: undefined });
    assert.equal(payload.outputTokenMode, "auto");
    assert.equal(payload.maxTokens, undefined);
    assert.equal(payload.contextWindow, 500000);
});

test("manual and unknown numeric settings remain fixed", () => {
    const row = admin.mapEngineModel("fixture", { models: { model: { maxTokens: 4096 } } }, "model");
    assert.equal(row.outputTokenMode, "fixed");
    const payload = admin.buildModelMutationPayload({ contextWindow: "128000", maxTokens: "8192", outputTokenMode: "fixed" });
    assert.equal(payload.contextWindow, 128000);
    assert.equal(payload.maxTokens, 8192);
    assert.equal(payload.outputTokenMode, "fixed");
});

test("selector accepts smaller context budgets and automatic output", () => {
    for (const contextWindow of [32000, 128000, 500000]) {
        assert.equal(select.contextWindowInvalidReason({ type: "TEXT", contextWindow, outputTokenMode: "auto" }), "");
    }
    assert.notEqual(select.contextWindowInvalidReason({ type: "TEXT", contextWindow: 128000, outputTokenMode: "fixed" }), "");
    assert.notEqual(select.contextWindowInvalidReason({ contextWindow: 128000, eligibility: { selectable: false, shortLabel: "disabled" } }), "");
});
