const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const repoRoot = path.resolve(__dirname, "../../..");

function readText(relativePath) {
  return fs.readFileSync(path.join(repoRoot, relativePath), "utf8");
}

test("Web renders cross-session coordination as governance instead of user content", () => {
  const types = readText("apps/v8-agent-os-web/src/store/chat-types.ts");
  const dispatcher = readText("apps/v8-agent-os-web/src/components/chat/ContentDispatcher.tsx");
  assert.match(types, /'session_coordination'/);
  assert.match(dispatcher, /function SessionCoordinationCard/);
  assert.match(dispatcher, /node\.governanceType === "session_coordination"/);
  assert.match(dispatcher, /web\.sessionCoordination\.incoming/);
  assert.doesNotMatch(dispatcher, /role:\s*['"]user['"]/);
});

test("Phone renders the same governance card and flushes coordination events immediately", () => {
  const types = readText("apps/v8-agent-os-phone/src/types/admin.ts");
  const block = readText("apps/v8-agent-os-phone/src/components/chat/MessageBlockItem.tsx");
  const screen = readText("apps/v8-agent-os-phone/src/screens/ChatScreen.tsx");
  assert.match(types, /"session_coordination"/);
  assert.match(block, /function SessionCoordinationInlineCard/);
  assert.match(block, /node\.governanceType === "session_coordination"/);
  assert.match(screen, /normalized\.name === "session_coordination"/);
});
