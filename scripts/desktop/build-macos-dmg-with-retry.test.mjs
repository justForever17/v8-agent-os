import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

import { isRetryableDmgDetachFailure, runMacDmgBuildWithRetry } from "./build-macos-dmg-with-retry.mjs";

// Public 2026.09.08.1 Intel job failed here, before any package startup smoke.
const BUSY = 'dmgbuild.core.DMGError: Unable to detach device cleanly: hdiutil: couldn\'t eject "disk4" - Resource busy';
const failed = (stderr = BUSY) => ({ status: 1, stdout: "", stderr });

test("the observed Intel detach failure retries once and still requires build success", async () => {
  const responses = [failed(), { status: 0 }];
  const calls = [];
  const delays = [];
  const code = await runMacDmgBuildWithRetry({ arch: "x64", platform: "darwin",
    spawn: (command, args) => { calls.push({ command, args }); return responses.shift(); },
    sleep: async (ms) => delays.push(ms),
  });
  assert.equal(code, 0);
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[0], { command: "npm", args: ["--prefix", "apps/v8-agent-os-shell", "run", "dist:mac:preview", "--", "--x64"] });
  assert.deepEqual(calls[1], calls[0]);
  assert.deepEqual(delays, [10_000]);
});

test("persistent detach failures fail the gate after exactly two builds", async () => {
  let calls = 0;
  let waits = 0;
  assert.equal(await runMacDmgBuildWithRetry({ arch: "arm64", platform: "darwin",
    spawn: (_command, args) => { calls += 1; assert.equal(args.at(-1), "--arm64"); return failed(); },
    sleep: async () => { waits += 1; },
  }), 1);
  assert.equal(calls, 2);
  assert.equal(waits, 1);
});

for (const result of [
  failed("codesign failed: invalid certificate"),
  failed("hdiutil: create failed - Resource busy"),
  failed("Unable to detach device cleanly: operation not permitted"),
  failed("npm ERR! ECONNRESET"),
  { ...failed(), status: null, signal: "SIGTERM" },
  { ...failed(), error: new Error("spawn timeout") },
]) {
  test(`unrelated error or cancellation does not retry: ${result.signal || result.error?.message || result.stderr}`, async () => {
    let calls = 0;
    assert.equal(isRetryableDmgDetachFailure(result), false);
    assert.equal(await runMacDmgBuildWithRetry({ arch: "x64", platform: "darwin",
      spawn: () => { calls += 1; return result; },
      sleep: async () => assert.fail("unrelated failure must not retry"),
    }), 1);
    assert.equal(calls, 1);
  });
}

test("unsupported targets never invoke npm or a host cleanup command", async () => {
  for (const [arch, platform] of [["x64;touch marker", "darwin"], ["x64", "linux"], ["universal", "darwin"]]) {
    await assert.rejects(runMacDmgBuildWithRetry({ arch, platform,
      spawn: () => assert.fail("invalid target must not spawn"),
    }), /requires macOS/);
  }
});

test("CI uses the bounded helper while retaining the separate package smoke", () => {
  const workflow = fs.readFileSync(new URL("../../.github/workflows/desktop-preview.yml", import.meta.url), "utf8");
  const build = workflow.slice(workflow.indexOf("- name: Build unsigned macOS desktop preview"), workflow.indexOf("- name: Build unsigned Linux desktop preview"));
  assert.match(build, /timeout-minutes: 35/);
  assert.match(build, /node scripts\/desktop\/build-macos-dmg-with-retry\.mjs \$\{\{ matrix\.arch \}\}/);
  const smoke = workflow.slice(workflow.indexOf("- name: Packaged macOS desktop smoke"), workflow.indexOf("- name: Cleanup macOS desktop smoke processes"));
  assert.match(smoke, /ci-macos-package-audit\.sh/);
  assert.match(smoke, /run_desktop_install_smoke\.mjs/);
  assert.doesNotMatch(smoke, /continue-on-error/);
});
