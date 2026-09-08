import assert from "node:assert/strict";
import fs from "node:fs";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { setTimeout as delay } from "node:timers/promises";

import { isPidAlive } from "../../src/process_state.mjs";

if (!process.argv.includes("--live") || process.platform !== "linux") {
  console.error("Requires --live on Linux; creates and reaps only a dedicated local test child. No model or network calls.");
  process.exit(2);
}

const source = [
  "import os, sys",
  "pid = os.fork()",
  "if pid == 0: os._exit(0)",
  "print(pid, flush=True)",
  "sys.stdin.readline()",
  "os.waitpid(pid, 0)",
].join("\n");
const parent = spawn("python3", ["-u", "-c", source], { stdio: ["pipe", "pipe", "pipe"] });
const exited = once(parent, "exit");
let errorText = "";
parent.stderr.on("data", (data) => { errorText += data.toString(); });
try {
  const [chunk] = await Promise.race([
    once(parent.stdout, "data"),
    exited.then(() => { throw new Error(`Fixture exited before reporting its child: ${errorText}`); }),
    delay(5_000).then(() => { throw new Error("Fixture startup deadline exceeded"); }),
  ]);
  const childPid = Number(String(chunk).trim());
  assert.ok(Number.isSafeInteger(childPid) && childPid > 1 && childPid !== process.pid);
  let state = "";
  const deadline = Date.now() + 2_000;
  while (Date.now() < deadline) {
    const stat = fs.readFileSync(`/proc/${childPid}/stat`, "utf8");
    state = stat.slice(stat.lastIndexOf(")") + 1).trimStart().split(/\s+/, 1)[0];
    if (state === "Z") break;
    await delay(10);
  }
  assert.equal(state, "Z");
  assert.equal(process.kill(childPid, 0), true, "The old presence-only predicate must reproduce the false positive");
  assert.equal(isPidAlive(childPid), false);
  assert.equal(isPidAlive(parent.pid), true, "The still-running owned parent must remain alive");
  parent.stdin.end("reap\n");
  const [code] = await exited;
  assert.equal(code, 0, errorText);
  assert.equal(isPidAlive(childPid), false);
  assert.equal(isPidAlive(parent.pid), false);
  console.log(JSON.stringify({ evidenceMode: "real-linux-process-integration-not-desktop-tray-acceptance",
    platform: process.platform, node: process.version, zombieFalsePositiveReproduced: true,
    zombieCorrectlyStopped: true, activeParentPreserved: true, ownedProcessesReaped: true }));
} finally {
  if (parent.exitCode === null) {
    parent.stdin.end("reap\n");
    await Promise.race([exited, delay(2_000)]);
    if (parent.exitCode === null) parent.kill("SIGTERM");
  }
}
