import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");

test("Phone artifact boundary rejects missing compiled module, test classes/trust and empty JVM guard results", () => {
  const result = spawnSync(process.platform === "win32" ? "python" : "python3", [
    "scripts/release/test_phone_executor_package.py",
  ], { cwd: root, encoding: "utf8" });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
});

test("both Phone release paths share native JVM regression and archive validation before upload", () => {
  const action = fs.readFileSync(path.join(root, ".github/actions/build-phone-package/action.yml"), "utf8");
  const guard = action.indexOf("./gradlew :v8-device-executor:testReleaseUnitTest");
  const build = action.indexOf("    - name: Build Android package");
  const verify = action.indexOf("python3 scripts/release/verify-phone-executor-package.py");
  const upload = action.indexOf("    - name: Upload Android package artifact");
  assert.ok(guard > 0 && guard < build && build < verify && verify < upload);
  assert.match(action, /npx expo prebuild --platform android --no-install/);
  assert.match(action, /--junit .*testReleaseUnitTest\/TEST-expo\.modules\.v8executor\.CommandGuardTest\.xml/);
  assert.doesNotMatch(action, /connected(?:Debug|Release)AndroidTest|run-executor-live/);
  for (const file of ["phone-build.yml", "release.yml"]) {
    assert.match(fs.readFileSync(path.join(root, ".github/workflows", file), "utf8"), /uses: \.\/.github\/actions\/build-phone-package/);
  }
});
