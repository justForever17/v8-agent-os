/* eslint-disable @typescript-eslint/no-require-imports */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const repoRoot = path.resolve(__dirname, "../../..");

function read(relativePath) {
  return fs.readFileSync(path.join(repoRoot, relativePath), "utf8");
}

test("project UI workbench opens supported source files and keeps project inspection allowlisted", () => {
  const fileRenderer = read("apps/v8-agent-os-web/src/components/workbench/WorkspaceFileRenderer.tsx");
  const artifactRenderer = read("apps/v8-agent-os-web/src/components/workbench/ArtifactRenderer.tsx");
  const route = read("apps/v8-agent-os-web/src/app/api/ui-patch/[[...segments]]/route.ts");

  for (const suffix of [".css", ".scss", ".sass", ".less", ".js", ".jsx", ".ts", ".tsx", ".vue"]) {
    assert.match(fileRenderer, new RegExp(suffix.replace(".", "\\.")));
    assert.match(artifactRenderer, new RegExp(suffix.replace(".", "\\.")));
  }
  assert.match(fileRenderer, /params\.set\("projectPath", workspacePath\)/);
  assert.match(artifactRenderer, /staticEntry \? "entryPath" : "projectPath"/);
  assert.match(route, /tail\[0\] === "projects" && tail\[1\] === "inspect"/);
  assert.doesNotMatch(route, /tail\.join/);
});

test("project UI workbench starts governed projects and observes HMR without weakening source proof", () => {
  const workbench = read("apps/v8-agent-os-web/src/components/ui-patch/UiPatchWorkbench.tsx");
  const proxy = read("apps/v8-agent-os-engine/scripts/ui_patch_preview_proxy.mjs");
  const service = read("apps/v8-agent-os-engine/core/ui_patch.py");

  assert.match(workbench, /sourceMode === "project"/);
  assert.match(workbench, /projectPath: projectPath\.trim\(\), startDevServer: true/);
  assert.match(workbench, /projects\/inspect/);
  assert.match(workbench, /v8-ui-patch:runtime-changed/);
  assert.match(workbench, /!Object\.keys\(changes\)\.length/);
  assert.match(workbench, /PROJECT_SOURCE_SETTLE_MS = 900/);
  assert.match(workbench, /preview\?\.mode === "project" \? PROJECT_SOURCE_SETTLE_MS : 0/);
  assert.match(proxy, /new MutationObserver/);
  assert.match(proxy, /v8-ui-patch:refresh-selection/);
  assert.match(proxy, /mode === "dev" \|\| mode === "project"/);
  assert.match(proxy, /mode !== "dev" && mode !== "project"/);
  assert.match(service, /create_terminal_session/);
  assert.match(service, /send_terminal_input/);
  assert.match(service, /terminate_terminal_session/);
  assert.match(service, /Project dev server did not become ready/);
  assert.match(service, /dynamicBindings": "read_only"/);
  assert.doesNotMatch(service, /for port in \(5173, 3000/);
});

test("React and Vue source adapters remain conservative and transaction-backed", () => {
  const service = read("apps/v8-agent-os-engine/core/ui_patch.py");
  const workbench = read("apps/v8-agent-os-web/src/components/ui-patch/UiPatchWorkbench.tsx");

  assert.match(service, /source_kind == "react_inline_style"/);
  assert.match(service, /source_kind in \{"html_style", "vue_style"\}/);
  assert.match(service, /source_kind == "component_text"/);
  assert.match(service, /is dynamic and remains read-only/);
  assert.match(service, /beforeHash/);
  assert.match(service, /afterHash/);
  assert.match(service, /_write_transaction\(transaction, before_bytes\)/);
  assert.match(service, /_atomic_replace/);
  assert.match(workbench, /"component_text"/);
  assert.match(workbench, /previewVerificationUnavailable/);
});
