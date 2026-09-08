import assert from "node:assert/strict";
import fs from "node:fs";
import { test, mock } from "node:test";

import { isPidAlive } from "../src/process_state.mjs";

test("Linux liveness distinguishes an exited zombie from an active process", () => {
  const platform = Object.getOwnPropertyDescriptor(process, "platform");
  Object.defineProperty(process, "platform", { value: "linux" });
  const kill = mock.method(process, "kill", () => true);
  let state = "S";
  const read = mock.method(fs, "readFileSync", (file) => {
    assert.equal(file, "/proc/12345/stat");
    return `12345 (node worker (ready)) ${state} 1 12345 12345 0`;
  });
  try {
    for (const current of ["S", "R", "D", "T"]) {
      state = current;
      assert.equal(isPidAlive(12345), true);
    }
    for (const current of ["Z", "X", "x"]) {
      state = current;
      assert.equal(isPidAlive(12345), false);
    }
  } finally {
    read.mock.restore();
    kill.mock.restore();
    Object.defineProperty(process, "platform", platform);
  }
});

test("permission denial and unreadable process state never claim a successful stop", () => {
  const platform = Object.getOwnPropertyDescriptor(process, "platform");
  Object.defineProperty(process, "platform", { value: "linux" });
  let probeError = "EPERM";
  let readError = "ENOENT";
  const kill = mock.method(process, "kill", () => {
    if (probeError) throw Object.assign(new Error("probe"), { code: probeError });
    return true;
  });
  const read = mock.method(fs, "readFileSync", () => { throw Object.assign(new Error("stat"), { code: readError }); });
  try {
    assert.equal(isPidAlive(12345), true);
    assert.equal(read.mock.callCount(), 0);
    probeError = "ESRCH";
    assert.equal(isPidAlive(12345), false);
    probeError = "";
    readError = "EACCES";
    assert.equal(isPidAlive(12345), true);
    readError = "ENOENT";
    assert.equal(isPidAlive(12345), false);
    assert.equal(isPidAlive(-1), false);
    assert.equal(isPidAlive("not-a-pid"), false);
  } finally {
    read.mock.restore();
    kill.mock.restore();
    Object.defineProperty(process, "platform", platform);
  }
});
