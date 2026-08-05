const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const phoneRoot = path.resolve(__dirname, "..");

function read(relativePath) {
  return fs.readFileSync(path.join(phoneRoot, relativePath), "utf8");
}

test("Phone user bubbles mask canonical Canvas messages behind one Human Surface sentence", () => {
  const bubble = read("src/components/chat/MessageBubble.tsx");
  const zh = JSON.parse(read("src/i18n/locales/zh-CN.json"));
  const en = JSON.parse(read("src/i18n/locales/en.json"));
  const assistantSurfaceStart = bubble.indexOf("<View style={styles.assistantRow}>");
  const userSurface = bubble.slice(
    bubble.indexOf("const userAttachments = useMemo"),
    assistantSurfaceStart,
  );

  assert.ok(assistantSurfaceStart > 0, "assistant surface should remain after the user surface");
  assert.equal(zh["src.components.chat.messagebubble.canvasMessage"], "本消息来自画布");
  assert.equal(en["src.components.chat.messagebubble.canvasMessage"], "This message was sent from the canvas");
  assert.match(bubble, /t\("src\.components\.chat\.messagebubble\.canvasMessage"\)/);
  assert.match(bubble, /"plugin", "canvas_resource"/);
  assert.match(bubble, /projectCreativeCanvasHumanSurfaceMessage\(message, canvasUserMessageText\)/);
  assert.doesNotMatch(bubble, /function hasCanvasUserMessageMetadata/);
  assert.doesNotMatch(bubble, /\[CANVAS EXECUTION CONTRACT v1\]/);
  assert.doesNotMatch(bubble, /\[CANVAS OPERATION\]/);
  assert.match(userSurface, /isCanvasUserMessage \? \[\] : extractUserAttachments/);
  assert.match(userSurface, /canvasHumanSurface\?\.copyText \|\| composerPresentation\?\.text \|\| userContentText/);
  assert.match(userSurface, /\{isCanvasUserMessage \? \([\s\S]*?\{canvasHumanSurface\?\.text\}<\/Text>[\s\S]*?\) : composerPresentation \? \(/);
  assert.match(userSurface, /!isCanvasUserMessage && contextSessionRefs\.map/);
  assert.match(userSurface, /!isCanvasUserMessage && composerSpecMode/);
});

test("Phone Canvas graph status consumes only the canonical typed projection", () => {
  const runtimeStage = read("src/lib/runtime-stage.ts");
  const bubble = read("src/components/chat/MessageBubble.tsx");
  const overview = read("src/components/chat/SessionOverviewPanel.tsx");
  const projector = runtimeStage.slice(
    runtimeStage.indexOf("export function projectLatestPhoneCanvasGraphRunState"),
    runtimeStage.indexOf("function phoneRuntimeTimelineEntrySemanticallyEqual"),
  );

  assert.match(projector, /normalizeCreativeCanvasGraphRunStateEvent\(event, scope\)/);
  assert.match(projector, /if \(!scope\.sessionId \|\| !scope\.workspaceId\) return null/);
  assert.match(projector, /projectCreativeCanvasGraphRunHumanSurface\(normalized\)/);
  assert.doesNotMatch(projector, /summary/i);
  assert.match(bubble, /projectLatestPhoneCanvasGraphRunState\(runtimeActivities/);
  assert.match(overview, /projectLatestPhoneCanvasGraphRunState\(runtimeActivities/);
  assert.match(bubble, /if \(!scopedSessionId \|\| !scopedWorkspaceId\) return null/);
  assert.match(overview, /if \(!scopedSessionId \|\| !scopedWorkspaceId\) return null/);
  assert.doesNotMatch(bubble, /workspaceId: String\(workspaceId \|\| ""\)\.trim\(\) \|\| undefined/);
  assert.doesNotMatch(overview, /workspaceId: String\(workspaceId \|\| ""\)\.trim\(\) \|\| undefined/);
});
