const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

const adminRoot = path.resolve(__dirname, "..");
const sourcePath = path.join(adminRoot, "src", "lib", "realtime", "engine-chat-request.ts");
const source = fs.readFileSync(sourcePath, "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
    esModuleInterop: true,
  },
  fileName: sourcePath,
}).outputText;
const testModule = { exports: {} };
new Function("require", "module", "exports", compiled)(require, testModule, testModule.exports);
const { buildEngineChatRequestPayload } = testModule.exports;

test("structured voice attachment wins over legacy fileUrls fallback", () => {
  const url = "/api/client/resource/voice.mp3";
  const result = buildEngineChatRequestPayload({
    data: {
      conversationId: "session-voice",
      fileUrls: [url],
      attachments: [{
        id: "src-voice",
        sourceId: "src-voice",
        sourceKind: "desktop_pet_voice",
        resourceRole: "source",
        name: "voice.mp3",
        url,
        publicUrl: url,
        previewUrl: url,
        workspaceRelativePath: ".v8/uploads/voice.mp3",
        mimeType: "audio/mpeg",
        mediaKind: "audio",
        size: 2048,
        resourceRef: { adminPath: url },
      }],
    },
    messages: [{ role: "user", content: "" }],
  }, "owner@example.com");

  assert.equal(result.attachments.length, 1);
  assert.equal(result.attachments[0].sourceId, "src-voice");
  assert.equal(result.attachments[0].sourceKind, "desktop_pet_voice");
  assert.equal(result.attachments[0].mimeType, "audio/mpeg");
  assert.equal(result.attachments[0].source, "client_upload");
  assert.deepEqual(result.attachments[0].resourceRef, { adminPath: url });
  assert.deepEqual(result.pythonPayload.attachments, result.attachments);
});
