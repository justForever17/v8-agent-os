import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import path from "node:path";
import process from "node:process";
import test from "node:test";

const ROOT = path.resolve(import.meta.dirname, "../..");
const GENERATOR = path.join(ROOT, "scripts/release/generate-release-notes.mjs");

test("unified preview notes expose Desktop and Android without claiming iOS", () => {
  const notes = execFileSync(process.execPath, [
    GENERATOR,
    "--product", "all",
    "--version", "2026.08.07.3",
    "--tag", "v8-os-v2026.08.07.3",
    "--channel", "preview",
  ], {
    cwd: ROOT,
    encoding: "utf8",
    env: { ...process.env, GITHUB_REF_NAME: "main" },
  });

  assert.match(notes, /^# V8 Agent OS Preview v2026\.08\.07\.3/m);
  assert.match(notes, /win-arm64-setup\.exe/);
  assert.match(notes, /android-preview\.apk/);
  assert.match(notes, /iOS 因缺少非交互签名凭据被明确禁用/);
  assert.match(notes, /标签：`v8-os-v2026\.08\.07\.3`/);
});

test("stable notes match the production Android asset contract", () => {
  const notes = execFileSync(process.execPath, [
    GENERATOR,
    "--product", "all",
    "--version", "2026.08.08.1",
    "--channel", "stable",
  ], { cwd: ROOT, encoding: "utf8" });

  assert.match(notes, /V8OS-Phone-2026\.08\.08\.1-android\.aab/);
  assert.doesNotMatch(notes, /android-preview\.apk/);
});
