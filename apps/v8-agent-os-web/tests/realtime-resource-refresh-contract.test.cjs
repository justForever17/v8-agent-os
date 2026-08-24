const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const webRoot = path.resolve(__dirname, "..");

test("web realtime keeps the full detail stream and refreshes durable resources on milestone events", () => {
  const streamRoute = fs.readFileSync(
    path.join(webRoot, "src", "app", "api", "realtime", "sessions", "[id]", "stream", "route.ts"),
    "utf8",
  );
  const workbench = fs.readFileSync(
    path.join(webRoot, "src", "components", "chat", "WorkspaceWorkbenchPanel.tsx"),
    "utf8",
  );
  const chatClient = fs.readFileSync(
    path.join(webRoot, "src", "app", "chat", "ChatClient.tsx"),
    "utf8",
  );
  const runtimeStage = fs.readFileSync(
    path.join(webRoot, "src", "lib", "runtime-stage.ts"),
    "utf8",
  );
  const adminStream = fs.readFileSync(
    path.resolve(webRoot, "..", "v8-agent-os-admin", "src", "app", "api", "realtime", "sessions", "[id]", "stream", "route.ts"),
    "utf8",
  );

  assert.match(streamRoute, /stream\?surface=web`/);
  assert.doesNotMatch(streamRoute, /surface=web&compact=1/);
  assert.match(streamRoute, /"X-Accel-Buffering": "no"/);
  assert.match(workbench, /topic === "artifact\.recorded"/);
  assert.match(workbench, /topic\.startsWith\("handoff\.ref\."\)/);
  assert.match(workbench, /\[resourceRevision, sessionId\]/);
  assert.match(workbench, /refresh\(\);\s*const timer = window\.setInterval\(refresh, 2000\)/);
  assert.match(chatClient, /snapshotLatestSeq < latestRealtimeSeqRef\.current/);
  assert.match(chatClient, /mergeRuntimeTimeline\(\s*normalizeRuntimeTimeline\(current\.runtimeTimeline/);
  assert.match(chatClient, /deriveLiveConversationTitle\(messages\)/);
  assert.match(chatClient, /title: liveConversationTitle/);
  assert.match(chatClient, /updateConversationPresentation\([\s\S]*?\{ title: liveConversationTitle \}[\s\S]*?\{ applyResponse: false \}/);
  assert.ok(
    chatClient.indexOf("title: liveConversationTitle") < chatClient.indexOf("conversationSummaryTimerRef.current = setTimeout"),
    "the first-user-message title must update before the debounced runtime summary",
  );
  assert.match(chatClient, /conversationSummaryTimerRef\.current = setTimeout/);
  assert.match(chatClient, /const processRefreshIntervalMs = activeConversationRunning \? 1800 : 5000/);
  assert.match(chatClient, /setInterval\(\(\) => \{[\s\S]*?loadSessionProcesses\(activeConversationId\)[\s\S]*?processRefreshIntervalMs/);
  assert.match(runtimeStage, /\.slice\(0, 1200\)/);
  assert.match(adminStream, /RUNTIME_EVENT_PAGE_LIMIT = 128/);
  assert.match(adminStream, /events\.length >= RUNTIME_EVENT_PAGE_LIMIT \? 0 : 260/);
});
