const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const repoRoot = path.resolve(__dirname, "..", "..", "..");
const webRoot = path.join(repoRoot, "apps", "v8-agent-os-web");
const adminRoot = path.join(repoRoot, "apps", "v8-agent-os-admin");

function read(...segments) {
  return fs.readFileSync(path.join(...segments), "utf8");
}

test("web message headers follow the current supervisor profile instead of stored display snapshots", () => {
  const chatClient = read(webRoot, "src", "app", "chat", "ChatClient.tsx");
  const chatWindow = read(webRoot, "src", "components", "chat", "ChatWindow.tsx");
  const chatMessage = read(webRoot, "src", "components", "chat", "ChatMessage.tsx");
  const streamState = read(webRoot, "src", "lib", "chat-stream-state.ts");

  assert.match(chatClient, /fetch\("\/api\/supervisor-profile", \{ cache: "no-store" \}\)/);
  assert.match(chatClient, /window\.setInterval\(refresh, 2_000\)/);
  assert.match(chatClient, /supervisorProfile=\{supervisorDisplayProfile\}/);
  assert.match(chatWindow, /supervisorProfile=\{supervisorProfile\}/);
  assert.match(chatMessage, /usesCurrentSupervisorProfile/);
  assert.match(chatMessage, /supervisorProfile\?\.name \|\| message\.agentName \|\| "智能主管"/);
  assert.match(chatMessage, /supervisorProfile\?\.roleLabel \|\| message\.agentRoleLabel \|\| "主理人"/);
  assert.match(streamState, /agentName: '智能主管'/);
  assert.match(streamState, /agentRoleLabel: '主理人'/);
});

test("client supervisor endpoint exposes profile fields only", () => {
  const adminRoute = read(adminRoot, "src", "app", "api", "client", "supervisor-profile", "route.ts");
  const webRoute = read(webRoot, "src", "app", "api", "supervisor-profile", "route.ts");

  assert.match(adminRoute, /fetchClientEngine\(req, "\/config-registry\/supervisor"\)/);
  assert.match(adminRoute, /name: String\(profile\.name/);
  assert.match(adminRoute, /roleLabel: String\(profile\.roleLabel/);
  assert.doesNotMatch(adminRoute, /systemPrompt|allowedTools|bindings/);
  assert.match(webRoute, /\/client\/supervisor-profile/);
  assert.match(webRoute, /"Cache-Control": "no-store"/);
});
