import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const testDir = path.dirname(fileURLToPath(import.meta.url));
const adminRoot = path.resolve(testDir, "..");

function read(relativePath) {
  return fs.readFileSync(path.join(adminRoot, relativePath), "utf8");
}

test("computer use availability only waits on an explicit environment refresh", () => {
  const proxy = read("src/app/api/computer-use/availability/route.ts");
  const page = read("src/app/admin/(dashboard)/desktop-automation/page.tsx");

  assert.match(proxy, /refreshEnvironment[\s\S]*\?refresh=true/);
  assert.match(page, /availabilityRefreshKey > 1[\s\S]*\/api\/computer-use\/availability\?refresh=true/);
  assert.match(page, /environmentProbe\?\.refreshing[\s\S]*attempt < 20/);
  assert.match(page, /loadAvailability\(attempt \+ 1\), 2_000/);
  assert.match(page, /force: availabilityRefreshKey > 1 \|\| attempt > 0/);
  assert.match(page, /requestUrl = attempt > 0 \? "\/api\/computer-use\/availability" : availabilityUrl/);
  assert.match(page, /available\?: boolean \| null/);
  assert.match(page, /ttlMs: 30_000/);
});

test("computer use availability exposes bilingual human status without raw probe errors", () => {
  const page = read("src/app/admin/(dashboard)/desktop-automation/page.tsx");
  const zh = JSON.parse(read("src/i18n/locales/zh-CN.json"));
  const en = JSON.parse(read("src/i18n/locales/en.json"));

  assert.match(page, /availability\.failedTitle/);
  assert.match(page, /availability\.refreshingStaleTitle/);
  assert.doesNotMatch(page, /environmentProbe\?\.error/);
  for (const key of [
    "app.admin.dashboard.desktop.automation.availability.failedTitle",
    "app.admin.dashboard.desktop.automation.availability.failedStaleDescription",
    "app.admin.dashboard.desktop.automation.availability.failedUnknownDescription",
    "app.admin.dashboard.desktop.automation.availability.refreshingTitle",
    "app.admin.dashboard.desktop.automation.availability.refreshingStaleTitle",
  ]) {
    assert.equal(typeof zh[key], "string", `missing zh-CN translation for ${key}`);
    assert.equal(typeof en[key], "string", `missing en translation for ${key}`);
  }
});
