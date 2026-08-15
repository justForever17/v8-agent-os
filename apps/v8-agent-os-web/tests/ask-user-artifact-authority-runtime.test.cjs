/* eslint-disable @typescript-eslint/no-require-imports, @next/next/no-assign-module-variable */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

const webRoot = path.resolve(__dirname, "..");

function loadAskUserAuthority() {
  const filename = path.join(webRoot, "src/components/chat/AskUserModal.tsx");
  const source = `${fs.readFileSync(filename, "utf8")}\nexport const __artifactAuthorityTest = { sessionBoundMediaRef, mediaPlaybackUrl };\n`;
  const output = ts.transpileModule(source, {
    compilerOptions: {
      esModuleInterop: true,
      jsx: ts.JsxEmit.ReactJSX,
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
    fileName: filename,
  }).outputText;
  const moduleRecord = { exports: {} };
  const localRequire = (specifier) => {
    const overrides = {
      "@/components/providers/LocaleProvider": { useT: () => (key) => key },
      "@/components/ui/tooltip": {
        Tooltip: () => null,
        TooltipContent: () => null,
        TooltipProvider: () => null,
        TooltipTrigger: () => null,
      },
    };
    return Object.hasOwn(overrides, specifier) ? overrides[specifier] : require(specifier);
  };
  new Function("require", "module", "exports", output)(localRequire, moduleRecord, moduleRecord.exports);
  return moduleRecord.exports.__artifactAuthorityTest;
}

test("Web ask_user media rejects stale session and untrusted direct URLs at runtime", () => {
  const { mediaPlaybackUrl } = loadAskUserAuthority();

  assert.equal(mediaPlaybackUrl({ artifactId: "artifact-a", sessionId: "session-b" }, "session-a"), "");
  assert.equal(mediaPlaybackUrl({
    artifactId: "artifact-a",
    resourceRef: { kind: "artifact_content", artifactId: "artifact-a", sessionId: "session-b" },
  }, "session-a"), "");
  assert.equal(mediaPlaybackUrl({
    artifactId: "artifact-a",
    resourceRef: { kind: "artifact_content", artifactId: "artifact-b", sessionId: "session-a" },
  }, "session-a"), "");
  assert.equal(mediaPlaybackUrl({ contentUrl: "https://untrusted.test/direct.png" }, "session-a"), "");
  assert.equal(mediaPlaybackUrl({ resourceRef: "https://untrusted.test/direct.png" }, "session-a"), "");
  assert.equal(
    mediaPlaybackUrl({ id: "artifact-a", contentUrl: "https://untrusted.test/direct.png" }, "session-a"),
    "/api/artifacts/artifact-a/content?sessionId=session-a",
  );
  assert.equal(
    mediaPlaybackUrl({ resourceRef: { kind: "external_url", url: "https://trusted.test/media.png" } }, "session-a"),
    "https://trusted.test/media.png",
  );
});
