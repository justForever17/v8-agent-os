#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
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

function pushOptionalCheck(checks, degraded, name, available, details = {}) {
  const payload = {
    name,
    ok: true,
    available: Boolean(available),
    degraded: !available,
    ...details,
  };
  if (!available) {
    degraded.push({
      name,
      reason: details.reason || "optional capability is not bundled in this preview release",
      ...details,
    });
  }
  checks.push(payload);
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

function installedSystemBrowser() {
  const candidates = process.platform === "win32"
    ? [
        ["edge", path.join(process.env["ProgramFiles(x86)"] || "C:\\Program Files (x86)", "Microsoft", "Edge", "Application", "msedge.exe")],
        ["edge", path.join(process.env.ProgramFiles || "C:\\Program Files", "Microsoft", "Edge", "Application", "msedge.exe")],
        ["edge", path.join(process.env.LOCALAPPDATA || "", "Microsoft", "Edge", "Application", "msedge.exe")],
        ["chrome", path.join(process.env.ProgramFiles || "C:\\Program Files", "Google", "Chrome", "Application", "chrome.exe")],
        ["chrome", path.join(process.env["ProgramFiles(x86)"] || "C:\\Program Files (x86)", "Google", "Chrome", "Application", "chrome.exe")],
        ["chromium", path.join(process.env.LOCALAPPDATA || "", "Chromium", "Application", "chrome.exe")],
      ]
    : process.platform === "darwin"
      ? [
          ["chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
          ["chromium", "/Applications/Chromium.app/Contents/MacOS/Chromium"],
          ["edge", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"],
        ]
      : [
          ["chrome", "google-chrome"],
          ["chrome", "google-chrome-stable"],
          ["chromium", "chromium-browser"],
          ["chromium", "chromium"],
          ["edge", "microsoft-edge"],
          ["edge", "microsoft-edge-stable"],
        ];
  for (const [kind, executable] of candidates) {
    if (path.isAbsolute(executable) ? exists(executable) : commandExists(executable).ok) {
      return { available: true, kind, executable };
    }
  }
  return { available: false, kind: null, executable: null };
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

function pythonEngineImportCheck(pythonExe, engineRoot) {
  const probeHome = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-engine-import-"));
  try {
    const result = spawnSync(
      pythonExe,
      ["-X", "utf8", "-c", "import main; print('V8OS_ENGINE_IMPORT_OK')"],
      {
        cwd: engineRoot,
        encoding: "utf8",
        windowsHide: true,
        timeout: 120000,
        env: {
          ...process.env,
          V8_AGENT_OS_HOME: probeHome,
          V8_AGENT_OS_DISABLE_BYTECODE: "1",
        },
      },
    );
    const output = `${result.stdout || ""}\n${result.stderr || ""}`.trim();
    return {
      ok: result.status === 0 && output.includes("V8OS_ENGINE_IMPORT_OK"),
      status: result.status,
      output: output.slice(-4000),
      error: result.error?.message || "",
    };
  } finally {
    fs.rmSync(probeHome, { recursive: true, force: true });
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
  ["engine.sandboxHost", path.join(engineRoot, "bin", process.platform === "win32" ? "v8-sandbox-host.exe" : "v8-sandbox-host")],
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

  const engineImportResult = pythonEngineImportCheck(pythonExe, engineRoot);
  pushCheck(checks, "engine.importMain", engineImportResult.ok, engineImportResult);

  const requiredModules = {
    playwright: "playwright",
    ytDlp: "yt_dlp",
    psdTools: "psd_tools",
    pillow: "PIL",
    pywin32: "win32api",
  };
  const optionalModules = {
    pywinauto: "pywinauto",
    patchright: "patchright",
    av: "av",
    soundcard: "soundcard",
    robotframework: "robot",
    rpaFramework: "RPA",
  };
  const moduleResult = pythonModuleCheck(pythonExe, { ...requiredModules, ...optionalModules });
  if (moduleResult.ok) {
    for (const [name, ok] of Object.entries(moduleResult.modules)) {
      if (Object.hasOwn(requiredModules, name)) {
        pushCheck(checks, `pythonModule.${name}`, ok);
      } else {
        pushOptionalCheck(checks, degraded, `pythonModule.${name}`, ok, {
          reason: `${name} is an optional heavy capability in the unsigned desktop preview package`,
        });
      }
    }
  } else {
    pushCheck(checks, "pythonModule.scan", false, { error: moduleResult.error });
  }
}

const chromium = walkFor(browserRoot, (_fullPath, name) => {
  const normalized = name.toLowerCase();
  return normalized === "chrome.exe" || normalized === "headless_shell.exe" || normalized === "chrome";
});
const systemBrowser = installedSystemBrowser();
pushOptionalCheck(checks, degraded, "agentBrowser.compatibleBrowser", Boolean(chromium) || systemBrowser.available, {
  source: chromium ? "bundled" : systemBrowser.available ? "system" : "missing",
  browserKind: systemBrowser.kind,
  path: chromium ? rel(chromium) : systemBrowser.executable || rel(browserRoot),
  reason: "No compatible browser was found. Install Microsoft Edge, Google Chrome, or Chromium; V8OS does not download one at runtime.",
});

const gitResult = commandExists("git");
pushCheck(checks, "external.git", gitResult.ok, {
  requiredFor: "managed engineering workspaces",
  preview: gitResult.preview || "Git is required but was not found on PATH",
});
const ffmpegResult = commandExists("ffmpeg");
pushOptionalCheck(checks, degraded, "external.ffmpeg", ffmpegResult.ok, {
  preview: ffmpegResult.preview || "not bundled or not on PATH",
  reason: "ffmpeg is an optional media capability in the unsigned desktop preview package",
});

const failures = checks.filter((item) => !item.ok);
const payload = {
  generatedAt: new Date().toISOString(),
  platform: process.platform,
  passed: failures.length === 0,
  failures,
  degraded,
  checks,
  notes: [
    "Git is a required managed-engineering dependency; ffmpeg remains a degraded external capability until bundled by the installer.",
    "Hard checks cover portable Engine Python, the native sandbox host, production bundles, desktop pet bundle, and the slim preview Python runtime.",
    "Agent Browser uses an installed Edge, Chrome, or Chromium through CDP; no browser download is triggered at runtime.",
    "Full RPA, realtime media, and heavy optional modules may be reported as degraded in unsigned preview builds.",
  ],
};

fs.mkdirSync(releaseDir, { recursive: true });
const outPath = path.join(releaseDir, "RUNTIME_PROBE.json");
fs.writeFileSync(outPath, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
console.log(outPath);
console.log(JSON.stringify(payload, null, 2));
process.exit(payload.passed ? 0 : 1);
