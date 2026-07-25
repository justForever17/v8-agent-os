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
  const webComposer = readText("apps/v8-agent-os-web/src/components/chat/InputArea.tsx");
  const phone = readText("apps/v8-agent-os-phone/src/screens/ChatScreen.tsx");
  const phoneApi = readText("apps/v8-agent-os-phone/src/lib/phone-api.ts");
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
  assert.doesNotMatch(webComposer, /nextData\.supervisorWorkMode/);
  assert.doesNotMatch(phoneApi, /supervisorWorkMode: options\.supervisorWorkMode/);
  assert.match(web, /updateConversationPresentation\(sessionId, \{ supervisorWorkMode: nextMode \}\)/);
  assert.match(phone, /updateConversationPresentation\(authorizedFetch, sessionId, \{ supervisorWorkMode: nextMode \}\)/);
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
