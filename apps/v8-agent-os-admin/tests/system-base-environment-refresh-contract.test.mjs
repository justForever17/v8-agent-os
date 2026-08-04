import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const adminRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function read(...segments) {
  return fs.readFileSync(path.join(adminRoot, ...segments), "utf8");
}

test("system base renders configuration before explicitly refreshing environment probes", () => {
  const page = read("src", "app", "admin", "(dashboard)", "system-base", "page.tsx");
  const client = read("src", "lib", "config-registry.ts");
  const proxy = read("src", "app", "api", "config-registry", "[domain]", "route.ts");

  assert.match(page, /environmentProbeStatus !== "refreshing"/);
  assert.match(page, /refreshEnvironment:\s*true/);
  assert.match(page, /app\.admin\.dashboard\.system\.base\.environment\.refreshing/);
  assert.match(page, /app\.admin\.dashboard\.system\.base\.environment\.failed/);
  assert.match(client, /refreshEnvironment \? `\$\{baseUrl\}\?refresh=true` : baseUrl/);
  assert.match(client, /primeAdminJsonCache\(configDomainUrl\(domain\), envelope\)/);
  assert.match(proxy, /\["1", "true"\]\.includes/);
  assert.match(proxy, /refreshEnvironment \? "\?refresh=true" : ""/);
});

test("system base environment status has bilingual human-surface copy", () => {
  const en = JSON.parse(read("src", "i18n", "locales", "en.json"));
  const zh = JSON.parse(read("src", "i18n", "locales", "zh-CN.json"));

  assert.match(en["app.admin.dashboard.system.base.environment.refreshing"], /Configuration is ready/);
  assert.match(en["app.admin.dashboard.system.base.environment.failed"], /Configuration remains editable/);
  assert.match(zh["app.admin.dashboard.system.base.environment.refreshing"], /配置已可用/);
  assert.match(zh["app.admin.dashboard.system.base.environment.failed"], /配置仍可编辑/);
});
