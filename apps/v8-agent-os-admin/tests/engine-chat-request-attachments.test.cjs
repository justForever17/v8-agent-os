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

test("canonical Canvas dispatch metadata survives the Admin submit boundary", () => {
  const result = buildEngineChatRequestPayload({
    data: {
      conversationId: "session-canvas",
      canvasSupervisorDirect: true,
      composerPresentation: { text: "本消息来自画布", references: [] },
      contextMentions: [{
        kind: "canvas_operation",
        id: "canvas-operation-1",
        label: "只修改蒙版区域",
        sourceType: "creative_media.edit_image_region",
      }],
      pluginReferences: [{
        pluginId: "byted-mediakit",
        scope: "task",
        componentIds: ["edit-image"],
      }],
    },
    attachments: [{
      sourceId: "source-image",
      sourceKind: "web_upload",
      resourceRole: "source",
      url: "/api/client/resource/source.png",
      mimeType: "image/png",
      mediaKind: "image",
    }],
    messages: [{
      role: "user",
      content: "本消息来自画布\n[CANVAS EXECUTION CONTRACT v1]\n{}\n[/CANVAS EXECUTION CONTRACT]",
    }],
  }, "owner@example.com");

  assert.equal(result.pythonPayload.data.canvasSupervisorDirect, true);
  assert.deepEqual(result.pythonPayload.data.composerPresentation, {
    text: "本消息来自画布",
    references: [],
  });
  assert.deepEqual(result.pythonPayload.data.contextMentions, [{
    kind: "canvas_operation",
    id: "canvas-operation-1",
    label: "只修改蒙版区域",
    sourceType: "creative_media.edit_image_region",
  }]);
  assert.deepEqual(result.pythonPayload.data.pluginReferences, [{
    pluginId: "byted-mediakit",
    scope: "task",
    componentIds: ["edit-image"],
  }]);
  assert.equal(result.pythonPayload.attachments.length, 1);
  assert.equal(result.pythonPayload.attachments[0].sourceKind, "web_upload");
});

test("non-boolean Canvas flags cannot opt a normal attachment into the privileged route", () => {
  const result = buildEngineChatRequestPayload({
    data: {
      conversationId: "session-normal-image",
      canvasSupervisorDirect: "true",
    },
    attachments: [{
      sourceId: "source-normal",
      sourceKind: "web_upload",
      url: "/api/client/resource/source.png",
      mimeType: "image/png",
      mediaKind: "image",
    }],
    messages: [{ role: "user", content: "请看看这张图片。" }],
  }, "owner@example.com");

  assert.equal(result.pythonPayload.data.canvasSupervisorDirect, undefined);
  assert.equal(result.pythonPayload.data.contextMentions, undefined);
  assert.equal(result.pythonPayload.attachments.length, 1);
  assert.equal(result.pythonPayload.attachments[0].sourceId, "source-normal");
});
