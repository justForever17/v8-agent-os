/* eslint-disable @typescript-eslint/no-require-imports */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const repoRoot = path.resolve(__dirname, "../../..");

function readText(relativePath) {
  return fs.readFileSync(path.join(repoRoot, relativePath), "utf8");
}

test("human guidance renders as a user-injected message without control chrome", () => {
  const dispatcher = readText("apps/v8-agent-os-web/src/components/chat/ContentDispatcher.tsx");
  const card = readText("apps/v8-agent-os-web/src/components/chat/ApprovalCard.tsx");
  const sharedLifecycle = readText("packages/session-realtime/src/message-lifecycle.ts");
  const taxonomy = readText("packages/session-realtime/src/event-taxonomy.ts");
  const zh = JSON.parse(readText("apps/v8-agent-os-web/src/i18n/locales/zh-CN.json"));
  const en = JSON.parse(readText("apps/v8-agent-os-web/src/i18n/locales/en.json"));

  assert.match(dispatcher, /const isHumanGuidance = node\.governanceType === ['"]human_guidance['"]/);
  assert.match(dispatcher, /t\("web\.governance\.humanGuidance"\)/);
  assert.match(dispatcher, /showIcon=\{!isHumanGuidance\}/);
  assert.match(dispatcher, /showStatus=\{!isHumanGuidance\}/);
  assert.match(dispatcher, /showHint=\{!isHumanGuidance\}/);
  assert.match(card, /showIcon = true/);
  assert.match(card, /showStatus = true/);
  assert.match(card, /showHint = true/);
  assert.match(sharedLifecycle, /human_guidance\.injected/);
  assert.match(taxonomy, /chat\.human_guidance_injected/);
  assert.equal(zh["web.governance.humanGuidance"], "用户主动注入消息");
  assert.equal(en["web.governance.humanGuidance"], "User-injected message");
});
