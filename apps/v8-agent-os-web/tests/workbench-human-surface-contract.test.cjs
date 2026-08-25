/* eslint-disable @typescript-eslint/no-require-imports */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const repoRoot = path.resolve(__dirname, "../../..");

function readText(relativePath) {
  return fs.readFileSync(path.join(repoRoot, relativePath), "utf8");
}

test("Spec confirmation uses a document review surface instead of generic approval details", () => {
  const governance = readText("apps/v8-agent-os-web/src/components/chat/GovernanceApprovalModal.tsx");
  const documentDialog = readText("apps/v8-agent-os-web/src/components/chat/SpecDocumentConfirmationDialog.tsx");
  const locale = readText("apps/v8-agent-os-web/src/i18n/locales/zh-CN.json");
  assert.match(governance, /SpecDocumentConfirmationDialog/);
  assert.match(documentDialog, /rewrite_stage/);
  assert.match(documentDialog, /web\.specConfirmation\.approve/);
  assert.match(documentDialog, /web\.specConfirmation\.requestRevision/);
  assert.match(locale, /"web\.specConfirmation\.approve": "同意并继续"/);
  assert.match(locale, /"web\.specConfirmation\.requestRevision": "需要修改"/);
  assert.doesNotMatch(documentDialog, />specId</);
  assert.doesNotMatch(documentDialog, />workspace</);
  const dialog = readText("apps/v8-agent-os-web/src/components/ui/dialog.tsx");
  assert.match(dialog, /z-\[110\]/);
  assert.doesNotMatch(dialog, /z-50/);
});

test("ask_user reserves a stable composer surface", () => {
  const askUser = readText("apps/v8-agent-os-web/src/components/chat/AskUserModal.tsx");
  assert.match(askUser, /h-\[clamp\(220px,34dvh,320px\)\]/);
  assert.match(askUser, /min-h-0 flex-1 overflow-y-auto/);
});

test("Workbench uses a compact overflow-safe tab row without embedded Agent Browser", () => {
  const shell = readText("apps/v8-agent-os-web/src/components/workbench/WorkbenchShell.tsx");
  const locale = readText("apps/v8-agent-os-web/src/i18n/locales/zh-CN.json");
  const store = readText("apps/v8-agent-os-web/src/store/workbench-store.ts");
  const proxy = readText("apps/v8-agent-os-web/src/app/api/workbench/[[...segments]]/route.ts");
  const nextConfig = readText("apps/v8-agent-os-web/next.config.ts");
  assert.match(shell, /data-workbench-tab-scroller/);
  assert.match(shell, /startAutoScroll/);
  assert.match(shell, /web\.workbench\.tabs\.previous/);
  assert.match(shell, /web\.workbench\.tabs\.next/);
  assert.match(locale, /"web\.workbench\.tabs\.previous": "向左浏览标签"/);
  assert.match(locale, /"web\.workbench\.tabs\.next": "向右浏览标签"/);
  assert.match(shell, /scrollbarWidth: "none"/);
  assert.doesNotMatch(shell, /browser\/prepare/);
  assert.doesNotMatch(shell, /createBrowser/);
  assert.match(store, /normalizedDocument\.kind === "browser"\) return true/);
  assert.doesNotMatch(proxy, /browser-sessions/);
  assert.doesNotMatch(nextConfig, /workbench-browser-ws/);
  assert.equal(fs.existsSync(path.join(repoRoot, "apps/v8-agent-os-web/src/components/workbench/BrowserRenderer.tsx")), false);
  assert.doesNotMatch(shell, /PanelRight/);
  assert.doesNotMatch(shell, /aria-label="关闭工作台"/);
});

test("runtime activity is a persistent Workbench timeline with bounded micro motion", () => {
  const sharedContract = readText("packages/session-realtime/src/contract.ts");
  const workbench = readText("apps/v8-agent-os-web/src/lib/workbench.ts");
  const overview = readText("apps/v8-agent-os-web/src/components/chat/WorkspaceWorkbenchPanel.tsx");
  const shell = readText("apps/v8-agent-os-web/src/components/workbench/WorkbenchShell.tsx");
  const renderer = readText("apps/v8-agent-os-web/src/components/workbench/RuntimeActivityRenderer.tsx");
  const runtimeStage = readText("apps/v8-agent-os-web/src/lib/runtime-stage.ts");
  const projection = readText("apps/v8-agent-os-engine/core/runtime_projection.py");
  const locale = readText("apps/v8-agent-os-web/src/i18n/locales/zh-CN.json");

  assert.match(sharedContract, /RuntimeActivityWorkbenchDocumentRef/);
  assert.match(workbench, /createRuntimeActivityDocument/);
  assert.match(workbench, /runtime-activity:\$\{input\.sessionId\}:\$\{input\.runtimeId\}/);
  assert.match(overview, /web\.workbench\.section\.runtimeActivity/);
  assert.match(overview, /data-runtime-activity-runtime/);
  assert.match(shell, /<RuntimeActivityRenderer document=\{document\} runtimeModel=\{props\.runtimeModel\}/);
  assert.match(renderer, /runtimeModel\.messageActivities/);
  assert.match(renderer, /left\.eventSeq - right\.eventSeq/);
  assert.match(renderer, /data-runtime-activity-seq/);
  assert.match(renderer, /animate-\[spin_1\.6s_linear_infinite\]/);
  assert.match(renderer, /data-runtime-activity-detail=\{runtimeId\}/);
  assert.match(renderer, /data-runtime-activity-motion=/);
  assert.match(renderer, /latestActivityStatus/);
  assert.match(renderer, /MousePointerClick/);
  assert.match(runtimeStage, /export function selectRuntimeActivityWindow/);
  assert.match(runtimeStage, /perRuntimeLimit = 400/);
  assert.match(runtimeStage, /selectRuntimeActivityWindow\(compacted, 80, 20\)/);
  assert.doesNotMatch(runtimeStage, /\.slice\(0, 1200\)/);
  assert.match(projection, /embedded_runtime_id != runtime_id/);
  assert.match(projection, /runtime-timeline:\{runtime_id\}/);
  assert.match(locale, /"web\.workbench\.runtimeActivity\.timeline": "运行过程时间线"/);
  assert.doesNotMatch(renderer, /providerPayload|workspacePath|rawOutput/);
});

test("Agent Browser remains Engine-managed and parent-bounded outside Workbench", () => {
  const service = readText("apps/v8-agent-os-engine/runtimes/computer_use/browser_session_service.py");
  const proxy = readText("apps/v8-agent-os-engine/scripts/browser_cdp_proxy.mjs");
  assert.match(service, /action == "set_viewport"/);
  assert.match(proxy, /Emulation\.setDeviceMetricsOverride/);
  assert.match(proxy, /V8_ENGINE_PARENT_PID/);
});

test("Spec documents are normal Workbench products and overview uses a human label", () => {
  const workbench = readText("apps/v8-agent-os-web/src/lib/workbench.ts");
  const summary = readText("apps/v8-agent-os-web/src/components/chat/WorkspaceWorkbenchPanel.tsx");
  const locale = readText("apps/v8-agent-os-web/src/i18n/locales/zh-CN.json");
  const projection = readText("packages/session-realtime/src/session-output-projection.ts");
  const specTool = readText("apps/v8-agent-os-engine/core/tools/native/spec.py");
  assert.match(workbench, /title: "web\.workbench\.overview"/);
  assert.match(locale, /"web\.workbench\.overview": "概览"/);
  assert.doesNotMatch(workbench, /title: "会话概览"/);
  assert.match(summary, /buildSessionOutputProjection/);
  assert.match(summary, /\{output\.name\}/);
  assert.match(projection, /linkedSections/);
  assert.match(projection, /specBrief/);
  assert.match(projection, /text\(message\.role\)\.toLowerCase\(\) === "user"/);
  assert.doesNotMatch(summary, /需求文档/);
  assert.doesNotMatch(summary, /设计文档/);
  assert.doesNotMatch(summary, /任务文档/);
  assert.match(summary, /resolveAndOpenWorkspaceFile\(output\.path/);
  assert.match(specTool, /emit_workbench_document_event/);
  assert.match(specTool, /spec-document:\{spec_id\}:\{stage\}/);
});

test("Workbench file reading reuses Markdown rendering, locates search matches, and sends line comments through chat", () => {
  const renderer = readText("apps/v8-agent-os-web/src/components/workbench/WorkspaceFileRenderer.tsx");
  const markdown = readText("apps/v8-agent-os-web/src/components/chat/MarkdownRenderer.tsx");
  const chat = readText("apps/v8-agent-os-web/src/app/chat/ChatClient.tsx");
  assert.match(renderer, /<MarkdownRenderer content=\{content\} searchQuery=\{query\} surface="document"/);
  assert.match(renderer, /data-workbench-search-match/);
  assert.match(renderer, /focusMatch/);
  assert.match(renderer, /WorkspaceFileLineComment/);
  assert.match(renderer, /script-src 'unsafe-inline'/);
  assert.match(renderer, /sandbox="allow-scripts"/);
  assert.doesNotMatch(renderer, /allow-same-origin/);
  assert.match(renderer, /MessageSquarePlus/);
  assert.match(markdown, /data-workbench-search-match/);
  assert.match(markdown, /surface === "document"/);
  assert.match(markdown, /whitespace-pre-wrap/);
  assert.match(markdown, /w-max min-w-full/);
  assert.match(chat, /messageOverride/);
  assert.match(chat, /handleFileLineComment/);
  assert.match(chat, /reference\.path/);
  assert.match(chat, /reference\.line/);
  assert.match(chat, /delete optionData\.messageOverride/);
});

test("Workbench add menu, files, and creative canvas remain session-scoped", () => {
  const shell = readText("apps/v8-agent-os-web/src/components/workbench/WorkbenchShell.tsx");
  const picker = readText("apps/v8-agent-os-web/src/components/workbench/WorkbenchFilePicker.tsx");
  const canvas = readText("apps/v8-agent-os-web/src/components/workbench/CreativeArtifactCanvas.tsx");
  const canvasSaveScheduler = readText("apps/v8-agent-os-web/src/components/workbench/creative-canvas/save-scheduler.ts");
  const actions = readText("apps/v8-agent-os-web/src/lib/workbench-actions.ts");
  const workbench = readText("apps/v8-agent-os-web/src/lib/workbench.ts");
  const store = readText("apps/v8-agent-os-web/src/store/workbench-store.ts");
  const route = readText("apps/v8-agent-os-engine/api/session_workflow_routes.py");
  const proxy = readText("apps/v8-agent-os-web/src/app/api/workbench/[[...segments]]/route.ts");
  const chat = readText("apps/v8-agent-os-web/src/app/chat/ChatClient.tsx");
  assert.ok(shell.indexOf("{tabs.map") < shell.indexOf("<DropdownMenu>"));
  assert.match(shell, /web\.workbench\.add\.file/);
  assert.match(shell, /web\.workbench\.add\.canvas/);
  assert.match(picker, /listWorkspaceFiles\(sessionId/);
  assert.match(picker, /resolveAndOpenWorkspaceFile\(item\.workspacePath, \{ sessionId/);
  assert.match(actions, /payload\.sessionId !== normalizedSessionId/);
  assert.match(actions, /WORKBENCH_FILE_CACHE_TTL_MS = 15_000/);
  assert.match(actions, /WORKBENCH_FILE_CACHE_LIMIT = 64/);
  assert.match(actions, /export function prefetchWorkspaceFiles/);
  assert.match(actions, /export function invalidateWorkbenchFileCatalog/);
  assert.match(shell, /prefetchWorkspaceFiles\(props\.sessionId\)/);
  assert.match(workbench, /creative-canvas:\$\{sessionId\}/);
  assert.match(store, /isWorkbenchDocumentOwnedBySession/);
  assert.match(canvasSaveScheduler, /v8-web-creative-canvas:v2:\$\{sessionId\}/);
  assert.match(canvasSaveScheduler, /encodeURIComponent\(sessionId\).*canvas\/graph/);
  assert.match(canvas, /\/api\/artifacts/);
  assert.match(canvas, /\/api\/sources/);
  assert.ok(proxy.includes("psd\\/(?:source|artifact|workspace_asset)"));
  assert.ok(proxy.includes("\\/(?:manifest|preview)"));
  assert.match(route, /inline: bool = Query\(False\)/);
  assert.match(route, /content_disposition_type="attachment" if download else "inline"/);
  assert.match(chat, /handleCanvasTask/);
  assert.match(chat, /messageOverride: message/);
  assert.match(chat, /t\("web\.workbench\.canvas\.humanMessage"\)/);
  assert.match(chat, /canvasSupervisorDirect: true/);
  assert.match(chat, /canvas_operation/);
  assert.match(chat, /sessionRunning=\{activeConversationRunning\}/);
  assert.doesNotMatch(canvas, /<aside/);
  assert.doesNotMatch(canvas, /taskPlaceholder/);
  assert.doesNotMatch(canvas, /dangerouslySetInnerHTML/);
});

test("Artifact subtitles keep runtime paths on the Runtime Surface", () => {
  const artifacts = readText("apps/v8-agent-os-web/src/lib/artifacts.ts");
  const projection = readText("packages/session-realtime/src/session-output-projection.ts");
  const overview = readText("apps/v8-agent-os-web/src/components/chat/WorkspaceWorkbenchPanel.tsx");
  assert.match(artifacts, /function resolveHumanArtifactSubtitle/);
  assert.match(artifacts, /storageClass === "runtime_artifact"/);
  assert.match(artifacts, /pathPlane === "runtime"/);
  assert.match(artifacts, /return mimeType \|\| "application\/octet-stream"/);
  assert.doesNotMatch(artifacts, /\|\| resolvedUrl\s*\|\| "暂无路径信息"/);
  assert.match(projection, /const runtimePrivate = storageClass === "runtime_artifact"/);
  assert.match(projection, /const path = runtimePrivate \? null : firstPath\(raw\) \|\| null/);
  assert.match(overview, /function humanSafeOutputPath/);
  assert.match(overview, /\^\[A-Za-z\]:\\\//);
  assert.match(overview, /const safePath = humanSafeOutputPath\(output\.path \|\| ""\)/);
});

test("message and Workbench output lists prioritize media and documents before a five-item disclosure", () => {
  const artifacts = readText("apps/v8-agent-os-web/src/lib/artifacts.ts");
  const message = readText("apps/v8-agent-os-web/src/components/chat/ChatMessage.tsx");
  const overview = readText("apps/v8-agent-os-web/src/components/chat/WorkspaceWorkbenchPanel.tsx");
  const locale = readText("apps/v8-agent-os-web/src/i18n/locales/zh-CN.json");
  assert.match(artifacts, /export function artifactPresentationPriority/);
  assert.match(artifacts, /export function prioritizeArtifactItems/);
  assert.match(artifacts, /export function dedupeArtifactItemsForPresentation/);
  assert.match(message, /dedupeArtifactItemsForPresentation/);
  assert.match(message, /prioritizedArtifacts\.slice\(0, 5\)/);
  assert.match(message, /data-artifact-disclosure="message"/);
  assert.match(overview, /prioritizedOutputs\.slice\(0, 5\)/);
  assert.match(overview, /data-artifact-disclosure="workbench"/);
  assert.match(overview, /data-session-output-row=\{output\.id\}/);
  assert.match(locale, /"web\.artifacts\.showRemaining": "展开其余 \{count\} 个产物"/);
});

test("Collapsed Web task sidebar removes its rail, keeps hidden controls inert, and avoids duplicate workspace controls", () => {
  const sidebar = readText("apps/v8-agent-os-web/src/components/layout/Sidebar.tsx");
  const chat = readText("apps/v8-agent-os-web/src/app/chat/ChatClient.tsx");
  const shell = readText("apps/v8-agent-os-shell/electron/main.cjs");
  const preload = readText("apps/v8-agent-os-shell/electron/preload.cjs");
  assert.match(sidebar, /isCollapsed \? "w-0 overflow-visible" : "w-\[280px\] glass-panel"/);
  assert.match(sidebar, /inert=\{isCollapsed\}/);
  assert.match(sidebar, /isCollapsed \? "pointer-events-none -translate-x-2 opacity-0"/);
  assert.match(sidebar, /openWorkspaceFolder/);
  assert.match(sidebar, /openProjectFolder/);
  assert.doesNotMatch(chat, /FolderTree/);
  assert.doesNotMatch(chat, /isContextExpanded/);
  assert.match(shell, /v8os-shell:open-workspace-folder/);
  assert.match(shell, /shell\.openPath/);
  assert.match(preload, /openWorkspaceFolder/);
});

test("Web and Phone summaries hide engineering counters and raw payload bodies", () => {
  const webSummary = readText("apps/v8-agent-os-web/src/components/chat/WorkspaceWorkbenchPanel.tsx");
  const phoneDock = readText("apps/v8-agent-os-phone/src/components/chat/RuntimeDock.tsx");
  const phoneTimeline = readText("apps/v8-agent-os-phone/src/components/chat/RuntimeTimelinePanel.tsx");
  const phoneOverview = readText("apps/v8-agent-os-phone/src/components/chat/SessionOverviewPanel.tsx");
  const phoneApi = readText("apps/v8-agent-os-phone/src/lib/phone-api.ts");
  const phoneChat = readText("apps/v8-agent-os-phone/src/screens/ChatScreen.tsx");
  const phoneProxy = readText("apps/v8-agent-os-admin/src/app/api/client/sessions/[id]/workbench/files/read/route.ts");
  assert.doesNotMatch(webSummary, /本会话已参与/);
  assert.doesNotMatch(webSummary, /commandPreview/);
  assert.doesNotMatch(phoneDock, /eventCount > 0/);
  assert.doesNotMatch(phoneTimeline, /<ContentDispatcher node=\{activity\.node\}/);
  assert.match(phoneOverview, /fileNameOf/);
  assert.match(phoneOverview, /readSessionWorkbenchFile/);
  assert.match(phoneOverview, /PAGE_LINES = 120/);
  assert.match(phoneOverview, /PanResponder\.create/);
  assert.match(phoneOverview, /buildSessionOutputProjection\(messages, sessionArtifacts, \{[\s\S]*?sessionId,[\s\S]*?workspaceId: scopedWorkspaceId/);
  assert.match(phoneApi, /include_archived: "true"/);
  assert.doesNotMatch(phoneOverview, /runId/);
  assert.match(phoneChat, /const showOverviewRail = Boolean\(activeConversationId\)/);
  assert.match(phoneChat, /<SessionOverviewPanel/);
  assert.match(phoneProxy, /workbench\/files\/read/);
  assert.match(phoneProxy, /resolveClientUserEmail/);
});

test("Subagent details stream the shared Human Surface components without exposing runtime topics", () => {
  const projection = readText("packages/session-realtime/src/subagent-return-projection.ts");
  const webOverview = readText("apps/v8-agent-os-web/src/components/chat/WorkspaceWorkbenchPanel.tsx");
  const webRenderer = readText("apps/v8-agent-os-web/src/components/workbench/SubagentActivityRenderer.tsx");
  const phoneOverview = readText("apps/v8-agent-os-phone/src/components/chat/SessionOverviewPanel.tsx");

  assert.match(projection, /eventSeq: number/);
  assert.match(projection, /ownerMessageId\?: string \| null/);
  assert.match(projection, /runtime:\$\{delegationId\}:\$\{eventSeq\}:\$\{topic\}/);
  assert.doesNotMatch(projection, /Date\.now\(|Math\.random\(/);
  assert.match(webOverview, /createSubagentActivityDocument/);
  assert.match(webRenderer, /<ContentDispatcher/);
  assert.match(webRenderer, /<ImagePreview/);
  assert.match(webRenderer, /<MediaPlayer/);
  assert.match(phoneOverview, /<ContentDispatcher/);
  assert.doesNotMatch(webRenderer, /\[runtime\.episode\./);
  assert.match(webRenderer, /failureDetail/);
  assert.match(webRenderer, /web\.workbench\.subagent\.failureTitle/);
  assert.doesNotMatch(webRenderer, /item\.acceptanceStatus/);
  assert.match(phoneOverview, /failureDetail/);
  assert.match(phoneOverview, /subagent_failure_title/);
});

test("Workbench confirmation status reopens the authoritative interaction and never labels generic attention as confirmation", () => {
  const chatClient = readText("apps/v8-agent-os-web/src/app/chat/ChatClient.tsx");
  const workbench = readText("apps/v8-agent-os-web/src/components/chat/WorkspaceWorkbenchPanel.tsx");
  const recoveryCallback = chatClient.match(/const openPendingConfirmation = useCallback\(\(\) => \{([\s\S]*?)\n    \}, \[openGovernanceApproval\]\);/);

  assert.ok(recoveryCallback, "approval recovery callback should remain explicit");
  assert.match(recoveryCallback[1], /openGovernanceApproval\(\)/);
  assert.doesNotMatch(recoveryCallback[1], /AskUser|setAskUserModalOpen/);
  assert.match(chatClient, /pendingConfirmation=\{effectivePendingApproval\}/);
  assert.match(chatClient, /const effectivePendingApproval = Boolean\(governancePendingApprovalId\)/);
  assert.match(chatClient, /liveGovernanceApprovals/);
  assert.match(chatClient, /resolvedGovernanceApprovalIds/);
  assert.match(chatClient, /slightly older snapshot can arrive after approval\.requested/);
  assert.match(chatClient, /setDismissedGovernanceApprovalId\(governancePendingApprovalId\)/);
  assert.match(chatClient, /removeGovernanceApproval\(governancePendingApprovalId\)/);
  assert.match(chatClient, /normalizedEvent\.topic === "approval\.requested"/);
  assert.match(chatClient, /normalizedEvent\.topic === "approval\.approved" \|\| normalizedEvent\.topic === "approval\.rejected"/);
  assert.match(workbench, /currentRuntime \|\| pendingConfirmation/);
  assert.match(workbench, /pendingConfirmation\s*\? t\("web\.workbench\.runtime\.awaiting"\)/);
  assert.match(workbench, /currentRuntime\?\.status === "attention"\s*\? t\("web\.workbench\.runtime\.needsAttention"\)/);
  assert.match(workbench, /onClick=\{pendingConfirmation \? onOpenPendingConfirmation : undefined\}/);
});

test("User sources stay separate from session artifacts and Phone renders voice playback inline", () => {
  const database = readText("apps/v8-agent-os-engine/core/database.py");
  const artifactStore = readText("apps/v8-agent-os-engine/core/artifact_store.py");
  const vision = readText("apps/v8-agent-os-engine/core/tools/vision_media_analyzer.py");
  const sourceProjection = readText("packages/session-realtime/src/session-source-projection.ts");
  const webOverview = readText("apps/v8-agent-os-web/src/components/chat/WorkspaceWorkbenchPanel.tsx");
  const phoneOverview = readText("apps/v8-agent-os-phone/src/components/chat/SessionOverviewPanel.tsx");
  const phoneMedia = readText("apps/v8-agent-os-phone/src/components/chat/MediaRenderers.tsx");

  assert.match(database, /CREATE TABLE IF NOT EXISTS session_sources/);
  assert.match(database, /COALESCE\(resource_role, 'artifact'\) = 'artifact'/);
  assert.match(database, /COALESCE\(auto_attach_to_message, 1\) = 1/);
  assert.match(artifactStore, /resource_role: str = "artifact"/);
  assert.match(vision, /resource_role="source_derivative"/);
  assert.match(sourceProjection, /text\(message\.role\)\.toLowerCase\(\) !== "user"/);
  assert.match(webOverview, /buildSessionSourceProjection/);
  assert.match(phoneOverview, /<SourcesSection[\s\S]*?items=\{sources\}/);
  assert.match(phoneMedia, /useAudioPlayerStatus/);
  assert.match(phoneMedia, /InlineAudioPlayback/);
});
