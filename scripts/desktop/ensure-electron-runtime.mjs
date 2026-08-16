#!/usr/bin/env node
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const defaultPackageRoots = [
  path.join(repoRoot, "apps", "v8-agent-os-shell"),
  path.join(repoRoot, "apps", "v8-agent-os-desktop-pet"),
];
const verifiedArchiveDigests = new Map();

function parseArgs(argv) {
  const packageRoots = [];
  let archive = "";
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--package-root") {
      packageRoots.push(path.resolve(String(argv[index += 1] || "")));
    } else if (argument === "--archive") {
      archive = path.resolve(String(argv[index += 1] || ""));
    } else {
      throw new Error(`Unknown argument: ${argument}`);
    }
  }
  return { packageRoots: packageRoots.length > 0 ? packageRoots : defaultPackageRoots, archive };
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function expectedExecutable(platform) {
  if (platform === "win32") return "electron.exe";
  if (platform === "darwin") return "Electron.app/Contents/MacOS/Electron";
  if (platform === "linux") return "electron";
  throw new Error(`Electron runtime is unsupported on ${platform}`);
}

function sha256(file) {
  const hash = crypto.createHash("sha256");
  const descriptor = fs.openSync(file, "r");
  const buffer = Buffer.allocUnsafe(1024 * 1024);
  try {
    let bytesRead = 0;
    do {
      bytesRead = fs.readSync(descriptor, buffer, 0, buffer.length, null);
      if (bytesRead > 0) hash.update(buffer.subarray(0, bytesRead));
    } while (bytesRead > 0);
  } finally {
    fs.closeSync(descriptor);
  }
  return hash.digest("hex");
}

function runtimeState(electronDir, expectedVersion, executableRelativePath) {
  const versionFile = path.join(electronDir, "dist", "version");
  const pathFile = path.join(electronDir, "path.txt");
  const executable = path.join(electronDir, "dist", executableRelativePath);
  const version = fs.existsSync(versionFile)
    ? fs.readFileSync(versionFile, "utf8").trim().replace(/^v/, "")
    : "";
  const recordedPath = fs.existsSync(pathFile) ? fs.readFileSync(pathFile, "utf8").trim() : "";
  return {
    executable,
    ready: version === expectedVersion && recordedPath === executableRelativePath && fs.existsSync(executable),
  };
}

function verifyArchive(archive, archiveName, expectedDigest) {
  if (path.basename(archive) !== archiveName) {
    throw new Error(`Electron archive must be named ${archiveName}, got ${path.basename(archive)}`);
  }
  const actualDigest = verifiedArchiveDigests.get(archive) || sha256(archive);
  verifiedArchiveDigests.set(archive, actualDigest);
  if (actualDigest !== expectedDigest) {
    throw new Error(`Electron archive checksum mismatch: expected ${expectedDigest}, got ${actualDigest}`);
  }
}

async function extractVerifiedArchive({ archive, electronDir, executableRelativePath }) {
  const distDir = path.join(electronDir, "dist");
  fs.rmSync(distDir, { recursive: true, force: true });
  fs.mkdirSync(distDir, { recursive: true });
  const requireFromElectron = createRequire(path.join(electronDir, "install.js"));
  const { extract } = requireFromElectron("@electron-internal/extract-zip");
  await extract(archive, { dir: distDir });
  fs.writeFileSync(path.join(electronDir, "path.txt"), executableRelativePath, "utf8");
}

function downloadWithElectronInstaller(packageRoot, electronDir) {
  const installer = path.join(electronDir, "install.js");
  if (!fs.existsSync(installer)) {
    throw new Error(`Electron package is missing under ${packageRoot}; run npm ci first`);
  }
  const result = spawnSync(process.execPath, [installer], {
    cwd: packageRoot,
    env: process.env,
    stdio: "inherit",
    windowsHide: true,
    timeout: 15 * 60_000,
  });
  if (result.error || result.status !== 0) {
    throw result.error || new Error(`Electron installer exited with status ${result.status}`);
  }
}

function verifyExecutable(executable, expectedVersion) {
  const result = spawnSync(executable, ["--version"], {
    encoding: "utf8",
    windowsHide: true,
    timeout: 15_000,
  });
  const output = `${result.stdout || ""}\n${result.stderr || ""}`.trim();
  if (result.error || result.status !== 0 || !output.split(/\s+/).includes(`v${expectedVersion}`)) {
    throw result.error || new Error(`Electron executable verification failed: ${output || `status ${result.status}`}`);
  }
}

async function ensurePackageRuntime(packageRoot, archive) {
  const appPackage = readJson(path.join(packageRoot, "package.json"));
  const expectedVersion = String(appPackage.devDependencies?.electron || appPackage.dependencies?.electron || "");
  if (!/^\d+\.\d+\.\d+$/.test(expectedVersion)) {
    throw new Error(`${appPackage.name || packageRoot} must pin Electron to an exact version`);
  }
  const electronDir = path.join(packageRoot, "node_modules", "electron");
  const electronPackage = readJson(path.join(electronDir, "package.json"));
  if (electronPackage.name !== "electron" || electronPackage.version !== expectedVersion) {
    throw new Error(`${appPackage.name || packageRoot} installed Electron ${electronPackage.version || "missing"}, expected ${expectedVersion}`);
  }
  const platform = process.env.ELECTRON_INSTALL_PLATFORM || process.env.npm_config_platform || process.platform;
  const arch = process.env.ELECTRON_INSTALL_ARCH || process.env.npm_config_arch || process.arch;
  const archiveName = `electron-v${expectedVersion}-${platform}-${arch}.zip`;
  const checksums = readJson(path.join(electronDir, "checksums.json"));
  const expectedDigest = String(checksums[archiveName] || "").toLowerCase();
  if (!/^[a-f0-9]{64}$/.test(expectedDigest)) {
    throw new Error(`Electron package does not declare a checksum for ${archiveName}`);
  }
  if (archive) verifyArchive(archive, archiveName, expectedDigest);
  const executableRelativePath = expectedExecutable(platform);
  let state = runtimeState(electronDir, expectedVersion, executableRelativePath);
  if (!state.ready) {
    if (archive) {
      await extractVerifiedArchive({ archive, electronDir, executableRelativePath });
    } else {
      downloadWithElectronInstaller(packageRoot, electronDir);
    }
    state = runtimeState(electronDir, expectedVersion, executableRelativePath);
  }
  if (!state.ready) {
    throw new Error(`${appPackage.name || packageRoot} Electron runtime did not become ready`);
  }
  verifyExecutable(state.executable, expectedVersion);
  return { package: appPackage.name, version: expectedVersion, platform, arch };
}

try {
  const { packageRoots, archive } = parseArgs(process.argv.slice(2));
  if (archive && !fs.existsSync(archive)) throw new Error(`Electron archive does not exist: ${archive}`);
  const results = [];
  for (const packageRoot of packageRoots) {
    results.push(await ensurePackageRuntime(packageRoot, archive));
  }
  const versions = new Set(results.map((item) => item.version));
  if (versions.size !== 1) throw new Error("Shell and Desktop Pet must use the same Electron version");
  process.stdout.write(`${JSON.stringify({ ok: true, runtimes: results }, null, 2)}\n`);
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}
