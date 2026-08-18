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
  assert.match(notes, /ARM64 包仅适用于原生 Windows on ARM，Intel\/AMD 电脑请选择 x64 包/);
  assert.match(notes, /android-preview\.apk/);
  assert.match(notes, /iOS 因缺少非交互签名凭据被明确禁用/);
  assert.match(notes, /标签：`v8-os-v2026\.08\.07\.3`/);
  assert.match(notes, /Linux 的 Engine\/Admin\/Web\/Shell 可用；当前桌宠.*blocked/);
  assert.match(notes, /Ubuntu 22\.04\/24\.04 GNU x64\/arm64/);
  assert.match(notes, /AppImage 不会静默回退到 `--no-sandbox`/);
  assert.match(notes, /Linux Wayland 的输入限制会被显式投影为 blocked/);
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

test("2026.08.18.1 notes describe the governed document, command, and run-state fixes only for that release", () => {
  const current = execFileSync(process.execPath, [
    GENERATOR,
    "--product", "all",
    "--version", "2026.08.18.1",
    "--tag", "v8-os-v2026.08.18.1",
    "--channel", "preview",
  ], { cwd: ROOT, encoding: "utf8" });
  const historical = execFileSync(process.execPath, [
    GENERATOR,
    "--product", "all",
    "--version", "2026.08.16.9",
    "--tag", "v8-os-v2026.08.16.9",
    "--channel", "preview",
  ], { cwd: ROOT, encoding: "utf8" });

  assert.match(current, /## 本次修复/);
  assert.match(current, /文档读取能力包/);
  assert.match(current, /普通管道与真实交互终端/);
  assert.match(current, /Web 与 Phone 的发送\/停止按钮.*权威运行状态/);
  assert.doesNotMatch(historical, /## 本次修复/);
});
