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

test("Web and Phone lock the V4 atlas with natural inspect frames and bridge turns without scale-through-zero animation", () => {
  const webScene = readText("apps/v8-agent-os-web/src/components/chat/collaboration/CollaborationMicroStageScene.tsx");
  const phoneScene = readText("apps/v8-agent-os-phone/src/components/chat/collaboration/CollaborationMicroStageScene.tsx");
  const webAtlas = readBinary("apps/v8-agent-os-web/public/supervisor_spritesheet.png");
  const phoneAtlas = readBinary("apps/v8-agent-os-phone/assets/images/supervisor_spritesheet.png");
  const expectedHash = "a9713e456f8c93ddcbf2ee1ede28f82341c2cbfce848b1ac02ea8daf6285a89a";

  assert.deepEqual(readPngSize(webAtlas), [1792, 1536]);
  assert.deepEqual(readPngSize(phoneAtlas), [1792, 1536]);
  assert.equal(crypto.createHash("sha256").update(webAtlas).digest("hex"), expectedHash);
  assert.equal(crypto.createHash("sha256").update(phoneAtlas).digest("hex"), expectedHash);

  for (const scene of [webScene, phoneScene]) {
    assert.match(scene, /frameWidth: 128/);
    assert.match(scene, /left: \[28\]/);
    assert.match(scene, /right: \[29\]/);
    assert.match(scene, /inspect: \[35, 36, 37, 38\]/);
    assert.match(scene, /return "inspect"/);
    assert.match(scene, /displayState\.action === "walk" \|\| displayState\.action === "inspect"/);
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
  assert.doesNotMatch(webScene, /transform: handoff \?/);
  assert.match(webScene, /microStageRobotCurtain/);
  assert.match(phoneScene, /robotVisibility/);
  assert.match(phoneScene, /reportTravel/);
  assert.match(webScene, /const \[element, setElement\] = useState<T \| null>\(null\)/);
  assert.match(webScene, /const ref = useCallback\(\(node: T \| null\) => setElement\(node\), \[\]\)/);
  assert.match(webScene, /observer\.observe\(element\)/);
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

test("Web and Phone use the standing subagent workstation and semantic event screen", () => {
  const webWorkstation = readText("apps/v8-agent-os-web/src/components/chat/collaboration/SubagentWorkstation.tsx");
  const phoneWorkstation = readText("apps/v8-agent-os-phone/src/components/chat/collaboration/SubagentWorkstation.tsx");
  const assets = [
    {
      web: "apps/v8-agent-os-web/public/subagent_workstation.png",
      phone: "apps/v8-agent-os-phone/assets/images/subagent_workstation.png",
      size: [512, 512],
      hash: "35aea5eb2ce19ccb2a67b505648dd75a79c60264f79d9b54c6873e66433b4738",
    },
    {
      web: "apps/v8-agent-os-web/public/subagent_robot_neutral.png",
      phone: "apps/v8-agent-os-phone/assets/images/subagent_robot_neutral.png",
      size: [1792, 1536],
      hash: "bc5f3e070e41af43039730ecbf5684964ee6d97fe2bf1eb2690c04ba50267ace",
    },
    {
      web: "apps/v8-agent-os-web/public/subagent_robot_emissive_mask.png",
      phone: "apps/v8-agent-os-phone/assets/images/subagent_robot_emissive_mask.png",
      size: [1792, 1536],
      hash: "49ac0e4e5e5a5057da7ba498251809c93dd3341ce02a4a90cce9e07b1e26f782",
    },
  ];

  for (const asset of assets) {
    const web = readBinary(asset.web);
    const phone = readBinary(asset.phone);
    assert.deepEqual(readPngSize(web), asset.size);
    assert.deepEqual(readPngSize(phone), asset.size);
    assert.equal(crypto.createHash("sha256").update(web).digest("hex"), asset.hash);
    assert.equal(crypto.createHash("sha256").update(phone).digest("hex"), asset.hash);
  }

  for (const component of [webWorkstation, phoneWorkstation]) {
    for (const pattern of [
      "network",
      "route",
      "research",
      "engineering",
      "creative",
      "desktop",
      "rpa",
      "waiting",
      "completed",
      "degraded",
      "failed",
    ]) {
      assert.match(component, new RegExp(`"${pattern}"`));
    }
    assert.match(component, /subagentRobotActionFor/);
    assert.match(component, /curtain: \[35, 36, 37, 38\]/);
    assert.match(component, /return "curtain"/);
    assert.match(component, /subagent_robot_neutral/);
    assert.match(component, /subagent_robot_emissive_mask/);
    assert.doesNotMatch(component, /Chair \(Integrated SVG\)|Sitting robot peeking head/);
  }

  assert.match(webWorkstation, /prefers-reduced-motion: reduce/);
  assert.match(phoneWorkstation, /withRepeat\(/);
});

test("Web and Phone keep Supervisor, robot, and workstation collision volumes separate", () => {
  const webScene = readText("apps/v8-agent-os-web/src/components/chat/collaboration/CollaborationMicroStageScene.tsx");
  const phoneScene = readText("apps/v8-agent-os-phone/src/components/chat/collaboration/CollaborationMicroStageScene.tsx");

  for (const scene of [webScene, phoneScene]) {
    assert.match(scene, /const SUPERVISOR_COLLISION: CollisionVolume/);
    assert.match(scene, /const WORKSTATION_COLLISION: CollisionVolume/);
    assert.match(scene, /const ROBOT_COLLISION: CollisionVolume/);
    assert.match(scene, /function collisionRectsForItem/);
    assert.match(scene, /function supervisorCollisionAt/);
    assert.match(scene, /collisionOverlapArea/);
    assert.match(scene, /supervisorWaypointForItem\(item, width, items\)/);
    assert.match(scene, /const actorName = actor\.label \|\| step\?\.actorLabel \|\| stage\.title/);
  }

  assert.match(webScene, /data-collision-supervisor/);
  assert.match(webScene, /data-collision-workstation/);
  assert.match(webScene, /data-collision-agent/);
  assert.match(phoneScene, /styles\.robotNameLabel/);
});
