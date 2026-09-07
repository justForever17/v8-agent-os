const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const ts = require("typescript");

const source = fs.readFileSync(path.join(__dirname, "../src/components/research/AgentBrowserPanel.tsx"), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
} }).outputText;
const sandbox = { exports: {}, require: () => ({}), URL };
vm.runInNewContext(compiled, sandbox);
const parse = sandbox.exports.parseAgentBrowserLoginTarget;

test("arbitrary login sites grant only the submitted hostname", () => {
    const target = parse("https://Docs.Example.org/login?next=%2Fdocs");
    assert.equal(target.url, "https://docs.example.org/login?next=%2Fdocs");
    assert.equal(JSON.stringify(target.hosts), '["docs.example.org"]');
});

test("login targets cannot smuggle credentials, local-file schemes or wildcard grants", () => {
    for (const input of ["file:///tmp/profile", "javascript:alert(1)", "https://user:secret@example.org", "https://*.example.org", "example.org", ""]) {
        assert.throws(() => parse(input));
    }
});
