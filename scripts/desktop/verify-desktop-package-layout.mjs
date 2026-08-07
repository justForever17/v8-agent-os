#!/usr/bin/env node
import fs from "node:fs";
import { spawnSync } from "node:child_process";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const repoRoot = path.resolve(path.dirname(__filename), "..", "..");
const shellRoot = path.join(repoRoot, "apps", "v8-agent-os-shell");
const releaseDir = path.join(shellRoot, "dist", "release");

function argValue(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? String(process.argv[index + 1] || "") : "";
}

function findDirectory(start, predicate, maxDepth = 4) {
  if (!fs.existsSync(start) || maxDepth < 0) return "";
  for (const entry of fs.readdirSync(start, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const fullPath = path.join(start, entry.name);
    if (predicate(entry.name, fullPath)) return fullPath;
    const nested = findDirectory(fullPath, predicate, maxDepth - 1);
    if (nested) return nested;
  }
  return "";
}

function packageResources(platform) {
  if (platform.startsWith("windows-")) {
    const unpacked = findDirectory(releaseDir, (name) => /win(?:-[a-z0-9]+)?-unpacked$/i.test(name));
    return unpacked ? path.join(unpacked, "resources", "v8os") : "";
  }
  if (platform.startsWith("macos-")) {
    const app = findDirectory(releaseDir, (name) => name === "V8 Agent OS.app");
    return app ? path.join(app, "Contents", "Resources", "v8os") : "";
  }
  const unpacked = findDirectory(releaseDir, (name) => /^linux(?:-[a-z0-9]+)?-unpacked$/i.test(name));
  return unpacked ? path.join(unpacked, "resources", "v8os") : "";
}

function verifyPackagedPython(python, engineRoot, platform) {
  const probeHome = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-package-runtime-probe-"));
  try {
    const linuxAtspiProbe = platform.startsWith("linux")
      ? "; import gi; gi.require_version('Atspi', '2.0'); import pyatspi; from gi.repository import Atspi"
      : "";
    const probe = [
      "import pathlib, sys, sysconfig",
      "runtime = pathlib.Path(sys.executable).resolve().parent.parent",
      "locations = [pathlib.Path(sys.prefix).resolve(), pathlib.Path(sys.base_prefix).resolve(), pathlib.Path(sysconfig.get_path('purelib')).resolve(), pathlib.Path(sysconfig.get_path('platlib')).resolve()]",
      "assert all(location.is_relative_to(runtime) for location in locations)",
      "import main",
      `print('V8OS_PACKAGED_RUNTIME_OK')${linuxAtspiProbe}`,
    ].join("; ");
    const result = spawnSync(python, ["-X", "utf8", "-c", probe], {
      cwd: engineRoot,
      env: {
        ...process.env,
        V8_AGENT_OS_HOME: probeHome,
        V8_AGENT_OS_DISABLE_BYTECODE: "1",
      },
      encoding: "utf8",
      timeout: 30000,
    });
    return {
      name: "engine.packagedRuntimeImport",
      ok: !result.error && result.status === 0 && String(result.stdout || "").includes("V8OS_PACKAGED_RUNTIME_OK"),
      path: path.relative(repoRoot, python),
    };
  } finally {
    fs.rmSync(probeHome, { recursive: true, force: true });
  }
}

try {
  const platform = argValue("--platform");
  if (!/^(windows|macos|linux)-(x64|arm64)$/.test(platform)) throw new Error(`Unsupported --platform ${JSON.stringify(platform)}`);
  const resourceRoot = packageResources(platform);
  const engineRoot = path.join(resourceRoot, "apps", "v8-agent-os-engine");
  const posix = !platform.startsWith("windows-");
  const python = posix
    ? [path.join(resourceRoot, "apps", "v8-agent-os-engine", ".python", "bin", "python3"), path.join(resourceRoot, "apps", "v8-agent-os-engine", ".python", "bin", "python")]
    : [path.join(resourceRoot, "apps", "v8-agent-os-engine", ".python", "python.exe")];
  const required = [
    resourceRoot,
    path.join(resourceRoot, "apps", "v8-agent-os-cli", "src", "shell_api.mjs"),
    path.join(resourceRoot, "apps", "v8-agent-os-engine", "main.py"),
    path.join(resourceRoot, "apps", "v8-agent-os-admin", ".next", "standalone"),
    path.join(resourceRoot, "apps", "v8-agent-os-web", ".next", "standalone"),
    path.join(resourceRoot, "apps", "v8-agent-os-desktop-pet", "dist", "server.cjs"),
  ];
  if (platform.startsWith("macos-")) {
    required.push(path.join(resourceRoot, "apps", "v8-agent-os-engine", "runtimes", "computer_use", "drivers", "bin", platform, "mac_ax_helper"));
  }
  const checks = required.map((filePath) => ({ path: filePath, ok: fs.existsSync(filePath) }));
  checks.push({ path: python.join(" | "), ok: python.some((filePath) => fs.existsSync(filePath)) });
  const packagedPython = python.find((filePath) => fs.existsSync(filePath));
  if (packagedPython) checks.push(verifyPackagedPython(packagedPython, engineRoot, platform));
  const failures = checks.filter((check) => !check.ok);
  const payload = {
    generatedAt: new Date().toISOString(),
    platform,
    resourceRoot,
    passed: failures.length === 0,
    failures,
    checks,
    scope: "Package resource layout only. Real GUI, OS permissions, and desktop-driver behavior require a matching physical host.",
  };
  fs.mkdirSync(releaseDir, { recursive: true });
  const output = path.join(releaseDir, `PACKAGE_LAYOUT-${platform}.json`);
  fs.writeFileSync(output, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  console.log(JSON.stringify(payload, null, 2));
  process.exit(payload.passed ? 0 : 1);
} catch (error) {
  console.error(error instanceof Error ? error.stack || error.message : String(error));
  process.exit(1);
}
