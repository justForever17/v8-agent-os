const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

const adminRoot = path.resolve(__dirname, "..");
const sourcePath = path.join(adminRoot, "src", "lib", "realtime", "engine-chat-request.ts");
const runtimeModeSourcePath = path.join(adminRoot, "src", "lib", "realtime", "supervisor-runtime-mode.ts");
const source = fs.readFileSync(sourcePath, "utf8");
const runtimeModeSource = fs.readFileSync(runtimeModeSourcePath, "utf8");
const runtimeModeCompiled = ts.transpileModule(runtimeModeSource, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
    esModuleInterop: true,
  },
  fileName: runtimeModeSourcePath,
}).outputText;
const runtimeModeModule = { exports: {} };
new Function("require", "module", "exports", runtimeModeCompiled)(require, runtimeModeModule, runtimeModeModule.exports);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
    esModuleInterop: true,
  },
  fileName: sourcePath,
}).outputText;
const testModule = { exports: {} };
const localRequire = (specifier) => specifier === "./supervisor-runtime-mode"
  ? runtimeModeModule.exports
  : require(specifier);
new Function("require", "module", "exports", compiled)(localRequire, testModule, testModule.exports);
const { buildEngineChatRequestPayload } = testModule.exports;
const { SupervisorRuntimeModeValidationError } = runtimeModeModule.exports;

function loadRouteModule(relativePath, requireOverrides = {}) {
  const filename = path.join(adminRoot, relativePath);
  const source = fs.readFileSync(filename, "utf8");
  const compiledRoute = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      esModuleInterop: true,
    },
    fileName: filename,
  }).outputText;
  const routeModule = { exports: {} };
  const localRouteRequire = (specifier) => Object.hasOwn(requireOverrides, specifier)
    ? requireOverrides[specifier]
    : require(specifier);
  new Function("require", "module", "exports", compiledRoute)(
    localRouteRequire,
    routeModule,
    routeModule.exports,
  );
  return routeModule.exports;
}

test("Admin runtime mode boundary mirrors the shared contract", () => {
  const { SUPERVISOR_RUNTIME_MODES } = require("@v8/session-realtime");
  assert.deepEqual(runtimeModeModule.exports.ADMIN_SUPERVISOR_RUNTIME_MODES, SUPERVISOR_RUNTIME_MODES);
});

test("Admin client presentation PATCH returns the normalized shared session contract", () => {
  const routePath = path.join(
    adminRoot,
    "src",
    "app",
    "api",
    "client",
    "conversations",
    "[id]",
    "route.ts",
  );
  const routeSource = fs.readFileSync(routePath, "utf8");
  assert.match(routeSource, /response\.ok \? normalizeAuthoritativeSessionHistoryRecord\(payload\) : payload/);
});

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

test("Supervisor runtime mode is allowlisted and remains independent from Canvas privilege", () => {
  const modes = ["auto", "engineering", "research", "creative_media", "computer_use", "rpa"];
  for (const supervisorRuntimeMode of modes) {
    const result = buildEngineChatRequestPayload({
      data: {
        conversationId: `session-${supervisorRuntimeMode}`,
        supervisorRuntimeMode,
      },
      messages: [{ role: "user", content: "Run this task." }],
    }, "owner@example.com");

    assert.equal(result.pythonPayload.data.supervisorRuntimeMode, supervisorRuntimeMode);
    assert.equal(result.pythonPayload.data.canvasSupervisorDirect, undefined);
  }

  assert.throws(() => buildEngineChatRequestPayload({
    data: {
      conversationId: "session-invalid-runtime-mode",
      supervisorRuntimeMode: "plugin_manager",
    },
    messages: [{ role: "user", content: "Run this task." }],
  }, "owner@example.com"), (error) => {
    assert.ok(error instanceof SupervisorRuntimeModeValidationError);
    assert.equal(error.code, "invalid_supervisor_runtime_mode");
    assert.equal(error.field, "supervisorRuntimeMode");
    assert.deepEqual(error.allowedValues, modes);
    return true;
  });

  const absent = buildEngineChatRequestPayload({
    data: {
      conversationId: "session-absent-runtime-mode",
    },
    messages: [{ role: "user", content: "Run this task." }],
  }, "owner@example.com");
  assert.equal(absent.pythonPayload.data.supervisorRuntimeMode, undefined);
});

test("Supervisor runtime mode aliases are canonical and explicit invalid values fail closed", () => {
  for (const invalidValue of [null, "", 42, { stale: true }]) {
    assert.throws(() => buildEngineChatRequestPayload({
      data: {
        conversationId: "session-invalid-runtime-alias",
        supervisorRuntimeMode: invalidValue,
      },
      messages: [{ role: "user", content: "Run this task." }],
    }, "owner@example.com"), SupervisorRuntimeModeValidationError);
  }

  const rootSnake = buildEngineChatRequestPayload({
    supervisor_runtime_mode: "rpa",
    data: { conversationId: "session-root-snake-runtime-mode" },
    messages: [{ role: "user", content: "Run this task." }],
  }, "owner@example.com");
  assert.equal(rootSnake.pythonPayload.data.supervisorRuntimeMode, "rpa");

  assert.throws(() => buildEngineChatRequestPayload({
    data: {
      conversationId: "session-conflicting-runtime-mode",
      supervisorRuntimeMode: "plugin_manager",
      supervisor_runtime_mode: "research",
    },
    messages: [{ role: "user", content: "Run this task." }],
  }, "owner@example.com"), SupervisorRuntimeModeValidationError);
});

test("invalid Supervisor runtime mode is a structured 400 on trusted, paired, and durable ingress", async () => {
  const { NextRequest, NextResponse } = require("next/server");
  let downstreamCalls = 0;
  const requireOverrides = {
    "next/server": { NextRequest, NextResponse },
    "@/lib/realtime/engine-chat-request": testModule.exports,
    "@/lib/realtime/supervisor-runtime-mode": runtimeModeModule.exports,
    "@/lib/server/client-request-auth": {
      resolveClientUserEmail: async () => "owner@example.com",
      unauthorizedClientJson: () => NextResponse.json({ error: "Unauthorized" }, { status: 401 }),
    },
    "@/lib/server/request-auth": {
      resolveAuthorizedUserEmail: async () => "owner@example.com",
      unauthorizedJson: () => NextResponse.json({ error: "Unauthorized" }, { status: 401 }),
    },
    "@/lib/server/client-proxy": {
      fetchClientEngine: async () => {
        downstreamCalls += 1;
        throw new Error("invalid route must not reach Engine");
      },
    },
    "@/lib/realtime/engine-chat-gateway": {
      createEngineChatGatewayStream: () => {
        downstreamCalls += 1;
        throw new Error("invalid route must not reach Engine");
      },
    },
    "@/lib/volcengine": {
      generateImageWithDoubao: async () => {
        downstreamCalls += 1;
        throw new Error("invalid route must not reach provider");
      },
    },
  };
  const trustedStreamRoute = loadRouteModule("src/app/api/chat/route.ts", requireOverrides);
  const streamRoute = loadRouteModule("src/app/api/client/chat/route.ts", requireOverrides);
  const submitRoute = loadRouteModule("src/app/api/client/chat-submit/route.ts", requireOverrides);
  const body = {
    data: {
      conversationId: "session-invalid-runtime-mode",
      supervisorRuntimeMode: "plugin_manager",
    },
    messages: [{ role: "user", content: "Run this task." }],
  };
  const request = (pathName) => new NextRequest(`http://admin.test${pathName}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  const responses = await Promise.all([
    trustedStreamRoute.POST(request("/api/chat")),
    streamRoute.POST(request("/api/client/chat")),
    submitRoute.POST(request("/api/client/chat-submit")),
  ]);

  for (const response of responses) {
    assert.equal(response.status, 400);
    const payload = await response.json();
    assert.equal(payload.error, "supervisorRuntimeMode is invalid");
    assert.equal(payload.errorCode, "invalid_supervisor_runtime_mode");
    assert.deepEqual(payload.detail, {
      field: "supervisorRuntimeMode",
      allowedValues: ["auto", "engineering", "research", "creative_media", "computer_use", "rpa"],
    });
  }
  assert.equal(downstreamCalls, 0);
});

test("trusted stream forwards the normalized Supervisor runtime mode to Engine data", async () => {
  const { NextRequest, NextResponse } = require("next/server");
  let gatewayPayload;
  const route = loadRouteModule("src/app/api/chat/route.ts", {
    "next/server": { NextRequest, NextResponse },
    "@/lib/realtime/engine-chat-request": testModule.exports,
    "@/lib/realtime/supervisor-runtime-mode": runtimeModeModule.exports,
    "@/lib/server/request-auth": {
      resolveAuthorizedUserEmail: async () => "owner@example.com",
      unauthorizedJson: () => NextResponse.json({ error: "Unauthorized" }, { status: 401 }),
    },
    "@/lib/realtime/engine-chat-gateway": {
      createEngineChatGatewayStream: (payload) => {
        gatewayPayload = payload;
        return new ReadableStream({ start(controller) { controller.close(); } });
      },
    },
    "@/lib/volcengine": {
      generateImageWithDoubao: async () => ({ choices: [] }),
    },
  });
  const response = await route.POST(new NextRequest("http://admin.test/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      data: {
        conversationId: "session-trusted-runtime-mode",
        supervisor_runtime_mode: "research",
      },
      messages: [{ role: "user", content: "Research this task." }],
    }),
  }));

  assert.equal(response.status, 200);
  assert.equal(gatewayPayload.data.supervisorRuntimeMode, "research");
});

test("paired stream and durable submit preserve a valid Supervisor runtime mode", async () => {
  const { NextRequest, NextResponse } = require("next/server");
  let streamPayload;
  let submitPayload;
  const requireOverrides = {
    "next/server": { NextRequest, NextResponse },
    "@/lib/realtime/engine-chat-request": testModule.exports,
    "@/lib/realtime/supervisor-runtime-mode": runtimeModeModule.exports,
    "@/lib/server/client-request-auth": {
      resolveClientUserEmail: async () => "owner@example.com",
      unauthorizedClientJson: () => NextResponse.json({ error: "Unauthorized" }, { status: 401 }),
    },
    "@/lib/realtime/engine-chat-gateway": {
      createEngineChatGatewayStream: (payload) => {
        streamPayload = payload;
        return new ReadableStream({ start(controller) { controller.close(); } });
      },
    },
    "@/lib/server/client-proxy": {
      fetchClientEngine: async (_request, pathName, init) => {
        assert.equal(pathName, "/chat/submit");
        submitPayload = JSON.parse(init.body);
        return new Response(JSON.stringify({ ok: true }), {
          status: 202,
          headers: { "Content-Type": "application/json" },
        });
      },
    },
    "@/lib/volcengine": {
      generateImageWithDoubao: async () => ({ choices: [] }),
    },
  };
  const streamRoute = loadRouteModule("src/app/api/client/chat/route.ts", requireOverrides);
  const submitRoute = loadRouteModule("src/app/api/client/chat-submit/route.ts", requireOverrides);
  const body = {
    data: {
      conversationId: "session-client-valid-runtime-mode",
      supervisorRuntimeMode: "computer_use",
    },
    messages: [{ role: "user", content: "Operate the selected desktop workflow." }],
  };
  const request = (pathName) => new NextRequest(`http://admin.test${pathName}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  const streamResponse = await streamRoute.POST(request("/api/client/chat"));
  const submitResponse = await submitRoute.POST(request("/api/client/chat-submit"));

  assert.equal(streamResponse.status, 200);
  assert.equal(submitResponse.status, 202);
  assert.equal(streamPayload.data.supervisorRuntimeMode, "computer_use");
  assert.equal(submitPayload.data.supervisorRuntimeMode, "computer_use");
});

test("explicit specialist mode cannot use the legacy Volcengine streaming shortcut", async () => {
  const { NextRequest, NextResponse } = require("next/server");
  let providerCalls = 0;
  const gatewayPayloads = [];
  const requireOverrides = {
    "next/server": { NextRequest, NextResponse },
    "@/lib/realtime/engine-chat-request": testModule.exports,
    "@/lib/realtime/supervisor-runtime-mode": runtimeModeModule.exports,
    "@/lib/server/client-request-auth": {
      resolveClientUserEmail: async () => "owner@example.com",
      unauthorizedClientJson: () => NextResponse.json({ error: "Unauthorized" }, { status: 401 }),
    },
    "@/lib/server/request-auth": {
      resolveAuthorizedUserEmail: async () => "owner@example.com",
      unauthorizedJson: () => NextResponse.json({ error: "Unauthorized" }, { status: 401 }),
    },
    "@/lib/realtime/engine-chat-gateway": {
      createEngineChatGatewayStream: (payload) => {
        gatewayPayloads.push(payload);
        return new ReadableStream({ start(controller) { controller.close(); } });
      },
    },
    "@/lib/volcengine": {
      generateImageWithDoubao: async () => {
        providerCalls += 1;
        return { choices: [] };
      },
    },
  };
  const trustedRoute = loadRouteModule("src/app/api/chat/route.ts", requireOverrides);
  const clientRoute = loadRouteModule("src/app/api/client/chat/route.ts", requireOverrides);
  const body = {
    data: {
      conversationId: "session-specialist-image",
      provider: "volcengine",
      fileUrls: ["/api/client/resource/reference.png"],
      supervisorRuntimeMode: "engineering",
    },
    messages: [{ role: "user", content: "Inspect this image in the selected runtime." }],
  };
  const request = (pathName) => new NextRequest(`http://admin.test${pathName}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  const responses = await Promise.all([
    trustedRoute.POST(request("/api/chat")),
    clientRoute.POST(request("/api/client/chat")),
  ]);

  assert.deepEqual(responses.map((response) => response.status), [200, 200]);
  assert.equal(providerCalls, 0);
  assert.equal(gatewayPayloads.length, 2);
  assert.deepEqual(
    gatewayPayloads.map((payload) => payload.data.supervisorRuntimeMode),
    ["engineering", "engineering"],
  );
});

test("canonical Canvas dispatch metadata survives the Admin submit boundary", () => {
  const result = buildEngineChatRequestPayload({
    data: {
      conversationId: "session-canvas",
      supervisorRuntimeMode: "auto",
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
  assert.equal(result.pythonPayload.data.supervisorRuntimeMode, "auto");
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
      supervisorRuntimeMode: "creative_media",
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
  assert.equal(result.pythonPayload.data.supervisorRuntimeMode, "creative_media");
  assert.equal(result.pythonPayload.data.contextMentions, undefined);
  assert.equal(result.pythonPayload.attachments.length, 1);
  assert.equal(result.pythonPayload.attachments[0].sourceId, "source-normal");
});
