const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
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
  const webFarewellAtlas = readBinary("apps/v8-agent-os-web/public/supervisor_farewell_spritesheet.png");
  const phoneFarewellAtlas = readBinary("apps/v8-agent-os-phone/assets/images/supervisor_farewell_spritesheet.png");
  const expectedFarewellHash = "69b71666b04c9aef0fd8f7c3d5ed4ec7bc30d0b76406ac45034b8f441e2be69a";

  assert.deepEqual(readPngSize(webAtlas), [1792, 1536]);
  assert.deepEqual(readPngSize(phoneAtlas), [1792, 1536]);
  assert.equal(crypto.createHash("sha256").update(webAtlas).digest("hex"), expectedHash);
  assert.equal(crypto.createHash("sha256").update(phoneAtlas).digest("hex"), expectedHash);
  assert.deepEqual(readPngSize(webFarewellAtlas), [2048, 768]);
  assert.deepEqual(readPngSize(phoneFarewellAtlas), [2048, 768]);
  assert.equal(crypto.createHash("sha256").update(webFarewellAtlas).digest("hex"), expectedFarewellHash);
  assert.equal(crypto.createHash("sha256").update(phoneFarewellAtlas).digest("hex"), expectedFarewellHash);

  for (const scene of [webScene, phoneScene]) {
    assert.match(scene, /frameWidth: 128/);
    assert.match(scene, /left: \[28\]/);
    assert.match(scene, /right: \[29\]/);
    assert.match(scene, /inspect: \[35, 36, 37, 38\]/);
    assert.match(scene, /celebrate: \[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23\]/);
    assert.match(scene, /const SUPERVISOR_FAREWELL_SHEET/);
    assert.match(scene, /displayState\.action === "celebrate"/);
    assert.match(scene, /return "inspect"/);
    assert.match(scene, /displayState\.action === "walk" \|\| displayState\.action === "inspect"/);
    assert.match(scene, /type SupervisorDisplayAction = SupervisorAction \| "turn"/);
    assert.match(scene, /function useSupervisorDisplayState/);
    assert.match(scene, /function isDirectionalSupervisorAction/);
    assert.match(scene, /const crossesDirectionalBoundary = isDirectionalSupervisorAction\(previous\.action\)/);
    assert.match(scene, /const needsTurnBridge = crossesDirectionalBoundary/);
    assert.match(scene, /inspect: \[560, 900, 1000, 720\]/);
    assert.match(scene, /const SUPERVISOR_TRAVEL_DURATION_MS = 1400/);
    assert.match(scene, /const PATROL_INTERVAL_MS = 5200/);
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
  assert.doesNotMatch(webScene, /microStageBotBob/);
  assert.match(webScene, /data-stage-depth/);
  assert.match(webScene, /data-supervisor-target-center-x/);
  assert.match(webScene, /zIndex: stageDepthZ\(y \+ WORK_CELL_HEIGHT \* scale\)/);
  assert.match(phoneScene, /zIndex: stageDepthZ\(y \+ WORK_CELL_HEIGHT \* scale\)/);
  assert.match(phoneScene, /Math\.min\(index, 6\) \* 70/);
  assert.match(webScene, /microStageRobotCurtain/);
  assert.match(phoneScene, /robotVisibility/);
  assert.match(phoneScene, /reportTravel/);
  assert.match(webScene, /data-subagent-status=\{status\}/);
  assert.match(webScene, /animate-spin rounded-full border-\[1\.5px\]/);
  assert.doesNotMatch(webScene, /<span className="text-muted-foreground">\{statusLabel\(status\)\}<\/span>/);
  assert.match(phoneScene, /styles\.statusRing/);
  assert.doesNotMatch(phoneScene, /styles\.statusText/);
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

test("Web composer follows the authoritative session run state instead of guessing from the local stream", () => {
  const client = readText("apps/v8-agent-os-web/src/app/chat/ChatClient.tsx");
  const input = readText("apps/v8-agent-os-web/src/components/chat/InputArea.tsx");

  assert.match(client, /sessionRunning=\{activeConversationRunning\}/);
  assert.match(client, /canStopRun=\{isLoading\}/);
  assert.match(client, /sessionProjection\?\.runtimeStatus/);
  assert.match(client, /currentRun\?\.status/);
  assert.match(client, /observedStatuses\.some\(\(status\) => activeStatuses\.includes\(status\)\)/);
  assert.ok((client.match(/if \(activeConversationRunning\)/g) || []).length >= 2);
  assert.match(input, /const runActive = sessionRunning \|\| isLoading/);
  assert.match(input, /const canQueueWhileRunning = runActive && canSubmit/);
  assert.match(input, /const canStopActiveRun = runActive && !canQueueWhileRunning/);
  assert.match(input, /const showRunBusy = runActive && !canQueueWhileRunning && !canStopActiveRun/);
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
    assert.match(component, /work: \[360, 320, 360, 760\]/);
    assert.doesNotMatch(
      component.match(/const LOOPING_ROBOT_ACTIONS[\s\S]*?\]\);/)?.[0] || "",
      /"failure"/,
    );
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
    assert.match(scene, /targetCenterX/);
    assert.match(scene, /const actorName = actor\.label \|\| step\?\.actorLabel \|\| stage\.title/);
  }

  assert.match(webScene, /data-collision-supervisor/);
  assert.match(webScene, /data-collision-workstation/);
  assert.match(webScene, /data-collision-agent/);
  assert.match(phoneScene, /styles\.robotNameLabel/);
  assert.match(phoneScene, /setSupervisorFacingLeft\(restingFacingLeft\)/);
});

test("Web topbar locale menu stays above the multifunction workbench", () => {
  const localeToggle = readText("apps/v8-agent-os-web/src/components/layout/LocaleToggle.tsx");
  const workbench = readText("apps/v8-agent-os-web/src/components/workbench/WorkbenchShell.tsx");

  assert.match(localeToggle, /DropdownMenuContent align="end" className="z-\[100\] w-44"/);
  assert.match(workbench, /"z-\[70\] flex min-h-0 flex-col/);
  assert.match(workbench, /className="relative z-\[71\]/);
});

test("Local clients consume the packed message-bound execution exports with matching lock integrity", async () => {
  const sharedPackage = JSON.parse(readText("packages/session-realtime/package.json"));
  const archiveName = `v8-session-realtime-${sharedPackage.version}.tgz`;
  const tgzRelativePath = `packages/session-realtime/${archiveName}`;
  const tgzPath = path.join(repoRoot, tgzRelativePath);
  const tgz = readBinary(tgzRelativePath);
  const expectedIntegrity = `sha512-${crypto.createHash("sha512").update(tgz).digest("base64")}`;
  for (const lockPath of [
    "apps/v8-agent-os-web/package-lock.json",
    "apps/v8-agent-os-phone/package-lock.json",
    "apps/v8-agent-os-admin/package-lock.json",
  ]) {
    const lock = JSON.parse(readText(lockPath));
    const lockEntry = lock.packages?.["node_modules/@v8/session-realtime"];
    assert.equal(lockEntry?.resolved, `file:../../packages/session-realtime/${archiveName}`);
    assert.equal(lockEntry?.integrity, expectedIntegrity, lockPath);
  }

  const packedIndex = execFileSync("tar", ["-xOf", tgzPath, "package/dist/index.js"], { encoding: "utf8" });
  const packedImplementation = execFileSync(
    "tar",
    ["-xOf", tgzPath, "package/dist/message-bound-execution-node.js"],
    { encoding: "utf8" },
  );
  assert.match(packedIndex, /export \* from "\.\/message-bound-execution-node\.js"/);
  assert.match(packedImplementation, /export function getMessageBoundExecutionTimelineNodeIdentityCandidates/);
  assert.match(packedImplementation, /export function buildMessageBoundCollaborationMicroStagePlacement/);

  const installed = await import("@v8/session-realtime/message-bound-execution-node");
  assert.equal(typeof installed.getMessageBoundExecutionTimelineNodeIdentityCandidates, "function");
  assert.equal(typeof installed.buildMessageBoundCollaborationMicroStagePlacement, "function");
});
