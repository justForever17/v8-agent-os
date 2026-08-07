#!/usr/bin/env node
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const repoRoot = path.resolve(path.dirname(__filename), "..", "..");
const engineDir = path.join(repoRoot, "apps", "v8-agent-os-engine");
const PYTHON_RELEASE = "20260805";
const RUNTIMES = {
  "macos-x64": {
    platform: "darwin",
    arch: "x64",
    asset: "cpython-3.11.15+20260805-x86_64-apple-darwin-install_only.tar.gz",
    sha256: "9e447f2e2e623dc2e99840b74b895becae673acecefe78a8ba9c61d71cbe2e71",
  },
  "macos-arm64": {
    platform: "darwin",
    arch: "arm64",
    asset: "cpython-3.11.15+20260805-aarch64-apple-darwin-install_only.tar.gz",
    sha256: "c1e8b4c910048be745d94b8605018f25531e7a4d3e35b6dbd50ce6705a1fb711",
  },
  "linux-x64": {
    platform: "linux",
    arch: "x64",
    asset: "cpython-3.11.15+20260805-x86_64-unknown-linux-gnu-install_only.tar.gz",
    sha256: "65fca9bb9e82a8498baf1281f89737c04601397e20a6444849b5fe7965299a23",
  },
  "linux-arm64": {
    platform: "linux",
    arch: "arm64",
    asset: "cpython-3.11.15+20260805-aarch64-unknown-linux-gnu-install_only.tar.gz",
    sha256: "f2bfe2882a5205cac5e052e0b8ac7287eccb8bfdb9a887b2028ddf8d6454168e",
  },
};

function argValue(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? String(process.argv[index + 1] || "") : "";
}

function hasFlag(name) {
  return process.argv.includes(name);
}

function fail(message) {
  throw new Error(message);
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd || repoRoot,
    env: { ...process.env, ...(options.env || {}) },
    encoding: "utf8",
    timeout: options.timeout ?? 20 * 60 * 1000,
  });
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  if (result.error) fail(`${command} ${args.join(" ")} failed: ${result.error.message}`);
  if (result.status !== 0) fail(`${command} ${args.join(" ")} exited with ${result.status}`);
}

async function download(url, target, expectedSha256) {
  let lastError = null;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      if (attempt > 1) console.log(`Retrying Python runtime download (${attempt}/3)…`);
      const response = await fetch(url, { redirect: "follow" });
      if (!response.ok) fail(`Download returned HTTP ${response.status}: ${url}`);
      const body = Buffer.from(await response.arrayBuffer());
      const actual = createHash("sha256").update(body).digest("hex");
      if (actual !== expectedSha256) {
        fail(`SHA256 mismatch for ${url}: expected ${expectedSha256}, got ${actual}`);
      }
      fs.writeFileSync(target, body);
      return;
    } catch (error) {
      lastError = error;
      if (attempt < 3) await new Promise((resolve) => setTimeout(resolve, attempt * 2500));
    }
  }
  fail(`Could not download verified Python runtime: ${lastError instanceof Error ? lastError.message : String(lastError)}`);
}

function pythonExecutable(runtimeDir) {
  const candidates = [
    path.join(runtimeDir, "bin", "python3"),
    path.join(runtimeDir, "bin", "python"),
  ];
  return candidates.find((candidate) => fs.existsSync(candidate)) || "";
}

async function main() {
  const target = argValue("--target");
  const runtime = RUNTIMES[target];
  if (!runtime) fail(`Unsupported --target ${JSON.stringify(target)}. Expected one of: ${Object.keys(RUNTIMES).join(", ")}`);
  if (process.platform !== runtime.platform || process.arch !== runtime.arch) {
    fail(`--target ${target} requires ${runtime.platform}/${runtime.arch}; runner is ${process.platform}/${process.arch}.`);
  }
  if (!fs.existsSync(path.join(engineDir, "main.py"))) fail(`Engine directory is invalid: ${engineDir}`);

  const requirementsArg = argValue("--requirements-path");
  const requirementsPath = requirementsArg ? path.resolve(requirementsArg) : path.join(engineDir, "requirements", "desktop-preview.txt");
  if (!fs.existsSync(requirementsPath)) fail(`Requirements file not found: ${requirementsPath}`);

  const runtimeDir = path.join(engineDir, ".python");
  const browserDir = path.join(engineDir, ".playwright-browsers");
  const tempRoot = process.env.RUNNER_TEMP || os.tmpdir();
  const workDir = fs.mkdtempSync(path.join(tempRoot, `v8os-python-${target}-`));
  const archivePath = path.join(workDir, runtime.asset);
  const extractDir = path.join(workDir, "extract");
  const sourceUrl = `https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_RELEASE}/${encodeURIComponent(runtime.asset)}`;

  try {
    console.log(`Preparing verified portable Python for ${target}: ${runtime.asset}`);
    await download(sourceUrl, archivePath, runtime.sha256);
    fs.mkdirSync(extractDir, { recursive: true });
    run("tar", ["-xzf", archivePath, "-C", extractDir]);

    // `install_only` archives changed from python/install to python at the
    // upstream 20260805 release.  Accept only these two documented layouts.
    const installRoot = [
      path.join(extractDir, "python", "install"),
      path.join(extractDir, "python"),
    ].find((candidate) => fs.existsSync(candidate));
    if (!installRoot) fail(`python-build-standalone archive did not contain python/install or python: ${runtime.asset}`);
    const parent = path.dirname(runtimeDir);
    if (parent !== engineDir || path.basename(runtimeDir) !== ".python") fail(`Refusing unexpected portable runtime path: ${runtimeDir}`);
    fs.rmSync(runtimeDir, { recursive: true, force: true });
    fs.cpSync(installRoot, runtimeDir, { recursive: true });

    const python = pythonExecutable(runtimeDir);
    if (!python) fail(`Portable Python executable was not found under ${runtimeDir}`);
    const pythonAlias = path.join(runtimeDir, "bin", "python");
    if (!fs.existsSync(pythonAlias)) fs.symlinkSync("python3", pythonAlias);
    fs.chmodSync(python, 0o755);

    run(python, ["-m", "ensurepip", "--upgrade"]);
    run(python, ["-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--upgrade", "pip", "setuptools", "wheel"]);
    run(python, ["-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--prefer-binary", "-r", requirementsPath]);

    fs.mkdirSync(browserDir, { recursive: true });
    if (hasFlag("--skip-playwright-browsers")) {
      fs.writeFileSync(
        path.join(browserDir, "DEGRADED.txt"),
        "Playwright browsers were intentionally skipped. V8OS discovers an installed Edge, Chrome, or Chromium at runtime.\n",
        "utf8",
      );
    } else {
      run(python, ["-m", "playwright", "install", "chromium"], { env: { PLAYWRIGHT_BROWSERS_PATH: browserDir } });
    }

    run(python, ["-X", "utf8", "-c", "import sys; print(sys.executable); assert 'hostedtoolcache' not in sys.executable.lower(); assert '/.venv/' not in sys.executable.replace('\\\\', '/').lower()"]);
    const probeHome = path.join(workDir, "engine-import-probe");
    fs.mkdirSync(probeHome, { recursive: true });
    run(python, ["-X", "utf8", "-c", "import main; print('V8OS_ENGINE_IMPORT_OK')"], {
      cwd: engineDir,
      env: { V8_AGENT_OS_HOME: probeHome, V8_AGENT_OS_DISABLE_BYTECODE: "1" },
    });
    console.log(`Portable Python runtime is ready for ${target}.`);
  } finally {
    fs.rmSync(workDir, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack || error.message : String(error));
  process.exit(1);
});
