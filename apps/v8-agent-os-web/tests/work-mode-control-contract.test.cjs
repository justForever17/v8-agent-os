const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const repoRoot = path.resolve(__dirname, "../../..");

function readText(relativePath) {
  return fs.readFileSync(path.join(repoRoot, relativePath), "utf8");
}

test("Web and Phone keep the selected Supervisor work mode while persistence catches up", () => {
  const web = readText("apps/v8-agent-os-web/src/app/chat/ChatClient.tsx");
  const phone = readText("apps/v8-agent-os-phone/src/screens/ChatScreen.tsx");
  const phoneProxy = readText("apps/v8-agent-os-admin/src/app/api/client/conversations/[id]/route.ts");

  assert.match(phoneProxy, /body\?\.supervisorWorkMode === "daily"/);
  assert.match(phoneProxy, /supervisorWorkMode: body\.supervisorWorkMode/);

  for (const surface of [web, phone]) {
    assert.match(surface, /supervisorWorkModeDrafts/);
    assert.match(surface, /setSupervisorWorkModeDrafts\(\(current\) => \(\{ \.\.\.current, \[sessionId\]: nextMode \}\)\)/);
    assert.match(surface, /delete next\[sessionId\]/);
  }

  assert.doesNotMatch(web, /patchConversationSummary\(sessionId, \{ supervisorWorkMode \}\)/);
  assert.doesNotMatch(phone, /\? \{ \.\.\.conversation, supervisorWorkMode \}/);
});

test("Engineering console exposes useful controls instead of the retired route test", () => {
  const page = readText("apps/v8-agent-os-admin/src/app/admin/(dashboard)/engineering-lane/page.tsx");

  assert.doesNotMatch(page, /\/api\/engineering-lane\/dry-run/);
  assert.doesNotMatch(page, /dryRunDiagnosticTitle|recentRiskTitle|filterRouteTest/);
  assert.match(page, /worksetGovernanceDescription/);
  assert.match(page, /codingExecutionContractEnabled/);
  assert.match(page, /worksetObservationEnabled/);
  assert.ok(page.indexOf("workflowMemoryTitle") < page.indexOf("advancedDiagnosticsTitle"));
});
