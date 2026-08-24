const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

const repoRoot = path.resolve(__dirname, "../../..");

function readText(relativePath) {
  return fs.readFileSync(path.join(repoRoot, relativePath), "utf8");
}

function readJson(relativePath) {
  return JSON.parse(readText(relativePath));
}

function loadTypeScriptModule(relativePath) {
  const modulePath = path.join(repoRoot, relativePath);
  const output = ts.transpileModule(readText(relativePath), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    fileName: modulePath,
  }).outputText;
  const moduleRecord = { exports: {} };
  new Function("require", "module", "exports", output)(require, moduleRecord, moduleRecord.exports);
  return moduleRecord.exports;
}

const { preserveLiveSessionTitles } = loadTypeScriptModule("apps/v8-agent-os-web/src/lib/session-history.ts");

test("a delayed placeholder refresh cannot overwrite an already-derived live title", () => {
  const current = [{ id: "session-a", sessionId: "session-a", title: "Read the VERSION file" }];
  const placeholderRefresh = [{ id: "session-a", sessionId: "session-a", title: "New Chat" }];
  const canonicalRefresh = [{ id: "session-a", sessionId: "session-a", title: "Canonical title" }];

  assert.equal(preserveLiveSessionTitles(current, placeholderRefresh)[0].title, "Read the VERSION file");
  assert.equal(preserveLiveSessionTitles(current, canonicalRefresh)[0].title, "Canonical title");
});

test("web history items expose a lightweight V8OS session ID context menu", () => {
  const source = readText("apps/v8-agent-os-web/src/components/layout/Sidebar.tsx");

  assert.match(source, /onContextMenu=\{\(event\) => openConversationMenu\(event, canonicalSessionId\)\}/);
  assert.match(source, /navigator\.clipboard\?\.writeText\(sessionId\)/);
  assert.match(source, /setDeleteId\(contextMenu\.sessionId\)/);
  assert.match(source, /continueInNewConversation\(contextMenu\.sessionId\)/);
  assert.match(source, /contextSessionId=\$\{encodeURIComponent\(sessionId\)\}/);
  assert.match(source, /web\.sidebar\.copySessionId/);
  assert.match(source, /web\.sidebar\.deleteConversation/);
  assert.match(source, /web\.sidebar\.continueInNewSession/);
  assert.match(source, /web\.sidebar\.renameTask/);
  assert.match(source, /web\.sidebar\.pinTask/);
  assert.match(source, /web\.sidebar\.renameProject/);
  assert.match(source, /web\.sidebar\.pinProject/);
  assert.match(source, /editingSessionId === canonicalSessionId/);
  assert.match(source, /editingGroupKey === group\.key/);
  assert.match(source, /\/api\/workspace-presentations/);
  assert.match(source, /group-hover\/header:visible/);
  assert.match(source, /group-hover\/task:pointer-events-auto/);
  assert.match(source, /invisible relative z-10 ml-auto flex shrink-0/);
  assert.match(source, /hover:bg-transparent hover:text-foreground hover:opacity-100 hover:brightness-125/);
  assert.match(source, /<Pin className=\{cn\("h-4 w-4 -rotate-45"/);
  assert.doesNotMatch(source, /text-muted-foreground hover:bg-muted hover:text-foreground/);
  assert.doesNotMatch(source, /rounded-md text-muted-foreground hover:bg-background\/80/);
  assert.doesNotMatch(source, /codex:\/\/threads/);
});

test("phone history entries expose long-press V8OS session ID actions", () => {
  const historyDrawer = readText("apps/v8-agent-os-phone/src/components/layout/HistoryDrawer.tsx");
  const sessionsScreen = readText("apps/v8-agent-os-phone/src/screens/SessionsScreen.tsx");
  const combined = `${historyDrawer}\n${sessionsScreen}`;

  assert.match(combined, /import \* as Clipboard from "expo-clipboard"/);
  assert.match(combined, /onLongPress=\{\(\) => openConversationActions\(item\)\}/);
  assert.match(combined, /Clipboard\.setStringAsync\(canonicalSessionId\)/);
  assert.match(combined, /shared\.conversation\.copy_session_id/);
  assert.match(combined, /shared\.conversation\.continue_in_new_session/);
  assert.match(combined, /suppressNextPressRef/);
  assert.match(historyDrawer, /shared\.conversation\.rename_task/);
  assert.match(historyDrawer, /shared\.conversation\.pin_task/);
  assert.match(historyDrawer, /shared\.workspace\.rename_project/);
  assert.match(historyDrawer, /shared\.workspace\.pin_project/);
  assert.match(historyDrawer, /openWorkspaceActions\(group\)/);
  assert.match(historyDrawer, /onCreateConversationInGroup\(group\)/);
  assert.doesNotMatch(historyDrawer, /style=\{styles\.groupCreateButton\}/);
  assert.doesNotMatch(historyDrawer, /style=\{styles\.deleteButton\}/);
  assert.match(historyDrawer, /<TextInput/);
  assert.doesNotMatch(combined, /codex:\/\/threads/);
});

test("session history action labels are localized", () => {
  const webZh = readJson("apps/v8-agent-os-web/src/i18n/locales/zh-CN.json");
  const webEn = readJson("apps/v8-agent-os-web/src/i18n/locales/en.json");
  const phoneZh = readJson("apps/v8-agent-os-phone/src/i18n/locales/zh-CN.json");
  const phoneEn = readJson("apps/v8-agent-os-phone/src/i18n/locales/en.json");

  assert.equal(webZh["web.sidebar.copySessionId"], "复制会话 ID");
  assert.equal(webZh["web.sidebar.copiedSessionId"], "已复制会话 ID");
  assert.equal(webZh["web.sidebar.deleteConversation"], "删除任务");
  assert.equal(webZh["web.sidebar.continueInNewSession"], "在新任务中继续");
  assert.equal(webEn["web.sidebar.copySessionId"], "Copy session ID");

  assert.equal(phoneZh["shared.conversation.copy_session_id"], "复制会话 ID");
  assert.equal(phoneZh["shared.conversation.session_id_copied"], "已复制会话 ID");
  assert.equal(phoneZh["shared.conversation.history_actions"], "会话操作");
  assert.equal(phoneZh["shared.conversation.continue_in_new_session"], "在新会话中继续");
  assert.equal(phoneEn["shared.conversation.copy_session_id"], "Copy session ID");
  assert.equal(webZh["web.sidebar.renameProject"], "重命名项目");
  assert.equal(webZh["web.sidebar.pinTask"], "置顶任务");
  assert.equal(phoneZh["shared.workspace.rename_project"], "重命名项目");
  assert.equal(phoneZh["shared.conversation.pin_task"], "置顶任务");
});

test("workspace and task presentation changes use durable shared routes", () => {
  const engineRoutes = readText("apps/v8-agent-os-engine/api/session_workflow_routes.py");
  const knowledgeRoutes = readText("apps/v8-agent-os-engine/api/knowledge_routes.py");
  const adminConversationRoute = readText("apps/v8-agent-os-admin/src/app/api/conversations/[id]/route.ts");
  const historyContract = readText("packages/session-realtime/src/history.ts");
  const pet = readText("apps/v8-agent-os-desktop-pet/src/components/CyberPet.tsx");

  assert.match(engineRoutes, /@router\.patch\("\/sessions\/\{session_id\}"\)/);
  assert.match(knowledgeRoutes, /@router\.put\("\/workspace-presentations"\)/);
  assert.match(adminConversationRoute, /export async function PATCH/);
  assert.match(historyContract, /workspaceDisplayName/);
  assert.match(historyContract, /workspacePinned/);
  assert.match(historyContract, /pinnedAt/);
  assert.match(pet, /workspacePinned/);
  assert.match(pet, /Boolean\(left\.pinned\)/);
  assert.match(pet, /Boolean\(right\.pinned\)/);
});

test("web and phone preserve contextSessionRefs through the first submitted user message", () => {
  const webClient = readText("apps/v8-agent-os-web/src/app/chat/ChatClient.tsx");
  const webInput = readText("apps/v8-agent-os-web/src/components/chat/InputArea.tsx");
  const webStream = readText("apps/v8-agent-os-web/src/hooks/use-langgraph-stream.ts");
  const phoneChat = readText("apps/v8-agent-os-phone/src/screens/ChatScreen.tsx");
  const phoneApi = readText("apps/v8-agent-os-phone/src/lib/phone-api.ts");
  const phoneComposer = readText("apps/v8-agent-os-phone/src/components/chat/Composer.tsx");

  assert.match(webClient, /pendingContextSessionRefs/);
  assert.match(webClient, /contextTakeoverConversationIdRef/);
  assert.match(webClient, /pendingContextSessionRefs\.length > 0\s*\? newConversation\.id/);
  assert.match(webClient, /urlId !== contextTakeoverConversationIdRef\.current/);
  assert.match(webClient, /clearPendingContextSessionRefs\(\)/);
  assert.match(webInput, /nextData\.contextSessionRefs = contextSessionRefs/);
  assert.match(webStream, /optimisticMetadata\.contextSessionRefs/);

  assert.match(phoneChat, /metadata\.contextSessionRefs/);
  assert.match(phoneChat, /contextSessionRefs: pendingSessionRefs/);
  assert.match(phoneApi, /contextSessionRefs: Array\.isArray\(options\.contextSessionRefs\)/);
  assert.match(phoneComposer, /contextSessionRefs\.map/);
});

test("human session surfaces compact oversized live-session payloads before Web or Phone parsing", () => {
  const engineRoutes = readText("apps/v8-agent-os-engine/api/session_workflow_routes.py");
  const adminResource = readText("apps/v8-agent-os-admin/src/lib/server/session-realtime-resource.ts");
  const adminDetail = readText("apps/v8-agent-os-admin/src/app/api/client/conversations/[id]/route.ts");
  const adminSync = readText("apps/v8-agent-os-admin/src/app/api/client/conversations/[id]/sync/route.ts");
  const adminTurns = readText("apps/v8-agent-os-admin/src/app/api/client/conversations/[id]/turns/route.ts");
  const phoneApi = readText("apps/v8-agent-os-phone/src/lib/phone-api.ts");
  const phoneDb = readText("apps/v8-agent-os-phone/src/services/LocalDatabaseService.ts");
  const webClient = readText("apps/v8-agent-os-web/src/app/chat/ChatClient.tsx");

  assert.match(engineRoutes, /select_runtime_timeline_window\(runtime_timeline, recent_limit=160, milestone_limit=32\)/);
  assert.match(engineRoutes, /"runtimeTimeline": compact_runtime_timeline/);
  assert.match(engineRoutes, /"sourceCount": runtime_timeline_count/);
  assert.match(adminResource, /compactSurfaceValue/);
  assert.match(adminResource, /visibleResult \?\? record\.result/);
  assert.match(adminDetail, /\?compact=1/);
  assert.match(adminDetail, /compactSurface: omitMessages/);
  assert.match(adminSync, /normalizeMessageForRealtimeSurface\(message, \{ publicBaseUrl, compactSurface \}\)/);
  assert.match(adminTurns, /normalizeMessageForRealtimeSurface\(message, \{ publicBaseUrl, compactSurface \}\)/);
  assert.match(phoneApi, /surface=phone&compact=1/);
  assert.match(webClient, /params\.set\("surface", "web"\)/);
  assert.match(webClient, /params\.set\("compact", "1"\)/);
  assert.match(phoneDb, /compact_message_surface_v1/);
  assert.match(phoneDb, /LENGTH\(raw_json\) > \?/);
});
