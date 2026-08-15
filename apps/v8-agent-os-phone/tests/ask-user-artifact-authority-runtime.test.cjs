const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

const phoneRoot = path.resolve(__dirname, "..");

function loadAskUserAuthority() {
  const filename = path.join(phoneRoot, "src/components/chat/AskUserModal.tsx");
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
  const component = () => null;
  const localRequire = (specifier) => {
    const overrides = {
      "react": { memo: (value) => value, useMemo: () => [], useState: () => [false, () => {}] },
      "react/jsx-runtime": { Fragment: Symbol("Fragment"), jsx: () => null, jsxs: () => null },
      "react-native": {
        Image: component,
        Modal: component,
        Pressable: component,
        ScrollView: component,
        StyleSheet: { create: (value) => value },
        Text: component,
        TextInput: component,
        View: component,
      },
      "react-native-webview": { WebView: component },
      "@expo/vector-icons": { MaterialCommunityIcons: component },
      "@/src/lib/phone-media-source": { usePreparedPhoneMediaSource: () => ({ resolvedSrc: "" }) },
      "@/src/providers/ui-prefs": { useUiPrefs: () => ({ colors: {}, t: (key) => key, themeMode: "light" }) },
      "@/src/theme/tokens": { radii: {}, spacing: {} },
    };
    return Object.hasOwn(overrides, specifier) ? overrides[specifier] : require(specifier);
  };
  new Function("require", "module", "exports", output)(localRequire, moduleRecord, moduleRecord.exports);
  return moduleRecord.exports.__artifactAuthorityTest;
}

test("Phone ask_user media rejects stale session and untrusted direct URLs at runtime", () => {
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
    "/api/client/artifacts/artifact-a/content?sessionId=session-a",
  );
  assert.equal(
    mediaPlaybackUrl({ resourceRef: { kind: "external_url", url: "https://trusted.test/media.png" } }, "session-a"),
    "https://trusted.test/media.png",
  );
});
