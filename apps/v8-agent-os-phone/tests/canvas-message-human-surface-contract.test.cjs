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
  const canvasMetadataGuard = bubble.slice(
    bubble.indexOf("function hasCanvasUserMessageMetadata"),
    bubble.indexOf("type UserAttachmentItem"),
  );
  const assistantSurfaceStart = bubble.indexOf("<View style={styles.assistantRow}>");
  const userSurface = bubble.slice(
    bubble.indexOf("const userAttachments = useMemo"),
    assistantSurfaceStart,
  );

  assert.ok(assistantSurfaceStart > 0, "assistant surface should remain after the user surface");
  assert.equal(zh["src.components.chat.messagebubble.canvasMessage"], "本消息来自画布");
  assert.equal(en["src.components.chat.messagebubble.canvasMessage"], "This message is from Canvas");
  assert.match(bubble, /t\("src\.components\.chat\.messagebubble\.canvasMessage"\)/);
  assert.match(bubble, /"plugin", "canvas_resource"/);
  assert.match(canvasMetadataGuard, /\[CANVAS EXECUTION CONTRACT v1\]/);
  assert.match(canvasMetadataGuard, /\[CANVAS OPERATION\]/);
  assert.match(canvasMetadataGuard, /=== "canvas_resource"/);
  assert.match(canvasMetadataGuard, /=== "canvas_operation"/);
  assert.match(userSurface, /isCanvasUserMessage \? \[\] : extractUserAttachments/);
  assert.match(userSurface, /isCanvasUserMessage \? canvasUserMessageText : \(composerPresentation\?\.text \|\| userContentText\)/);
  assert.match(userSurface, /\{isCanvasUserMessage \? \([\s\S]*?\{canvasUserMessageText\}<\/Text>[\s\S]*?\) : composerPresentation \? \(/);
  assert.match(userSurface, /!isCanvasUserMessage && contextSessionRefs\.map/);
  assert.match(userSurface, /!isCanvasUserMessage && composerSpecMode/);
});
