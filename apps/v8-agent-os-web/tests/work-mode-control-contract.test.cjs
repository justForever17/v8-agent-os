const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const repoRoot = path.resolve(__dirname, "../../..");

function readText(relativePath) {
  return fs.readFileSync(path.join(repoRoot, relativePath), "utf8");
}

test("Web and Phone keep a session-scoped Supervisor runtime mode and snapshot it per message", () => {
  const web = readText("apps/v8-agent-os-web/src/app/chat/ChatClient.tsx");
  const webComposer = readText("apps/v8-agent-os-web/src/components/chat/InputArea.tsx");
  const webContext = readText("apps/v8-agent-os-web/src/context/ConversationContext.tsx");
  const phone = readText("apps/v8-agent-os-phone/src/screens/ChatScreen.tsx");
  const phoneComposer = readText("apps/v8-agent-os-phone/src/components/chat/Composer.tsx");
  const phoneApi = readText("apps/v8-agent-os-phone/src/lib/phone-api.ts");
  const localSessionProxy = readText("apps/v8-agent-os-admin/src/app/api/conversations/[id]/route.ts");
  const clientSessionProxy = readText("apps/v8-agent-os-admin/src/app/api/client/conversations/[id]/route.ts");
  const sharedContract = readText("packages/session-realtime/src/contract.ts");
  const zh = JSON.parse(readText("apps/v8-agent-os-web/src/i18n/locales/zh-CN.json"));
  const en = JSON.parse(readText("apps/v8-agent-os-web/src/i18n/locales/en.json"));

  const runtimeModes = ["auto", "engineering", "research", "creative_media", "computer_use", "rpa"];
  for (const mode of runtimeModes) {
    assert.match(webComposer, new RegExp(`mode: "${mode}"`));
    assert.match(phoneComposer, new RegExp(`mode: "${mode}"`));
    assert.match(sharedContract, new RegExp(`"${mode}"`));
  }

  for (const surface of [web, phone]) {
    assert.match(surface, /supervisorRuntimeModeDrafts/);
    assert.match(surface, /supervisorRuntimeModeRef\.current/);
    assert.match(surface, /supervisorRuntimeMode: nextMode/);
    assert.match(surface, /delete next\[sessionId\]/);
  }

  const webModeHandler = web.slice(
    web.indexOf("const handleSupervisorRuntimeModeChange"),
    web.indexOf("const localConversationLoading"),
  );
  assert.match(web, /supervisorRuntimeModeConfirmedRef/);
  assert.match(web, /supervisorRuntimeModePersistenceQueueRef/);
  assert.match(webModeHandler, /supervisorRuntimeModePersistenceQueueRef\.current\[sessionId\] \|\| Promise\.resolve\(\)/);
  assert.match(webModeHandler, /updateConversationPresentation\([\s\S]*?\{ supervisorRuntimeMode: nextMode \},[\s\S]*?\{ applyResponse: false \}/);
  assert.match(webModeHandler, /supervisorRuntimeModePersistenceQueueRef\.current\[sessionId\] = persistence/);
  assert.match(webModeHandler, /const fallbackMode = supervisorRuntimeModeConfirmedRef\.current\[sessionId\] \|\| "auto"/);
  assert.match(webModeHandler, /if \(activeConversationIdRef\.current === sessionId\) \{\s*supervisorRuntimeModeRef\.current = fallbackMode/);
  assert.match(webModeHandler, /setSupervisorRuntimeModeSyncErrors\(\(current\) => \(\{\s*\.\.\.current,\s*\[sessionId\]:/);
  assert.doesNotMatch(webModeHandler, /const previousMode = supervisorRuntimeModeRef\.current/);
  assert.ok(
    webModeHandler.indexOf("supervisorRuntimeModePersistenceQueueRef.current[sessionId] = persistence")
      < webModeHandler.indexOf("const updated = await persistence"),
    "the per-session persistence queue must be registered before awaiting its response",
  );

  assert.match(web, /const supervisorRuntimeModeSnapshot = supervisorRuntimeModeRef\.current/);
  assert.match(web, /supervisorRuntimeMode: supervisorRuntimeModeSnapshot/);
  assert.match(web, /supervisorRuntimeMode: supervisorRuntimeModeRef\.current/);
  assert.match(web, /submitQueuedMessage\(currentInput, submissionData\)/);
  assert.match(web, /sendMessage\(currentInput, \{[\s\S]*?\.\.\.submissionData/);
  assert.match(webComposer, /nextData\.supervisorRuntimeMode = supervisorRuntimeMode/);
  assert.match(webComposer, /supervisorRuntimeMode,\s+safetyApprovalMode/);
  assert.match(phone, /const pendingSupervisorRuntimeMode = supervisorRuntimeModeRef\.current/);
  assert.match(phoneApi, /supervisorRuntimeMode: options\.supervisorRuntimeMode \|\| undefined/);
  assert.match(webContext, /supervisorRuntimeMode\?: SupervisorRuntimeMode/);
  assert.match(webContext, /options\.applyResponse !== false/);
  assert.match(phone, /updateConversationPresentation\(authorizedFetch, sessionId, \{ supervisorRuntimeMode: nextMode \}\)/);

  for (const proxy of [localSessionProxy, clientSessionProxy]) {
    assert.match(proxy, /isSupervisorRuntimeMode\(body\?\.supervisorRuntimeMode\)/);
    assert.match(proxy, /supervisorRuntimeMode: body\.supervisorRuntimeMode/);
  }

  assert.match(webComposer, /<DropdownMenu open=\{supervisorRuntimeModeOpen\}/);
  assert.match(webComposer, /<DropdownMenuRadioGroup[\s\S]*?value=\{supervisorRuntimeMode\}/);
  assert.match(webComposer, /<DropdownMenuRadioItem[\s\S]*?value=\{option\.mode\}/);
  assert.equal(zh["web.chat.runtimeMode.auto.title"], "智能模式");
  assert.equal(zh["web.chat.runtimeMode.engineering.title"], "编程模式");
  assert.equal(zh["web.chat.runtimeMode.research.title"], "调研模式");
  assert.equal(zh["web.chat.runtimeMode.creativeMedia.title"], "媒体创作");
  assert.equal(zh["web.chat.runtimeMode.computerUse.title"], "桌面操作");
  assert.equal(zh["web.chat.runtimeMode.rpa.title"], "RPA模式");
  assert.equal(en["web.chat.runtimeMode.auto.title"], "Smart mode");
  assert.match(web, /web\.chat\.runtimeMode\.syncFailed/);
  assert.match(web, /supervisorRuntimeModeSyncErrors\[activeConversationId\]/);
  assert.match(web, /role="alert"/);

  const canvasHandler = web.slice(web.indexOf("const handleCanvasTask"), web.indexOf("const handleVoiceAudioMessage"));
  const fileCommentHandler = web.slice(web.indexOf("const handleFileLineComment"), web.indexOf("const handleCanvasTask"));
  assert.match(fileCommentHandler, /handleSend\(syntheticEvent/);
  assert.match(canvasHandler, /handleSend\(syntheticEvent/);
  assert.match(canvasHandler, /canvasSupervisorDirect: true/);
  assert.doesNotMatch(web, /supervisorRuntimeMode[^\n]*canvasSupervisorDirect/);
  assert.doesNotMatch(webComposer, /supervisorWorkMode|onSupervisorWorkModeChange/);
});

test("Engineering console exposes useful controls instead of the retired route test", () => {
  const page = readText("apps/v8-agent-os-admin/src/app/admin/(dashboard)/engineering-lane/page.tsx");

  assert.doesNotMatch(page, /\/api\/engineering-lane\/dry-run/);
  assert.doesNotMatch(page, /dryRunDiagnosticTitle|recentRiskTitle|filterRouteTest/);
  assert.match(page, /worksetGovernanceDescription/);
  assert.match(page, /codingExecutionContractEnabled/);
  assert.match(page, /worksetObservationEnabled/);
  assert.ok(page.indexOf("workflowMemoryTitle") < page.indexOf("advancedDiagnosticsTitle"));
});
