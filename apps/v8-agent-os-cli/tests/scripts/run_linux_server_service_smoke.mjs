// Real systemd user-manager lifecycle with a synthetic HTTP Engine fixture.
// Does not validate production Engine imports, provider calls or Phone behavior.
import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import net from "node:net";
import { execFileSync, spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { createServerServiceManager } from "../../src/server_service.mjs";

if (!process.argv.includes("--live")) throw new Error("Pass --live to exercise a real systemd user manager in a dedicated disposable user account");
if (process.platform !== "linux" || process.getuid() === 0) throw new Error("Run as a dedicated non-root Linux test user with linger enabled");
const cliRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const normalReceipt = path.join(os.homedir(), ".config/v8-agent-os/server-service.json");
const normalUnit = path.join(os.homedir(), ".config/systemd/user/v8os-server.service");
if (fs.existsSync(normalReceipt) || fs.existsSync(normalUnit)) throw new Error("Dedicated user already has a service installation; refusing to modify it");
const root = fs.mkdtempSync(path.join(os.homedir(), "v8os-service-smoke-"));
const stateRoot = path.join(root, "state");
const keyDir = path.join(root, "keys");
fs.mkdirSync(stateRoot, { mode: 0o700 });
fs.mkdirSync(keyDir, { mode: 0o700 });
const keyFile = path.join(keyDir, "server-key");
fs.writeFileSync(keyFile, crypto.randomBytes(32), { mode: 0o600 });
fs.writeFileSync(path.join(stateRoot, "keep.txt"), "synthetic configuration data\n");
const keyHash = crypto.createHash("sha256").update(fs.readFileSync(keyFile)).digest("hex");
const port = await new Promise((resolve, reject) => {
  const server = net.createServer();
  server.once("error", reject);
  server.listen(0, "127.0.0.1", () => { const value = server.address().port; server.close(() => resolve(value)); });
});
const pythonFixture = `import http.server, json, os, sqlite3, sys
from core.database import DATABASE_SCHEMA_VERSION
assert not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY")
assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == os.path.join(os.path.dirname(__file__), ".playwright-browsers")
with sqlite3.connect(os.path.join(os.environ["V8_AGENT_OS_HOME"], "state.db")) as connection:
    assert connection.execute("PRAGMA user_version").fetchone()[0] <= DATABASE_SCHEMA_VERSION, "cannot downgrade this state"
    connection.execute("CREATE TABLE IF NOT EXISTS fixture_data (schema_version INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute("INSERT OR IGNORE INTO fixture_data VALUES (?, ?)", (DATABASE_SCHEMA_VERSION, "synthetic schema " + str(DATABASE_SCHEMA_VERSION)))
    connection.execute("PRAGMA user_version=" + str(DATABASE_SCHEMA_VERSION))
if os.path.exists(os.path.join(os.environ["V8_AGENT_OS_HOME"], "fail-after-migration")):
    raise SystemExit(32)
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        result = {"status": "ok", "service": "v8-agent-os-engine", "startupProfile": os.environ["ENGINE_INSTALL_PROFILE"], "engineRuntime": {"managedRuntimeRoot": sys.prefix, "reload": False}}
        body = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass
http.server.HTTPServer((os.environ["ENGINE_HOST"], int(os.environ["ENGINE_PORT"])), Handler).serve_forever()
`;

function bundle(name, version, fail = false, schemaVersion = 3) {
  const dir = path.join(root, name);
  const engineDir = path.join(dir, "apps/v8-agent-os-engine");
  fs.mkdirSync(path.join(engineDir, "core"), { recursive: true });
  fs.writeFileSync(path.join(engineDir, "core", "database.py"), `DATABASE_SCHEMA_VERSION = ${schemaVersion}\n`);
  fs.cpSync(path.join(cliRoot, "src"), path.join(dir, "apps/v8-agent-os-cli/src"), { recursive: true });
  fs.cpSync(path.join(cliRoot, "bin"), path.join(dir, "apps/v8-agent-os-cli/bin"), { recursive: true });
  fs.writeFileSync(path.join(engineDir, "main.py"), fail ? "raise SystemExit(31)\n" : pythonFixture);
  execFileSync("python3", ["-m", "venv", "--without-pip", path.join(engineDir, ".venv")], { stdio: "pipe" });
  fs.writeFileSync(path.join(dir, "server-manifest.json"), JSON.stringify({ schema: 1, profile: "server", platform: "linux", arch: process.arch,
    version, engine: "apps/v8-agent-os-engine", cli: "apps/v8-agent-os-cli/bin/v8os.mjs" }));
  return dir;
}

const first = bundle("v1 space $dollar %percent", "2026.09.16.3");
const second = bundle("v2", "2026.09.16.4");
const broken = bundle("broken", "2026.09.16.5", true);
const interrupted = bundle("interrupted", "2026.09.16.6");
const migrating = bundle("schema4", "2026.09.17.1", false, 4);
function databaseEvidence() {
  return JSON.parse(execFileSync("python3", ["-I", "-c", "import json,sqlite3,sys; c=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True); print(json.dumps({'schema':c.execute('PRAGMA user_version').fetchone()[0], 'rows':c.execute('SELECT schema_version,value FROM fixture_data ORDER BY schema_version').fetchall()}))", path.join(stateRoot, "state.db")], { encoding: "utf8" }));
}
const manager = createServerServiceManager({ stateRoot, timeoutMs: 8000 });
const evidence = { platform: os.platform(), systemd: execFileSync("systemd-analyze", ["--version"], { encoding: "utf8" }).split("\n")[0],
  fixture: "synthetic HTTP Engine; real systemd user service, Python process and TCP ownership", checks: [] };
let clean = false;
try {
  const installed = await manager.perform("install", { bundleRoot: first, keyFile, port });
  assert.equal(installed.status, "installed");
  process.kill(installed.mainPid, 0);
  evidence.checks.push("real_user_service_install_and_process_owned_listener");
  const child = spawnSync(process.execPath, [path.join(first, "apps/v8-agent-os-cli/bin/v8os.mjs"), "service", "status", "--json"], {
    encoding: "utf8", env: { ...process.env, V8_AGENT_OS_HOME: stateRoot }, timeout: 15000,
  });
  assert.equal(child.status, 0, child.stderr || child.stdout);
  assert.equal(JSON.parse(child.stdout).mainPid, installed.mainPid);
  process.kill(installed.mainPid, 0);
  evidence.checks.push("service_survives_management_cli_exit");
  const upgraded = await manager.perform("upgrade", { bundleRoot: second });
  assert.notEqual(upgraded.mainPid, installed.mainPid);
  assert.equal((await manager.perform("status")).version, "2026.09.16.4");
  evidence.checks.push("upgrade_switches_real_python_process");
  const interruptedSource = [
    `import { createServerServiceManager } from ${JSON.stringify(new URL("../../src/server_service.mjs", import.meta.url).href)};`,
    'import { execFile } from "node:child_process";',
    'import { promisify } from "node:util";',
    'const exec = promisify(execFile);',
    `const manager = createServerServiceManager({ stateRoot: ${JSON.stringify(stateRoot)}, run: async (command, args) => {`,
    '  const result = await exec(command, args, { encoding: "utf8" });',
    '  if (command === "systemctl" && args.includes("start")) {',
    '    process.stdout.write("interrupt-after-start\\n");',
    '    await new Promise(() => setInterval(() => {}, 1000));',
    '  }',
    '  return { code: 0, stdout: result.stdout, stderr: result.stderr };',
    '} });',
    `await manager.perform("upgrade", { bundleRoot: ${JSON.stringify(interrupted)} });`,
  ].join("\n");
  const cancelled = spawn(process.execPath, ["--input-type=module", "-e", interruptedSource], { stdio: ["ignore", "pipe", "pipe"] });
  try {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Interrupted-upgrade fixture did not reach start")), 10000);
      cancelled.once("error", (error) => { clearTimeout(timer); reject(error); });
      cancelled.once("exit", (code) => { clearTimeout(timer); reject(new Error(`Interrupted-upgrade fixture exited early: ${code}`)); });
      cancelled.stdout.on("data", (data) => { if (String(data).includes("interrupt-after-start")) { clearTimeout(timer); resolve(); } });
    });
  } finally {
    const exited = new Promise((resolve) => cancelled.once("close", resolve));
    cancelled.kill("SIGKILL");
    await exited;
  }
  assert.equal((await manager.perform("status")).status, "recovery_required");
  await manager.perform("rollback");
  assert.equal((await manager.perform("status")).version, "2026.09.16.4");
  assert.equal((await manager.perform("status")).status, "active");
  evidence.checks.push("killed_upgrade_cli_releases_shared_lease_and_rolls_back_pending_installation");
  await assert.rejects(manager.perform("upgrade", { bundleRoot: broken }), /Recovery: previous_service_restored/u);
  assert.equal((await manager.perform("status")).version, "2026.09.16.4");
  assert.equal((await manager.perform("status")).status, "active");
  evidence.checks.push("crashing_upgrade_restores_prior_running_release");
  assert.deepEqual(databaseEvidence(), { schema: 3, rows: [[3, "synthetic schema 3"]] });
  fs.writeFileSync(path.join(stateRoot, "fail-after-migration"), "synthetic readiness failure\n");
  await assert.rejects(manager.perform("upgrade", { bundleRoot: migrating }), { code: "server_schema_incompatible" });
  const pending = await manager.perform("status");
  assert.equal(pending.status, "recovery_required");
  assert.equal(pending.version, "2026.09.17.1");
  assert.equal(pending.mainPid, null);
  assert.equal(pending.rollbackAvailable, false);
  assert.equal(pending.rollbackBlockedCode, "server_schema_incompatible");
  const migratedData = { schema: 4, rows: [[3, "synthetic schema 3"], [4, "synthetic schema 4"]] };
  assert.deepEqual(databaseEvidence(), migratedData);
  await assert.rejects(manager.perform("rollback"), { code: "server_schema_incompatible" });
  assert.deepEqual(databaseEvidence(), migratedData);
  evidence.checks.push("schema3_to4_readiness_failure_stops_candidate_preserves_rows_and_blocks_old_binary");
  fs.unlinkSync(path.join(stateRoot, "fail-after-migration"));
  const recovered = await manager.perform("start");
  assert.equal(recovered.status, "started");
  process.kill(recovered.mainPid, 0);
  assert.equal((await manager.perform("status")).status, "active");
  assert.deepEqual(databaseEvidence(), migratedData);
  await assert.rejects(manager.perform("rollback"), { code: "server_schema_incompatible" });
  await assert.rejects(manager.perform("upgrade", { bundleRoot: second }), { code: "server_schema_incompatible" });
  assert.equal((await manager.perform("status")).mainPid, recovered.mainPid);
  process.kill(recovered.mainPid, 0);
  evidence.checks.push("forward_start_retries_schema4_and_unsafe_downgrade_keeps_healthy_pid");
  await manager.perform("stop");
  assert.equal((await manager.perform("status")).status, "inactive");
  const restarted = await manager.perform("restart");
  process.kill(restarted.mainPid, 0);
  await manager.perform("uninstall");
  assert.equal((await manager.perform("status")).status, "not_installed");
  assert.throws(() => process.kill(restarted.mainPid, 0), { code: "ESRCH" });
  evidence.checks.push("stop_restart_uninstall_leave_no_server_process");
  assert.equal(fs.readFileSync(path.join(stateRoot, "keep.txt"), "utf8"), "synthetic configuration data\n");
  assert.equal(crypto.createHash("sha256").update(fs.readFileSync(keyFile)).digest("hex"), keyHash);
  assert.ok(fs.existsSync(path.join(second, "server-manifest.json")));
  evidence.checks.push("configuration_key_and_extracted_bundles_preserved");
  clean = true;
  console.log(JSON.stringify(evidence, null, 2));
} finally {
  if (!clean) {
    try {
      const current = await manager.perform("status");
      if (current.status === "recovery_required") {
        if (current.rollbackAvailable) await manager.perform("rollback");
        else {
          // This fixture intentionally migrates state; recover forward before cleanup.
          fs.rmSync(path.join(stateRoot, "fail-after-migration"), { force: true });
          await manager.perform("start");
        }
      }
      await manager.perform("uninstall");
      clean = true;
    } catch (error) { console.error(`Cleanup needs attention. Fixture retained at ${root}: ${error.message}`); }
  }
  if (clean) fs.rmSync(root, { recursive: true, force: true });
}
