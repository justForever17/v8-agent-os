import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { checkServerBrowser } from "../src/doctor.mjs";

test("browser doctor reports missing OS libraries without launching Chromium or installing anything", t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-browser-doctor-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const python = path.join(root, ".python/bin/python3");
  const browser = path.join(root, ".playwright-browsers/chromium-1/chrome-linux64/chrome");
  for (const file of [python, browser]) {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, "fixture");
  }
  const calls = [];
  const result = checkServerBrowser({ engineDir: root, platform: "linux", run: (command, args) => {
    calls.push([command, ...args]);
    return command === python ? { status: 0, stdout: browser } : { status: 0, stdout: "libnspr4.so => not found\nlibnss3.so => not found\nlibasound.so.2 => not found\n" };
  } });
  assert.equal(result.status, "warning");
  assert.deepEqual(result.missingLibraries, ["libnspr4.so", "libnss3.so", "libasound.so.2"]);
  assert.equal(result.scope, "browser_only");
  assert.match(result.repairCommand, /-m playwright install-deps chromium$/u);
  assert.deepEqual(calls.map(call => call[0]), [python, "ldd"]);
  assert.equal(calls.some(call => call.join(" ").includes("sudo")), false);
});

test("server doctor does not report absent desktop surfaces as broken runtime dependencies", t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-server-doctor-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const doctor = new URL("../src/doctor.mjs", import.meta.url).href;
  const script = `const {runDoctor}=await import(${JSON.stringify(doctor)}); console.log(JSON.stringify(await runDoctor({preferEngine:false})));`;
  const result = spawnSync(process.execPath, ["--input-type=module", "-e", script], {
    env: { ...process.env, V8_AGENT_OS_HOME: root, ENGINE_INSTALL_PROFILE: "server", V8_ENGINE_DIR: path.join(root, "missing-engine") },
    encoding: "utf8", windowsHide: true, timeout: 20000,
  });
  assert.equal(result.status, 0, result.stderr);
  const report = JSON.parse(result.stdout);
  assert.equal(report.profile, "server");
  const ids = report.checks.map(item => item.id);
  assert.ok(ids.includes("engine_python"));
  assert.ok(ids.includes("engine_browser_libraries"));
  assert.ok(ids.includes("model_roles"));
  assert.equal(ids.some(id => /^(admin|web|desktop_pet|cybercore)_/u.test(id)), false);
});
