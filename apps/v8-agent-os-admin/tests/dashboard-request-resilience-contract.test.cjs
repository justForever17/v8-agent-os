const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const adminRoot = path.resolve(__dirname, "..");

function read(relativePath) {
  return fs.readFileSync(path.join(adminRoot, relativePath), "utf8");
}

test("dashboard requests have bounded server and browser lifetimes", () => {
  const cache = read("src/lib/admin-client-cache.ts");
  const hook = read("src/lib/use-admin-json-resource.ts");
  const route = read("src/app/api/stats/route.ts");
  const page = read("src/app/admin/(dashboard)/page.tsx");

  assert.match(cache, /timeoutMs\?: number/);
  assert.match(cache, /controller\.abort\(\)/);
  assert.match(cache, /admin_request_timeout/);
  assert.match(hook, /snapshot\.data === undefined && snapshot\.error === null/);
  assert.match(route, /AbortSignal\.timeout\(8_000\)/);
  assert.match(route, /telemetry_timeout/);
  assert.match(page, /timeoutMs: 12_000/);
});

test("dashboard renders a retry command after telemetry settles as unavailable", () => {
  const page = read("src/app/admin/(dashboard)/page.tsx");
  const en = JSON.parse(read("src/i18n/locales/en.json"));
  const zh = JSON.parse(read("src/i18n/locales/zh-CN.json"));

  assert.match(page, /statsResource\.refresh\(\)\.catch/);
  assert.match(page, /statsResource\.isFetching/);
  assert.match(page, /\{!telemetryError \? \([\s\S]*?Key Metrics[\s\S]*?\) : null\}/);
  assert.equal(typeof en["app.admin.dashboard.page.telemetryRetry"], "string");
  assert.equal(typeof zh["app.admin.dashboard.page.telemetryRetry"], "string");
});
