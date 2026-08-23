/* eslint-disable @typescript-eslint/no-require-imports, @next/next/no-assign-module-variable */
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
  assert.equal(actions.CREATIVE_MEDIA_NATIVE_ACTIONS.find((item) => item.actionId === "creative_media.trim_video_exact")?.executionClass, "graph_direct");
  assert.equal(actions.CANVAS_MESSAGE_ACTIONS.find((item) => item.actionId === "message.submit_selection")?.executionClass, "supervisor_message");
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
  const modelLease = read("apps/v8-agent-os-web/src/components/chat/gltf-resource-lease.ts");
  const engineGraph = read("apps/v8-agent-os-engine/core/creative_canvas_graph.py");
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
  assert.match(canvas, /sourceKind = "canvas_upload"/);
  assert.match(canvas, /uploadFiles\(\[file\], undefined, "canvas_camera"\)/);
  assert.doesNotMatch(canvas, /grid-cols-\[168px_/);
  assert.doesNotMatch(canvas, /<textarea[^>]+taskPlaceholder/);
  assert.match(shell, /sessionRunning=\{props\.sessionRunning\}/);
  assert.match(shell, /visible=\{shouldShow && document\.kind === "creative_canvas"\}/);
  assert.match(shell, /containerWidth > 0 && containerWidth < 760/);
  assert.match(shell, /mode === "focus" \|\| compactWorkbench \? "focus" : "split"/);
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
  assert.match(media, /usesOriginalResource = kind === "model_3d" \|\| kind === "motion"/);
  assert.match(media, /const previewRequested = inspect \|\| modelPreviewKey === cacheKey/);
  assert.match(media, /const shouldRenderModel = !compact && previewRequested && \(inspect \? effectiveVisible : active\)/);
  assert.match(media, /shouldRenderModel \? <ModelViewer[\s\S]*active=\{effectiveVisible\} interactive=\{inspect\}/);
  assert.match(media, /web\.workbench\.canvas\.media\.load3d/);
  assert.match(model, /acquireGltfResourceLease/);
  assert.match(model, /useGLTF\.clear\(url\)/);
  assert.match(modelLease, /MAX_IDLE_GLTF_RESOURCES = 8/);
  assert.match(modelLease, /disposeSceneResources/);
  assert.match(canvas, /active=\{selected && inspectNodeId !== node\.nodeId\}/);
  assert.match(canvas, /visible=\{visible && visibleNodeIds\.has\(node\.nodeId\)\}/);
  assert.match(canvas, /<CanvasInspectorReviewPanel/);
  assert.match(canvas, /<CreativeCanvasMedia resource=\{candidate\} inspect visible=\{visible\} \/>/);
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
  assert.match(canvas, /retryFailedGraph/);
  assert.match(canvas, /canvas\/graph\/runs\/\$\{encodeURIComponent\(graphRunId\)\}\/retry-failed-branch/);
  assert.match(canvas, /const graphRunActive = \["queued", "running", "cancelling"\]\.includes\(graphRuntime\.status\)/);
  assert.match(canvas, /const graphRunCancellable = \["queued", "running"\]\.includes\(graphRuntime\.status\)/);
  assert.match(canvas, /canvas\/graph\/runs\/\$\{encodeURIComponent\(graphRunId\)\}\/cancel/);
  assert.match(canvas, /body: JSON\.stringify\(\{ reason: "user_cancelled" \}\)/);
  assert.match(canvas, /\? \{ \.\.\.current, status: "cancelling" \}/);
  assert.match(canvas, /current\.graphRunId === graphRunId \? reconcileCanvasRuntimeProjection\(current/);
  assert.match(canvas, /error: String\(projectedError\.message \|\| ""\)/);
  assert.match(canvas, /outputs: current\.outputs/);
  assert.match(canvas, /current\.graphRunId === graphRunId && current\.status === "cancelling"/);
  assert.match(canvas, /\? \{ \.\.\.current, status: graphRuntime\.status \}/);
  assert.match(canvas, /sameCanvasRequestOwner\(graphCancelOwnerRef\.current, owner\)/);
  assert.match(canvas, /disabled=\{!graphRunCancellable\}/);
  assert.match(canvas, /h-8 shrink-0 items-center gap-1\.5 whitespace-nowrap/);
  assert.match(canvas, /pointer-events-none absolute left-1\/2 top-14[\s\S]{0,300}web\.workbench\.canvas\.graph\.locked/);
  assert.equal(zh["web.workbench.canvas.graph.state.cancelling"], "正在取消");
  assert.equal(en["web.workbench.canvas.graph.state.cancelling"], "Cancelling");
  assert.equal(zh["web.workbench.canvas.graph.cancel"], "取消运行");
  assert.equal(en["web.workbench.canvas.graph.cancel"], "Cancel run");
  assert.match(canvas, /canvasOperationId: String\(run\.canvasOperationId \|\| run\.canvas_operation_id \|\| current\.canvasOperationId \|\| ""\)/);
  assert.doesNotMatch(canvas, /operationId: retry\?\.canvasOperationId/);
  assert.match(canvas, /targetNodeIds: targetIds/);
  assert.match(canvas, /Array\.isArray\(run\.targetNodeIds\) \? run\.targetNodeIds\.map\(String\) : current\.targetNodeIds/);
  assert.match(engineGraph, /"graphRevision": int\(run_data\.get\("graph_revision"\) or 0\) or None/);
  assert.match(engineGraph, /_json\(run_data\.get\("target_node_ids_json"\), \[\]\)/);
  assert.doesNotMatch(canvas, /actionState === "failed"[\s\S]{0,400}requestGraphRun\(\[node\.nodeId\]\)/);
  const taskContract = read("apps/v8-agent-os-web/src/lib/creative-canvas-task-contract.ts");
  assert.match(taskContract, /retryGraphRunId: String\(parameters\.retryGraphRunId\)\.trim\(\)/);
  assert.match(canvas, /adoptWorkspaceResource\(dropped\)/);
  assert.match(canvas, /catalogAbortRef\.current\?\.abort\(\)/);
  const initializationLoad = canvas.slice(
    canvas.indexOf("const loadPayload = async"),
    canvas.indexOf("}, 0);", canvas.indexOf("const loadPayload = async")),
  );
  assert.match(initializationLoad, /void loadGraph\(\)\.catch\(reportLoadError\)\.finally/);
  assert.match(initializationLoad, /void loadActions\(\)\.catch\(reportLoadError\)/);
  assert.match(initializationLoad, /void loadTemplates\(\)\.catch\(reportLoadError\)/);
  assert.doesNotMatch(initializationLoad, /Promise\.all\(/);
  const catalogLoad = canvas.slice(canvas.indexOf("const loadCatalog"), canvas.indexOf("const reconcileCatalog"));
  assert.match(catalogLoad, /Promise\.allSettled/);
  assert.match(catalogLoad, /\["artifacts", loadResources\("\/api\/artifacts"/);
  assert.match(catalogLoad, /\["sources", loadResources\("\/api\/sources"/);
  assert.match(catalogLoad, /\["assets", loadWorkspaceAssets\(\)\]/);
  assert.match(catalogLoad, /\["folders", loadWorkspaceFolders\(\)\]/);
  assert.match(catalogLoad, /replaceResourceChannel/);
  assert.match(catalogLoad, /reportChannelError\(channel, reason\)/);
  assert.match(canvas, /data-canvas-catalog-error=\{channel\}/);
  assert.match(canvas, /reconcileCanvasMediaCatalog\(sessionId\)\.then/);
  assert.doesNotMatch(catalogLoad, /mediaBase\}\/reconcile/);
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

test("canvas graph actions persist parameters and run through the direct session-scoped endpoint", () => {
  const canvas = read("apps/v8-agent-os-web/src/components/workbench/CreativeArtifactCanvas.tsx");
  const graphOperations = read("apps/v8-agent-os-web/src/components/workbench/creative-canvas/graph-operations.ts");
  const timeline = read("apps/v8-agent-os-web/src/components/workbench/creative-canvas/timeline.ts");
  const timelineEditor = read("apps/v8-agent-os-web/src/components/workbench/creative-canvas/time-range-editor.tsx");
  const conversationGroups = read("apps/v8-agent-os-web/src/lib/conversation-groups.ts");
  const actions = read("apps/v8-agent-os-web/src/lib/creative-canvas-actions.ts");
  const workbenchShell = read("apps/v8-agent-os-web/src/components/workbench/WorkbenchShell.tsx");
  const route = read("apps/v8-agent-os-engine/api/creative_canvas_routes.py");
  const workbenchProxy = read("apps/v8-agent-os-web/src/app/api/workbench/[[...segments]]/route.ts");
  const configSlice = canvas.slice(canvas.indexOf("const submitComposer"), canvas.indexOf("const runGraph"));
  const runSlice = canvas.slice(canvas.indexOf("const runGraph"), canvas.indexOf("const refreshTemplates"));
  assert.match(configSlice, /action\.executionClass === "supervisor_message"/);
  assert.match(configSlice, /onSubmitTask\(\{/);
  assert.doesNotMatch(runSlice, /onSubmitTask\(\{/);
  assert.match(runSlice, /canvas\/graph\/runs/);
  assert.match(runSlice, /startResponse/);
  assert.match(runSlice, /isCurrentMutationOwner\(owner\)/);
  assert.match(runSlice, /releaseMutationOwner\(owner\)/);
  assert.match(canvas, /isActiveCanvasRequestOwner\(/);
  assert.match(canvas, /sameCanvasRequestOwner\(/);
  assert.doesNotMatch(canvas, /graphSubmittingRef/);
  assert.match(canvas, /signal: controller\.signal/);
  assert.match(canvas, /controller\.signal\.aborted \|\| !mountedRef\.current \|\| sessionIdRef\.current !== sessionId/);
  assert.match(workbenchShell, /key=\{canvasTab\.document\.subjectRef\.sessionId\}/);
  assert.match(canvas, /const runnablePreviewTargetIds/);
  assert.match(canvas, /const canvasNodeTitle/);
  assert.match(canvas, /const blockingIssues = issues\.filter\(\(issue\) => issue\.severity === "error"\)/);
  assert.match(canvas, /结果槽\|结果\|result/);
  assert.match(canvas, /web\.workbench\.canvas\.graph\.result/);
  assert.match(canvas, /actionTimelineMode/);
  assert.match(canvas, /<CanvasTimeRangeEditor/);
  assert.match(canvas, /const actionTimelineReady = actionTimelineMode === "frame"/);
  assert.match(canvas, /probeFingerprint && Number\.isInteger\(parameters\.frameIndex\)/);
  assert.match(canvas, /startFrameIndex[\s\S]*endFrameIndexExclusive/);
  assert.match(canvas, /loading: true,/);
  assert.match(canvas, /web\.workbench\.canvas\.graph\.actionPrompt/);
  assert.doesNotMatch(canvas, /web\.workbench\.canvas\.graph\.runToHere/);
  assert.match(graphOperations, /export function canvasPortsForNode/);
  assert.match(canvas, /const displayResourceForNode = useCallback/);
  assert.doesNotMatch(canvas, /const createSinkCard/);
  assert.match(timelineEditor, /onInput=\{\(event\) => setBoundary\("start", Number\(event\.currentTarget\.value\), false, true\)\}/);
  assert.match(timelineEditor, /const queueScrubPreview/);
  assert.match(timelineEditor, /requestVideoFrameCallback/);
  assert.match(timelineEditor, /preload="metadata"/);
  assert.doesNotMatch(timelineEditor, /\bcontrols\b/);
  assert.match(timelineEditor, /const togglePlayback/);
  assert.match(canvas, /const hasPendingResult = snapshot\.nodes\.some/);
  assert.match(canvas, /\["reserved", "running", "waiting"\]\.includes/);
  assert.doesNotMatch(canvas, /hasPendingPlaceholder/);
  assert.match(timelineEditor, /requestProbe\("preview"\)/);
  assert.match(timeline, /range\.exact !== false/);
  assert.match(timelineEditor, /exact: !approximate/);
  assert.match(timelineEditor, /if \(!incomingIsHydrated && currentIsHydrated\) return;/);
  assert.match(timelineEditor, /if \(!resource \|\| !currentRange\.loading\) return;/);
  assert.match(timelineEditor, /onPointerUp=\{\(\) => commitDraftRange\("start"\)\}/);
  assert.match(canvas, /window\.localStorage\.removeItem\(legacyStorageKey\)/);
  assert.match(conversationGroups, /\["failed", "recoverable_failed", "cancelled", "degraded"\]/);
  assert.match(actions, /"graph_direct"/);
  assert.match(actions, /"supervisor_message"/);
  assert.match(route, /@router\.post\("\/sessions\/\{session_id\}\/canvas\/graph\/runs"\)/);
  assert.match(route, /prepare_direct_execution/);
  assert.match(route, /asyncio\.create_task/);
  assert.match(workbenchProxy, /\|runs\(\?:/);
  assert.match(workbenchProxy, /retry-failed-branch/);
});

test("canvas request ownership prevents a stale Session from clearing the active Session", () => {
  const owners = loadTypeScriptModule(
    "apps/v8-agent-os-web/src/components/workbench/creative-canvas/request-owner.ts",
  );
  const ownerA = { sessionId: "session-a", token: "request-a" };
  const ownerB = { sessionId: "session-b", token: "request-b" };

  assert.equal(owners.isActiveCanvasRequestOwner(ownerA, ownerA, "session-a", true), true);
  assert.equal(owners.isActiveCanvasRequestOwner(ownerB, ownerA, "session-b", true), false);
  assert.equal(owners.sameCanvasRequestOwner(ownerB, ownerA), false);
  assert.equal(owners.sameCanvasRequestOwner(ownerB, ownerB), true);
  assert.equal(owners.isActiveCanvasRequestOwner(ownerB, ownerB, "session-b", false), false);
});

test("canvas runtime polling rejects stale epochs and same-run status regressions", () => {
  const canvas = read("apps/v8-agent-os-web/src/components/workbench/CreativeArtifactCanvas.tsx");
  const runtime = loadTypeScriptModule(
    "apps/v8-agent-os-web/src/components/workbench/creative-canvas/request-owner.ts",
  );
  const running = { graphRunId: "graph-run-a", status: "running" };

  assert.match(canvas, /const requestEpoch = runtimeMutationEpochRef\.current/);
  assert.match(canvas, /isCurrentCanvasRuntimeEpoch\(requestEpoch, runtimeMutationEpochRef\.current\)/);
  assert.match(canvas, /advanceRuntimeMutationEpoch\(\)/);
  assert.match(canvas, /allowExplicitActiveTransition: true/);
  assert.equal(runtime.isCurrentCanvasRuntimeEpoch(4, 4), true);
  assert.equal(runtime.isCurrentCanvasRuntimeEpoch(4, 5), false);
  for (const status of ["cancelling", "cancelled", "failed", "succeeded", "interrupted"]) {
    const current = { graphRunId: "graph-run-a", status };
    assert.equal(runtime.reconcileCanvasRuntimeProjection(current, running), current);
  }
  const failed = { graphRunId: "graph-run-a", status: "failed" };
  assert.equal(runtime.reconcileCanvasRuntimeProjection(
    failed,
    running,
    { allowExplicitActiveTransition: true },
  ), running);
  assert.equal(runtime.reconcileCanvasRuntimeProjection(
    { ...failed, updatedAt: "2026-08-15T08:00:00Z" },
    { ...running, updatedAt: "2026-08-15T08:00:01Z" },
  ).status, "running");
  assert.equal(runtime.reconcileCanvasRuntimeProjection(
    { ...failed, updatedAt: "2026-08-15T08:00:01Z" },
    { ...running, updatedAt: "2026-08-15T08:00:00Z" },
  ).status, "failed");
  for (const status of ["cancelling", "cancelled", "completed", "succeeded", "interrupted"]) {
    assert.equal(runtime.reconcileCanvasRuntimeProjection(
      { graphRunId: "graph-run-a", status, updatedAt: "2026-08-15T08:00:00Z" },
      { ...running, updatedAt: "2026-08-15T08:00:01Z" },
    ).status, status);
  }
  assert.equal(runtime.reconcileCanvasRuntimeProjection(
    failed,
    { graphRunId: "graph-run-b", status: "running" },
  ).graphRunId, "graph-run-b");
});

test("canvas Human Surface stays one opaque product message after authoritative replay", () => {
  const contractModule = loadTypeScriptModule(
    "apps/v8-agent-os-web/src/lib/creative-canvas-task-contract.ts",
  );
  const zh = JSON.parse(read("apps/v8-agent-os-web/src/i18n/locales/zh-CN.json"));
  const en = JSON.parse(read("apps/v8-agent-os-web/src/i18n/locales/en.json"));
  assert.equal(zh["web.workbench.canvas.humanMessage"], "本消息来自画布");
  assert.equal(en["web.workbench.canvas.humanMessage"], "This message was sent from the canvas");
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

test("canvas persistence ignores hydration presentation calibration but keeps graph edits", () => {
  const canvas = read("apps/v8-agent-os-web/src/components/workbench/CreativeArtifactCanvas.tsx");
  const serialization = loadTypeScriptModule(
    "apps/v8-agent-os-web/src/components/workbench/creative-canvas/serialization.ts",
    {
      "../CreativeCanvasMedia": { creativeCanvasMediaType: () => "image" },
      "./types": {
        EMPTY_SNAPSHOT: {
          schema: "v8.creative_canvas_graph.v1",
          version: 3,
          graphId: "",
          nodes: [],
          edges: [],
          viewport: { x: 24, y: 24, scale: 1 },
        },
        MAX_EDGES: 320,
        MAX_NODES: 160,
        MEDIA_FOOTER_HEIGHT: 36,
        NODE_HEIGHT: 190,
        NODE_WIDTH: 280,
      },
    },
  );
  const base = {
    schema: "v8.creative_canvas_graph.v1",
    version: 3,
    graphId: "canvas-a",
    nodes: [{
      nodeId: "source-a",
      kind: "resource",
      origin: "source",
      resourceId: "source-a",
      x: 40,
      y: 60,
      width: 280,
      height: 190,
      mediaType: "image",
    }],
    edges: [],
    viewport: { x: 24, y: 24, scale: 1 },
  };
  const key = serialization.canvasGraphPersistenceKey(base);

  assert.equal(serialization.canvasGraphPersistenceKey({
    ...base,
    viewport: { x: -420, y: 120, scale: 1.8 },
  }), key);
  assert.equal(serialization.canvasGraphPersistenceKey({
    ...base,
    nodes: [{ ...base.nodes[0], width: 420, height: 312 }],
  }), key);
  assert.notEqual(serialization.canvasGraphPersistenceKey({
    ...base,
    nodes: [{ ...base.nodes[0], x: 140 }],
  }), key);
  assert.notEqual(serialization.canvasGraphPersistenceKey({
    ...base,
    nodes: [{ ...base.nodes[0], mask: { revision: 1, strokes: [] } }],
  }), key);
  assert.notEqual(serialization.canvasGraphPersistenceKey({
    ...base,
    edges: [{
      edgeId: "edge-a",
      from: "source-a",
      to: "action-a",
      fromPort: "right",
      toPort: "left",
      fromPortId: "output",
      toPortId: "input",
      dataType: "image",
      role: "data",
      order: 0,
      note: "",
    }],
  }), key);

  assert.equal(serialization.canvasGraphNeedsPersistence(base, key, true, false), false);
  assert.equal(serialization.canvasGraphNeedsPersistence(base, key, false, false), false);
  assert.equal(serialization.canvasGraphNeedsPersistence(base, key, false, true), true);
  assert.equal(serialization.canvasGraphNeedsPersistence(base, key, true, true), false);
  assert.equal(serialization.canvasGraphNeedsPersistence({
    ...base,
    viewport: { x: -420, y: 120, scale: 1.8 },
  }, key, true, false), false);
  assert.equal(serialization.canvasGraphNeedsPersistence({
    ...base,
    nodes: [{ ...base.nodes[0], x: 140 }],
  }, key, true, false), true);

  const merged = serialization.mergeCanvasPresentationState({
    ...base,
    nodes: [
      { ...base.nodes[0], x: 180, width: 300, height: 210 },
      {
        ...base.nodes[0],
        nodeId: "source-b",
        resourceId: "source-b",
        x: 520,
        width: 310,
        height: 220,
      },
    ],
    viewport: { x: 0, y: 0, scale: 1 },
  }, {
    ...base,
    nodes: [{ ...base.nodes[0], x: 999, width: 420, height: 312 }],
    viewport: { x: -420, y: 120, scale: 1.8 },
  });
  assert.deepEqual(merged.viewport, { x: -420, y: 120, scale: 1.8 });
  assert.equal(merged.nodes[0].x, 180, "Engine remains authoritative for graph placement");
  assert.equal(merged.nodes[0].width, 420, "local media calibration survives hydration");
  assert.equal(merged.nodes[0].height, 312);
  assert.equal(merged.nodes[1].width, 310, "new Engine nodes keep authoritative dimensions");

  const cachedDuringFlush = {
    ...base,
    nodes: [{ ...base.nodes[0], x: 999, width: 420, height: 312 }],
    viewport: { x: -420, y: 120, scale: 1.8 },
  };
  const pendingHydration = serialization.resolveCanvasHydrationSnapshot({
    engineValue: { ...base, nodes: [{ ...base.nodes[0], x: 180 }] },
    cachedValue: cachedDuringFlush,
    responseRevision: 7,
    laneRevision: 7,
    laneDirty: true,
  });
  assert.equal(pendingHydration.snapshot.nodes[0].x, 999, "an immediate reopen keeps the dirty local graph");

  const conflictHydration = serialization.resolveCanvasHydrationSnapshot({
    engineValue: { ...base, nodes: [{ ...base.nodes[0], x: 180 }] },
    cachedValue: cachedDuringFlush,
    responseRevision: 7,
    laneRevision: 8,
    laneDirty: false,
    settledValue: { ...base, nodes: [{ ...base.nodes[0], x: 260, width: 300, height: 210 }] },
  });
  assert.equal(conflictHydration.staleHydration, true);
  assert.equal(conflictHydration.snapshot.nodes[0].x, 260, "the settled Engine conflict graph wins");
  assert.equal(conflictHydration.snapshot.nodes[0].width, 420, "local presentation calibration still survives");

  assert.match(canvas, /graphSaveScheduler\.configureSession\(sessionId,[\s\S]*lastSavedKey: canvasGraphPersistenceKey\(authoritative\)/);
  assert.match(canvas, /persisted: Boolean\(graphPayload\?\.graph\)/);
  assert.match(canvas, /migrationPending: !graphPayload\?\.graph && Boolean\(cached\.nodes\.length \|\| cached\.edges\.length\)/);
  assert.match(canvas, /const graphSaveScheduler: CanvasGraphSaveScheduler/);
  assert.match(canvas, /graphSaveScheduler\.getSettled\(sessionId\)/);
  assert.match(canvas, /graphSaveScheduler\.getDesired\(sessionId\)/);
  assert.match(canvas, /resolveCanvasHydrationSnapshot\(/);
  assert.match(canvas, /graphSaveScheduler\.flush\(detachedSessionId, candidate\)/);
  assert.match(canvas, /const liveSnapshot = snapshotRef\.current/);
  assert.match(canvas, /const editedDuringHydration = canvasGraphPersistenceKey\(liveSnapshot\) !== canvasGraphPersistenceKey\(cached\)/);
  assert.match(canvas, /lane = graphSaveScheduler\.flush\(sessionId, liveSnapshot\)/);
  assert.match(canvas, /const laneOwnsLocalGraph = Boolean\(/);
  assert.match(canvas, /laneDirty: laneOwnsLocalGraph/);
  assert.match(canvas, /mergeCanvasPresentationState\(result\.meta\.recoveredGraph, desired\?\.graph \?\? savedGraph\)/);
  assert.doesNotMatch(canvas, /graphSaveScheduler\.dispose\(\)/);
  assert.match(canvas, /canvasGraphNeedsPersistence\([\s\S]*graphPersistedRef\.current,[\s\S]*localGraphMigrationPendingRef\.current/);
  assert.match(canvas, /mergeCanvasPresentationState\(graphPayload\.graph, cached\)/);
  assert.doesNotMatch(canvas, /graphPayload\?\.graph \? JSON\.stringify\(recovered\) : ""/);
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
  assert.match(media, /useSyncExternalStore/);
  assert.match(media, /documentVisibilitySubscribers/);
  assert.match(media, /readyResourceUrls/);
  assert.match(canvas, /const DRAWER_COLUMN_COUNT = 3/);
  assert.match(canvas, /const DRAWER_ROW_HEIGHT = 116/);
  assert.match(canvas, /const DRAWER_OVERSCAN_ROWS = 2/);
  assert.match(canvas, /getCanvasAssetWindow\(visibleWorkspaceResources\.length, assetViewport\.scrollTop, assetViewport\.height\)/);
  assert.match(canvas, /visibleWorkspaceResources\.slice\(workspaceAssetWindow\.startIndex, workspaceAssetWindow\.endIndex\)/);
  assert.match(canvas, /data-canvas-asset-window-start=\{workspaceAssetWindow\.startIndex\}/);
  assert.match(canvas, /onScroll=\{handleDrawerScroll\}/);
  assert.doesNotMatch(canvas, /visibleAssetLimit|setVisibleAssetLimit|slice\(0, visibleAssetLimit\)/);
  assert.match(canvas, /const board = boardRef\.current;[\s\S]*new ResizeObserver\(update\);[\s\S]*\}, \[visible\]\);/);
  assert.match(canvas, /if \(mountedRef\.current && sessionIdRef\.current !== sessionId\) \{[\s\S]*persistLocal\(\)/);

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
  const psdDefinition = { ...frameDefinition, parameterEditor: "psd_layers" };
  assert.equal(graph.isCanvasActionConfigured({ ...frameAction, parameters: { layerEdits: [{ layerPath: "0" }] } }, psdDefinition), false);
  assert.equal(graph.isCanvasActionConfigured({ ...frameAction, parameters: { edits: [{ layerPath: "0" }] } }, psdDefinition), true);
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
  assert.match(renderer, /new URLSearchParams\(\{ sessionId: String\(document\.subjectRef\.sessionId \|\| ""\) \}\)/);
  assert.match(renderer, /fetch\(`\/api\/artifacts\/\$\{encodeURIComponent\(document\.subjectRef\.artifactId\)\}\?\$\{query\.toString\(\)\}`/);
  assert.match(renderer, /artifactSessionId !== document\.subjectRef\.sessionId/);
  assert.match(renderer, /createWorkspaceFileDocument\(\{/);
  assert.match(renderer, /\["code", "text", "markdown", "html"\]\.includes\(document\.renderer\)/);
  assert.match(renderer, /<WorkspaceFileRenderer document=\{sourceDocument\} onSendLineComment=\{onSendLineComment\}/);
  const openOutput = overview.slice(
    overview.indexOf("const openOutput = useCallback"),
    overview.indexOf("const openSource = useCallback"),
  );
  assert.ok(
    openOutput.indexOf("const artifact = output.rawArtifact") < openOutput.indexOf("if (output.path)"),
    "governed artifact lineage must take precedence over managed-worktree paths",
  );
});
