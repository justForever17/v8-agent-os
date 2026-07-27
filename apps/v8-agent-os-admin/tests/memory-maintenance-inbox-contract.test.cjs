const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const adminRoot = path.resolve(__dirname, "..");

test("missed memory maintenance is a topbar inbox link, not an automatic recovery", () => {
  const inbox = fs.readFileSync(path.join(adminRoot, "src", "app", "api", "admin-inbox", "route.ts"), "utf8");
  const cronPage = fs.readFileSync(
    path.join(adminRoot, "src", "app", "admin", "(dashboard)", "automation", "cron", "page.tsx"),
    "utf8",
  );

  assert.match(inbox, /memory-maintenance-missed/);
  assert.match(inbox, /memory\?\.maintenance\?\.due/);
  assert.match(inbox, /\/admin\/automation\?tab=cron#system-memory-maintenance/);
  assert.match(cronPage, /id="system-memory-maintenance"/);
  assert.match(cronPage, /handleRunNow\(systemMemoryJob\.id\)/);
});
