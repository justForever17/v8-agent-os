import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { spawn, spawnSync } from "node:child_process";
import { once } from "node:events";
import { fileURLToPath } from "node:url";
import { createServerServiceManager, discoverServerServiceReceipt, inspectServerBundle, renderServerServiceUnit, SERVER_SERVICE_NAME } from "../src/server_service.mjs";

const PYTHON = process.env.V8_SERVER_TEST_PYTHON || (process.platform === "win32" ? "python" : "python3");

test("portable Engine service uses its bundled Python for install, schema admission and readiness", async t => {
  const f = fixture(t);
  const engineDir = path.join(f.first, "apps/v8-agent-os-engine");
  fs.renameSync(path.join(engineDir, ".venv"), path.join(engineDir, ".python"));
  fs.writeFileSync(path.join(f.first, "engine-manifest.json"), JSON.stringify({ schema: 1, profile: "engine", target: `linux-${process.arch}`,
    version: "2026.09.16.3", engineDir: "apps/v8-agent-os-engine", python: "apps/v8-agent-os-engine/.python/bin/python3", cli: "apps/v8-agent-os-cli/bin/v8os.mjs" }));
  const bundle = inspectServerBundle(f.first);
  assert.equal(bundle.runtimeDirectory, ".python");
  f.setHealth({ status: "ok", service: "v8-agent-os-engine", startupProfile: "server", engineRuntime: {
    managedRuntimeRoot: path.join(engineDir, ".python"), reload: false,
  } });
  const result = await f.manager.perform("install", { bundleRoot: f.first, keyFile: f.keyFile });
  assert.equal(result.status, "installed");
  const unit = fs.readFileSync(f.manager.unitPath, "utf8");
  assert.ok(unit.includes(path.join(engineDir, ".python", "bin", "python3").replaceAll("\\", "\\\\")));
  assert.equal(unit.includes(".venv"), false);
  assert.ok(f.calls.some(call => call[0] === bundle.python && call.includes("-I")));
  assert.equal((await f.manager.perform("restart")).status, "restarted");
});

test("service discovery returns only matching state and never exposes credential locations", async t => {
  const f = fixture(t);
  await f.manager.perform("install", { bundleRoot: f.first, keyFile: f.keyFile });
  const options = { platform: "linux", stateRoot: f.stateRoot, configHome: f.configHome };
  assert.deepEqual(discoverServerServiceReceipt(options), {
    bundleRoot: f.first, version: "2026.09.16.3", port: 9530, stateRoot: f.stateRoot, phase: "ready",
  });
  assert.equal(discoverServerServiceReceipt({ ...options, stateRoot: path.join(f.root, "another-state") }), null);
  assert.equal(discoverServerServiceReceipt({ ...options, platform: "win32" }), null);
});

test("service installation rejects a port different from its configured client endpoint before start", async t => {
  const f = fixture(t);
  await assert.rejects(f.manager.perform("install", { bundleRoot: f.first, keyFile: f.keyFile, port: 19530 }), /Service port must match/u);
  assert.equal(f.calls.some(call => call[4] === SERVER_SERVICE_NAME && call[3] === "start"), false);
  assert.equal(fs.existsSync(f.manager.receiptPath), false);
});

test("install over a running daemon gives a stop-and-retry path without writing or stopping services", async t => {
  const f = fixture(t);
  f.setPortOccupied(true);
  await assert.rejects(f.manager.perform("install", { bundleRoot: f.first, keyFile: f.keyFile }), error =>
    error.code === "server_port_in_use" && /v8os stop.*v8os service install/u.test(error.message));
  assert.equal(f.calls.some(call => ["start", "stop", "restart", "enable", "daemon-reload"].includes(call[3])), false);
  assert.equal(fs.existsSync(f.manager.receiptPath), false);
  assert.equal(fs.existsSync(f.manager.unitPath), false);
});

function sqliteFixture(stateRoot, script) {
  const result = spawnSync(PYTHON, ["-I", "-c", `import sqlite3,sys\nconn=sqlite3.connect(sys.argv[1])\n${script}\nconn.commit()\nconn.close()`, path.join(stateRoot, "state.db")], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  return result.stdout.trim();
}

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-service-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const stateRoot = path.join(root, "state");
  const configHome = path.join(root, "config");
  const keyDir = path.join(root, "keys");
  fs.mkdirSync(stateRoot);
  fs.mkdirSync(keyDir, { mode: 0o700 });
  const keyFile = path.join(keyDir, "key");
  fs.writeFileSync(keyFile, Buffer.alloc(32, 0x31), { mode: 0o600 });
  fs.writeFileSync(path.join(stateRoot, "data-to-preserve"), "synthetic persistent data");
  const bundle = (name, version) => {
    const dir = path.join(root, name);
    for (const relative of ["apps/v8-agent-os-engine/main.py", "apps/v8-agent-os-engine/.venv/bin/python3", "apps/v8-agent-os-cli/bin/v8os.mjs"]) {
      const filename = path.join(dir, relative);
      fs.mkdirSync(path.dirname(filename), { recursive: true });
      fs.writeFileSync(filename, "synthetic bundle fixture\n", { mode: 0o700 });
    }
    fs.writeFileSync(path.join(dir, "server-manifest.json"), JSON.stringify({ schema: 1, profile: "server", platform: "linux", arch: process.arch,
      version, engine: "apps/v8-agent-os-engine", cli: "apps/v8-agent-os-cli/bin/v8os.mjs" }));
    fs.mkdirSync(path.join(dir, "apps/v8-agent-os-engine/core"));
    fs.writeFileSync(path.join(dir, "apps/v8-agent-os-engine/core/database.py"), "DATABASE_SCHEMA_VERSION = 3\nraise RuntimeError('must never import Engine during schema preflight')\n");
    return dir;
  };
  const first = bundle("v1", "2026.09.16.3");
  const second = bundle("v2", "2026.09.16.4");
  const unitPath = path.join(configHome, "systemd/user", SERVER_SERVICE_NAME);
  const calls = [];
  let state = { LoadState: "not-found", ActiveState: "inactive", SubState: "dead", MainPID: "0", UnitFileState: "disabled", FragmentPath: "", DropInPaths: "", Result: "success" };
  let linger = "yes";
  let failure = null;
  let healthOverride;
  let ownSocket = true;
  let portOccupied = false;
  let onStart;
  let tick = 0;
  const activeBundle = () => {
    const unit = fs.readFileSync(unitPath, "utf8");
    const value = unit.match(/^Environment=("V8_REPO_ROOT=.*")$/mu)[1];
    return JSON.parse(value).slice("V8_REPO_ROOT=".length).replaceAll("%%", "%");
  };
  const manager = createServerServiceManager({ configHome, stateRoot, platform: "linux", uid: process.getuid?.() || 1000,
    timeoutMs: 1000, now: () => tick, pause: async (ms) => { tick += ms; }, ownsPort: async () => ownSocket,
    probePort: async () => portOccupied,
    fetchHealth: async () => healthOverride !== undefined ? healthOverride : ({ status: "ok", service: "v8-agent-os-engine", startupProfile: "server",
      engineRuntime: { managedRuntimeRoot: path.join(activeBundle(), "apps/v8-agent-os-engine/.venv"), reload: false } }),
    run: async (command, args) => {
      calls.push([command, ...args]);
      if (command.endsWith("python3")) {
        const result = spawnSync(PYTHON, args, { encoding: "utf8" });
        return { code: result.status, stdout: result.stdout, stderr: result.stderr };
      }
      if (command === "loginctl") return { code: 0, stdout: linger };
      assert.equal(command, "systemctl");
      assert.deepEqual(args.slice(0, 3), ["--user", "--no-pager", "--no-ask-password"]);
      const action = args[3];
      if (failure?.(action, state)) return { code: 1, stderr: "injected systemd failure" };
      if (action === "show" && args[4] === "--property=Version") return { code: 0, stdout: "Version=249\n" };
      if (action === "show") return { code: 0, stdout: Object.entries(state).map(([key, value]) => `${key}=${value}`).join("\n") };
      if (action === "daemon-reload") {
        state = { ...state, LoadState: fs.existsSync(unitPath) ? "loaded" : "not-found", FragmentPath: fs.existsSync(unitPath) ? unitPath : "" };
      } else if (action === "start" || action === "restart") {
        assert.equal(state.LoadState, "loaded");
        try { onStart?.(activeBundle()); }
        catch (error) { state = { ...state, ActiveState: "failed", SubState: "failed", MainPID: "0" }; return { code: 1, stderr: error.message }; }
        state = { ...state, ActiveState: "active", SubState: "running", MainPID: String(Number(state.MainPID) + 100), Result: "success" };
      } else if (action === "stop") state = { ...state, ActiveState: "inactive", SubState: "dead", MainPID: "0" };
      else if (action === "enable") state.UnitFileState = "enabled";
      else if (action === "disable") state.UnitFileState = "disabled";
      else assert.equal(action, "reset-failed");
      return { code: 0, stdout: "" };
    },
  });
  return { root, stateRoot, configHome, keyFile, first, second, bundle, calls, manager, activeBundle,
    setLinger: (value) => { linger = value; }, setFailure: (value) => { failure = value; },
    setHealth: (value) => { healthOverride = value; }, setOwnSocket: (value) => { ownSocket = value; },
    setPortOccupied: (value) => { portOccupied = value; },
    setOnStart: (value) => { onStart = value; },
    updateState: (value) => { state = { ...state, ...value }; }, state: () => state };
}

test("install, repeat install, restart, stop/start and uninstall preserve state/key/bundles", async (t) => {
  const f = fixture(t);
  const originalKey = fs.readFileSync(f.keyFile);
  const result = await f.manager.perform("install", { bundleRoot: f.first, keyFile: f.keyFile });
  assert.equal(result.status, "installed");
  assert.equal(result.persistentAfterLogout, true);
  assert.equal((await f.manager.perform("install", { bundleRoot: f.first })).status, "already_installed");
  assert.equal(f.calls.filter((call) => call[4] === "start").length, 1);
  assert.equal((await f.manager.perform("restart")).status, "restarted");
  assert.equal((await f.manager.perform("stop")).status, "stopped");
  assert.equal((await f.manager.perform("status")).status, "inactive");
  assert.equal((await f.manager.perform("start")).status, "started");
  assert.equal((await f.manager.perform("uninstall")).status, "uninstalled");
  assert.equal((await f.manager.perform("uninstall")).status, "not_installed");
  assert.equal(fs.existsSync(f.manager.unitPath), false);
  assert.equal(fs.existsSync(f.manager.receiptPath), false);
  assert.equal(fs.readFileSync(path.join(f.stateRoot, "data-to-preserve"), "utf8"), "synthetic persistent data");
  assert.deepEqual(fs.readFileSync(f.keyFile), originalKey);
  assert.ok(fs.existsSync(path.join(f.first, "server-manifest.json")));
});

test("upgrade preserves the old release and manual rollback restores it", async (t) => {
  const f = fixture(t);
  await f.manager.perform("install", { bundleRoot: f.first, keyFile: f.keyFile });
  const originalUnit = fs.readFileSync(f.manager.unitPath, "utf8");
  const result = await f.manager.perform("upgrade", { bundleRoot: f.second });
  assert.equal(result.status, "upgraded");
  assert.equal(result.rollbackAvailable, true);
  assert.equal(f.activeBundle(), f.second);
  assert.equal((await f.manager.perform("rollback")).status, "previous_service_restored");
  assert.equal(fs.readFileSync(f.manager.unitPath, "utf8"), originalUnit);
  assert.equal(f.state().ActiveState, "active");
  assert.equal(f.state().UnitFileState, "enabled");
  assert.ok(fs.existsSync(f.second));
});

function migratingFixture(t) {
  const f = fixture(t);
  fs.writeFileSync(path.join(f.second, "apps/v8-agent-os-engine/core/database.py"), "DATABASE_SCHEMA_VERSION = 4\nraise RuntimeError('no Engine import')\n");
  sqliteFixture(f.stateRoot, "conn.execute('PRAGMA user_version=3')\nconn.execute('CREATE TABLE messages(content TEXT)')\nconn.execute(\"INSERT INTO messages VALUES ('synthetic preserved conversation')\")");
  const starts = [];
  f.setOnStart((bundle) => {
    starts.push(bundle);
    const supported = bundle === f.first ? 3 : 4;
    const current = Number(sqliteFixture(f.stateRoot, "print(conn.execute('PRAGMA user_version').fetchone()[0])"));
    if (current > supported) throw new Error(`Old Engine refuses schema ${current}`);
    sqliteFixture(f.stateRoot, `conn.execute('PRAGMA user_version=${supported}')`);
  });
  return { ...f, starts };
}

test("schema migration followed by readiness failure blocks old binary rollback and preserves new state", async (t) => {
  const f = migratingFixture(t);
  await f.manager.perform("install", { bundleRoot: f.first, keyFile: f.keyFile });
  f.setHealth({ status: "not-ready" });
  await assert.rejects(f.manager.perform("upgrade", { bundleRoot: f.second }), /schema 4.*supports 3/u);
  assert.deepEqual(f.starts, [f.first, f.second]);
  assert.equal(f.activeBundle(), f.second);
  assert.equal(JSON.parse(fs.readFileSync(f.manager.receiptPath)).phase, "pending");
  assert.equal(sqliteFixture(f.stateRoot, "print(conn.execute('PRAGMA user_version').fetchone()[0])"), "4");
  assert.equal(sqliteFixture(f.stateRoot, "print(conn.execute('SELECT content FROM messages').fetchone()[0])"), "synthetic preserved conversation");
  f.setHealth(undefined);
  assert.equal((await f.manager.perform("start")).status, "started");
  assert.equal((await f.manager.perform("status")).status, "active");
});

test("manual rollback after schema upgrade does not stop the healthy new service", async (t) => {
  const f = migratingFixture(t);
  await f.manager.perform("install", { bundleRoot: f.first, keyFile: f.keyFile });
  const upgraded = await f.manager.perform("upgrade", { bundleRoot: f.second });
  assert.equal(upgraded.rollbackAvailable, false);
  const before = f.calls.length;
  await assert.rejects(f.manager.perform("rollback"), /schema 4.*supports 3/u);
  assert.ok(f.calls.slice(before).every((call) => call[4] !== "stop"));
  assert.equal(f.activeBundle(), f.second);
  assert.equal(f.state().ActiveState, "active");
  assert.equal(JSON.parse(fs.readFileSync(f.manager.receiptPath)).phase, "ready");
});

test("explicit downgrade through upgrade is rejected before stopping or altering the unit", async (t) => {
  const f = migratingFixture(t);
  await f.manager.perform("install", { bundleRoot: f.second, keyFile: f.keyFile });
  const unit = fs.readFileSync(f.manager.unitPath);
  await assert.rejects(f.manager.perform("upgrade", { bundleRoot: f.first }), /schema 4.*supports 3/u);
  assert.equal(f.activeBundle(), f.second);
  assert.deepEqual(fs.readFileSync(f.manager.unitPath), unit);
  assert.equal(f.state().ActiveState, "active");
});

test("a pending migrated upgrade can recover forward into a compatible fixed bundle", async (t) => {
  const f = migratingFixture(t);
  await f.manager.perform("install", { bundleRoot: f.first, keyFile: f.keyFile });
  f.setHealth({ status: "not-ready" });
  await assert.rejects(f.manager.perform("upgrade", { bundleRoot: f.second }), /schema 4.*supports 3/u);
  const fixed = f.bundle("fixed", "2026.09.17.2");
  fs.writeFileSync(path.join(fixed, "apps/v8-agent-os-engine/core/database.py"), "DATABASE_SCHEMA_VERSION = 4\n");
  f.setHealth(undefined);
  const result = await f.manager.perform("upgrade", { bundleRoot: fixed });
  assert.equal(result.status, "upgraded");
  assert.equal(result.rollbackAvailable, false);
  assert.equal(f.activeBundle(), fixed);
  const receipt = JSON.parse(fs.readFileSync(f.manager.receiptPath));
  assert.equal(receipt.phase, "ready");
  assert.equal(receipt.previous.bundleRoot, f.first);
  assert.equal(sqliteFixture(f.stateRoot, "print(conn.execute('SELECT content FROM messages').fetchone()[0])"), "synthetic preserved conversation");
});

test("pending start repairs a unit missing after interruption without dropping its journal", async (t) => {
  const f = fixture(t);
  await f.manager.perform("install", { bundleRoot: f.first, keyFile: f.keyFile });
  const receipt = JSON.parse(fs.readFileSync(f.manager.receiptPath));
  fs.writeFileSync(f.manager.receiptPath, JSON.stringify({ ...receipt, phase: "pending" }));
  fs.unlinkSync(f.manager.unitPath);
  f.updateState({ LoadState: "not-found", FragmentPath: "", ActiveState: "inactive", MainPID: "0" });
  assert.equal((await f.manager.perform("start")).status, "started");
  assert.equal(f.activeBundle(), f.first);
  assert.equal(JSON.parse(fs.readFileSync(f.manager.receiptPath)).phase, "ready");
});

test("unverifiable database or schema source blocks lifecycle mutation", async (t) => {
  for (const fault of ["corrupt-db", "missing-source", "nonliteral-source"]) {
    const f = fixture(t);
    await f.manager.perform("install", { bundleRoot: f.first, keyFile: f.keyFile });
    if (fault === "corrupt-db") fs.writeFileSync(path.join(f.stateRoot, "state.db"), "not sqlite");
    else {
      const source = path.join(f.second, "apps/v8-agent-os-engine/core/database.py");
      if (fault === "missing-source") fs.unlinkSync(source);
      else fs.writeFileSync(source, "DATABASE_SCHEMA_VERSION = int('4')\n");
    }
    const before = f.calls.length;
    const unit = fs.readFileSync(f.manager.unitPath);
    await assert.rejects(f.manager.perform("upgrade", { bundleRoot: f.second }), { code: "server_schema_unverified" });
    assert.ok(f.calls.slice(before).every((call) => call[4] !== "stop"));
    assert.deepEqual(fs.readFileSync(f.manager.unitPath), unit);
    assert.equal(f.state().ActiveState, "active");
  }
});

test("schema admission reads committed WAL instead of the stale main-file version", async (t) => {
  const f = fixture(t);
  sqliteFixture(f.stateRoot, "conn.execute('PRAGMA user_version=3')");
  await f.manager.perform("install", { bundleRoot: f.first, keyFile: f.keyFile });
  const child = spawn(PYTHON, ["-I", "-u", "-c", [
    "import sqlite3,sys", "conn=sqlite3.connect(sys.argv[1])", "conn.execute('PRAGMA journal_mode=WAL')",
    "conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')", "conn.execute('PRAGMA user_version=4')",
    "conn.commit()", "print('ready',flush=True)", "sys.stdin.readline()", "conn.close()",
  ].join("\n"), path.join(f.stateRoot, "state.db")], { stdio: ["pipe", "pipe", "pipe"] });
  const exited = once(child, "exit");
  try {
    const [chunk] = await once(child.stdout, "data");
    assert.match(String(chunk), /ready/u);
    assert.equal(fs.readFileSync(path.join(f.stateRoot, "state.db")).readUInt32BE(60), 3);
    const before = f.calls.length;
    await assert.rejects(f.manager.perform("restart"), { code: "server_schema_incompatible" });
    assert.ok(f.calls.slice(before).every((call) => call[4] !== "restart"));
    assert.equal(f.state().ActiveState, "active");
  } finally {
    child.stdin.end("done\n");
    await exited;
  }
});

test("failed new process restores prior running unit and reports failed upgrade", async (t) => {
  const f = fixture(t);
  await f.manager.perform("install", { bundleRoot: f.first, keyFile: f.keyFile });
  const oldUnit = fs.readFileSync(f.manager.unitPath, "utf8");
  f.setFailure((action) => action === "start" && f.activeBundle() === f.second);
  await assert.rejects(f.manager.perform("upgrade", { bundleRoot: f.second }), /upgrade failed:.*Recovery: previous_service_restored/u);
  assert.equal(fs.readFileSync(f.manager.unitPath, "utf8"), oldUnit);
  assert.equal(f.state().ActiveState, "active");
  assert.equal(JSON.parse(fs.readFileSync(f.manager.receiptPath)).current.bundleRoot, f.first);
  assert.equal((await f.manager.perform("status")).version, "2026.09.16.3");
});

test("failed first install removes the unit and preserves data", async (t) => {
  const f = fixture(t);
  f.setFailure((action) => action === "start");
  await assert.rejects(f.manager.perform("install", { bundleRoot: f.first, keyFile: f.keyFile }), /Recovery: installation_removed/u);
  assert.equal(fs.existsSync(f.manager.unitPath), false);
  assert.equal(fs.existsSync(f.manager.receiptPath), false);
  assert.ok(fs.existsSync(f.keyFile));
  assert.ok(fs.existsSync(path.join(f.stateRoot, "data-to-preserve")));
});

test("failed rollback retains pending receipt and supports explicit recovery", async (t) => {
  const f = fixture(t);
  await f.manager.perform("install", { bundleRoot: f.first, keyFile: f.keyFile });
  f.setFailure((action) => action === "start");
  await assert.rejects(f.manager.perform("upgrade", { bundleRoot: f.second }), /Rollback failed:.*Receipt retained/u);
  assert.equal(JSON.parse(fs.readFileSync(f.manager.receiptPath)).phase, "pending");
  await assert.rejects(f.manager.perform("restart"), /interrupted service installation/u);
  f.setFailure(null);
  assert.equal((await f.manager.perform("rollback")).status, "previous_service_restored");
  assert.equal(f.activeBundle(), f.first);
  assert.equal(f.state().ActiveState, "active");
});

test("rollback restores stopped and disabled state, not just previous bytes", async (t) => {
  const f = fixture(t);
  await f.manager.perform("install", { bundleRoot: f.first, keyFile: f.keyFile });
  await f.manager.perform("stop");
  f.updateState({ UnitFileState: "disabled" });
  await f.manager.perform("upgrade", { bundleRoot: f.second });
  await f.manager.perform("rollback");
  assert.equal(f.state().ActiveState, "inactive");
  assert.equal(f.state().UnitFileState, "disabled");
});

test("an unrelated healthy listener cannot prove this systemd process is ready", async (t) => {
  const f = fixture(t);
  f.setOwnSocket(false);
  await assert.rejects(f.manager.perform("install", { bundleRoot: f.first, keyFile: f.keyFile }), /readiness timed out/u);
  assert.equal(fs.existsSync(f.manager.unitPath), false);
});

test("desktop health or another bundle cannot prove server readiness", async (t) => {
  const f = fixture(t);
  f.setHealth({ status: "ok", service: "v8-agent-os-engine", startupProfile: "desktop", engineRuntime: { managedRuntimeRoot: path.join(f.first, "apps/v8-agent-os-engine/.venv"), reload: false } });
  await assert.rejects(f.manager.perform("install", { bundleRoot: f.first, keyFile: f.keyFile }), /readiness timed out/u);
  assert.equal(fs.existsSync(f.manager.unitPath), false);
});

test("missing linger blocks installation without writing an installation", async (t) => {
  const f = fixture(t);
  f.setLinger("no");
  await assert.rejects(f.manager.perform("install", { bundleRoot: f.first, keyFile: f.keyFile }), /loginctl enable-linger/u);
  assert.equal(fs.existsSync(f.manager.unitPath), false);
  assert.equal(fs.existsSync(f.manager.receiptPath), false);
  assert.ok(f.calls.every((call) => call[4] !== "enable" && call[4] !== "start"));
});

test("manual unit edits and a different state root are not overwritten or stopped", async (t) => {
  const f = fixture(t);
  await f.manager.perform("install", { bundleRoot: f.first, keyFile: f.keyFile });
  const callsBefore = f.calls.length;
  fs.appendFileSync(f.manager.unitPath, "# manual edit\n");
  await assert.rejects(f.manager.perform("uninstall"), /modified outside v8os/u);
  assert.equal(f.state().ActiveState, "active");
  assert.ok(f.calls.slice(callsBefore).every((call) => call[4] !== "stop"));
  const other = createServerServiceManager({ configHome: f.configHome, stateRoot: path.join(f.root, "other-state"), platform: "linux", uid: 1000,
    run: async () => ({ code: 0, stdout: "" }) });
  await assert.rejects(other.perform("uninstall"), /different V8_AGENT_OS_HOME/u);
});

test("unmanaged loaded units and systemd overrides are rejected before stopping", async (t) => {
  const f = fixture(t);
  f.updateState({ LoadState: "loaded", FragmentPath: "/etc/systemd/user/v8os-server.service" });
  await assert.rejects(f.manager.perform("install", { bundleRoot: f.first, keyFile: f.keyFile }), /unmanaged/u);
  assert.equal(fs.existsSync(f.manager.receiptPath), false);
  f.updateState({ LoadState: "not-found", FragmentPath: "" });
  await f.manager.perform("install", { bundleRoot: f.first, keyFile: f.keyFile });
  f.updateState({ DropInPaths: "/etc/systemd/user/v8os-server.service.d/override.conf" });
  await assert.rejects(f.manager.perform("stop"), /external override/u);
  assert.equal(f.state().ActiveState, "active");
});

test("invalid and missing bundles fail before stopping the installed service", async (t) => {
  const f = fixture(t);
  await f.manager.perform("install", { bundleRoot: f.first, keyFile: f.keyFile });
  fs.rmSync(path.join(f.second, "apps/v8-agent-os-engine/.venv/bin/python3"));
  await assert.rejects(f.manager.perform("upgrade", { bundleRoot: f.second }), /not installed/u);
  assert.equal(f.state().ActiveState, "active");
  assert.equal(f.activeBundle(), f.first);
  assert.throws(() => inspectServerBundle(f.first, { arch: "wrong-arch" }), /Unsupported server bundle/u);
});

test("missing credential key and key rotation during upgrade are explicit errors", async (t) => {
  const f = fixture(t);
  await assert.rejects(f.manager.perform("install", { bundleRoot: f.first, keyFile: path.join(f.root, "missing") }), /credentials init/u);
  await f.manager.perform("install", { bundleRoot: f.first, keyFile: f.keyFile });
  const other = path.join(path.dirname(f.keyFile), "other");
  fs.writeFileSync(other, Buffer.alloc(32), { mode: 0o600 });
  await assert.rejects(f.manager.perform("upgrade", { bundleRoot: f.second, keyFile: other }), /cannot change the credential key/u);
  assert.equal(f.activeBundle(), f.first);
});

test("unit escaping does not expand percent specifiers or command environment variables", () => {
  const text = renderServerServiceUnit({ bundleRoot: '/opt/v8 $USER %h "quoted"', stateRoot: "/srv/v8 state", keyFile: "/keys/%h", port: 9530 }, { nodePath: "/usr/bin/node" });
  assert.match(text, /ExecStart=.*v8 \$USER %%h \\"quoted\\".* -X utf8 .*v8 \$\$USER %%h/u);
  assert.match(text, /Environment="V8_AGENT_OS_CREDENTIAL_KEY_FILE=\/keys\/%%h"/u);
  assert.match(text, /ENGINE_HOST=127\.0\.0\.1/u);
  assert.match(text, /ENGINE_INSTALL_PROFILE=server/u);
  assert.doesNotMatch(text, /ExecStart=.*v8os.*start/u);
  assert.throws(() => renderServerServiceUnit({ bundleRoot: "/opt/v8\nExecStart=bad", stateRoot: "/state", keyFile: "/key", port: 9530 }), /control characters/u);
});

test("non-Linux CLI JSON failure remains machine readable with nonzero exit", { skip: process.platform === "linux" }, () => {
  const result = spawnSync(process.execPath, [fileURLToPath(new URL("../bin/v8os.mjs", import.meta.url)), "service", "--json"], { encoding: "utf8" });
  assert.equal(result.status, 1);
  assert.equal(JSON.parse(result.stdout).status, "failed");
  assert.equal(result.stderr, "");
});

test("invalid CLI options produce JSON failure without invoking a service", () => {
  const result = spawnSync(process.execPath, [fileURLToPath(new URL("../bin/v8os.mjs", import.meta.url)), "service", "uninstall", "--unexpected", "--json"], { encoding: "utf8" });
  assert.equal(result.status, 1);
  const payload = JSON.parse(result.stdout);
  assert.equal(payload.status, "failed");
  assert.match(payload.error, /Invalid service option/u);
  assert.equal(result.stderr, "");
});
