import assert from "node:assert/strict";
import test from "node:test";
import { createEventSender, postJson } from "../../scripts/rpa_playwright_inspector_sidecar.mjs";
import http from "node:http";

const request = () => ({ oneTimeToken: "fixture-token", generation: "fixture-generation", callback: { url: "http://fixture.invalid/events" } });

test("a lost ready ACK retries the identical event before a candidate uses the next sequence", async () => {
  const bodies = [];
  const sender = createEventSender(request(), async (_url, body) => {
    bodies.push(structuredClone(body));
    return { ok: bodies.length > 1 };
  });
  assert.equal((await sender("ready", { sidecar: { status: "attached" } })).ok, false);
  assert.equal((await sender("candidate", { eventId: "candidate-1", candidate: { name: "fixture" } })).ok, true);
  assert.deepEqual(bodies[0], bodies[1]);
  assert.equal(bodies[0].seq, 1);
  assert.equal(bodies[2].seq, 2);
  assert.notEqual(bodies[0].eventId, bodies[2].eventId);
});

test("concurrent candidate and heartbeat writes remain single flight", async () => {
  let release;
  const blocked = new Promise(resolve => { release = resolve; });
  let active = 0, maximum = 0;
  const bodies = [];
  const sender = createEventSender(request(), async (_url, body) => {
    bodies.push(body); active++; maximum = Math.max(maximum, active);
    await blocked; active--;
    return { ok: true };
  });
  const first = sender("candidate", { eventId: "first" });
  const second = sender("heartbeat");
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(bodies.length, 1);
  release();
  assert.equal((await first).ok, true); assert.equal((await second).ok, true);
  assert.equal(maximum, 1);
  assert.deepEqual(bodies.map(body => body.seq), [1, 2]);
});

test("failed candidate retains its original payload and sequence across a retry", async () => {
  const bodies = [];
  const sender = createEventSender(request(), async (_url, body) => {
    bodies.push(structuredClone(body));
    return { ok: bodies.length > 1 };
  });
  const payload = { eventId: "pending", candidate: { name: "first" } };
  assert.equal((await sender("candidate", payload)).ok, false);
  payload.candidate.name = "changed-after-send";
  assert.equal((await sender("candidate", payload)).ok, true);
  assert.deepEqual(bodies[0], bodies[1]);
});

test("revocation stops pending retries and prevents later events", async () => {
  let count = 0;
  const sender = createEventSender(request(), async () => { count++; return { ok: false, statusCode: 403 }; });
  assert.equal((await sender("candidate", { eventId: "pending" })).ok, false);
  assert.equal((await sender("heartbeat")).ok, false);
  assert.equal(count, 1);
});

test("HTTP 200 is not an event ACK unless the body explicitly confirms it", async () => {
  let response = "{}";
  const server = http.createServer((req, res) => { req.resume(); res.writeHead(200, { "content-type": "application/json" }); res.end(response); });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  try {
    const url = `http://127.0.0.1:${server.address().port}/events`;
    assert.equal((await postJson(url, {})).ok, false);
    response = '{"ok":true,"duplicate":true}';
    assert.equal((await postJson(url, {})).ok, true);
  } finally { await new Promise(resolve => server.close(resolve)); }
});
