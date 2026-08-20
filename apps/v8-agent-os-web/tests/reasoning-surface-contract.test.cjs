const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const repoRoot = path.resolve(__dirname, "../../..");

function readText(relativePath) {
  return fs.readFileSync(path.join(repoRoot, relativePath), "utf8");
}

test("structured Web narrative does not reinterpret inline think tags", () => {
  const parser = readText("apps/v8-agent-os-web/src/lib/chat/content-detector.ts");
  const dispatcher = readText("apps/v8-agent-os-web/src/components/chat/ContentDispatcher.tsx");
  assert.match(parser, /parseInlineThinking = true/);
  assert.match(dispatcher, /parseContentToBlocks\(node\.content, isStreaming, 0, false\)/);
});

test("structured Phone narrative does not reinterpret inline think tags", () => {
  const parser = readText("apps/v8-agent-os-phone/src/lib/content-detector.ts");
  const dispatcher = readText("apps/v8-agent-os-phone/src/components/chat/ContentDispatcher.tsx");
  const bubble = readText("apps/v8-agent-os-phone/src/components/chat/MessageBubble.tsx");
  assert.match(parser, /parseInlineThinking = true/);
  assert.match(dispatcher, /parsePhoneContentBlocks\(String\(node\.content \|\| ""\), false, 0, false\)/);
  assert.match(bubble, /hasStructuredNodes \? \[\] : parsePhoneContentBlocks\(String\(message\.content \|\| ""\)\)/);
});

test("Web and Phone animate only the terminal node of the active trace segment", () => {
  const web = readText("apps/v8-agent-os-web/src/components/chat/ChatMessage.tsx");
  const phone = readText("apps/v8-agent-os-phone/src/components/chat/MessageBubble.tsx");

  assert.match(web, /index === timelineSegments\.length - 1/);
  assert.match(web, /nodeIdx === segment\.nodes\.length - 1/);
  assert.doesNotMatch(web, /isExecuting=\{!!\(isLoading && isLast\)\}/);
  assert.match(phone, /active=\{segment\.active\}/);
  assert.match(phone, /index === nodes\.length - 1/);
  assert.match(phone, /index === timelineSegments\.length - 1/);
  assert.match(phone, /index === fallbackBlocks\.length - 1/);
  assert.doesNotMatch(phone, /isExecuting=\{assistantActive\}/);
});
