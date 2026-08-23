const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const webRoot = path.resolve(__dirname, "..");

test("web realtime keeps compact snapshots and refreshes durable resources on milestone events", () => {
  const streamRoute = fs.readFileSync(
    path.join(webRoot, "src", "app", "api", "realtime", "sessions", "[id]", "stream", "route.ts"),
    "utf8",
  );
  const workbench = fs.readFileSync(
    path.join(webRoot, "src", "components", "chat", "WorkspaceWorkbenchPanel.tsx"),
    "utf8",
  );

  assert.match(streamRoute, /surface=web&compact=1/);
  assert.match(workbench, /topic === "artifact\.recorded"/);
  assert.match(workbench, /topic\.startsWith\("handoff\.ref\."\)/);
  assert.match(workbench, /\[resourceRevision, sessionId\]/);
});
