import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import test from "node:test";

import {
  evaluateStageBudgets,
  parseOptions,
  validateProbeResponse,
  waitForHttpProbe,
} from "./scripts/run_v8os_cli_cold_start_smoke.mjs";

const coldStartHarnessSource = fs.readFileSync(
  new URL("./scripts/run_v8os_cli_cold_start_smoke.mjs", import.meta.url),
  "utf8",
);

test("cold-start options default to the production service mode without implicit budgets", () => {
  assert.deepEqual(parseOptions([]), {
    help: false,
    json: false,
    mode: "start",
    timeoutMs: 120_000,
    budgets: {
      engineReadyMs: null,
      adminReadyMs: null,
      webReadyMs: null,
      allReadyMs: null,
    },
  });
  assert.deepEqual(parseOptions([
    "--mode", "dev",
    "--timeout-ms", "9000",
    "--engine-ready-budget-ms", "4000",
    "--all-ready-budget-ms", "8000",
    "--json",
  ]), {
    help: false,
    json: true,
    mode: "dev",
    timeoutMs: 9_000,
    budgets: {
      engineReadyMs: 4_000,
      adminReadyMs: null,
      webReadyMs: null,
      allReadyMs: 8_000,
    },
  });
  assert.throws(() => parseOptions(["--mode", "invalid"]), /dev or start/);
  assert.throws(() => parseOptions(["--all-ready-budget-ms", "0"]), /positive integer/);
  assert.throws(() => parseOptions(["--unknown"]), /Unknown option/);
});

test("cold-start subprocesses stay hidden and the performance clock excludes preflight", () => {
  assert.match(coldStartHarnessSource, /spawnSync\(process\.execPath[\s\S]*?windowsHide:\s*true/);
  const preflightIndex = coldStartHarnessSource.indexOf("const preflightStartedAtMs = Date.now()");
  const startupOriginIndex = coldStartHarnessSource.indexOf("const originMs = Date.now()");
  const startCommandIndex = coldStartHarnessSource.indexOf('run(["start", "--mode"');
  assert.ok(preflightIndex >= 0 && preflightIndex < startupOriginIndex);
  assert.ok(startupOriginIndex >= 0 && startupOriginIndex < startCommandIndex);
  assert.match(coldStartHarnessSource, /preflight:\s*\{[\s\S]*?durationMs:/);
  assert.match(coldStartHarnessSource, /startupStartedAt:/);
});

test("readiness contracts reject a generic 200 response without the expected surface marker", () => {
  const urls = {
    engine: "http://127.0.0.1:9530/readyz",
    admin: "http://127.0.0.1:9528/login",
    web: "http://127.0.0.1:9527/chat",
  };
  assert.deepEqual(validateProbeResponse("engine", {
    ok: true,
    status: 200,
    contentType: "application/json; charset=utf-8",
    body: JSON.stringify({ status: "ok", service: "v8-agent-os-engine", ready: true, startup: { readyMs: 10 } }),
    expectedUrl: urls.engine,
    responseUrl: urls.engine,
  }), {
    ok: true,
    marker: "v8-agent-os-engine:ready",
    startup: { readyMs: 10 },
  });
  assert.equal(validateProbeResponse("engine", {
    ok: true,
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ status: "ok", service: "wrong-engine", ready: true }),
    expectedUrl: urls.engine,
    responseUrl: urls.engine,
  }).ok, false);
  assert.equal(validateProbeResponse("admin", {
    ok: true,
    status: 200,
    body: '<html><input id="login"></html>',
    expectedUrl: urls.admin,
    responseUrl: urls.admin,
  }).ok, true);
  assert.equal(validateProbeResponse("admin", {
    ok: true,
    status: 200,
    body: "<html>loading</html>",
    expectedUrl: urls.admin,
    responseUrl: urls.admin,
  }).ok, false);
  assert.equal(validateProbeResponse("web", {
    ok: true,
    status: 200,
    body: "<title>V8 Agent OS - AI Assistant</title>",
    expectedUrl: urls.web,
    responseUrl: urls.web,
  }).ok, true);
  assert.equal(validateProbeResponse("web", {
    ok: true,
    status: 200,
    body: "<title>V8 Agent OS - AI Assistant</title>",
    expectedUrl: urls.web,
    responseUrl: "https://example.test/chat",
  }).reason, "web_redirect_origin_mismatch");
  assert.equal(validateProbeResponse("web", {
    ok: true,
    status: 200,
    body: "<title>V8 Agent OS - AI Assistant</title>",
    expectedUrl: urls.web,
    responseUrl: "http://127.0.0.1:9527/error",
  }).reason, "web_response_path_mismatch");
  assert.equal(validateProbeResponse("web", { ok: false, status: 503, body: "" }).reason, "http_503");
});

test("HTTP readiness retries non-2xx responses and records elapsed stage data", async (t) => {
  let attempts = 0;
  const server = http.createServer((_req, res) => {
    attempts += 1;
    if (attempts < 3) {
      res.writeHead(503, { "content-type": "text/plain" });
      res.end("starting");
      return;
    }
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ status: "ok", service: "v8-agent-os-engine", ready: true }));
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  t.after(() => new Promise((resolve) => server.close(resolve)));

  const originMs = Date.now();
  const result = await waitForHttpProbe({
    id: "engine_ready",
    label: "Engine /readyz",
    kind: "engine",
    url: `http://127.0.0.1:${server.address().port}/readyz`,
  }, {
    originMs,
    timeoutMs: 2_000,
    intervalMs: 10,
  });

  assert.equal(result.ok, true);
  assert.equal(result.attempts, 3);
  assert.equal(result.status, 200);
  assert.equal(result.marker, "v8-agent-os-engine:ready");
  assert.ok(result.elapsedMs >= 0);
});

test("configured stage budgets are explicit and fail closed when a stage is absent", () => {
  const checks = evaluateStageBudgets([
    { id: "engine_ready", ok: true, elapsedMs: 3_500 },
    { id: "all_ready", ok: true, elapsedMs: 8_100 },
  ], {
    engineReadyMs: 4_000,
    adminReadyMs: 5_000,
    webReadyMs: null,
    allReadyMs: 8_000,
  });
  assert.deepEqual(checks, [
    {
      budgetKey: "engineReadyMs",
      stageId: "engine_ready",
      budgetMs: 4_000,
      elapsedMs: 3_500,
      ok: true,
    },
    {
      budgetKey: "adminReadyMs",
      stageId: "admin_http_ready",
      budgetMs: 5_000,
      elapsedMs: null,
      ok: false,
    },
    {
      budgetKey: "allReadyMs",
      stageId: "all_ready",
      budgetMs: 8_000,
      elapsedMs: 8_100,
      ok: false,
    },
  ]);
});
