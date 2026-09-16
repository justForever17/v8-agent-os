import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const bin = fileURLToPath(new URL("../bin/v8os.mjs", import.meta.url));

// Real CLI process and HTTP transport; the fixture substitutes only Engine.
async function runChat(t, scenario, args = []) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-chat-completion-"));
  let submitted = 0;
  let reads = 0;
  const requests = [];
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    requests.push(`${req.method} ${url.pathname}`);
    let body;
    if (req.method === "POST" && url.pathname === "/v1/chat/submit") {
      submitted += 1;
      body = { runId: "run-current" };
    } else if (req.method === "GET" && url.pathname === "/v1/runs") {
      assert.equal(url.searchParams.get("session_id"), "session-fixture");
      body = { runs: [{ id: "run-current", status: scenario(reads).runState }] };
    } else if (req.method === "GET" && url.pathname === "/v1/sessions/session-fixture/turns") {
      // Match the native FastAPI Query ge=1/le=10 boundary, not a permissive
      // fixture which could hide production 422s forever.
      if (Number(url.searchParams.get("limit")) > 10) {
        res.writeHead(422, { "content-type": "application/json" }).end('{"detail":"limit must be at most 10"}');
        return;
      }
      if (submitted) reads += 1;
      if (scenario(reads).httpStatus) {
        res.writeHead(scenario(reads).httpStatus, { "content-type": "application/json" }).end('{"detail":"fixture transport failure"}');
        return;
      }
      body = { messages: submitted ? scenario(reads).messages : [] };
    } else {
      res.writeHead(404).end();
      return;
    }
    res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(body));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  fs.writeFileSync(path.join(root, "config.json"), JSON.stringify({ systemBase: { bridge: { engineBaseUrl: `http://127.0.0.1:${server.address().port}` } } }));
  t.after(async () => {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    fs.rmSync(root, { recursive: true, force: true });
  });
  const child = spawn(process.execPath, [bin, "chat", "fixture message", "--session", "session-fixture", ...(args.includes("--timeout") ? [] : ["--timeout", "5"]), ...args], {
    env: { ...process.env, V8_AGENT_OS_HOME: root }, windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
  });
  let stdout = "", stderr = "";
  child.stdout.on("data", (chunk) => { stdout += chunk; });
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  const guard = setTimeout(() => child.kill(), 10_000);
  const code = await new Promise((resolve, reject) => { child.once("error", reject); child.once("exit", resolve); });
  clearTimeout(guard);
  return { code, stdout, stderr, submitted, reads, requests };
}

const message = (state, content = "partial text", runId = "run-current") => ({ id: `message-${runId}`, role: "assistant", runId, state, content });

for (const state of ["failed", "cancelled", "degraded"]) {
  test(`chat does not report text from a ${state} run as success`, async (t) => {
    const result = await runChat(t, () => ({ runState: state, messages: [message(state)] }));
    assert.equal(result.code, 1);
    assert.match(result.stderr, new RegExp(`status: ${state}`));
    assert.match(result.stderr, /session: session-fixture; run: run-current/);
    assert.doesNotMatch(result.stdout, /partial text/);
    assert.equal(result.submitted, 1);
  });
}

test("chat waits beyond progress text until both run and transcript are complete", async (t) => {
  const result = await runChat(t, (reads) => reads < 2
    ? { runState: "running", messages: [message("completed", "intermediate delivery")] }
    : { runState: "completed", messages: [message("completed", "final verified answer")] });
  assert.equal(result.code, 0, result.stderr);
  assert.ok(result.reads >= 2);
  assert.match(result.stdout, /final verified answer/);
  assert.doesNotMatch(result.stdout, /intermediate delivery/);
  assert.equal(result.submitted, 1);
});

test("chat rejects a failed transcript even when the run status says completed", async (t) => {
  const result = await runChat(t, () => ({ runState: "completed", messages: [message("failed", "incomplete result")] }));
  assert.equal(result.code, 1);
  assert.match(result.stderr, /status: failed/);
});

test("chat cannot return an earlier completed delivery while its latest message is streaming", async (t) => {
  const result = await runChat(t, (reads) => ({ runState: "completed", messages: [
    message("completed", "earlier delivery"),
    { ...message(reads < 2 ? "streaming" : "completed", reads < 2 ? "partial final" : "complete final"), id: "latest-message" },
  ] }));
  assert.equal(result.code, 0, result.stderr);
  assert.ok(result.reads >= 2);
  assert.match(result.stdout, /complete final/);
  assert.doesNotMatch(result.stdout, /earlier delivery|partial final/);
});

test("a completed recovery uses the latest message without reviving an earlier failure", async (t) => {
  const result = await runChat(t, () => ({ runState: "completed", messages: [
    message("failed", "earlier failure"),
    { ...message("completed", "recovered final"), id: "recovered-message" },
  ] }));
  assert.equal(result.code, 0, result.stderr);
  assert.match(result.stdout, /recovered final/);
  assert.doesNotMatch(result.stdout, /earlier failure/);
});

test("chat cannot take a different run's final answer as its own", async (t) => {
  const result = await runChat(t, () => ({ runState: "completed", messages: [message("completed", "other answer", "run-other")] }), ["--timeout", "0.2"]);
  assert.equal(result.code, 1);
  assert.match(result.stderr, /status: unknown/);
  assert.doesNotMatch(result.stdout, /other answer/);
  assert.equal(result.submitted, 1);
});

test("chat timeout is unknown and nonzero without cancel or duplicate submission", async (t) => {
  const result = await runChat(t, () => ({ runState: "running", messages: [message("streaming")] }), ["--timeout", "0.2"]);
  assert.equal(result.code, 1);
  assert.match(result.stderr, /结果待确认/);
  assert.match(result.stderr, /status: unknown/);
  assert.equal(result.submitted, 1);
  assert.deepEqual(result.requests.filter((request) => request.startsWith("POST")), ["POST /v1/chat/submit"]);
});

test("explicit no-wait confirms submission only", async (t) => {
  const result = await runChat(t, () => ({ runState: "running", messages: [] }), ["--no-wait"]);
  assert.equal(result.code, 0);
  assert.match(result.stdout, /未等待最终结果/);
  assert.equal(result.reads, 0);
  assert.equal(result.submitted, 1);
});

test("invalid timeout fails before creating side effects", async (t) => {
  const result = await runChat(t, () => ({ runState: "running", messages: [] }), ["--timeout", "NaN"]);
  assert.equal(result.code, 1);
  assert.match(result.stderr, /--timeout/);
  assert.equal(result.submitted, 0);
});

test("a deterministic HTTP read error fails promptly with unknown outcome instead of repeated reads", async (t) => {
  const result = await runChat(t, () => ({ runState: "running", httpStatus: 422, messages: [] }));
  assert.equal(result.code, 1);
  assert.match(result.stderr, /422/);
  assert.match(result.stderr, /status: unknown/);
  assert.equal(result.reads, 1);
  assert.equal(result.submitted, 1);
});
