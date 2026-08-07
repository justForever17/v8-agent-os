#!/usr/bin/env node
import fs from "node:fs";

const TARGETS = [
  { id: "windows-x64", label: "Windows x64 unsigned preview installer", runner: "windows-latest", os: "windows", arch: "x64" },
  { id: "macos-x64", label: "macOS Intel unsigned preview installer", runner: "macos-13", os: "macos", arch: "x64" },
  { id: "macos-arm64", label: "macOS Apple Silicon unsigned preview installer", runner: "macos-14", os: "macos", arch: "arm64" },
  { id: "linux-x64", label: "Linux x64 unsigned preview packages", runner: "ubuntu-24.04", os: "linux", arch: "x64" },
  { id: "linux-arm64", label: "Linux arm64 unsigned preview packages", runner: "ubuntu-24.04-arm", os: "linux", arch: "arm64" },
];

function argValue(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? String(process.argv[index + 1] || "") : "";
}

function resolveTargets({ eventName, ref, platform }) {
  const isTag = /^refs\/tags\/v8-os-desktop-v/.test(ref);
  const enabled = isTag || eventName === "workflow_dispatch";
  const requested = isTag ? "all" : platform || "all";
  const include = requested === "all"
    ? TARGETS
    : TARGETS.filter((target) => target.id === requested);
  if (!include.length) throw new Error(`Unsupported desktop platform: ${requested}`);
  return { enabled, matrix: { include } };
}

try {
  const result = resolveTargets({
    eventName: argValue("--event") || process.env.GITHUB_EVENT_NAME || "",
    ref: argValue("--ref") || process.env.GITHUB_REF || "",
    platform: argValue("--platform") || "all",
  });
  const lines = [
    `enabled=${result.enabled}`,
    `matrix=${JSON.stringify(result.matrix)}`,
  ];
  const output = argValue("--github-output");
  if (output) {
    fs.appendFileSync(output, `${lines.join("\n")}\n`, "utf8");
  } else {
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  }
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}
