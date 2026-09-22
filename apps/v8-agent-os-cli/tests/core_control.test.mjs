import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { desktopOwnedIdentities } from "../src/core_control.mjs";

const coreUrl = new URL("../src/core_control.mjs", import.meta.url).href;

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-core-control-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

function run(script, state, extra = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ["--input-type=module", "-e", script], {
      env: { ...process.env, V8_AGENT_OS_HOME: state, ...extra }, windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let output = "", errors = "";
    child.stdout.on("data", chunk => { output += chunk; });
    child.stderr.on("data", chunk => { errors += chunk; });
    child.on("error", reject);
    child.on("exit", code => code === 0 ? resolve(output) : reject(new Error(`${code}: ${errors}\n${output}`)));
  });
}

test("desktop receipts transfer desktop restarts but never adopt an existing daemon", () => {
  const identity = { pid: 10, launchId: "launch-a" };
  assert.deepEqual(desktopOwnedIdentities([
    { id: "engine", status: "already_running", lifecycle: "daemon", recordIdentity: identity },
    { id: "web", status: "started", lifecycle: "desktop", recordIdentity: identity },
  ]), { web: identity });
  assert.deepEqual(desktopOwnedIdentities([
    { id: "engine", status: "already_running", lifecycle: "desktop", recordIdentity: identity },
  ]), { engine: identity });
  assert.deepEqual(desktopOwnedIdentities([{ id: "engine", status: "already_running", recordIdentity: identity }]), {});
});

test("CLI packs delegates to the shipped pack manager with exact arguments and exit status", t => {
  const root = fixture(t);
  const manager = path.join(root, "scripts/server/feature-packs.mjs");
  fs.mkdirSync(path.dirname(manager), { recursive: true });
  fs.writeFileSync(manager, "console.log(JSON.stringify(process.argv.slice(2))); process.exitCode = 7;\n");
  const entry = new URL("../bin/v8os.mjs", import.meta.url);
  const result = spawnSync(process.execPath, [fileURLToPath(entry), "packs", "install", "voice test", "--json"], {
    env: { ...process.env, V8_REPO_ROOT: root, V8_AGENT_OS_HOME: root }, encoding: "utf8", windowsHide: true,
  });
  assert.equal(result.status, 7, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), ["install", "voice test", "--json"]);
});

test("Core start/status/stop preserve an attached daemon and reject a replaced launch receipt", { timeout: 45000 }, async t => {
  const root = fixture(t);
  const engine = path.join(root, "engine");
  fs.mkdirSync(engine);
  // Synthetic executable exercises the real process/port owner without loading
  // Python/provider dependencies. Actual Engine smoke is a separate live layer.
  fs.writeFileSync(path.join(engine, "main.py"), "require('node:http').createServer((req,res)=>res.end('fixture')).listen(Number(process.env.ENGINE_PORT),'127.0.0.1');\n");
  const reservation = net.createServer();
  await new Promise(resolve => reservation.listen(0, "127.0.0.1", resolve));
  const port = reservation.address().port;
  await new Promise(resolve => reservation.close(resolve));
  fs.writeFileSync(path.join(root, "config.json"), JSON.stringify({ systemBase: { bridge: { engineBaseUrl: `http://127.0.0.1:${port}` } } }));
  const script = `
    import assert from 'node:assert/strict';
    const core = await import(${JSON.stringify(coreUrl)});
    const options = { mode: 'start' };
    const started = await core.startCoreComponentsWithRuntimePorts(['engine'], options);
    try {
      assert.equal(started.profile.ports.engine, ${port});
      assert.equal(started.results[0].status, 'started');
      const attached = await core.startCoreComponents(['engine'], { lifecycle: 'desktop' });
      assert.equal(attached[0].status, 'already_running');
      assert.equal(attached[0].lifecycle, 'daemon');
      assert.deepEqual(core.desktopOwnedIdentities(attached), {});
      assert.equal((await core.stopCoreComponents(['engine'], { expectedIdentities: {} }))[0].status, 'not_owned');
      assert.equal((await core.statusCoreComponents(['engine']))[0].pidAlive, true);
      const wrong = { engine: { ...started.results[0].recordIdentity, launchId: 'replacement' } };
      assert.equal((await core.stopCoreComponents(['engine'], { expectedIdentities: wrong }))[0].status, 'not_owned');
      const detached = await core.statusCoreComponents(['engine'], { expectedIdentities: wrong });
      assert.equal(detached[0].pidAlive, true);
      assert.equal(detached[0].ownership, 'detached');
    } finally {
      const stopped = await core.stopCoreComponents(['engine'], { expectedIdentities: { engine: started.results[0].recordIdentity } });
      assert.equal(stopped[0].status, 'stopped');
    }
    assert.equal((await core.statusCoreComponents(['engine']))[0].pidAlive, false);
  `;
  await run(script, root, { V8_ENGINE_DIR: engine, V8_ENGINE_PYTHON: process.execPath });
});

test("Core readiness requires readyz and the exact local state instance identity", async t => {
  const root = fixture(t);
  fs.mkdirSync(path.join(root, "runtime"));
  fs.writeFileSync(path.join(root, "runtime", "instance.json"), JSON.stringify({ instanceId: "local-state-instance" }));
  let actualId = "other-state-instance";
  let ready = true;
  const server = http.createServer((request, response) => {
    response.setHeader("content-type", "application/json");
    response.end(JSON.stringify(request.url === "/readyz"
      ? { service: "v8-agent-os-engine", ready }
      : { instanceId: actualId }));
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const origin = `http://127.0.0.1:${server.address().port}`;
  fs.writeFileSync(path.join(root, "config.json"), JSON.stringify({ systemBase: { bridge: { engineBaseUrl: origin } } }));
  const invoke = `const c=await import(${JSON.stringify(coreUrl)}); try { console.log(JSON.stringify(await c.waitForCoreReadiness({timeoutMs:300}))); } catch(e) { console.log(JSON.stringify({error:e.message, code:e.code})); }`;
  assert.equal(JSON.parse(await run(invoke, root)).error, "engine_instance_mismatch");
  actualId = "local-state-instance";
  assert.deepEqual(JSON.parse(await run(invoke, root)), { ready: true, origin, instanceId: actualId });
  ready = false;
  assert.equal(JSON.parse(await run(invoke, root)).code, "V8OS_ENGINE_READINESS_TIMEOUT");
});

test("Core routes an installed server through its existing service manager and keeps desktop detach harmless", async t => {
  const root = fixture(t);
  fs.writeFileSync(path.join(root, "config.json"), JSON.stringify({ systemBase: { bridge: { engineBaseUrl: "http://127.0.0.1:65321/v1" } } }));
  const result = await run(`
    import assert from 'node:assert/strict';
    import fs from 'node:fs';
    import path from 'node:path';
    const core = await import(${JSON.stringify(coreUrl)});
    const calls = [];
    const serverService = { receipt: { port: 65321 }, manager: { perform: async action => {
      calls.push(action);
      return { status: action === 'status' ? 'active' : action === 'start' ? 'started' : 'stopped', mainPid: 1234 };
    } } };
    const started = await core.startCoreComponents(['engine'], { serverService, lifecycle: 'desktop' });
    assert.equal(started[0].status, 'already_running');
    assert.equal(started[0].manager, 'systemd');
    assert.deepEqual(core.desktopOwnedIdentities(started), {});
    const before = calls.length;
    assert.equal((await core.stopCoreComponents(['engine'], { serverService, expectedIdentities: {} }))[0].status, 'not_owned');
    assert.equal(calls.length, before);
    assert.equal((await core.statusCoreComponents(['engine'], { serverService }))[0].manager, 'systemd');
    assert.equal((await core.stopCoreComponents(['engine'], { serverService }))[0].status, 'stopped');
    assert.equal(fs.existsSync(path.join(process.env.V8_AGENT_OS_HOME, 'runtime/cli/processes.json')), false);
    console.log(JSON.stringify(calls));
  `, root);
  assert.deepEqual(JSON.parse(result), ["status", "start", "status", "stop"]);
});
