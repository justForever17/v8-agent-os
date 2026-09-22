#!/usr/bin/env node
// Actual Engine lifecycle smoke; uses a fresh state root and no model provider.
import assert from "node:assert/strict";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";

if (!process.argv.includes("--live")) throw new Error("Pass --live to start an actual isolated Engine");
const stateIndex = process.argv.indexOf("--state");
const stateRoot = stateIndex < 0 ? fs.mkdtempSync(path.join(os.tmpdir(), "v8os-core-live-")) : path.resolve(process.argv[stateIndex + 1]);
const realState = path.join(os.homedir(), ".v8-agent-os");
if (stateRoot === realState || stateRoot.startsWith(`${realState}${path.sep}`)
  || fs.existsSync(path.join(stateRoot, "config.json"))) throw new Error("Use a new isolated state root");
fs.mkdirSync(stateRoot, { recursive: true });
const portReservation = net.createServer();
await new Promise(resolve => portReservation.listen(0, "127.0.0.1", resolve));
const port = portReservation.address().port;
await new Promise(resolve => portReservation.close(resolve));
fs.writeFileSync(path.join(stateRoot, "config.json"), JSON.stringify({ systemBase: { bridge: { engineBaseUrl: `http://127.0.0.1:${port}/v1` } } }));
process.env.V8_AGENT_OS_HOME = stateRoot;
process.env.ENGINE_STARTUP_PROFILE = "minimal";
process.env.ENGINE_INSTALL_PROFILE = "minimal";
process.env.ENGINE_RELOAD = "0";
const core = await import("../../src/core_control.mjs");
const evidence = { layer: "actual-engine-local", stateRoot, port, results: [] };
let started;
try {
  started = await core.startCoreComponents(["engine"], { lifecycle: "daemon" });
  assert.equal(started[0].status, "started");
  const ready = await core.waitForCoreReadiness();
  assert.equal(ready.ready, true);
  const attached = await core.startCoreComponents(["engine"], { lifecycle: "desktop" });
  assert.equal(attached[0].status, "already_running");
  const receipt = core.desktopOwnedIdentities(attached);
  assert.deepEqual(receipt, {});
  const detachedStop = await core.stopCoreComponents(["engine"], { expectedIdentities: receipt });
  assert.equal(detachedStop[0].status, "not_owned");
  assert.equal((await core.statusCoreComponents(["engine"]))[0].pidAlive, true);
  assert.equal((await core.waitForCoreReadiness({ timeoutMs: 10000 })).instanceId, ready.instanceId);
  evidence.results.push({ phase: "daemon_attach", daemonSurvivedDesktopExit: true, sameInstance: true });
  assert.equal((await core.stopCoreComponents(["engine"]))[0].status, "stopped");
  started = await core.startCoreComponents(["engine"], { lifecycle: "desktop" });
  assert.equal(started[0].status, "started");
  assert.equal((await core.waitForCoreReadiness()).instanceId, ready.instanceId);
  const owned = core.desktopOwnedIdentities(started);
  assert.ok(owned.engine?.launchId);
  assert.equal((await core.stopCoreComponents(["engine"], { expectedIdentities: owned }))[0].status, "stopped");
  const final = (await core.statusCoreComponents(["engine"]))[0];
  assert.equal(final.pidAlive, false);
  assert.equal(final.portOpen, false);
  evidence.results.push({ phase: "desktop_owner", ownedEngineStopped: true, portClosed: true, stateIdentityPreserved: true });
  evidence.ok = true;
} finally {
  if (started?.[0]?.recordIdentity) {
    await core.stopCoreComponents(["engine"], { expectedIdentities: { engine: started[0].recordIdentity } });
  }
  fs.writeFileSync(path.join(stateRoot, "lifecycle-evidence.json"), JSON.stringify(evidence, null, 2));
}
console.log(JSON.stringify(evidence, null, 2));
