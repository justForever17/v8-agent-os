import assert from "node:assert/strict";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { DEFAULT_PORTS } from "../src/paths.mjs";
import {
  WEB_FALLBACK_PORT_END,
  WEB_FALLBACK_PORT_START,
  readRuntimePortProfile,
  resolveRuntimePorts,
} from "../src/runtime_ports.mjs";

function testContext(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-runtime-ports-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return {
    profilePath: path.join(root, "runtime", "cli", "ports.json"),
    withLease: (callback) => callback(),
  };
}

test("runtime ports keep the governed defaults when Web 9527 is free", async (t) => {
  const context = testContext(t);
  const profile = await resolveRuntimePorts({
    ...context,
    probePort: async () => false,
  });
  assert.deepEqual(profile.ports, {
    engine: DEFAULT_PORTS.engine,
    admin: DEFAULT_PORTS.admin,
    web: DEFAULT_PORTS.web,
  });
  assert.equal(profile.reason, "default_available");
  assert.deepEqual(readRuntimePortProfile(context).ports, profile.ports);
});

test("runtime ports avoid an externally occupied Web 9527 without moving Engine or Admin", async (t) => {
  const context = testContext(t);
  const profile = await resolveRuntimePorts({
    ...context,
    probePort: async (port) => port === DEFAULT_PORTS.web,
  });
  assert.deepEqual(profile.ports, {
    engine: DEFAULT_PORTS.engine,
    admin: DEFAULT_PORTS.admin,
    web: WEB_FALLBACK_PORT_START,
  });
  assert.equal(profile.reason, "default_conflict");
});

test("runtime ports detect a real loopback listener on Web 9527", async (t) => {
  const context = testContext(t);
  const blocker = net.createServer((socket) => socket.destroy());
  let ownsBlocker = false;
  try {
    await new Promise((resolve, reject) => {
      blocker.once("error", (error) => {
        if (error?.code === "EADDRINUSE") resolve();
        else reject(error);
      });
      blocker.listen({ host: "127.0.0.1", port: DEFAULT_PORTS.web, exclusive: true }, () => {
        ownsBlocker = true;
        resolve();
      });
    });
    const profile = await resolveRuntimePorts(context);
    assert.notEqual(profile.ports.web, DEFAULT_PORTS.web);
    assert.ok(profile.ports.web >= WEB_FALLBACK_PORT_START && profile.ports.web <= WEB_FALLBACK_PORT_END);
  } finally {
    if (ownsBlocker) await new Promise((resolve) => blocker.close(resolve));
  }
});

test("runtime ports reuse an available persisted fallback across Shell restarts", async (t) => {
  const context = testContext(t);
  const first = await resolveRuntimePorts({
    ...context,
    probePort: async (port) => port === DEFAULT_PORTS.web,
  });
  const second = await resolveRuntimePorts({
    ...context,
    probePort: async () => false,
  });
  assert.equal(first.ports.web, WEB_FALLBACK_PORT_START);
  assert.equal(second.ports.web, WEB_FALLBACK_PORT_START);
  assert.equal(second.reason, "persisted_fallback_available");
  assert.equal(second.reservationExpiresAt, null);
});

test("runtime port selection serializes concurrent callers through the shared lease", async (t) => {
  const context = testContext(t);
  delete context.withLease;
  let activeProbes = 0;
  let maximumActiveProbes = 0;
  const probePort = async () => {
    activeProbes += 1;
    maximumActiveProbes = Math.max(maximumActiveProbes, activeProbes);
    await new Promise((resolve) => setTimeout(resolve, 20));
    activeProbes -= 1;
    return false;
  };
  const [first, second] = await Promise.all([
    resolveRuntimePorts({ ...context, probePort }),
    resolveRuntimePorts({ ...context, probePort }),
  ]);
  assert.equal(maximumActiveProbes, 1);
  assert.deepEqual(first.ports, second.ports);
  assert.equal(first.reason, "default_available");
  assert.equal(second.reason, "default_available");
});

test("runtime ports reuse only a strongly verified managed Web port", async (t) => {
  const context = testContext(t);
  const managed = await resolveRuntimePorts({
    ...context,
    verifiedManagedWebPort: WEB_FALLBACK_PORT_START,
    probePort: async () => true,
  });
  assert.equal(managed.ports.web, WEB_FALLBACK_PORT_START);
  assert.equal(managed.reason, "managed_process");

  await assert.rejects(
    resolveRuntimePorts({
      ...context,
      verifiedManagedWebPort: null,
      probePort: async () => true,
    }),
    (error) => error?.code === "V8OS_WEB_PORT_RANGE_EXHAUSTED",
  );
});

test("runtime ports fail closed when the governed Web range is exhausted", async (t) => {
  const context = testContext(t);
  await assert.rejects(
    resolveRuntimePorts({ ...context, probePort: async () => true }),
    (error) => error?.code === "V8OS_WEB_PORT_RANGE_EXHAUSTED",
  );
  assert.equal(fs.existsSync(context.profilePath), false);
  assert.equal(WEB_FALLBACK_PORT_END - WEB_FALLBACK_PORT_START + 1, 20);
});

test("runtime ports replace a malformed profile with a validated profile", async (t) => {
  const context = testContext(t);
  fs.mkdirSync(path.dirname(context.profilePath), { recursive: true });
  fs.writeFileSync(context.profilePath, JSON.stringify({ version: 1, ports: { web: 80 } }), "utf8");
  assert.equal(readRuntimePortProfile(context), null);
  const profile = await resolveRuntimePorts({ ...context, probePort: async () => false });
  assert.equal(profile.ports.web, DEFAULT_PORTS.web);
  assert.equal(readRuntimePortProfile(context).policy, "web-fallback-v1");
});
