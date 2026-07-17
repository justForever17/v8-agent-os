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
  assert.match(governance, /SpecDocumentConfirmationDialog/);
  assert.match(documentDialog, /rewrite_stage/);
  assert.match(documentDialog, /同意并继续/);
  assert.match(documentDialog, /需要修改/);
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
  const store = readText("apps/v8-agent-os-web/src/store/workbench-store.ts");
  const proxy = readText("apps/v8-agent-os-web/src/app/api/workbench/[[...segments]]/route.ts");
  const nextConfig = readText("apps/v8-agent-os-web/next.config.ts");
  assert.match(shell, /data-workbench-tab-scroller/);
  assert.match(shell, /startAutoScroll/);
  assert.match(shell, /向左浏览标签/);
  assert.match(shell, /向右浏览标签/);
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
  const projection = readText("packages/session-realtime/src/session-output-projection.ts");
  const specTool = readText("apps/v8-agent-os-engine/core/tools/native/spec.py");
  assert.match(workbench, /title: "概览"/);
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
  assert.match(phoneOverview, /buildSessionOutputProjection\(messages, sessionArtifacts, \{ sessionId/);
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
  assert.match(phoneOverview, /<SourcesSection items=\{sources\}/);
  assert.match(phoneMedia, /useAudioPlayerStatus/);
  assert.match(phoneMedia, /InlineAudioPlayback/);
});
