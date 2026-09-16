import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const bin = fileURLToPath(new URL("../bin/v8os.mjs", import.meta.url));
async function fixture(t, mode = "success") {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-cli-authority-"));
  const workspace = path.join(root, "workspace-new");
  const secret = randomBytes(32).toString("hex");
  const observed = [];
  let foreignCalls = 0;
  const foreign = http.createServer((_req, res) => { foreignCalls++; res.end("{}"); });
  await new Promise((resolve) => foreign.listen(0, "127.0.0.1", resolve));
  const foreignOrigin = `http://127.0.0.1:${foreign.address().port}`;
  const server = http.createServer(async (req, res) => {
    observed.push({ method: req.method, path: req.url, proofMatches: req.headers["x-v8-agent-os-secret"] === secret, authorizationAbsent: !req.headers.authorization });
    let raw = "";
    for await (const chunk of req) raw += chunk;
    if (mode === "redirect") return res.writeHead(307, { location: `${foreignOrigin}/capture` }).end();
    if (mode === "unauthorized") return res.writeHead(401, { "content-type": "application/json" }).end('{"error":"unknown_write_outcome"}');
    if (req.method !== "POST" || req.url !== "/v1/projects") return res.writeHead(404).end();
    const body = JSON.parse(raw);
    res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify({ ...body, id: "project-fixture", workspaceId: "workspace-fixture" }));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const config = { systemBase: { bridge: { engineBaseUrl: origin, internalSecret: secret } }, workspace: { path: path.join(root, "previous"), workspaceTrustState: "trusted", projectId: "previous-project" } };
  fs.writeFileSync(path.join(root, "config.json"), JSON.stringify(config));
  const oldSessionFile = path.join(root, "runtime", "cli", "local-session.json");
  fs.mkdirSync(path.dirname(oldSessionFile), { recursive: true });
  const oldSession = JSON.stringify({ engineOrigin: origin, accessToken: `header.${Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url")}.old`, refreshToken: "synthetic-old-refresh" });
  fs.writeFileSync(oldSessionFile, oldSession);
  t.after(async () => {
    server.closeAllConnections(); foreign.closeAllConnections();
    await Promise.all([new Promise((resolve) => server.close(resolve)), new Promise((resolve) => foreign.close(resolve))]);
    fs.rmSync(root, { recursive: true, force: true });
  });
  async function run(args = [], override = "") {
    const child = spawn(process.execPath, [bin, "workspace", "create", workspace, "--select", "--json", ...args], {
      env: { ...process.env, V8_AGENT_OS_HOME: root, V8OS_ENGINE_URL: override, V8_AGENT_OS_ENGINE_URL: "" },
      windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "", stderr = "";
    child.stdout.on("data", (data) => { stdout += data; });
    child.stderr.on("data", (data) => { stderr += data; });
    const code = await new Promise((resolve, reject) => { child.once("error", reject); child.once("exit", resolve); });
    assert.equal(stdout.includes(secret) || stderr.includes(secret), false, "service proof must not enter CLI output");
    assert.equal(fs.readFileSync(oldSessionFile, "utf8"), oldSession, "obsolete token file is not read/refreshed/rewritten");
    return { code, stdout, stderr, config: JSON.parse(fs.readFileSync(path.join(root, "config.json"), "utf8")) };
  }
  return { run, observed, foreignOrigin, foreignCalls: () => foreignCalls, workspace, config };
}

test("workspace create/select reaches canonical Engine with its service proof and no old token", async (t) => {
  const f = await fixture(t);
  const result = await f.run();
  assert.equal(result.code, 0, result.stderr);
  const output = JSON.parse(result.stdout);
  assert.equal(output.trust.registered, true);
  assert.equal(result.config.workspace.path, f.workspace);
  assert.equal(result.config.workspace.projectId, "project-fixture");
  assert.equal(result.config.workspace.workspaceTrustState, "trusted");
  assert.deepEqual(f.observed, [{ method: "POST", path: "/v1/projects", proofMatches: true, authorizationAbsent: true }]);
  assert.equal(f.foreignCalls(), 0);
});

test("environment origin cannot redirect a local proof or obsolete token to another Engine", async (t) => {
  const f = await fixture(t);
  const result = await f.run([], f.foreignOrigin);
  assert.equal(result.code, 1);
  assert.match(result.stderr, /未发送凭据/);
  assert.equal(f.foreignCalls(), 0);
  assert.equal(f.observed.length, 0);
  assert.equal(result.config.workspace.path, f.config.workspace.path);
});

test("redirect does not forward service proof and failed trust does not select workspace", async (t) => {
  const f = await fixture(t, "redirect");
  const result = await f.run();
  assert.equal(result.code, 1);
  assert.equal(f.foreignCalls(), 0);
  assert.equal(f.observed.length, 1);
  assert.equal(result.config.workspace.path, f.config.workspace.path);
});

test("a write returning 401 is not replayed or repaired by minting another token", async (t) => {
  const f = await fixture(t, "unauthorized");
  const result = await f.run();
  assert.equal(result.code, 1);
  assert.match(result.stderr, /401/);
  assert.deepEqual(f.observed.map(({ method, path }) => `${method} ${path}`), ["POST /v1/projects"]);
  assert.equal(f.foreignCalls(), 0);
  assert.equal(result.config.workspace.path, f.config.workspace.path);
});
