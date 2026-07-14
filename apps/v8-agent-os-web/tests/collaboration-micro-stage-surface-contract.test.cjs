const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const repoRoot = path.resolve(__dirname, "../../..");

function readText(relativePath) {
  return fs.readFileSync(path.join(repoRoot, relativePath), "utf8");
}

test("Web anchors the collaboration stage in the message timeline and leaves an overview link after settling", () => {
  const message = readText("apps/v8-agent-os-web/src/components/chat/ChatMessage.tsx");
  const scene = readText("apps/v8-agent-os-web/src/components/chat/collaboration/CollaborationMicroStageScene.tsx");

  assert.ok(message.indexOf("{timelineSegments.map") < message.indexOf("<CollaborationMicroStageScene"));
  assert.match(message, /createSessionOverviewDocument\(workbench\.sessionId\)/);
  assert.match(message, /onOpenOverview=\{handleOpenMicroStageOverview\}/);
  assert.match(scene, /initialized && hasFinalOutcome && onOpenOverview/);
  assert.match(scene, /overviewLinkLabel/);
});

test("Phone opens the existing overview drawer from the settled collaboration link", () => {
  const message = readText("apps/v8-agent-os-phone/src/components/chat/MessageBubble.tsx");
  const window = readText("apps/v8-agent-os-phone/src/components/chat/ChatWindow.tsx");
  const screen = readText("apps/v8-agent-os-phone/src/screens/ChatScreen.tsx");
  const panel = readText("apps/v8-agent-os-phone/src/components/chat/SessionOverviewPanel.tsx");

  assert.ok(message.indexOf("timelineSegments.map") < message.indexOf("<CollaborationMicroStageScene"));
  assert.match(message, /onOpenOverview=\{onOpenOverview\}/);
  assert.match(window, /onOpenOverview=\{onOpenOverview\}/);
  assert.match(screen, /onOpenOverview=\{openOverviewPanel\}/);
  assert.match(panel, /SlideInRight\.duration\(220\)/);
});

test("Web and Phone overview surfaces reuse the shared nested subagent return projection", () => {
  const webOverview = readText("apps/v8-agent-os-web/src/components/chat/WorkspaceWorkbenchPanel.tsx");
  const phoneOverview = readText("apps/v8-agent-os-phone/src/components/chat/SessionOverviewPanel.tsx");

  assert.match(webOverview, /buildSubagentReturnProjection\(messages, runtimeModel\.messageActivities\.map/);
  assert.match(webOverview, /item\.children\.map/);
  assert.match(phoneOverview, /buildSubagentReturnProjection\(messages, runtimeActivities\.map/);
  assert.match(readText("apps/v8-agent-os-phone/src/screens/ChatScreen.tsx"), /runtimeActivities=\{projection\.runtimeStageModel\.messageActivities\}/);
  assert.match(phoneOverview, /item\.children\.map/);
});
