#!/usr/bin/env node
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const currentFile = fileURLToPath(import.meta.url);
const cliRoot = path.resolve(path.dirname(currentFile), "..", "..");
const repoRoot = path.resolve(cliRoot, "..", "..");
const bin = path.join(cliRoot, "bin", "v8os.mjs");

const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-cli-daily-home-"));
const workspaceRoot = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-cli-daily-workspace-"));
const workspacePath = path.join(workspaceRoot, "main");
const reportDir = path.join(stateRoot, "reports", "cli_daily");
const reportPath = path.join(reportDir, "v8os_cli_daily_entry_smoke.json");

function base64UrlJson(value) {
  return Buffer.from(JSON.stringify(value)).toString("base64url");
}

function fakeAccessToken() {
  return `${base64UrlJson({ alg: "none", typ: "JWT" })}.${base64UrlJson({ exp: Math.floor(Date.now() / 1000) + 3600 })}.signature`;
}

function sendJson(res, status, data) {
  const text = JSON.stringify(data);
  res.writeHead(status, {
    "content-type": "application/json",
    "content-length": Buffer.byteLength(text),
  });
  res.end(text);
}

function readBody(req) {
  return new Promise((resolve) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      const text = Buffer.concat(chunks).toString("utf8");
      if (!text.trim()) {
        resolve(null);
        return;
      }
      try {
        resolve(JSON.parse(text));
      } catch {
        resolve({ rawText: text });
      }
    });
  });
}

function createMockAdmin() {
  const requests = [];
  const session = {
    id: "session-daily",
    title: "CLI daily session",
    status: "active",
    projectId: "daily",
    workspacePath,
    updatedAt: "2026-07-08T00:00:00.000Z",
    askUserInteractions: [
      {
        id: "ask-1",
        question: "Need a short answer",
        status: "pending",
      },
    ],
  };
  const approvals = [
    { id: "appr-1", title: "Allow daily smoke", status: "pending", sessionId: session.id },
    { id: "appr-2", title: "Reject daily smoke", status: "pending", sessionId: session.id },
  ];

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url || "/", "http://127.0.0.1");
    const body = await readBody(req);
    requests.push({ method: req.method, pathname: url.pathname, search: url.search, body });

    if (req.method === "POST" && url.pathname === "/api/client/auth/local-session") {
      sendJson(res, 200, {
        accessToken: fakeAccessToken(),
        accessTokenExpiresAt: new Date(Date.now() + 3600_000).toISOString(),
        refreshToken: "refresh-token",
        refreshTokenExpiresAt: new Date(Date.now() + 7200_000).toISOString(),
      });
      return;
    }

    if (req.method === "GET" && url.pathname === "/api/client/conversations") {
      sendJson(res, 200, [session]);
      return;
    }

    if (req.method === "GET" && url.pathname === `/api/client/conversations/${session.id}`) {
      sendJson(res, 200, session);
      return;
    }

    if (req.method === "GET" && url.pathname === `/api/client/conversations/${session.id}/turns`) {
      sendJson(res, 200, {
        messages: [
          { role: "user", content: "hello" },
          { role: "assistant", content: "world" },
        ],
      });
      return;
    }

    if (req.method === "GET" && url.pathname === "/api/client/approvals") {
      sendJson(res, 200, { approvals });
      return;
    }

    if (req.method === "POST" && url.pathname === "/api/client/approvals/appr-1/approve") {
      sendJson(res, 200, { ok: true, id: "appr-1", status: "approved", note: body?.note || "" });
      return;
    }

    if (req.method === "POST" && url.pathname === "/api/client/approvals/appr-2/reject") {
      sendJson(res, 200, { ok: true, id: "appr-2", status: "rejected", reason: body?.reason || "" });
      return;
    }

    if (req.method === "POST" && url.pathname === "/api/client/ask-user/ask-1/respond") {
      sendJson(res, 200, { ok: true, id: "ask-1", answer: body?.answer || "" });
      return;
    }

    sendJson(res, 404, { error: `Unhandled mock route: ${req.method} ${url.pathname}` });
  });

  return { server, requests };
}

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve(server.address().port));
  });
}

function close(server) {
  return new Promise((resolve) => server.close(resolve));
}

function parseJson(stdout) {
  return JSON.parse(String(stdout || "").trim());
}

function runCli(args, env, steps) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [bin, ...args], {
      cwd: repoRoot,
      env,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error(`Timed out: v8os ${args.join(" ")}`));
    }, 20_000);
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.on("close", (status) => {
      clearTimeout(timer);
      const step = {
        command: `v8os ${args.join(" ")}`,
        status,
        stdout,
        stderr,
      };
      steps.push(step);
      try {
        assert.equal(status, 0, stderr || stdout);
        resolve(step);
      } catch (error) {
        reject(error);
      }
    });
  });
}

async function main() {
  fs.mkdirSync(reportDir, { recursive: true });
  const { server, requests } = createMockAdmin();
  const steps = [];
  const report = {
    createdAt: new Date().toISOString(),
    stateRoot,
    workspacePath,
    steps,
    requests,
    result: null,
  };

  try {
    const port = await listen(server);
    const env = {
      ...process.env,
      V8_AGENT_OS_HOME: stateRoot,
      V8_REPO_ROOT: repoRoot,
      V8OS_ADMIN_URL: `http://127.0.0.1:${port}`,
      NO_COLOR: "1",
    };

    const sessions = parseJson((await runCli(["sessions", "list", "--json", "--limit", "5"], env, steps)).stdout);
    assert.equal(sessions[0]?.id, "session-daily");

    const sessionDetail = parseJson((await runCli(["sessions", "show", "session-daily", "--json"], env, steps)).stdout);
    assert.equal(sessionDetail.id, "session-daily");

    const turns = parseJson((await runCli(["sessions", "turns", "session-daily", "--limit", "2", "--json"], env, steps)).stdout);
    assert.equal(turns.messages.length, 2);

    const inbox = parseJson((await runCli(["inbox", "list", "--json", "--limit", "5"], env, steps)).stdout);
    assert.deepEqual(inbox.map((item) => item.id).sort(), ["appr-1", "appr-2", "ask-1"]);

    const approved = parseJson((await runCli(["inbox", "approve", "appr-1", "--reason", "ok", "--json"], env, steps)).stdout);
    assert.equal(approved.status, "approved");

    const rejected = parseJson((await runCli(["inbox", "reject", "appr-2", "--reason", "no", "--json"], env, steps)).stdout);
    assert.equal(rejected.status, "rejected");

    const answered = parseJson((await runCli(["inbox", "answer", "ask-1", "--answer", "done", "--json"], env, steps)).stdout);
    assert.equal(answered.answer, "done");

    const created = parseJson((await runCli(["workspace", "create", workspacePath, "--select", "--json"], env, steps)).stdout);
    assert.equal(path.resolve(created.path), path.resolve(workspacePath));
    assert.equal(created.selected, true);

    const current = parseJson((await runCli(["workspace", "show", "--json"], env, steps)).stdout);
    assert.equal(path.resolve(current.path), path.resolve(workspacePath));

    const workspaceDoctor = parseJson((await runCli(["workspace", "doctor", "--json"], env, steps)).stdout);
    assert.equal(path.resolve(workspaceDoctor.path), path.resolve(workspacePath));
    assert.ok(workspaceDoctor.checks.some((check) => check.id === "agents_rules" && check.status === "ok"));

    assert.ok(requests.some((item) => item.pathname === "/api/client/auth/local-session"));
    assert.ok(requests.some((item) => item.pathname === "/api/client/conversations"));
    assert.ok(requests.some((item) => item.pathname === "/api/client/approvals"));
    assert.ok(requests.some((item) => item.pathname === "/api/client/ask-user/ask-1/respond"));

    report.result = { ok: true };
  } catch (error) {
    report.result = { ok: false, reason: error instanceof Error ? error.stack || error.message : String(error) };
    process.exitCode = 1;
  } finally {
    await close(server);
    fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
    console.log(`Report: ${reportPath}`);
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack || error.message : String(error));
  process.exitCode = 1;
});
