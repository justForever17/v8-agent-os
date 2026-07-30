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

function loadTypeScriptModule(relativePath, requireOverrides = {}) {
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
  const localRequire = (specifier) => Object.hasOwn(requireOverrides, specifier)
    ? requireOverrides[specifier]
    : require(specifier);
  new Function("module", "exports", "require", output)(module, module.exports, localRequire);
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
  const canvas = read("apps/v8-agent-os-web/src/components/workbench/CreativeArtifactCanvas.tsx");
  const engineGraph = read("apps/v8-agent-os-engine/core/creative_canvas_graph.py");
  const selection = [{ id: "image-1", mediaType: "image", order: 0 }];
  const running = actions.getCreativeCanvasActions({
    target: "node",
    selection,
    sessionRunning: true,
    pluginAvailable: true,
    pluginGranted: true,
  });
  assert.deepEqual(running.map((item) => item.actionId).sort(), ["local.download", "local.open_in_file_manager", "local.view"]);

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
  assert.equal(actions.MEDIAKIT_CREATIVE_CANVAS_ACTIONS.length, 36);
  assert.equal(actions.MEDIAKIT_CREATIVE_CANVAS_ACTIONS.some((item) => item.actionId === "mediakit.editing.trim-video"), false);
  assert.equal(actions.LOCAL_CREATIVE_CANVAS_ACTIONS.some((item) => item.actionId === "local.create_preview_card"), false);
  assert.equal(actions.LOCAL_CREATIVE_CANVAS_ACTIONS.some((item) => item.actionId === "local.create_download_card"), false);
  assert.equal(actions.LOCAL_CREATIVE_CANVAS_ACTIONS.some((item) => item.actionId === "local.open_in_file_manager"), true);
  assert.equal(actions.CREATIVE_MEDIA_NATIVE_ACTIONS.find((item) => item.actionId === "creative_media.extract_video_frame_exact")?.parameterEditor, "frame_pick");
  assert.equal(actions.CREATIVE_MEDIA_NATIVE_ACTIONS.find((item) => item.actionId === "creative_media.trim_video_exact")?.parameterEditor, "time_range");
  assert.equal(actions.CREATIVE_MEDIA_NATIVE_ACTIONS.find((item) => item.actionId === "creative_media.trim_video_exact")?.networkRequired, false);
  assert.equal(actions.MEDIAKIT_CREATIVE_CANVAS_ACTIONS.find((item) => item.actionId === "mediakit.video.probe-video-metadata")?.requiresPrompt, false);
  assert.match(canvas, /actionDefinitions\.some\(\(definition\) => definition\.actionId === action\.actionId\)/);
  assert.match(canvas, /if \(!definition \|\| sessionRunningRef\.current\) return;/);
  for (const action of actions.CREATIVE_MEDIA_NATIVE_ACTIONS) {
    assert.match(engineGraph, new RegExp(`['"]${action.actionId.replaceAll(".", "\\.")}['"]`));
  }
});

test("canvas is one floating surface and reuses normal chat plus lazy 3D preview", () => {
  const canvas = read("apps/v8-agent-os-web/src/components/workbench/CreativeArtifactCanvas.tsx");
  const chat = read("apps/v8-agent-os-web/src/app/chat/ChatClient.tsx");
  const shell = read("apps/v8-agent-os-web/src/components/workbench/WorkbenchShell.tsx");
  const media = read("apps/v8-agent-os-web/src/components/workbench/CreativeCanvasMedia.tsx");
  const serialization = read("apps/v8-agent-os-web/src/components/workbench/creative-canvas/serialization.ts");
  const timelineEditor = read("apps/v8-agent-os-web/src/components/workbench/creative-canvas/time-range-editor.tsx");
  const model = read("apps/v8-agent-os-web/src/components/chat/ModelViewer.tsx");
  const zh = JSON.parse(read("apps/v8-agent-os-web/src/i18n/locales/zh-CN.json"));
  const en = JSON.parse(read("apps/v8-agent-os-web/src/i18n/locales/en.json"));

  assert.match(canvas, /data-testid="creative-artifact-canvas"/);
  assert.match(canvas, /selectionRect/);
  assert.match(canvas, /message\.comment_connection/);
  assert.match(canvas, /CreativeCanvasMaskEditor/);
  assert.match(canvas, /rasterizeCreativeCanvasMask/);
  assert.match(canvas, /const resource = displayResourceForNode\(node\)/);
  assert.match(canvas, /const maskResource = maskNode \? displayResourceForNode\(maskNode\) : null/);
  assert.match(serialization, /if \(storedKind === "sink"\) return \[\]/);
  assert.doesNotMatch(canvas, /kind: "resource" \| "input" \| "action" \| "result" \| "sink"/);
  assert.doesNotMatch(serialization, /sinkKind/);
  assert.match(canvas, /<CreativeCanvasMaskOverlay mask=\{node\.mask\} \/>/);
  const maskEditor = read("apps/v8-agent-os-web/src/components/workbench/CreativeCanvasMaskEditor.tsx");
  assert.match(maskEditor, /destination-out/);
  assert.match(maskEditor, /transparent mask pixels as the editable region/);
  assert.match(canvas, /sourceKind", "web_upload"/);
  assert.doesNotMatch(canvas, /grid-cols-\[168px_/);
  assert.doesNotMatch(canvas, /<textarea[^>]+taskPlaceholder/);
  assert.match(shell, /sessionRunning=\{props\.sessionRunning\}/);
  assert.match(shell, /dynamic\([\s\S]*import\("\.\/CreativeArtifactCanvas"\)/);
  assert.match(chat, /if \(activeConversationRunning \|\| activeConversationIdRef\.current !== canvasSessionId\) return false/);
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
  assert.match(media, /preload="metadata"/);
  assert.doesNotMatch(media, /preload="auto"/);
  assert.match(media, /pointer-events-none h-full w-full object-contain/);
  assert.match(media, /web\.workbench\.canvas\.media\.pauseVideo/);
  assert.equal(zh["web.workbench.canvas.media.pauseVideo"], "暂停视频");
  assert.equal(en["web.workbench.canvas.media.pauseVideo"], "Pause video");
  assert.match(canvas, /onWheel=\{\(event\) => event\.stopPropagation\(\)\}/);
  const wheelHandler = canvas.slice(canvas.indexOf("const handleWheel"), canvas.indexOf("const processPointerMove"));
  assert.match(wheelHandler, /addEventListener\("wheel", handleWheel, \{ passive: false \}\)/);
  assert.match(wheelHandler, /closest\("\[data-canvas-wheel-isolation\]"\)/);
  assert.match(wheelHandler, /const factor = Math\.exp\(-deltaY \* 0\.002\)/);
  assert.match(wheelHandler, /x: point\.x - worldX \* scale, y: point\.y - worldY \* scale/);
  assert.doesNotMatch(wheelHandler, /event\.ctrlKey|event\.metaKey|viewport\.x - event\.deltaX|viewport\.y - event\.deltaY/);
  assert.match(canvas, /pendingConnectionDrop/);
  assert.match(canvas, /adoptWorkspaceResource\(dropped\)/);
  assert.match(canvas, /catalogAbortRef\.current\?\.abort\(\)/);
  assert.match(canvas, /sessionIdRef\.current !== sessionId/);
  assert.match(timelineEditor, /onChangeRef\.current\(/);
  assert.match(timelineEditor, /\[mode, resource\?\.id, resource\?\.origin, sessionId, t\]/);
  assert.match(canvas, /kind: "reconnect"/);
  assert.match(canvas, /handleEdgePointerDown/);
  assert.match(canvas, /from\.kind === "action" && to\.kind === "result" && to\.producerActionNodeId === from\.nodeId/);
  assert.match(canvas, /current\.edges\.filter\(\(edge\) => edge\.edgeId !== interaction\.edgeId\)/);
  assert.match(canvas, /cursor-default/);
  assert.doesNotMatch(canvas, /cursor-crosshair/);
  assert.match(model, /Bounds/);
  assert.doesNotMatch(model, /environment="city"/);
  assert.equal(zh["web.creativeCanvas.actions.mediakit.video.segment-scenes.label"], "按场景切分视频");
  assert.equal(zh["web.creativeCanvas.actions.local.fit_view.label"], "显示全部");
  assert.equal(zh["web.creativeCanvas.actions.local.open_in_file_manager.label"], "在文件管理器中打开");
  assert.equal(zh["web.creativeCanvas.actions.creative_media.compose_psd.label"], "合成分层 PSD");
  assert.equal(zh["web.creativeCanvas.actions.mediakit.editing.trim-video.label"], "分割视频片段");
  assert.equal(zh["web.creativeCanvas.actions.mediakit.video.probe-video-metadata.label"], "读取视频参数");
});

test("canvas resource URLs never depend on worktree or physical source paths", () => {
  const serialization = read("apps/v8-agent-os-web/src/components/workbench/creative-canvas/serialization.ts");
  const normalizeSlice = serialization.slice(
    serialization.indexOf("export function normalizeResource"),
    serialization.indexOf("function sanitizeMask"),
  );

  assert.match(
    normalizeSlice,
    /stringValue\(record, "previewUrl", "preview_url", "contentUrl", "content_url", "downloadUrl", "download_url", "url", "publicUrl", "public_url", "externalUrl", "external_url"\)/,
  );
  assert.doesNotMatch(normalizeSlice, /stringValue\(record,[^\n]*(?:sourcePath|source_path)/);
  assert.doesNotMatch(normalizeSlice, /stringValue\(record,[^\n]*(?:worktreeRoot|worktree_root)/);
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

  const trimContract = contractModule.buildCreativeCanvasExecutionContract({
    instruction: "分割视频片段",
    refs: [{ id: "workspace-video", origin: "workspace_asset", mediaType: "video" }],
    operation: {
      operationId: "operation-trim",
      actionId: "creative_media.trim_video_exact",
      outputKind: "artifact",
      outputSlot: "video_derivative",
      parameters: { probeFingerprint: "v8mf-proof", startFrameIndex: 31, endFrameIndexExclusive: 115 },
      binding: { kind: "creative_media", capability: "video.trim_exact" },
    },
  });
  assert.deepEqual(trimContract.resources, { workspaceAssetIds: ["workspace-video"] });
  assert.deepEqual(trimContract.execution, {
    tool: "creative_media_jobs",
    arguments: {
      action: "create",
      request: {
        modality: "video",
        operationKind: "video.trim_exact",
        prompt: "分割视频片段",
        canvasOperationId: "operation-trim",
        workspaceAssetId: "workspace-video",
        probeFingerprint: "v8mf-proof",
        startFrameIndex: 31,
        endFrameIndexExclusive: 115,
      },
    },
  });

  const frameContract = contractModule.buildCreativeCanvasExecutionContract({
    instruction: "抽取指定画面",
    refs: [{ id: "workspace-video", origin: "workspace_asset", mediaType: "video" }],
    operation: {
      operationId: "operation-frame",
      actionId: "creative_media.extract_video_frame_exact",
      outputKind: "artifact",
      outputSlot: "image_derivative",
      parameters: { probeFingerprint: "v8mf-proof", frameIndex: 73 },
      binding: { kind: "creative_media", capability: "video.extract_frame_exact" },
    },
  });
  assert.deepEqual(frameContract.execution.arguments.request, {
    modality: "video",
    operationKind: "video.extract_frame_exact",
    prompt: "抽取指定画面",
    canvasOperationId: "operation-frame",
    workspaceAssetId: "workspace-video",
    probeFingerprint: "v8mf-proof",
    frameIndex: 73,
  });

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

test("canvas graph edits stay inert until one explicit run enters the normal message pipeline", () => {
  const contractModule = loadTypeScriptModule(
    "apps/v8-agent-os-web/src/lib/creative-canvas-task-contract.ts",
  );
  const graphContract = contractModule.buildCreativeCanvasExecutionContract({
    instruction: "运行到此",
    refs: [
      { id: "source-image", origin: "source", mediaType: "image" },
      { id: "artifact-video", origin: "artifact", mediaType: "video" },
      { id: "workspace-audio", origin: "workspace_asset", mediaType: "audio" },
    ],
    operation: {
      operationId: "operation-graph",
      actionId: "canvas.graph.run_to_here",
      outputKind: "artifacts",
      outputSlot: "canvas_graph",
      binding: { kind: "creative_media", capability: "canvas.graph.execute" },
      parameters: {
        graphId: "canvas-graph-a",
        graphRevision: 7,
        targetNodeIds: ["result-a"],
      },
    },
  });
  assert.deepEqual(graphContract.execution.arguments.request, {
    modality: "workflow",
    operationKind: "canvas.graph.execute",
    canvasOperationId: "operation-graph",
    graphId: "canvas-graph-a",
    graphRevision: 7,
    targetNodeIds: ["result-a"],
  });
  assert.deepEqual(graphContract.resources, {
    sourceIds: ["source-image"],
    artifactIds: ["artifact-video"],
    workspaceAssetIds: ["workspace-audio"],
  });
  assert.equal("prompt" in graphContract.execution.arguments.request, false);
  assert.throws(() => contractModule.buildCreativeCanvasExecutionContract({
    instruction: "运行全部",
    refs: [],
    operation: {
      operationId: "operation-missing-graph",
      actionId: "canvas.graph.run_all",
      outputKind: "artifacts",
      outputSlot: "canvas_graph",
      binding: { kind: "creative_media", capability: "canvas.graph.execute" },
      parameters: {},
    },
  }), /persisted graph id and revision/);

  const canvas = read("apps/v8-agent-os-web/src/components/workbench/CreativeArtifactCanvas.tsx");
  const graphOperations = read("apps/v8-agent-os-web/src/components/workbench/creative-canvas/graph-operations.ts");
  const timeline = read("apps/v8-agent-os-web/src/components/workbench/creative-canvas/timeline.ts");
  const timelineEditor = read("apps/v8-agent-os-web/src/components/workbench/creative-canvas/time-range-editor.tsx");
  const conversationGroups = read("apps/v8-agent-os-web/src/lib/conversation-groups.ts");
  const configSlice = canvas.slice(canvas.indexOf("const submitComposer"), canvas.indexOf("const runGraph"));
  const runSlice = canvas.slice(canvas.indexOf("const runGraph"), canvas.indexOf("const refreshTemplates"));
  assert.doesNotMatch(configSlice, /onSubmitTask/);
  assert.match(runSlice, /onSubmitTask\(\{/);
  assert.match(runSlice, /sessionId,/);
  assert.match(runSlice, /sessionIdRef\.current !== sessionId/);
  assert.match(runSlice, /graphSubmittingRef\.current/);
  assert.match(canvas, /const selectedExecutableTargetIds/);
  assert.match(canvas, /canvasTargetHasAction\(snapshot, nodeId\)/);
  assert.match(graphOperations, /export function canvasPortsForNode/);
  assert.match(canvas, /const displayResourceForNode = useCallback/);
  assert.doesNotMatch(canvas, /const createSinkCard/);
  assert.match(timelineEditor, /onInput=\{\(event\) => setBoundary\("start", Number\(event\.currentTarget\.value\), false, true\)\}/);
  assert.match(timelineEditor, /const queueScrubPreview/);
  assert.match(timelineEditor, /requestVideoFrameCallback/);
  assert.match(timelineEditor, /preload="metadata"/);
  assert.match(canvas, /const hasPendingResult = snapshot\.nodes\.some/);
  assert.match(canvas, /\["reserved", "running", "waiting"\]\.includes/);
  assert.doesNotMatch(canvas, /hasPendingPlaceholder/);
  assert.match(timelineEditor, /requestProbe\("preview"\)/);
  assert.match(timeline, /range\.exact !== false/);
  assert.match(timelineEditor, /exact: !approximate/);
  assert.match(timelineEditor, /onPointerUp=\{\(\) => commitDraftRange\("start"\)\}/);
  assert.match(canvas, /window\.localStorage\.removeItem\(legacyStorageKey\)/);
  assert.match(conversationGroups, /\["failed", "recoverable_failed", "cancelled", "degraded"\]/);
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
  assert.equal(contractModule.isCreativeCanvasCanonicalMessage("[CANVAS OPERATION]", {}), false);

  const chatMessage = read("apps/v8-agent-os-web/src/components/chat/ChatMessage.tsx");
  const chatClient = read("apps/v8-agent-os-web/src/app/chat/ChatClient.tsx");
  const stream = read("apps/v8-agent-os-web/src/hooks/use-langgraph-stream.ts");
  assert.match(chatMessage, /isCreativeCanvasCanonicalMessage\(message\.content, message\.metadata\)/);
  assert.match(chatMessage, /canvasHumanSurface \? \[\] : extractMessageAttachments\(message\)/);
  assert.match(chatMessage, /canvasHumanSurface \? \[\] : Array\.from/);
  assert.match(chatMessage, /return text \? \{ text, references \} : null/);
  assert.match(chatClient, /activeConversationIdRef\.current !== canvasSessionId/);
  assert.match(stream, /if \(presentationText\) \{/);
});

test("canvas cards preserve media proportions and connect through reusable edge ports", () => {
  const canvas = read("apps/v8-agent-os-web/src/components/workbench/CreativeArtifactCanvas.tsx");
  const media = read("apps/v8-agent-os-web/src/components/workbench/CreativeCanvasMedia.tsx");
  const types = read("apps/v8-agent-os-web/src/components/workbench/creative-canvas/types.ts");
  const graphOperations = read("apps/v8-agent-os-web/src/components/workbench/creative-canvas/graph-operations.ts");

  assert.match(types, /export const NODE_WIDTH = 280/);
  assert.match(types, /export const NODE_HEIGHT = 190/);
  assert.match(types, /export type CanvasPort = "left" \| "right"/);
  assert.match(types, /fromPort: CanvasPort/);
  assert.match(types, /toPort: CanvasPort/);
  assert.match(canvas, /kind: "connect"/);
  assert.match(canvas, /data-canvas-port=\{port\}/);
  assert.match(graphOperations, /edge\.fromPort === "right" && edge\.toPort === "left"/);
  assert.match(graphOperations, /portPoint\(from, edge\.fromPort\)/);
  assert.match(canvas, /mediaNodeDimensions\(dimensions\.width, dimensions\.height\)/);
  assert.match(canvas, /data-canvas-title=\{node\.nodeId\}/);
  assert.match(canvas, /group-hover\/title/);
  assert.match(media, /naturalWidth/);
  assert.match(media, /naturalHeight/);
  assert.match(media, /videoWidth/);
  assert.match(media, /videoHeight/);
});

test("canvas interaction layers expose reversible graph editing, contextual feedback, and bounded media work", () => {
  const canvas = read("apps/v8-agent-os-web/src/components/workbench/CreativeArtifactCanvas.tsx");
  const history = read("apps/v8-agent-os-web/src/components/workbench/creative-canvas/history.ts");
  const graphSource = read("apps/v8-agent-os-web/src/components/workbench/creative-canvas/graph-operations.ts");
  const overlays = read("apps/v8-agent-os-web/src/components/workbench/creative-canvas/overlays.tsx");
  const media = read("apps/v8-agent-os-web/src/components/workbench/CreativeCanvasMedia.tsx");
  const graph = loadTypeScriptModule(
    "apps/v8-agent-os-web/src/components/workbench/creative-canvas/graph-operations.ts",
    { "./types": { MAX_EDGES: 320 } },
  );
  const timeline = loadTypeScriptModule(
    "apps/v8-agent-os-web/src/components/workbench/creative-canvas/timeline.ts",
  );

  assert.match(history, /export function useCanvasHistory/);
  assert.match(history, /beginTransaction/);
  assert.match(history, /finishTransaction[\s\S]*setHistoryVersion/);
  assert.match(canvas, /event\.code === "Space"/);
  assert.match(canvas, /event\.key\.toLowerCase\(\) === "z"/);
  assert.match(canvas, /event\.key\.toLowerCase\(\) === "d"/);
  assert.match(canvas, /layoutCanvasGraph/);
  assert.match(canvas, /alignCanvasNodes/);
  assert.match(canvas, /<CanvasMiniMap/);
  assert.match(overlays, /filtered\.slice\(0, 5\)/);
  assert.match(overlays, /onWheel=\{\(event\) => event\.stopPropagation\(\)\}/);
  assert.match(overlays, /export function CanvasPreflightPanel/);
  assert.match(graphSource, /"cycle"/);
  assert.match(graphSource, /getCanvasPreflight/);
  assert.match(graphSource, /"missing-configuration"/);
  assert.match(canvas, /\/api\/workbench\/sessions\/\$\{encodeURIComponent\(sessionId\)\}\/canvas\/graph\/validate/);
  assert.match(canvas, /requestGraphRun/);
  assert.match(media, /IntersectionObserver/);
  assert.match(media, /preload=\{effectiveVisible \? "metadata" : "none"\}/);
  assert.match(media, /document\.hidden/);
  assert.match(media, /readyResourceUrls/);

  const snapshot = {
    schema: "v8.creative_canvas_graph.v1",
    version: 3,
    graphId: "graph-cycle",
    viewport: { x: 0, y: 0, scale: 1 },
    nodes: [
      { nodeId: "action-a", kind: "action", origin: "placeholder", actionDefinitionId: "edit-a", x: 0, y: 0, width: 280, height: 190 },
      { nodeId: "result-a", kind: "result", origin: "artifact", producerActionNodeId: "action-a", mediaType: "image", x: 320, y: 0, width: 280, height: 190 },
      { nodeId: "action-b", kind: "action", origin: "placeholder", actionDefinitionId: "edit-b", x: 640, y: 0, width: 280, height: 190 },
      { nodeId: "result-b", kind: "result", origin: "artifact", producerActionNodeId: "action-b", mediaType: "image", x: 960, y: 0, width: 280, height: 190 },
    ],
    edges: [{ edgeId: "edge-a-b", from: "result-a", to: "action-b", fromPort: "right", toPort: "left", fromPortId: "output", toPortId: "image", dataType: "image", role: "data", order: 0, note: "" }],
  };
  const definitions = ["edit-a", "edit-b"].map((actionId) => ({
    actionId,
    inputs: [{ portId: "image", mediaTypes: ["image"], min: 1, max: 1, ordered: false }],
    output: { portId: "output", slot: "image", mediaTypes: ["image"] },
    requiresPrompt: false,
  }));
  assert.equal(graph.getConnectionVerdict(snapshot, definitions, "result-b", "action-a").issue, "cycle");
  assert.equal(graph.getConnectionVerdict(snapshot, definitions, "result-a", "action-b").issue, "duplicate");
  assert.equal(graph.getCanvasPreflight(snapshot, definitions, { status: "idle", nodeStates: {}, outputs: {} })
    .some((issue) => issue.nodeId === "action-a" && issue.code === "missing-input"), true);
  const configuredSnapshot = {
    ...snapshot,
    nodes: snapshot.nodes.map((node) => node.nodeId === "action-b" ? { ...node, parameters: {} } : node),
  };
  const configuredDefinitions = definitions.map((definition) => definition.actionId === "edit-b"
    ? { ...definition, parameterEditor: "frame_pick" }
    : definition);
  assert.equal(graph.getCanvasPreflight(configuredSnapshot, configuredDefinitions, { status: "idle", nodeStates: {}, outputs: {} })
    .some((issue) => issue.nodeId === "action-b" && issue.code === "missing-configuration"), true);
  const frameAction = configuredSnapshot.nodes.find((node) => node.nodeId === "action-b");
  const frameDefinition = configuredDefinitions.find((definition) => definition.actionId === "edit-b");
  assert.equal(graph.isCanvasActionConfigured(frameAction, frameDefinition), false);
  assert.equal(graph.isCanvasActionConfigured({ ...frameAction, parameters: { frameIndex: 12 } }, frameDefinition), true);
  const storedFramePick = {
    unit: "frame",
    count: 0,
    startIndex: 602,
    endIndexExclusive: 0,
    durationSeconds: "0",
    timeBaseNumerator: 1,
    timeBaseDenominator: 1,
    displayPrecision: 6,
    loading: true,
  };
  const previewTimeline = {
    ...storedFramePick,
    count: 688,
    startIndex: 0,
    endIndexExclusive: 688,
    durationSeconds: "27.52",
    timeBaseNumerator: 1,
    timeBaseDenominator: 25,
    averageFrameRateNumerator: 25,
    averageFrameRateDenominator: 1,
  };
  const previewReconciled = timeline.reconcileCanvasTimeRange(storedFramePick, previewTimeline, "frame");
  assert.equal(previewReconciled.startIndex, 602);
  assert.equal(previewReconciled.endIndexExclusive, 603);
  const exactTimeline = {
    ...previewTimeline,
    boundaryTicks: Array.from({ length: 689 }, (_, index) => index),
    probeFingerprint: "probe-1",
    exact: true,
    loading: false,
  };
  const exactReconciled = timeline.reconcileCanvasTimeRange(previewReconciled, exactTimeline, "frame");
  assert.equal(exactReconciled.startIndex, 602);
  assert.equal(exactReconciled.endIndexExclusive, 603);
  assert.match(canvas, /const composerPanelWidth = Math\.min/);
  assert.match(canvas, /setPreflightOpen\(false\);[\s\S]*setTemplateOpen\(false\);[\s\S]*setTrayOpen\(false\);[\s\S]*setContextMenu\(null\);/);
});

test("canvas source catalog includes unsent uploads and projects Admin preview URLs to Web", () => {
  const canvas = read("apps/v8-agent-os-web/src/components/workbench/CreativeArtifactCanvas.tsx");
  const serialization = read("apps/v8-agent-os-web/src/components/workbench/creative-canvas/serialization.ts");
  const uploadRoute = read("apps/v8-agent-os-web/src/app/api/upload/route.ts");
  const webSources = read("apps/v8-agent-os-web/src/app/api/sources/route.ts");
  const adminSources = read("apps/v8-agent-os-admin/src/app/api/client/sources/route.ts");
  const engineSources = read("apps/v8-agent-os-engine/api/session_workflow_routes.py");

  assert.match(canvas, /params\.set\("includeUnbound", "true"\)/);
  assert.match(serialization, /\/api\/client\/workspace\/resource/);
  assert.match(serialization, /`\/api\/workspace\/resource\$\{url\.slice/);
  assert.match(uploadRoute, /previewUrl: toWebResourceUrl/);
  assert.match(webSources, /query\.set\("includeUnbound", "true"\)/);
  assert.match(adminSources, /query\.set\("include_unbound", "true"\)/);
  assert.match(engineSources, /include_unbound: bool = False/);
  assert.match(engineSources, /include_unbound=include_unbound/);
  assert.match(engineSources, /include_internal: bool = False/);
  assert.match(engineSources, /canvas_mask/);
  assert.match(canvas, /sourceKind", "canvas_mask"/);
  assert.match(serialization, /sourceKind === "canvas_mask"/);
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
