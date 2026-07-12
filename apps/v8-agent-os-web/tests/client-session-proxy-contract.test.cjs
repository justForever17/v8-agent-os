const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const repoRoot = path.resolve(__dirname, "../../..");

function readText(relativePath) {
  return fs.readFileSync(path.join(repoRoot, relativePath), "utf8");
}

test("Web keeps session detail reads behind the authenticated local proxy", () => {
  const nextConfig = readText("apps/v8-agent-os-web/next.config.ts");
  const client = readText("apps/v8-agent-os-web/src/app/chat/ChatClient.tsx");
  const askUser = readText("apps/v8-agent-os-web/src/components/chat/AskUserModal.tsx");
  const detail = readText("apps/v8-agent-os-web/src/app/api/conversations/[id]/detail/route.ts");
  const turns = readText("apps/v8-agent-os-web/src/app/api/conversations/[id]/turns/route.ts");
  const processes = readText("apps/v8-agent-os-web/src/app/api/sessions/[id]/processes/route.ts");
  const clientAuth = readText("apps/v8-agent-os-admin/src/lib/server/client-request-auth.ts");

  for (const proxy of [detail, turns, processes]) {
    assert.match(proxy, /requireAdminProxyContext/);
    assert.match(proxy, /safeAdminProxyFetch/);
    assert.doesNotMatch(proxy, /export async function POST/);
  }
  assert.match(client, /\/api\/conversations\/\$\{conversationId\}\/detail/);
  assert.match(client, /\/api\/conversations\/\$\{conversationId\}\/turns/);
  assert.match(client, /\/api\/sessions\/\$\{encodeURIComponent\(conversationId\)\}\/processes/);
  assert.match(askUser, /\/api\/artifacts\/\$\{encodeURIComponent\(artifactId\)\}\/content/);
  assert.doesNotMatch(`${client}\n${askUser}`, /\/api\/client\/(?:conversations|sessions|artifacts)/);
  assert.match(nextConfig, /beforeFiles:/);
  assert.match(nextConfig, /fallback:/);
  assert.doesNotMatch(nextConfig, /localApiNamespaces/);
  assert.match(clientAuth, /verifyServiceAuth\(req\)/);
  assert.match(clientAuth, /findUserByIdentifier\(serviceIdentifier\)/);
});

test("Web refreshes visible session activity without replaying the initial loading state", () => {
  const context = readText("apps/v8-agent-os-web/src/context/ConversationContext.tsx");

  assert.match(context, /document\.visibilityState === "visible"/);
  assert.match(context, /window\.setInterval\(refreshWhenVisible, 3500\)/);
  assert.match(context, /refreshInFlightRef/);
  assert.match(context, /showInitialLoading = !hasLoadedRef\.current/);
});

test("stream completion callbacks do not close over a later temporal-dead-zone declaration", () => {
  const client = readText("apps/v8-agent-os-web/src/app/chat/ChatClient.tsx");

  assert.ok(client.indexOf("const loadRuns = useCallback") < client.indexOf("useLangGraphStream({"));
});

test("ask_user responses use the authenticated Admin client surface", () => {
  const route = readText("apps/v8-agent-os-web/src/app/api/ask-user/[id]/respond/route.ts");

  assert.match(route, /\/client\/ask-user\/\$\{encodeURIComponent\(id\)\}\/respond/);
  assert.match(route, /requireAdminProxyContext/);
});
