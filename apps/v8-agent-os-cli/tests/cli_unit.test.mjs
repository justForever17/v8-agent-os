import assert from "node:assert/strict";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { ALL_COMPONENTS, COMPONENTS, DEFAULT_START_COMPONENTS, parseComponentSelection } from "../src/components.mjs";
import { backupFile, readJsonFile, writeJsonFile } from "../src/json_file.mjs";
import { isPortOpen } from "../src/ports.mjs";
import { buildLocalRepairPlan, runDoctor } from "../src/doctor.mjs";

test("default start components exclude CyberCore", () => {
  assert.deepEqual(DEFAULT_START_COMPONENTS, ["engine", "admin", "web"]);
  assert.deepEqual(parseComponentSelection([]), ["engine", "admin", "web"]);
});

test("--with adds optional components without replacing defaults", () => {
  assert.deepEqual(parseComponentSelection(["--with", "cybercore"]), ["engine", "admin", "web", "cybercore"]);
});

test("--only narrows component set", () => {
  assert.deepEqual(parseComponentSelection(["--only", "engine,admin"]), ["engine", "admin"]);
});

test("--all returns all components", () => {
  assert.deepEqual(parseComponentSelection(["--all"]), ALL_COMPONENTS);
});

test("local repair plan proposes safe state root creation", () => {
  const plan = buildLocalRepairPlan([{ id: "state_root", status: "warning" }]);
  assert.equal(plan.actions[0].id, "create_state_root");
  assert.equal(plan.actions[0].safe, true);
});

test("local repair plan treats runtime installation as explicit action", () => {
  const plan = buildLocalRepairPlan([{ id: "python", status: "warning" }]);
  assert.equal(plan.actions[0].id, "install_python");
  assert.equal(plan.actions[0].safe, false);
});

test("port probe detects a listening local port", async () => {
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  try {
    assert.equal(await isPortOpen(address.port), true);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

test("json backup writes timestamped backup without changing source", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-cli-json-"));
  const file = path.join(dir, "config.json");
  writeJsonFile(file, { ok: true });
  const backup = backupFile(file, "unit");
  assert.deepEqual(readJsonFile(file), { ok: true });
  assert.deepEqual(readJsonFile(backup), { ok: true });
  assert.match(path.basename(backup), /^config\.json\.unit\./);
});

test("admin and web commands use managed auth launcher", () => {
  const admin = COMPONENTS.admin.command({ mode: "dev" });
  const web = COMPONENTS.web.command({ mode: "start" });
  assert.equal(admin.command, process.execPath);
  assert.equal(web.command, process.execPath);
  assert.ok(admin.args.some((part) => part.endsWith("run-next-with-managed-auth.mjs")));
  assert.ok(web.args.some((part) => part.endsWith("run-next-with-managed-auth.mjs")));
  assert.ok(admin.args.includes("admin"));
  assert.ok(web.args.includes("web"));
  assert.ok(admin.args.includes("9528"));
  assert.ok(web.args.includes("9527"));
});

test("doctor fallback returns local checks without requiring engine", async () => {
  const result = await runDoctor({ preferEngine: false });
  assert.equal(result.source, "local_fallback");
  assert.ok(result.summary.total >= 4);
  assert.ok(result.checks.some((check) => check.id === "config.json"));
});
