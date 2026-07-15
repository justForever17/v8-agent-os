const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const repoRoot = path.resolve(__dirname, "../../..");

function readText(relativePath) {
  return fs.readFileSync(path.join(repoRoot, relativePath), "utf8");
}

function readBinary(relativePath) {
  return fs.readFileSync(path.join(repoRoot, relativePath));
}

function readPngSize(buffer) {
  assert.equal(buffer.subarray(1, 4).toString("ascii"), "PNG");
  return [buffer.readUInt32BE(16), buffer.readUInt32BE(20)];
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

test("Web and Phone lock the V4 atlas and bridge turns without scale-through-zero animation", () => {
  const webScene = readText("apps/v8-agent-os-web/src/components/chat/collaboration/CollaborationMicroStageScene.tsx");
  const phoneScene = readText("apps/v8-agent-os-phone/src/components/chat/collaboration/CollaborationMicroStageScene.tsx");
  const webAtlas = readBinary("apps/v8-agent-os-web/public/supervisor_spritesheet.png");
  const phoneAtlas = readBinary("apps/v8-agent-os-phone/assets/images/supervisor_spritesheet.png");
  const expectedHash = "4e20de37e94d81feb4609cadf8f430ae8548fbf4f82f892331217af00ff4d407";

  assert.deepEqual(readPngSize(webAtlas), [1792, 1280]);
  assert.deepEqual(readPngSize(phoneAtlas), [1792, 1280]);
  assert.equal(crypto.createHash("sha256").update(webAtlas).digest("hex"), expectedHash);
  assert.equal(crypto.createHash("sha256").update(phoneAtlas).digest("hex"), expectedHash);

  for (const scene of [webScene, phoneScene]) {
    assert.match(scene, /frameWidth: 128/);
    assert.match(scene, /left: \[28\]/);
    assert.match(scene, /right: \[29\]/);
    assert.match(scene, /type SupervisorDisplayAction = SupervisorAction \| "turn"/);
    assert.match(scene, /function useSupervisorDisplayState/);
    assert.match(scene, /const needsTurnBridge = previous\.facingLeft !== facingLeft \|\| crossesWalkBoundary/);
    assert.match(scene, /function settleIncompleteStage/);
    assert.match(scene, /function preserveMonotonicFinalStageState/);
    assert.match(scene, /const renderStage = preserveMonotonicFinalStageState\(stage, previous\)/);
    assert.match(scene, /const unfinishedStages = visibleStages\.filter\(\(stage\) => !isFinalStatus\(stage\.status\)\)/);
    assert.match(scene, /if \(unfinishedStages\.length > 0\) return "working"/);
    assert.match(scene, /if \(visibleStages\.length === 0 && stages\.length > 0\)/);
    assert.match(scene, /const shouldSettleIncomplete = settlementLocked\.current/);
    assert.doesNotMatch(scene, /settlementLocked\.current = false/);
    assert.match(scene, /settledOutcome \|\| stages\.some/);
  }

  assert.doesNotMatch(webScene, /transition-transform duration-300/);
  assert.doesNotMatch(phoneScene, /scaleX: mirrored\.value/);
  assert.match(phoneScene, /setTimeout\(\(\) => onOpenOverview\(\), 220\)/);
  assert.match(readText("apps/v8-agent-os-web/src/components/chat/ChatMessage.tsx"), /executionActive=\{executionActive\}/);
  assert.match(readText("apps/v8-agent-os-web/src/components/chat/ChatMessage.tsx"), /key=\{microStageSceneKey\}/);
  assert.match(readText("apps/v8-agent-os-web/src/components/chat/ChatWindow.tsx"), /`assistant:\$\{m\.runId\}`/);
  assert.match(readText("apps/v8-agent-os-phone/src/components/chat/MessageBubble.tsx"), /executionActive=\{executionActive\}/);
  assert.match(readText("apps/v8-agent-os-phone/src/components/chat/MessageBubble.tsx"), /key=\{microStageSceneKey\}/);
  assert.match(readText("apps/v8-agent-os-web/src/app/chat/ChatClient.tsx"), /sessionRunning=\{activeConversationRunning\}/);
  assert.match(readText("apps/v8-agent-os-phone/src/screens/ChatScreen.tsx"), /sessionRunning=\{isSessionRunning\}/);
});
