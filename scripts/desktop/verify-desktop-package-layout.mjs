#!/usr/bin/env node
import fs from "node:fs";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { isValidReleaseVersion, toSemver } from "../release/release-manifest.mjs";

const __filename = fileURLToPath(import.meta.url);
const repoRoot = path.resolve(path.dirname(__filename), "..", "..");
const shellRoot = path.join(repoRoot, "apps", "v8-agent-os-shell");
const releaseDir = path.join(shellRoot, "dist", "release");
const shellRequire = createRequire(path.join(shellRoot, "package.json"));

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

function nextStandaloneRequired(resourceRoot, app) {
  const appRoot = path.join(resourceRoot, "apps", `v8-agent-os-${app}`);
  const standaloneRoot = path.join(appRoot, ".next", "standalone");
  const serverCandidates = [
    path.join(standaloneRoot, "apps", `v8-agent-os-${app}`, "server.js"),
    path.join(standaloneRoot, "server.js"),
  ];
  const server = serverCandidates.find((candidate) => fs.existsSync(candidate)) || serverCandidates[0];
  const standaloneAppRoot = path.dirname(server);
  return [
    server,
    path.join(appRoot, ".next", "static"),
    path.join(appRoot, "public"),
    path.join(standaloneAppRoot, ".next", "static"),
    path.join(standaloneAppRoot, "public"),
  ];
}

function directoryHasFiles(directory) {
  if (!fs.existsSync(directory)) return false;
  const pending = [directory];
  while (pending.length) {
    const current = pending.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      if (entry.isFile()) return true;
      if (entry.isDirectory()) pending.push(path.join(current, entry.name));
    }
  }
  return false;
}

function verifyNextStandaloneAssets(resourceRoot, app) {
  const required = nextStandaloneRequired(resourceRoot, app);
  const server = required[0];
  const standaloneAppRoot = path.dirname(server);
  const buildManifest = path.join(standaloneAppRoot, ".next", "build-manifest.json");
  return [
    {
      path: server,
      ok: fs.existsSync(server) && fs.statSync(server).isFile() && fs.statSync(server).size > 0,
    },
    ...required.slice(1).map((directory) => ({ path: directory, ok: directoryHasFiles(directory) })),
    {
      path: buildManifest,
      ok: fs.existsSync(buildManifest) && fs.statSync(buildManifest).isFile() && fs.statSync(buildManifest).size > 2,
    },
  ];
}

function verifyPackagedPython(python, engineRoot, platform) {
  const probeHome = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-package-runtime-probe-"));
  try {
    const linuxAtspiProbe = platform.startsWith("linux")
      ? "; import gi; gi.require_version('Atspi', '2.0'); import pyatspi; from gi.repository import Atspi"
      : "";
    const probe = [
      "import pathlib, sys, sysconfig",
      "sys.path.insert(0, str(pathlib.Path.cwd()))",
      "runtime = pathlib.Path(sys.executable).resolve().parent.parent",
      "locations = [pathlib.Path(sys.prefix).resolve(), pathlib.Path(sys.base_prefix).resolve(), pathlib.Path(sysconfig.get_path('purelib')).resolve(), pathlib.Path(sysconfig.get_path('platlib')).resolve()]",
      "assert all(location.is_relative_to(runtime) for location in locations)",
      "import main",
      `print('V8OS_PACKAGED_RUNTIME_OK')${linuxAtspiProbe}`,
    ].join("; ");
    const sanitizedEnv = Object.fromEntries(
      Object.entries(process.env).filter(([key]) => {
        const normalized = key.toUpperCase();
        return normalized !== "PYTHONPATH"
          && normalized !== "PYTHONHOME"
          && !/(API[_-]?KEY|TOKEN|SECRET|PASSWORD|COOKIE|AUTHORIZATION|BEARER|CREDENTIAL)/i.test(key);
      }),
    );
    const result = spawnSync(python, ["-I", "-X", "utf8", "-c", probe], {
      cwd: engineRoot,
      env: {
        ...sanitizedEnv,
        V8_AGENT_OS_HOME: probeHome,
        V8_AGENT_OS_DISABLE_BYTECODE: "1",
        PYTHONNOUSERSITE: "1",
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

function verifyDesktopPetServerBundle(serverBundle) {
  if (!fs.existsSync(serverBundle)) {
    return { name: "desktopPet.selfContainedServer", ok: false, path: path.relative(repoRoot, serverBundle) };
  }
  const source = fs.readFileSync(serverBundle, "utf8");
  const externalRuntimeRequires = [...source.matchAll(/require\(["'](express|ws|vite)["']\)/g)].map((match) => match[1]);
  return {
    name: "desktopPet.selfContainedServer",
    ok: externalRuntimeRequires.length === 0,
    path: path.relative(repoRoot, serverBundle),
    externalRuntimeRequires,
  };
}

function verifyShellBootstrap(appAsar, expectedVersion) {
  if (!fs.existsSync(appAsar)) {
    return { name: "shell.bootstrap", ok: false, path: path.relative(repoRoot, appAsar) };
  }
  try {
    const asar = shellRequire("@electron/asar");
    const packagedManifest = JSON.parse(asar.extractFile(appAsar, "package.json").toString("utf8"));
    const packagedFiles = new Set(
      asar.listPackage(appAsar).map((entry) => String(entry).replace(/\\/g, "/").replace(/^\/+/, "")),
    );
    const expectedMain = "electron/bootstrap.cjs";
    return {
      name: "shell.bootstrap",
      ok: packagedManifest.main === expectedMain
        && packagedFiles.has(expectedMain)
        && packagedManifest.version === expectedVersion,
      path: path.relative(repoRoot, appAsar),
      main: packagedManifest.main || null,
      version: packagedManifest.version || null,
      expectedVersion,
    };
  } catch (error) {
    return {
      name: "shell.bootstrap",
      ok: false,
      path: path.relative(repoRoot, appAsar),
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

try {
  const platform = argValue("--platform");
  if (!/^(windows|macos|linux)-(x64|arm64)$/.test(platform)) throw new Error(`Unsupported --platform ${JSON.stringify(platform)}`);
  const resourceRootOverride = argValue("--resource-root");
  const outputOverride = argValue("--output");
  const resourceRoot = resourceRootOverride
    ? path.resolve(resourceRootOverride)
    : packageResources(platform);
  if (!resourceRoot) throw new Error("Packaged resource root was not found");
  const engineRoot = path.join(resourceRoot, "apps", "v8-agent-os-engine");
  const posix = !platform.startsWith("windows-");
  const python = posix
    ? [path.join(resourceRoot, "apps", "v8-agent-os-engine", ".python", "bin", "python3"), path.join(resourceRoot, "apps", "v8-agent-os-engine", ".python", "bin", "python")]
    : [path.join(resourceRoot, "apps", "v8-agent-os-engine", ".python", "python.exe")];
  const desktopPetServerBundle = path.join(resourceRoot, "apps", "v8-agent-os-desktop-pet", "dist", "server.cjs");
  const desktopPetIndex = path.join(resourceRoot, "apps", "v8-agent-os-desktop-pet", "dist", "index.html");
  const desktopPetAssets = path.join(resourceRoot, "apps", "v8-agent-os-desktop-pet", "dist", "assets");
  const featurePackRequirements = path.join(engineRoot, "requirements", "feature-packs");
  const featurePackLocks = path.join(featurePackRequirements, "locks");
  const featurePackPlatform = platform.split("-")[0];
  const featurePackArchitecture = platform.split("-")[1];
  const appAsar = path.join(path.dirname(resourceRoot), "app.asar");
  const releaseManifestPath = path.join(resourceRoot, "release-manifest.json");
  const packagedReleaseManifest = JSON.parse(fs.readFileSync(releaseManifestPath, "utf8"));
  const expectedReleaseVersion = argValue("--release-version")
    || String(packagedReleaseManifest?.release?.version || "");
  if (!isValidReleaseVersion(expectedReleaseVersion)) {
    throw new Error(`Invalid packaged --release-version ${JSON.stringify(expectedReleaseVersion)}`);
  }
  if (packagedReleaseManifest?.release?.version !== expectedReleaseVersion) {
    throw new Error(
      `Packaged release manifest is ${packagedReleaseManifest?.release?.version || "missing"}; expected ${expectedReleaseVersion}`,
    );
  }
  const expectedPackageVersion = toSemver(expectedReleaseVersion);
  const required = [
    resourceRoot,
    appAsar,
    releaseManifestPath,
    path.join(resourceRoot, "apps", "v8-agent-os-cli", "src", "shell_api.mjs"),
    path.join(resourceRoot, "apps", "v8-agent-os-engine", "main.py"),
    ...nextStandaloneRequired(resourceRoot, "admin"),
    ...nextStandaloneRequired(resourceRoot, "web"),
    desktopPetServerBundle,
    desktopPetIndex,
    path.join(resourceRoot, "apps", "v8-agent-os-desktop-pet", "electron", "main.cjs"),
    path.join(resourceRoot, "apps", "v8-agent-os-shell", "scripts", "electron-launcher.mjs"),
    path.join(resourceRoot, "apps", "v8-agent-os-shell", "scripts", "launch-desktop-pet.mjs"),
    path.join(resourceRoot, "apps", "v8-agent-os-shell", "scripts", "launch-shell.mjs"),
    path.join(resourceRoot, "apps", "v8-agent-os-shell", "scripts", "spawn-detached-electron.mjs"),
    path.join(resourceRoot, "apps", "v8-agent-os-shell", "scripts", "feature_pack_runtime_probe.py"),
    path.join(featurePackRequirements, "rpa-automation.txt"),
    path.join(featurePackRequirements, "creative-media-image-analysis.manifest.json"),
    path.join(featurePackLocks, `rpa-automation-cp311-${featurePackPlatform}-${featurePackArchitecture}.txt`),
  ];
  if (platform !== "macos-x64") {
    required.push(
      path.join(
        featurePackLocks,
        `creative-media-image-analysis-cp311-${featurePackPlatform}-${featurePackArchitecture}.txt`,
      ),
    );
  }
  if (platform.startsWith("macos-")) {
    required.push(path.join(resourceRoot, "apps", "v8-agent-os-engine", "runtimes", "computer_use", "drivers", "bin", platform, "mac_ax_helper"));
  }
  if (platform.startsWith("linux-")) {
    required.push(path.join(engineRoot, ".python", "THIRD_PARTY_NOTICES", "pyatspi2-COPYING"));
  }
  const checks = required.map((filePath) => ({ path: filePath, ok: fs.existsSync(filePath) }));
  checks.push(...verifyNextStandaloneAssets(resourceRoot, "admin"));
  checks.push(...verifyNextStandaloneAssets(resourceRoot, "web"));
  checks.push(verifyShellBootstrap(appAsar, expectedPackageVersion));
  checks.push(verifyDesktopPetServerBundle(desktopPetServerBundle));
  checks.push({ name: "desktopPet.rendererAssets", path: desktopPetAssets, ok: directoryHasFiles(desktopPetAssets) });
  checks.push({ path: python.join(" | "), ok: python.some((filePath) => fs.existsSync(filePath)) });
  const packagedPython = python.find((filePath) => fs.existsSync(filePath));
  if (packagedPython) checks.push(verifyPackagedPython(packagedPython, engineRoot, platform));
  const failures = checks.filter((check) => !check.ok);
  const payload = {
    generatedAt: new Date().toISOString(),
    platform,
    releaseVersion: expectedReleaseVersion,
    packageVersion: expectedPackageVersion,
    resourceRoot,
    passed: failures.length === 0,
    failures,
    checks,
    scope: "Package resource layout only. Real GUI, OS permissions, and desktop-driver behavior require a matching physical host.",
  };
  const output = outputOverride
    ? path.resolve(outputOverride)
    : path.join(releaseDir, `PACKAGE_LAYOUT-${platform}.json`);
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  console.log(JSON.stringify(payload, null, 2));
  process.exit(payload.passed ? 0 : 1);
} catch (error) {
  console.error(error instanceof Error ? error.stack || error.message : String(error));
  process.exit(1);
}
