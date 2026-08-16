#!/usr/bin/env node
import fs from "node:fs";

const TARGETS = [
  { id: "windows-x64", label: "Windows x64 unsigned preview installer", runner: "windows-latest", os: "windows", arch: "x64", pythonArch: "amd64" },
  // Windows ARM64 is a native GitHub-hosted runner target. Keep its Python
  // architecture explicit: the Windows embeddable package calls x64 "amd64",
  // while the Electron/Rust target calls it "arm64".
  { id: "windows-arm64", label: "Windows ARM64 unsigned preview installer", runner: "windows-11-arm", os: "windows", arch: "arm64", pythonArch: "arm64" },
  // GitHub's public labels are architecture-qualified for Intel; macos-15 is
  // the native Apple Silicon label (macos-15-intel is the x64 counterpart).
  { id: "macos-x64", label: "macOS Intel unsigned preview installer", runner: "macos-15-intel", os: "macos", arch: "x64" },
  { id: "macos-arm64", label: "macOS Apple Silicon unsigned preview installer", runner: "macos-15", os: "macos", arch: "arm64" },
  // Build native extensions on the oldest declared GNU/Linux baseline. The
  // exact artifacts are consumed again by clean Ubuntu 24.04 smoke jobs.
  { id: "linux-x64", label: "Linux x64 unsigned preview packages", runner: "ubuntu-22.04", os: "linux", arch: "x64" },
  { id: "linux-arm64", label: "Linux arm64 unsigned preview packages", runner: "ubuntu-22.04-arm", os: "linux", arch: "arm64" },
];

function argValue(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? String(process.argv[index + 1] || "") : "";
}

function parseRequestedTargetIds(targetsJson) {
  if (!targetsJson) return [];
  const parsed = JSON.parse(targetsJson);
  if (!Array.isArray(parsed) || parsed.some((value) => typeof value !== "string")) {
    throw new Error("--targets-json must be a JSON array of desktop target ids");
  }
  if (new Set(parsed).size !== parsed.length) {
    throw new Error("--targets-json contains duplicate desktop target ids");
  }
  return parsed;
}

function resolveTargets({ eventName, platform, targetsJson }) {
  const requestedTargetIds = parseRequestedTargetIds(targetsJson);
  const enabled = eventName === "workflow_call"
    || eventName === "workflow_dispatch"
    || requestedTargetIds.length > 0;
  const requested = platform || "all";
  const targetIds = requestedTargetIds.length > 0
    ? requestedTargetIds
    : requested === "all"
      ? TARGETS.map((target) => target.id)
      : [requested];
  const targetById = new Map(TARGETS.map((target) => [target.id, target]));
  const unsupported = targetIds.filter((targetId) => !targetById.has(targetId));
  if (unsupported.length > 0) {
    throw new Error(`Unsupported desktop platform(s): ${unsupported.join(", ")}`);
  }
  const include = targetIds.map((targetId) => targetById.get(targetId));
  if (enabled && include.length === 0) {
    throw new Error("At least one desktop target is required when desktop packaging is enabled");
  }
  const linuxCleanInclude = include
    .filter((target) => target.os === "linux")
    .flatMap((target) => ["22.04", "24.04"].map((ubuntuVersion) => ({
      id: target.id,
      arch: target.arch,
      distro: `ubuntu-${ubuntuVersion}`,
      runner: target.arch === "arm64" ? `ubuntu-${ubuntuVersion}-arm` : `ubuntu-${ubuntuVersion}`,
      label: `${target.id} clean Ubuntu ${ubuntuVersion} DEB smoke`,
    })));
  return {
    enabled,
    matrix: { include },
    linuxCleanEnabled: enabled && linuxCleanInclude.length > 0,
    linuxCleanMatrix: { include: linuxCleanInclude },
  };
}

try {
  const result = resolveTargets({
    eventName: argValue("--event") || process.env.GITHUB_EVENT_NAME || "",
    platform: argValue("--platform") || "all",
    targetsJson: argValue("--targets-json"),
  });
  const lines = [
    `enabled=${result.enabled}`,
    `matrix=${JSON.stringify(result.matrix)}`,
    `linux_clean_enabled=${result.linuxCleanEnabled}`,
    `linux_clean_matrix=${JSON.stringify(result.linuxCleanMatrix)}`,
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
