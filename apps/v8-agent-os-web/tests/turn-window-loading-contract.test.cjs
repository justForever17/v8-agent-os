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
  const state = readText("apps/v8-agent-os-web/src/lib/chat-stream-state.ts");
  const window = readText("apps/v8-agent-os-web/src/components/chat/ChatWindow.tsx");
  const route = readText("apps/v8-agent-os-web/src/app/api/conversations/[id]/turn-index/route.ts");

  assert.match(client, /new URLSearchParams\(\{ limit: "1" \}\)/);
  assert.match(client, /const indexPagePromise = loadConversationTurnIndexPage/);
  assert.ok(client.indexOf("setMessages(normalized)") < client.indexOf("const indexPage = await indexPagePromise"));
  assert.match(client, /around: target\.turnId/);
  assert.match(client, /messagesRef\.current\.some\(\(message\) => message\.turnId === target\.turnId\)/);
  assert.match(client, /\.\.\.messagesRef\.current,\s*\.\.\.turnPage\.messages/);
  assert.match(client, /radius: 1/);
  assert.match(state, /leftOrdinal > 0 && rightOrdinal > 0 && leftOrdinal !== rightOrdinal/);
  assert.match(state, /leftPosition > 0 && rightPosition > 0 && leftPosition !== rightPosition/);
  assert.match(state, /return left\.index - right\.index/);
  assert.doesNotMatch(client, /\]\.map\(\(message, index\) => \(\{ message, index \}\)\)\.sort/);
  assert.match(client, /messageCacheRef/);
  assert.match(client, /useLayoutEffect/);
  assert.match(client, /onReachTop=\{loadOlderConversationTurn\}/);
  assert.match(client, /mergeTurnIndexEntries\(incoming\.flatMap<ChatTurnIndexEntry>/);
  assert.match(window, /<TurnNavigator/);
  assert.match(route, /requireAdminProxyContext/);
});

test("Web keeps historical messages mounted without replaying entrance motion", () => {
  const message = readText("apps/v8-agent-os-web/src/components/chat/ChatMessage.tsx");
  const window = readText("apps/v8-agent-os-web/src/components/chat/ChatWindow.tsx");

  assert.match(message, /animateEntrance\?: boolean/);
  assert.match(message, /initial=\{animateEntrance \? \{ opacity: 0/);
  assert.match(window, /animateEntrance=\{Boolean\(isLoading && index >= messages\.length - 2\)\}/);
});

test("Web keeps queued messages isolated to the active conversation", () => {
  const client = readText("apps/v8-agent-os-web/src/app/chat/ChatClient.tsx");

  assert.match(client, /normalized\.sessionId !== sessionId/);
  assert.match(client, /incoming\.filter\(\(item\) => item\.sessionId === sessionId\)/);
  assert.match(client, /item\.sessionId === activeConversationId && isVisibleQueuedMessage\(item\)/);
  assert.match(client, /applyQueuedMessagesSnapshot\(extractQueuedMessages\(snapshotPayload\), activeConversationId\)/);
});

test("Web turn navigator lives in the outer gutter and expands the hovered tick", () => {
  const navigator = readText("apps/v8-agent-os-web/src/components/chat/TurnNavigator.tsx");

  const client = readText("apps/v8-agent-os-web/src/app/chat/ChatClient.tsx");
  assert.match(navigator, /absolute inset-y-5 left-\[-14px\][^\n]+lg:left-\[-22px\]/);
  assert.match(client, /min-h-0 flex-1 overflow-visible py-1/);
  assert.match(navigator, /data-testid="chat-turn-hover-marker"/);
  assert.match(navigator, /TURN_MARKER_GAP_PX = 9/);
  assert.match(navigator, /safeActivePosition \+ Math\.round\(offsetFromCenter \/ TURN_MARKER_GAP_PX\)/);
  assert.match(navigator, /hoverDistance === 1[\s\S]*lg:w-4/);
  assert.match(navigator, /hoverDistance === 2[\s\S]*lg:w-3/);
  assert.match(navigator, /hoverDistance === 3[\s\S]*lg:w-2\.5/);
  assert.match(navigator, /w-3 opacity-100 lg:w-5/);
  assert.match(navigator, /className=\{`absolute left-0 h-px/);
  assert.match(navigator, /backgroundColor: `hsl\(var\(--foreground\) \/ \$\{markerAlphaForPosition\(position\)\}\)`/);
  assert.match(navigator, /backgroundColor: "hsl\(var\(--foreground\) \/ 0\.86\)"/);
  assert.match(navigator, /formatTurnTimestamp\(hoveredEntry\?\.createdAt\)/);
  assert.doesNotMatch(navigator, /hoveredMarker\.position\} \/ \{total\}/);
  assert.match(client, /createdAt: readString\(record\.createdAt\)/);
  assert.doesNotMatch(navigator, /bg-primary/);
  assert.doesNotMatch(navigator, /rounded-full border-2 border-primary bg-background/);
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
