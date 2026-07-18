const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const repoRoot = path.resolve(__dirname, "../../..");

function readText(relativePath) {
  return fs.readFileSync(path.join(repoRoot, relativePath), "utf8");
}

test("Web renders the newest canonical turn before the optional navigation index", () => {
  const client = readText("apps/v8-agent-os-web/src/app/chat/ChatClient.tsx");
  const window = readText("apps/v8-agent-os-web/src/components/chat/ChatWindow.tsx");
  const route = readText("apps/v8-agent-os-web/src/app/api/conversations/[id]/turn-index/route.ts");

  assert.match(client, /new URLSearchParams\(\{ limit: "1" \}\)/);
  assert.match(client, /const indexPagePromise = loadConversationTurnIndexPage/);
  assert.ok(client.indexOf("setMessages(normalized)") < client.indexOf("const indexPage = await indexPagePromise"));
  assert.match(client, /around: target\.turnId/);
  assert.match(client, /onReachTop=\{loadOlderConversationTurn\}/);
  assert.match(window, /<TurnNavigator/);
  assert.match(route, /requireAdminProxyContext/);
});

test("Web realtime hydration stays compact so a snapshot cannot replay the full transcript", () => {
  const webRoute = readText("apps/v8-agent-os-web/src/app/api/realtime/sessions/[id]/stream/route.ts");
  const adminRoute = readText("apps/v8-agent-os-admin/src/app/api/realtime/sessions/[id]/stream/route.ts");

  assert.match(webRoute, /surface=web&compact=1/);
  assert.match(adminRoute, /compactPhone \? "\?compact=1" : ""/);
});

test("Phone uses a virtualized cache-first newest-turn surface and never starts with an empty full sync", () => {
  const screen = readText("apps/v8-agent-os-phone/src/screens/ChatScreen.tsx");
  const window = readText("apps/v8-agent-os-phone/src/components/chat/ChatWindow.tsx");
  const database = readText("apps/v8-agent-os-phone/src/services/LocalDatabaseService.ts");

  assert.match(screen, /getLatestTurnMessages\(conversationId\)/);
  assert.match(screen, /getConversationTurnPage\(authorizedFetch, conversationId, \{ limit: 1 \}\)/);
  assert.match(screen, /syncCursor\s*\?\s*getConversationTimelineSync/);
  assert.doesNotMatch(screen, /getConversationTimelineSync\([^\n]+syncCursor \|\| ""/);
  assert.match(window, /<FlatList/);
  assert.match(window, /maintainVisibleContentPosition/);
  assert.match(database, /CREATE TABLE IF NOT EXISTS local_session_indexes/);
  assert.match(database, /deleteSessionData/);
});
