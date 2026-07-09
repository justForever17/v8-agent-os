#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const scriptDir = path.dirname(__filename);
const shellRoot = path.resolve(scriptDir, "..", "..");
const repoRoot = path.resolve(shellRoot, "..", "..");
const releaseDir = path.join(shellRoot, "dist", "release");

function exists(filePath) {
  return fs.existsSync(filePath);
}

function rel(filePath) {
  return path.relative(repoRoot, filePath).replace(/\\/g, "/");
}

function pushCheck(checks, name, ok, details = {}) {
  checks.push({
    name,
    ok: Boolean(ok),
    ...details,
  });
}

function walkFor(dir, predicate, maxDepth = 6) {
  if (!exists(dir) || maxDepth < 0) return "";
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isFile() && predicate(fullPath, entry.name)) {
      return fullPath;
    }
    if (entry.isDirectory()) {
      const found = walkFor(fullPath, predicate, maxDepth - 1);
      if (found) return found;
    }
  }
  return "";
}

function standaloneServerFor(appRoot, appName) {
  const standaloneRoot = path.join(appRoot, ".next", "standalone");
  const candidates = [
    path.join(standaloneRoot, "apps", `v8-agent-os-${appName}`, "server.js"),
    path.join(standaloneRoot, "server.js"),
  ];
  return candidates.find((candidate) => exists(candidate)) || "";
}

function commandExists(command, args = ["--version"]) {
  const result = spawnSync(command, args, {
    encoding: "utf8",
    shell: process.platform === "win32",
    windowsHide: true,
    timeout: 15000,
  });
  return {
    ok: result.status === 0,
    preview: `${result.stdout || result.stderr || ""}`.trim().split(/\r?\n/)[0] || "",
  };
}

function pythonModuleCheck(pythonExe, modules) {
  const script = `
import importlib.util, json
modules = ${JSON.stringify(modules)}
result = {}
for key, spec_name in modules.items():
    result[key] = importlib.util.find_spec(spec_name) is not None
print(json.dumps(result, ensure_ascii=False, sort_keys=True))
`;
  const result = spawnSync(pythonExe, ["-c", script], {
    encoding: "utf8",
    windowsHide: true,
    timeout: 30000,
  });
  if (result.status !== 0) {
    return { ok: false, error: `${result.stderr || result.stdout || ""}`.trim() };
  }
  try {
    return { ok: true, modules: JSON.parse(result.stdout) };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
}

function pythonRuntimeCheck(pythonExe) {
  const script = `
import json
import sys
print(json.dumps({"executable": sys.executable, "prefix": sys.prefix}, ensure_ascii=False, sort_keys=True))
`;
  const result = spawnSync(pythonExe, ["-c", script], {
    encoding: "utf8",
    windowsHide: true,
    timeout: 30000,
  });
  if (result.status !== 0) {
    return { ok: false, error: `${result.stderr || result.stdout || ""}`.trim() };
  }
  try {
    const payload = JSON.parse(result.stdout);
    const executable = String(payload.executable || "");
    const normalized = executable.toLowerCase().replace(/\\/g, "/");
    return {
      ok: !normalized.includes("hostedtoolcache") && !normalized.includes("/.venv/"),
      executable,
      prefix: payload.prefix || "",
    };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
}

const engineRoot = path.join(repoRoot, "apps", "v8-agent-os-engine");
const adminRoot = path.join(repoRoot, "apps", "v8-agent-os-admin");
const webRoot = path.join(repoRoot, "apps", "v8-agent-os-web");
const petRoot = path.join(repoRoot, "apps", "v8-agent-os-desktop-pet");
const portablePythonRoot = path.join(engineRoot, ".python");
const pythonExe = path.join(portablePythonRoot, "python.exe");
const pythonwExe = path.join(portablePythonRoot, "pythonw.exe");
const legacyVenvPython = path.join(engineRoot, ".venv", "Scripts", "python.exe");
const browserRoot = path.join(engineRoot, ".playwright-browsers");
const adminStandaloneExpected = path.join(adminRoot, ".next", "standalone", "apps", "v8-agent-os-admin", "server.js");
const webStandaloneExpected = path.join(webRoot, ".next", "standalone", "apps", "v8-agent-os-web", "server.js");
const adminStandaloneServer = standaloneServerFor(adminRoot, "admin");
const webStandaloneServer = standaloneServerFor(webRoot, "web");

const checks = [];
const degraded = [];

for (const [name, filePath] of [
  ["engine.portablePython", pythonExe],
  ["engine.portablePythonw", pythonwExe],
  ["engine.portablePythonPathConfig", walkFor(portablePythonRoot, (_fullPath, fileName) => /^python.*\._pth$/i.test(fileName), 1)],
  ["admin.productionBuild", path.join(adminRoot, ".next", "BUILD_ID")],
  ["admin.standaloneServer", adminStandaloneServer || adminStandaloneExpected],
  ["web.productionBuild", path.join(webRoot, ".next", "BUILD_ID")],
  ["web.standaloneServer", webStandaloneServer || webStandaloneExpected],
  ["shell.main", path.join(shellRoot, "electron", "main.cjs")],
  ["shell.builderConfig", path.join(shellRoot, "electron-builder.yml")],
  ["desktopPet.serverBundle", path.join(petRoot, "dist", "server.cjs")],
]) {
  pushCheck(checks, name, exists(filePath), { path: rel(filePath) });
}

pushCheck(checks, "engine.noPackagedVenvPython", !exists(legacyVenvPython), {
  path: rel(legacyVenvPython),
});

if (exists(pythonExe)) {
  const runtimeResult = pythonRuntimeCheck(pythonExe);
  pushCheck(checks, "engine.portablePythonExecutable", runtimeResult.ok, runtimeResult);

  const requiredModules = {
    playwright: "playwright",
    patchright: "patchright",
    ytDlp: "yt_dlp",
    psdTools: "psd_tools",
    pillow: "PIL",
    av: "av",
    soundcard: "soundcard",
    robotframework: "robot",
    rpaFramework: "RPA",
    pywinauto: "pywinauto",
    pywin32: "win32api",
  };
  const moduleResult = pythonModuleCheck(pythonExe, requiredModules);
  if (moduleResult.ok) {
    for (const [name, ok] of Object.entries(moduleResult.modules)) {
      pushCheck(checks, `pythonModule.${name}`, ok);
    }
  } else {
    pushCheck(checks, "pythonModule.scan", false, { error: moduleResult.error });
  }
}

const chromium = walkFor(browserRoot, (_fullPath, name) => {
  const normalized = name.toLowerCase();
  return normalized === "chrome.exe" || normalized === "headless_shell.exe" || normalized === "chrome";
});
pushCheck(checks, "playwright.chromiumBrowser", Boolean(chromium), {
  path: chromium ? rel(chromium) : rel(browserRoot),
});

for (const [name, command] of [
  ["external.git", "git"],
  ["external.ffmpeg", "ffmpeg"],
]) {
  const result = commandExists(command);
  const item = { name, ok: result.ok, preview: result.preview };
  if (!result.ok) degraded.push(item);
  pushCheck(checks, name, true, {
    degraded: !result.ok,
    preview: result.preview || (result.ok ? "" : "not bundled or not on PATH"),
  });
}

const failures = checks.filter((item) => !item.ok);
const payload = {
  generatedAt: new Date().toISOString(),
  platform: process.platform,
  passed: failures.length === 0,
  failures,
  degraded,
  checks,
  notes: [
    "Git/ffmpeg are treated as degraded external capabilities until bundled by the installer.",
    "Hard checks cover portable Engine Python, production bundles, desktop pet bundle, Python media/automation modules, and Playwright Chromium.",
  ],
};

fs.mkdirSync(releaseDir, { recursive: true });
const outPath = path.join(releaseDir, "RUNTIME_PROBE.json");
fs.writeFileSync(outPath, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
console.log(outPath);
console.log(JSON.stringify(payload, null, 2));
process.exit(payload.passed ? 0 : 1);
