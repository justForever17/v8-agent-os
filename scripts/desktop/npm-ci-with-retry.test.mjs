import assert from "node:assert/strict";
import test from "node:test";

import { runNpmCiWithRetry } from "./npm-ci-with-retry.mjs";

function result(status, stderr = "") {
  return { status, stdout: "", stderr, error: null };
}

test("npm ci retries a bounded network reset and then succeeds", async () => {
  const responses = [result(1, "npm error code ECONNRESET\nnpm error network aborted"), result(0)];
  const delays = [];
  const calls = [];

  const status = await runNpmCiWithRetry({
    platform: "linux",
    spawn: (command, args) => {
      calls.push({ command, args });
      return responses.shift();
    },
    sleep: async (delayMs) => delays.push(delayMs),
  });

  assert.equal(status, 0);
  assert.equal(calls.length, 2);
  assert.deepEqual(delays, [10_000]);
  assert.deepEqual(calls[0].args, ["ci", "--include=dev", "--workspaces=false"]);
});

test("npm ci uses a fixed cmd.exe command on Windows", async () => {
  const calls = [];
  const status = await runNpmCiWithRetry({
    platform: "win32",
    comspec: "cmd.exe",
    spawn: (command, args) => {
      calls.push({ command, args });
      return result(0);
    },
  });

  assert.equal(status, 0);
  assert.deepEqual(calls, [
    {
      command: "cmd.exe",
      args: ["/d", "/s", "/c", "npm ci --include=dev --workspaces=false"],
    },
  ]);
});

test("npm ci does not retry a deterministic dependency failure", async () => {
  let calls = 0;
  const status = await runNpmCiWithRetry({
    spawn: () => {
      calls += 1;
      return result(1, "npm error code EUSAGE\nnpm ci requires package-lock.json to match");
    },
    sleep: async () => assert.fail("deterministic failure must not sleep"),
  });

  assert.equal(status, 1);
  assert.equal(calls, 1);
});

test("npm ci stops after three retryable failures", async () => {
  let calls = 0;
  const delays = [];
  const status = await runNpmCiWithRetry({
    spawn: () => {
      calls += 1;
      return result(1, "npm error code ETIMEDOUT");
    },
    sleep: async (delayMs) => delays.push(delayMs),
  });

  assert.equal(status, 1);
  assert.equal(calls, 3);
  assert.deepEqual(delays, [10_000, 30_000]);
});
