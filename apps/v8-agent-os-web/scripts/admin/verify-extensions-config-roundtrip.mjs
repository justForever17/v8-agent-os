import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const source = fs.readFileSync(new URL("../../src/app/admin/(dashboard)/extensions/page.tsx", import.meta.url), "utf8");
const tree = ts.createSourceFile("page.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const names = new Set(["normalizeMcpTransportType", "parseMcpArgs", "parseMcpKeyValueLines", "formatMcpArgsText", "formatMcpKeyValueText", "mcpFormFromServerConfig", "buildMcpFormPayload", "validateMcpJsonInput"]);
const functions = tree.statements.filter(node => ts.isFunctionDeclaration(node) && names.has(node.name?.text)).map(node => node.getText(tree));
assert.equal(functions.length, names.size);
const js = ts.transpileModule(functions.join("\n"), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
const context = vm.createContext({ structuredClone });
vm.runInContext(js, context);
const base = { type: "http", endpointRef: "cred:v8-plugin:synthetic", endpointHost: "fixture.invalid", headers: { "X-Unknown": "keep" },
  "x-v8-credential-refs": { auth: { target: "header", targetName: "Authorization", secretRef: "cred:v8-plugin:synthetic-header" } },
  futureField: { keep: 42 }, disabled: false };
context.base = base;
vm.runInContext(`const t = value => value;
const form = mcpFormFromServerConfig("fixture", base);
const kept = buildMcpFormPayload(form, t);
globalThis.result = JSON.stringify(kept);
globalThis.validated = validateMcpJsonInput(JSON.stringify(kept), t).serverCount;
form.headersText = "X-Unknown=keep\\nAuthorization=NEW_SYNTHETIC";
globalThis.changed = JSON.stringify(buildMcpFormPayload(form, t));
form.clearedCredentials = ["auth"];
form.headersText = "X-Unknown=keep";
globalThis.cleared = JSON.stringify(buildMcpFormPayload(form, t));`, context);
const kept = JSON.parse(context.result).mcpServers.fixture;
assert.deepEqual(kept["x-v8-edit-base"], base);
delete kept["x-v8-edit-base"];
assert.deepEqual(kept, base);
assert.equal(context.validated, 1);
assert.equal(JSON.parse(context.changed).mcpServers.fixture.headers.Authorization, "NEW_SYNTHETIC");
assert.deepEqual(JSON.parse(context.cleared).mcpServers.fixture["x-v8-credential-refs"], {});
assert.deepEqual(base.futureField, { keep: 42 });
console.log("Production form roundtrip passed: endpoint ref, header refs, unknown fields, replace and explicit clear.");

const exactArgCases = [
  ["--label", "  spaced value  ", ""],
  [""],
  [],
  ["line\nbreak", "line\r\nbreak", "[literal]", "\twhitespace\t", 'quotes " and \\'],
];
function exactArgsOracle(runtime) {
  for (const args of exactArgCases) {
    runtime.stdio = { type: "stdio", command: "fixture", args, env: { NORMAL: "value" } };
    vm.runInContext(`globalThis.exactArgs = JSON.stringify(buildMcpFormPayload(mcpFormFromServerConfig("fixture", stdio), value => value).mcpServers.fixture.args);`, runtime);
    assert.deepEqual(JSON.parse(runtime.exactArgs), args);
  }
}
exactArgsOracle(context);
vm.runInContext(`globalThis.editedArgs = JSON.stringify(parseMcpArgs('--label\\n  spaced value  \\n', value => value));`, context);
assert.deepEqual(JSON.parse(context.editedArgs), ["--label", "  spaced value  ", ""]);
for (const invalid of ['[1]', '[null]', '["unfinished"']) {
  context.invalid = invalid;
  assert.throws(() => vm.runInContext("parseMcpArgs(invalid, value => value)", context), /extensions.store.invalidArgsArray/);
}
console.log("Exact stdio arguments roundtrip passed: whitespace, empty arguments and embedded newlines.");

const requestSource = fs.readFileSync(new URL("../src/lib/extensions-store-state.ts", import.meta.url), "utf8");
const requestJs = ts.transpileModule(requestSource, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
const requestContext = vm.createContext({ exports: {} });
vm.runInContext(requestJs, requestContext);
function lateResponseOracle(Owner) {
  const owner = new Owner();
  const a = owner.begin("provider-A:query-old");
  const b = owner.begin("provider-B:query-new");
  let visible;
  if (owner.accepts(b)) visible = "B";
  if (owner.accepts(a)) visible = "A";
  assert.equal(visible, "B");
  const beforeClose = owner.capture();
  owner.invalidate();
  owner.begin("provider-B:query-new");
  assert.equal(owner.accepts(beforeClose), false);
}
lateResponseOracle(requestContext.exports.StoreRequestOwner);
class WrongOwner extends requestContext.exports.StoreRequestOwner { accepts() { return true; } }
assert.throws(() => lateResponseOracle(WrongOwner), /'A' !== 'B'/);
console.log("Request ownership oracle passed; disabling generation checks is detected.");
