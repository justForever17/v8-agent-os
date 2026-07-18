const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const root = path.resolve(__dirname, "../../..");
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), "utf8");

test("Web and Phone expose /memory as a governed manual action", () => {
  const webInput = read("apps/v8-agent-os-web/src/components/chat/InputArea.tsx");
  const webClient = read("apps/v8-agent-os-web/src/app/chat/ChatClient.tsx");
  const phoneScreen = read("apps/v8-agent-os-phone/src/screens/ChatScreen.tsx");
  const phoneApi = read("apps/v8-agent-os-phone/src/lib/phone-api.ts");

  assert.match(webInput, /name:\s*"memory"/);
  assert.match(webInput, /memoryAction:\s*"session_extraction"/);
  assert.match(webInput, /await onManualMemory\(\)/);
  assert.match(webClient, /fetch\("\/api\/memory\/session-extraction"/);

  assert.match(phoneScreen, /name:\s*"memory"/);
  assert.match(phoneScreen, /pendingCommand\?\.memoryAction === "session_extraction"/);
  assert.match(phoneScreen, /runManualMemoryExtraction\(authorizedFetch, currentConversationId\)/);
  assert.match(phoneApi, /"\/api\/client\/memory\/session-extraction"/);
});

test("Admin mode switch and client proxy preserve the manual extraction contract", () => {
  const panel = read("apps/v8-agent-os-admin/src/components/memory/MemoryConfigPanel.tsx");
  const route = read("apps/v8-agent-os-admin/src/app/api/client/memory/session-extraction/route.ts");

  assert.match(panel, /extraction_mode:\s*checked \? "manual" : "auto"/);
  assert.match(panel, /extraction_enabled:\s*!checked/);
  assert.match(route, /fetchClientEngine\(req, "\/memory\/session-extraction"/);
});
