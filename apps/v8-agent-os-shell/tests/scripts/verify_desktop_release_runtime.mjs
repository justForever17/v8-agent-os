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
const minimumFfmpegVersion = [7, 0];
const minimumFfmpegVersionText = "7.0";

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

function extraResourceBlock(config, sourcePath) {
  const marker = `  - from: ${sourcePath}`;
  const start = config.indexOf(marker);
  if (start < 0) return "";
  const next = config.indexOf("\n  - from:", start + marker.length);
  return config.slice(start, next < 0 ? config.length : next);
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

function mediaToolVersion(command) {
  const result = spawnSync(command, ["-hide_banner", "-version"], {
    encoding: "utf8",
    shell: process.platform === "win32",
    windowsHide: true,
    timeout: 15000,
  });
  const output = `${result.stdout || result.stderr || ""}`.trim();
  const preview = output.split(/\r?\n/)[0] || "";
  const match = preview.match(new RegExp(`^${command}\\s+version\\s+(\\d+)\\.(\\d+)(?:\\.(\\d+))?`, "i"));
  const versionTuple = match ? [Number(match[1]), Number(match[2]), Number(match[3] || 0)] : null;
  const meetsMinimum = Boolean(
    result.status === 0
    && versionTuple
    && (versionTuple[0] > minimumFfmpegVersion[0]
      || (versionTuple[0] === minimumFfmpegVersion[0] && versionTuple[1] >= minimumFfmpegVersion[1])),
  );
  return {
    ok: result.status === 0,
    meetsMinimum,
    preview,
    version: versionTuple ? versionTuple.join(".") : "",
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
const portablePythonCandidates = process.platform === "win32"
  ? [path.join(portablePythonRoot, "python.exe")]
  : [
      path.join(portablePythonRoot, "bin", "python3"),
      path.join(portablePythonRoot, "bin", "python"),
    ];
const pythonExe = portablePythonCandidates.find((candidate) => exists(candidate)) || portablePythonCandidates[0];
const pythonwExe = path.join(portablePythonRoot, "pythonw.exe");
const legacyVenvPython = process.platform === "win32"
  ? path.join(engineRoot, ".venv", "Scripts", "python.exe")
  : path.join(engineRoot, ".venv", "bin", "python");
const builderConfigPath = path.join(shellRoot, "electron-builder.yml");
const builderConfig = fs.readFileSync(builderConfigPath, "utf8");
const engineResourceBlock = extraResourceBlock(builderConfig, "../../apps/v8-agent-os-engine");
const browserRoot = path.join(engineRoot, ".playwright-browsers");
const adminStandaloneExpected = path.join(adminRoot, ".next", "standalone", "apps", "v8-agent-os-admin", "server.js");
const webStandaloneExpected = path.join(webRoot, ".next", "standalone", "apps", "v8-agent-os-web", "server.js");
const adminStandaloneServer = standaloneServerFor(adminRoot, "admin");
const webStandaloneServer = standaloneServerFor(webRoot, "web");
const macosHelper = process.platform === "darwin"
  ? path.join(engineRoot, "runtimes", "computer_use", "drivers", "bin", `macos-${process.arch === "arm64" ? "arm64" : "x64"}`, "mac_ax_helper")
  : "";

const checks = [];
const degraded = [];

const requiredFiles = [
  ["engine.portablePython", pythonExe],
  ["engine.sandboxHost", path.join(engineRoot, "bin", process.platform === "win32" ? "v8-sandbox-host.exe" : "v8-sandbox-host")],
  ["admin.productionBuild", path.join(adminRoot, ".next", "BUILD_ID")],
  ["admin.standaloneServer", adminStandaloneServer || adminStandaloneExpected],
  ["web.productionBuild", path.join(webRoot, ".next", "BUILD_ID")],
  ["web.standaloneServer", webStandaloneServer || webStandaloneExpected],
  ["shell.main", path.join(shellRoot, "electron", "main.cjs")],
  ["shell.builderConfig", path.join(shellRoot, "electron-builder.yml")],
  ["desktopPet.serverBundle", path.join(petRoot, "dist", "server.cjs")],
];
if (process.platform === "win32") {
  requiredFiles.splice(1, 0,
    ["engine.portablePythonw", pythonwExe],
    ["engine.portablePythonPathConfig", walkFor(portablePythonRoot, (_fullPath, fileName) => /^python.*\._pth$/i.test(fileName), 1)],
  );
}
if (macosHelper) requiredFiles.push(["computerUse.packagedMacAXHelper", macosHelper]);
for (const [name, filePath] of requiredFiles) {
  pushCheck(checks, name, exists(filePath), { path: rel(filePath) });
}

pushCheck(checks, "engine.devVenvExcludedFromPackage", engineResourceBlock.includes('- "!.venv/**"'), {
  sourcePath: rel(legacyVenvPython),
  sourcePresent: exists(legacyVenvPython),
  exclusion: "!.venv/**",
});

if (exists(pythonExe)) {
  const runtimeResult = pythonRuntimeCheck(pythonExe);
  pushCheck(checks, "engine.portablePythonExecutable", runtimeResult.ok, runtimeResult);

  const engineImportResult = pythonEngineImportCheck(pythonExe, engineRoot);
  pushCheck(checks, "engine.importMain", engineImportResult.ok, engineImportResult);

  const requiredModules = {
    langgraphCheckpointSqlite: "langgraph.checkpoint.sqlite",
    chromaRustNative: "chromadb_rust_bindings",
    playwright: "playwright",
    tiktokenNative: "tiktoken._tiktoken",
    ytDlp: "yt_dlp",
    psdTools: "psd_tools",
    pillow: "PIL",
  };
  if (process.platform === "win32") {
    requiredModules.pywin32 = "win32api";
    requiredModules.windowsCredentialManager = "win32cred";
  }
  if (process.platform === "win32" && process.arch === "arm64") {
    requiredModules.grpcNative = "grpc._cython.cygrpc";
    requiredModules.httptoolsNative = "httptools.parser.parser";
    requiredModules.yaml = "yaml";
  }
  if (process.platform === "darwin") {
    requiredModules.pyobjcAppKit = "AppKit";
    requiredModules.pyobjcQuartz = "Quartz";
    requiredModules.keyring = "keyring";
    requiredModules.macOSKeychainApi = "keyring.backends.macOS.api";
  }
  if (process.platform === "linux") {
    requiredModules.pyGObject = "gi";
    requiredModules.pyatspi = "pyatspi";
    requiredModules.pythonXlib = "Xlib";
    requiredModules.keyring = "keyring";
    requiredModules.secretStorage = "secretstorage";
  }
  const optionalModules = {
    sqliteVec: "sqlite_vec",
    pywinauto: "pywinauto",
    patchright: "patchright",
    av: "av",
    soundcard: "soundcard",
    robotframework: "robot",
    rpaFramework: "RPA",
  };
  const optionalModuleReasons = {
    sqliteVec:
      process.platform === "win32" && process.arch === "arm64"
        ? "sqlite-vec does not publish a Windows ARM64 wheel; the required checkpoint saver path is verified separately"
        : "sqlite-vec is an optional checkpoint extension and is not used by the current V8OS checkpoint path",
  };
  const moduleResult = pythonModuleCheck(pythonExe, { ...requiredModules, ...optionalModules });
  if (moduleResult.ok) {
    for (const [name, ok] of Object.entries(moduleResult.modules)) {
      if (Object.hasOwn(requiredModules, name)) {
        pushCheck(checks, `pythonModule.${name}`, ok);
      } else {
        pushOptionalCheck(checks, degraded, `pythonModule.${name}`, ok, {
          reason:
            optionalModuleReasons[name] ??
            `${name} is an optional heavy capability in the unsigned desktop preview package`,
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
if (process.platform === "linux") {
  const xdotoolResult = commandExists("xdotool");
  const wmctrlResult = commandExists("wmctrl");
  const xclipResult = commandExists("xclip");
  const xselResult = commandExists("xsel");
  const linuxDesktopToolsReady = xdotoolResult.ok
    && wmctrlResult.ok
    && (xclipResult.ok || xselResult.ok);
  pushOptionalCheck(checks, degraded, "external.linuxX11DesktopTools", linuxDesktopToolsReady, {
    externalHostDependency: true,
    requiredFor: "Linux X11 computer-use assistive operations",
    tools: {
      xdotool: xdotoolResult.preview || "not found",
      wmctrl: wmctrlResult.preview || "not found",
      xclip: xclipResult.preview || "not found",
      xsel: xselResult.preview || "not found",
    },
    reason: "Linux X11 desktop assistance requires xdotool, wmctrl, and either xclip or xsel. The DEB declares them; AppImage users must install them on the host. Wayland restrictions remain a real-host gate.",
  });
}
const ffmpegResult = mediaToolVersion("ffmpeg");
const ffprobeResult = mediaToolVersion("ffprobe");
const ffmpegReady = ffmpegResult.meetsMinimum && ffprobeResult.meetsMinimum;
pushOptionalCheck(checks, degraded, "external.ffmpeg", ffmpegReady, {
  minimumVersion: minimumFfmpegVersionText,
  ffmpegVersion: ffmpegResult.version,
  ffprobeVersion: ffprobeResult.version,
  preview: ffmpegResult.preview || "not bundled or not on PATH",
  reason: `FFmpeg and FFprobe ${minimumFfmpegVersionText}+ are required for V8OS media capabilities`,
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
    `Git is a required managed-engineering dependency; FFmpeg and FFprobe ${minimumFfmpegVersionText}+ remain degraded external capabilities until bundled by the installer.`,
    "Hard checks cover portable Engine Python, the native sandbox host, production bundles, desktop pet bundle, and the slim preview Python runtime.",
    "Agent Browser uses an installed Edge, Chrome, or Chromium through CDP; no browser download is triggered at runtime.",
    "Linux X11 assistive tools are declared by the DEB but remain host dependencies for AppImage; Wayland, TCC, and window-manager behavior require a real GUI host.",
    "Full RPA, realtime media, and heavy optional modules may be reported as degraded in unsigned preview builds; a degraded capability is not an installed runtime claim.",
  ],
};

fs.mkdirSync(releaseDir, { recursive: true });
const outPath = path.join(releaseDir, "RUNTIME_PROBE.json");
fs.writeFileSync(outPath, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
console.log(outPath);
console.log(JSON.stringify(payload, null, 2));
process.exit(payload.passed ? 0 : 1);
