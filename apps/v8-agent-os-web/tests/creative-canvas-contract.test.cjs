const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

const webRoot = path.resolve(__dirname, "..");
const repoRoot = path.resolve(webRoot, "../..");

function read(relativePath) {
  return fs.readFileSync(path.join(repoRoot, relativePath), "utf8");
}

function loadTypeScriptModule(relativePath) {
  const filename = path.join(repoRoot, relativePath);
  const source = fs.readFileSync(filename, "utf8");
  const output = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
    fileName: filename,
  }).outputText;
  const module = { exports: {} };
  new Function("module", "exports", "require", output)(module, module.exports, require);
  return module.exports;
}

test("canvas event projection proves lineage and isolates two sessions", () => {
  const { buildCreativeCanvasEventProjection } = loadTypeScriptModule(
    "apps/v8-agent-os-web/src/lib/creative-canvas-events.ts",
  );
  const messages = [{
    id: "message-a",
    role: "user",
    runId: "run-a",
    metadata: {
      sessionId: "session-a",
      contextMentions: [{
        kind: "canvas_operation",
        id: "operation-a",
        label: "生成图片",
        sourceType: "creative_media.generate_image",
      }],
    },
    nodes: [{
      kind: "execution",
      executionType: "tool_call",
      toolCallId: "tool-a",
      data: { canvasOperationId: "operation-a", status: "running" },
    }],
  }];
  const artifacts = [
    {
      artifactId: "artifact-a",
      sessionId: "session-a",
      mimeType: "image/png",
      metadata: { toolCallId: "tool-a" },
      resourceRef: { adminPath: "/client/artifacts/a", resourceId: "artifact-a" },
    },
    {
      artifactId: "artifact-a-run-only",
      sessionId: "session-a",
      runId: "run-a",
      mimeType: "image/png",
    },
    {
      artifactId: "artifact-b",
      sessionId: "session-b",
      mimeType: "video/mp4",
    },
    {
      artifactId: "artifact-without-session",
      mimeType: "application/json",
    },
    {
      artifactId: "uploaded-source-a",
      sessionId: "session-a",
      resourceRole: "source",
      metadata: { toolCallId: "tool-a" },
      mimeType: "image/png",
    },
  ];

  const sessionA = buildCreativeCanvasEventProjection({ sessionId: "session-a", messages, artifacts });
  assert.equal(sessionA.operations.length, 1);
  assert.equal(sessionA.operations[0].state, "ready");
  assert.deepEqual(sessionA.operations[0].artifacts.map((item) => item.artifactId), ["artifact-a"]);
  assert.deepEqual(sessionA.unplacedArtifacts.map((item) => item.artifactId), ["artifact-a-run-only"]);
  assert.equal(sessionA.operations[0].artifacts[0].resourceRef.adminPath, undefined);
  assert.equal(sessionA.operations[0].artifacts[0].resourceRef.resourceId, "artifact-a");

  const sessionB = buildCreativeCanvasEventProjection({ sessionId: "session-b", messages, artifacts });
  assert.equal(sessionB.operations.length, 0);
  assert.deepEqual(sessionB.unplacedArtifacts.map((item) => item.artifactId), ["artifact-b"]);
});

test("canvas action catalog locks mutation and capability-gates MediaKit", () => {
  const actions = loadTypeScriptModule("apps/v8-agent-os-web/src/lib/creative-canvas-actions.ts");
  const selection = [{ id: "image-1", mediaType: "image", order: 0 }];
  const running = actions.getCreativeCanvasActions({
    target: "node",
    selection,
    sessionRunning: true,
    pluginAvailable: true,
    pluginGranted: true,
  });
  assert.deepEqual(running.map((item) => item.actionId).sort(), ["local.download", "local.view"]);

  const unavailable = actions.getCreativeCanvasActions({
    target: "node",
    selection,
    sessionRunning: false,
    pluginAvailable: false,
    pluginGranted: false,
    allowPluginGrantRequest: true,
  });
  assert.equal(unavailable.some((item) => item.binding?.kind === "mediakit"), false);

  const explicitRequest = actions.getCreativeCanvasActions({
    target: "node",
    selection,
    sessionRunning: false,
    pluginAvailable: true,
    pluginGranted: false,
    allowPluginGrantRequest: true,
  });
  assert.equal(explicitRequest.some((item) => item.actionId === "mediakit.image.remove-image-background"), true);
  assert.equal(actions.MEDIAKIT_CREATIVE_CANVAS_ACTIONS.length, 38);
});

test("canvas is one floating surface and reuses normal chat plus lazy 3D preview", () => {
  const canvas = read("apps/v8-agent-os-web/src/components/workbench/CreativeArtifactCanvas.tsx");
  const chat = read("apps/v8-agent-os-web/src/app/chat/ChatClient.tsx");
  const shell = read("apps/v8-agent-os-web/src/components/workbench/WorkbenchShell.tsx");
  const media = read("apps/v8-agent-os-web/src/components/workbench/CreativeCanvasMedia.tsx");
  const model = read("apps/v8-agent-os-web/src/components/chat/ModelViewer.tsx");
  const zh = JSON.parse(read("apps/v8-agent-os-web/src/i18n/locales/zh-CN.json"));

  assert.match(canvas, /data-testid="creative-artifact-canvas"/);
  assert.match(canvas, /selectionRect/);
  assert.match(canvas, /message\.comment_connection/);
  assert.match(canvas, /CreativeCanvasMaskEditor/);
  assert.match(canvas, /rasterizeCreativeCanvasMask/);
  const maskEditor = read("apps/v8-agent-os-web/src/components/workbench/CreativeCanvasMaskEditor.tsx");
  assert.match(maskEditor, /destination-out/);
  assert.match(maskEditor, /transparent mask pixels as the editable region/);
  assert.match(canvas, /sourceKind", "web_upload"/);
  assert.doesNotMatch(canvas, /grid-cols-\[168px_/);
  assert.doesNotMatch(canvas, /<textarea[^>]+taskPlaceholder/);
  assert.match(shell, /sessionRunning=\{props\.sessionRunning\}/);
  assert.match(shell, /dynamic\([\s\S]*import\("\.\/CreativeArtifactCanvas"\)/);
  assert.match(chat, /if \(activeConversationRunning\) return false/);
  assert.match(chat, /composerPresentation/);
  assert.match(chat, /t\("web\.workbench\.canvas\.humanMessage"\)/);
  assert.match(chat, /canvasSupervisorDirect: true/);
  assert.match(chat, /buildCreativeCanvasExecutionContract/);
  assert.match(chat, /resourceRole: "source"/);
  const canvasSubmit = chat.slice(
    chat.indexOf("const handleCanvasTask"),
    chat.indexOf("const handleVoiceAudioMessage"),
  );
  assert.equal((canvasSubmit.match(/handleSend\(syntheticEvent/g) || []).length, 1);
  assert.match(media, /dynamic\(/);
  assert.match(model, /Bounds/);
  assert.doesNotMatch(model, /environment="city"/);
  assert.equal(zh["web.creativeCanvas.actions.mediakit.video.segment-scenes.label"], "按场景切分视频");
});

test("canvas task contract gives Supervisor exact native, mask, tool and edge bindings", () => {
  const contractModule = loadTypeScriptModule(
    "apps/v8-agent-os-web/src/lib/creative-canvas-task-contract.ts",
  );
  const maskContract = contractModule.buildCreativeCanvasExecutionContract({
    instruction: "只替换蒙版内的头部",
    refs: [
      { id: "source-image", origin: "source", mediaType: "image" },
      { id: "source-mask", origin: "source", mediaType: "mask" },
    ],
    operation: {
      operationId: "operation-mask",
      actionId: "creative_media.edit_image_region",
      outputKind: "artifact",
      outputSlot: "image_derivative",
      maskRevision: 3,
      binding: { kind: "creative_media", capability: "image.edit" },
    },
  });
  assert.deepEqual(maskContract.execution, {
    tool: "creative_media_jobs",
    arguments: {
      action: "create",
      request: {
        modality: "image",
        operationKind: "image.edit",
        prompt: "只替换蒙版内的头部",
        canvasOperationId: "operation-mask",
        sourceId: "source-image",
        maskSourceId: "source-mask",
      },
    },
  });
  assert.deepEqual(maskContract.resources, {
    sourceIds: ["source-image"],
    maskSourceId: "source-mask",
  });
  assert.equal(JSON.stringify(maskContract).includes("workspacePath"), false);
  assert.equal(JSON.stringify(maskContract).includes("resourceRef"), false);

  const keyframeContract = contractModule.buildCreativeCanvasExecutionContract({
    instruction: "在首尾帧之间生成视频",
    refs: [
      { id: "first-frame", origin: "source", mediaType: "image" },
      { id: "last-frame", origin: "source", mediaType: "image" },
    ],
    operation: {
      operationId: "operation-keyframes",
      actionId: "creative_media.generate_video_from_keyframes",
      outputKind: "artifact",
      outputSlot: "video",
      binding: { kind: "creative_media", capability: "video.keyframe_to_video" },
    },
  });
  assert.equal(keyframeContract.execution.arguments.request.modality, "video");
  assert.equal(keyframeContract.execution.arguments.request.operationKind, "video.first_last_frame");
  assert.deepEqual(keyframeContract.execution.arguments.request.sourceIds, ["first-frame", "last-frame"]);

  const toolContract = contractModule.buildCreativeCanvasExecutionContract({
    instruction: "去掉背景",
    refs: [{ id: "source-image", origin: "source", mediaType: "image" }],
    operation: {
      operationId: "operation-tool",
      actionId: "mediakit.image.remove-image-background",
      outputKind: "artifact",
      outputSlot: "image_derivative",
      binding: {
        kind: "mediakit",
        pluginId: "volcengine-mediakit",
        componentId: "mediakit-cli",
        domain: "image",
        action: "remove-image-background",
      },
    },
  });
  assert.equal(toolContract.execution.tool, "plugin_cli");
  assert.equal(toolContract.execution.pluginId, "volcengine-mediakit");
  assert.equal(toolContract.execution.profileId, "mediakit-cli");
  assert.equal(toolContract.execution.actionId, "remove-image-background");

  const edgeContract = contractModule.buildCreativeCanvasExecutionContract({
    instruction: "让右侧卡片延续左侧卡片",
    refs: [
      { id: "source-left", origin: "source", mediaType: "image" },
      { id: "artifact-right", origin: "artifact", mediaType: "video" },
    ],
    operation: {
      operationId: "operation-edge",
      actionId: "message.comment_connection",
      outputKind: "runtime_status",
      outputSlot: "chat_run",
      binding: { kind: "canvas_message", action: "comment_connection" },
      edge: {
        edgeId: "edge-a",
        fromNodeId: "node-left",
        toNodeId: "node-right",
        fromResourceId: "source-left",
        toResourceId: "artifact-right",
      },
    },
  });
  assert.equal(edgeContract.canvasOperationId, "operation-edge");
  assert.deepEqual(edgeContract.edge, {
    edgeId: "edge-a",
    fromNodeId: "node-left",
    toNodeId: "node-right",
    fromResourceId: "source-left",
    toResourceId: "artifact-right",
  });
});

test("canvas Human Surface stays one opaque product message after authoritative replay", () => {
  const contractModule = loadTypeScriptModule(
    "apps/v8-agent-os-web/src/lib/creative-canvas-task-contract.ts",
  );
  const zh = JSON.parse(read("apps/v8-agent-os-web/src/i18n/locales/zh-CN.json"));
  const en = JSON.parse(read("apps/v8-agent-os-web/src/i18n/locales/en.json"));
  assert.equal(zh["web.workbench.canvas.humanMessage"], "本消息来自画布");
  assert.equal(en["web.workbench.canvas.humanMessage"], "This message is from Canvas");
  assert.equal(contractModule.isCreativeCanvasCanonicalMessage(
    "本消息来自画布\n[CANVAS EXECUTION CONTRACT v1]\n{\"sourceId\":\"secret\"}",
    {},
  ), true);
  assert.equal(contractModule.isCreativeCanvasCanonicalMessage("opaque replay", {
    attachments: [{ metadata: { canvasOperationId: "operation-a" } }],
  }), true);

  const chatMessage = read("apps/v8-agent-os-web/src/components/chat/ChatMessage.tsx");
  const stream = read("apps/v8-agent-os-web/src/hooks/use-langgraph-stream.ts");
  assert.match(chatMessage, /isCreativeCanvasCanonicalMessage\(message\.content, message\.metadata\)/);
  assert.match(chatMessage, /canvasHumanSurface \? \[\] : extractMessageAttachments\(message\)/);
  assert.match(chatMessage, /canvasHumanSurface \? \[\] : Array\.from/);
  assert.match(chatMessage, /return text \? \{ text, references \} : null/);
  assert.match(stream, /if \(presentationText\) \{/);
});

test("canvas cards preserve media proportions and connect through reusable edge ports", () => {
  const canvas = read("apps/v8-agent-os-web/src/components/workbench/CreativeArtifactCanvas.tsx");
  const media = read("apps/v8-agent-os-web/src/components/workbench/CreativeCanvasMedia.tsx");

  assert.match(canvas, /const NODE_WIDTH = 280/);
  assert.match(canvas, /const NODE_HEIGHT = 190/);
  assert.match(canvas, /type CanvasPort = "left" \| "right"/);
  assert.match(canvas, /fromPort: CanvasPort/);
  assert.match(canvas, /toPort: CanvasPort/);
  assert.match(canvas, /kind: "connect"/);
  assert.match(canvas, /data-canvas-port=\{port\}/);
  assert.match(canvas, /edge\.fromPort === fromPort && edge\.toPort === toPort/);
  assert.match(canvas, /portPoint\(from, edge\.fromPort\)/);
  assert.match(canvas, /mediaNodeDimensions\(dimensions\.width, dimensions\.height\)/);
  assert.match(canvas, /data-canvas-title=\{node\.nodeId\}/);
  assert.match(canvas, /group-hover\/title/);
  assert.match(media, /naturalWidth/);
  assert.match(media, /naturalHeight/);
  assert.match(media, /videoWidth/);
  assert.match(media, /videoHeight/);
});

test("canvas source catalog includes unsent uploads and projects Admin preview URLs to Web", () => {
  const canvas = read("apps/v8-agent-os-web/src/components/workbench/CreativeArtifactCanvas.tsx");
  const uploadRoute = read("apps/v8-agent-os-web/src/app/api/upload/route.ts");
  const webSources = read("apps/v8-agent-os-web/src/app/api/sources/route.ts");
  const adminSources = read("apps/v8-agent-os-admin/src/app/api/client/sources/route.ts");
  const engineSources = read("apps/v8-agent-os-engine/api/session_workflow_routes.py");

  assert.match(canvas, /params\.set\("includeUnbound", "true"\)/);
  assert.match(canvas, /\/api\/client\/workspace\/resource/);
  assert.match(canvas, /`\/api\/workspace\/resource\$\{url\.slice/);
  assert.match(uploadRoute, /previewUrl: toWebResourceUrl/);
  assert.match(webSources, /query\.set\("includeUnbound", "true"\)/);
  assert.match(adminSources, /query\.set\("include_unbound", "true"\)/);
  assert.match(engineSources, /include_unbound: bool = False/);
  assert.match(engineSources, /include_unbound=include_unbound/);
  assert.match(engineSources, /include_internal: bool = False/);
  assert.match(engineSources, /canvas_mask/);
  assert.match(canvas, /sourceKind", "canvas_mask"/);
  assert.match(canvas, /sourceKind === "canvas_mask"/);
  const submit = read("apps/v8-agent-os-web/src/app/chat/ChatClient.tsx");
  assert.match(submit, /reference\.mediaType !== "mask"/);
  assert.match(submit, /reference\.sourceKind !== "canvas_mask"/);
});

test("Workbench fails closed on artifact sessions and previews text formats by file extension", () => {
  const workbench = read("apps/v8-agent-os-web/src/lib/workbench.ts");
  const renderer = read("apps/v8-agent-os-web/src/components/workbench/ArtifactRenderer.tsx");
  const overview = read("apps/v8-agent-os-web/src/components/chat/WorkspaceWorkbenchPanel.tsx");

  assert.match(workbench, /!artifactSessionId \|\| artifactSessionId !== ownerSessionId/);
  assert.match(workbench, /\["md", "markdown", "mdown", "mkd"\]\.includes\(extension\)/);
  assert.match(workbench, /"json", "jsonc", "jsonl", "ndjson", "txt", "log"/);
  assert.match(workbench, /return "code"/);
  assert.match(renderer, /fetch\(`\/api\/artifacts\/\$\{encodeURIComponent\(document\.subjectRef\.artifactId\)\}`/);
  assert.match(renderer, /artifactSessionId !== document\.subjectRef\.sessionId/);
  const openOutput = overview.slice(
    overview.indexOf("const openOutput = useCallback"),
    overview.indexOf("const openSource = useCallback"),
  );
  assert.ok(
    openOutput.indexOf("const artifact = output.rawArtifact") < openOutput.indexOf("if (output.path)"),
    "governed artifact lineage must take precedence over managed-worktree paths",
  );
});
