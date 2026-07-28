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
  const turnIndex = readText("apps/v8-agent-os-web/src/app/api/conversations/[id]/turn-index/route.ts");
  const processes = readText("apps/v8-agent-os-web/src/app/api/sessions/[id]/processes/route.ts");
  const clientAuth = readText("apps/v8-agent-os-admin/src/lib/server/client-request-auth.ts");

  for (const proxy of [detail, turns, turnIndex, processes]) {
    assert.match(proxy, /requireAdminProxyContext/);
    assert.match(proxy, /safeAdminProxyFetch/);
    assert.doesNotMatch(proxy, /export async function POST/);
  }
  assert.match(client, /\/api\/conversations\/\$\{encodeURIComponent\(conversationId\)\}\/detail/);
  assert.match(client, /\/api\/conversations\/\$\{encodeURIComponent\(conversationId\)\}\/turns/);
  assert.match(client, /\/api\/conversations\/\$\{encodeURIComponent\(conversationId\)\}\/turn-index/);
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
  assert.match(context, /new EventSource\("\/api\/realtime\/session-activity\/stream"\)/);
  assert.match(context, /activityStream\.addEventListener\("activity", handleActivity\)/);
  assert.match(context, /const scheduleRefresh = \(delayMs = 80\)/);
  assert.doesNotMatch(context, /window\.setInterval\(refreshWhenVisible/);
  assert.match(context, /refreshInFlightRef/);
  assert.match(context, /showInitialLoading = !hasLoadedRef\.current/);
});

test("Web waits for the trusted local session before hydrating conversation history and realtime", () => {
  const client = readText("apps/v8-agent-os-web/src/app/chat/ChatClient.tsx");

  assert.match(
    client,
    /\/\/ Fetch history when ID changes\s+useEffect\(\(\) => \{\s+if \(status !== "authenticated"\) \{\s+return;/,
  );
  assert.match(
    client,
    /useEffect\(\(\) => \{\s+if \(status !== "authenticated" \|\| !activeConversationId\) \{\s+return;\s+\}\s+\s*const eventSource = new EventSource/,
  );
  assert.match(
    client,
    /\[activeConversationId, clearApprovalState, isLoading, loadConversationHistory, loadRuns, loadSessionScope, status, stop, setMessages\]/,
  );
  assert.match(
    client,
    /\[activeConversationId, applyProjectedSnapshot, applyQueuedMessagesSnapshot, applyRemoteRuntimeEvent, applySessionProcessSurface, isLocalStreamActive, loadConversationHistory, loadRuns, status\]/,
  );
  assert.match(
    client,
    /if \(status !== "authenticated" \|\| !activeConversationId\) \{\s+applySessionProcessSurface\(\[\], \{ forceClear: true \}\);/,
  );
});

test("local HTTP preview cookies follow the configured public protocol instead of production mode", () => {
  const auth = readText("apps/v8-agent-os-web/src/lib/auth.ts");
  const connection = readText("apps/v8-agent-os-web/src/app/api/connection/route.ts");
  const policy = readText("apps/v8-agent-os-web/src/lib/server/cookie-policy.ts");

  assert.match(policy, /AUTH_URL \|\| process\.env\.NEXTAUTH_URL/);
  assert.match(policy, /startsWith\("https:\/\/"\)/);
  assert.match(auth, /secure: shouldUseSecureCookies\(\)/);
  assert.match(connection, /secure: shouldUseSecureCookies\(\)/);
  assert.doesNotMatch(`${auth}\n${connection}`, /secure: process\.env\.NODE_ENV === ["']production["']/);
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

test("Web durable chat submission stays behind the authenticated Admin proxy", () => {
  const route = readText("apps/v8-agent-os-web/src/app/api/chat-submit/route.ts");

  assert.match(route, /requireAdminProxyContext/);
  assert.match(route, /safeAdminProxyFetch/);
  assert.match(route, /"\/client\/chat-submit"/);
  assert.match(route, /clientMessageId/);
});

test("Web workspace media previews use an authenticated binary proxy", () => {
  const route = readText("apps/v8-agent-os-web/src/app/api/workspace/resource/route.ts");

  assert.match(route, /requireAdminProxyContext/);
  assert.match(route, /safeAdminProxyFetch/);
  assert.match(route, /`\/workspace\/resource\$\{req\.nextUrl\.search\}`/);
  assert.match(route, /req\.headers\.get\("range"\)/);
  assert.match(route, /"Content-Range"/);
  assert.match(route, /"Accept-Ranges"/);
});

test("keyboard submission cannot outrun attachment persistence", () => {
  const input = readText("apps/v8-agent-os-web/src/components/chat/InputArea.tsx");

  assert.match(input, /if \(uploading\) \{\s+showInlineNotice\("info", t\("web\.chat\.attachments\.uploading"\)\);\s+return;/);
  assert.match(input, /onSubmit=\{async \(e\) => \{\s+if \(uploading\) \{\s+e\.preventDefault\(\);/);
  assert.match(input, /disabled=\{uploading \|\| showRunBusy \|\| \(!runActive && !canSubmit\)\}/);
});
