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
const LINUX_PYATSPI_SOURCE = {
  // GNOME pyatspi2 2.58.2.  The previously pinned pre-Python-3 source used
  // `self.async`, which is a syntax error on Python 3.11.
  // Use GNOME's versioned source release instead of the GitLab archive API,
  // which can reject unauthenticated GitHub-hosted runners with HTTP 406.
  commit: "f2fb289a9d2e4dac65fca8db0f4d3d65607a0cf2",
  archive: "24590e5b60fec8dfb59fcd27d2a90de7034060be318ca3f7770e0f984f1f94e2",
  url: "https://download.gnome.org/sources/pyatspi/2.58/pyatspi-2.58.2.tar.xz",
};
const MACOS_MINIMUM_SYSTEM_VERSION = "12.3";
const MACOS_SQLITE_VEC_SOURCE = {
  version: "0.1.9",
  archive: "3acd67cb4aff080c7050926fd3cf8227905fe5b7ee3829d8ee5024ab1283cf61",
  url: "https://github.com/asg017/sqlite-vec/releases/download/v0.1.9/sqlite-vec-0.1.9-amalgamation.tar.gz",
  licenses: [
    {
      name: "LICENSE-MIT",
      sha256: "6ce72bbe12d975bd5286e5ab0a064c069693300c47bccbc57bec18485f1621ea",
      url: "https://raw.githubusercontent.com/asg017/sqlite-vec/v0.1.9/LICENSE-MIT",
    },
    {
      name: "LICENSE-APACHE",
      sha256: "a38070a94d4afd9cd710e3ce67bd1de78097cfe1784c1f0109ac95d3c196bfdc",
      url: "https://raw.githubusercontent.com/asg017/sqlite-vec/v0.1.9/LICENSE-APACHE",
    },
  ],
};
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

async function download(url, target, expectedSha256, artifactLabel = "archive") {
  let lastError = null;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      if (attempt > 1) console.log(`Retrying verified ${artifactLabel} download (${attempt}/3)…`);
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
  fail(`Could not download verified ${artifactLabel}: ${lastError instanceof Error ? lastError.message : String(lastError)}`);
}

function pythonExecutable(runtimeDir) {
  const candidates = [
    path.join(runtimeDir, "bin", "python3"),
    path.join(runtimeDir, "bin", "python"),
  ];
  return candidates.find((candidate) => fs.existsSync(candidate)) || "";
}

function isPathWithin(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === "" || (!relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative));
}

function verifyPortablePythonLocation(python, runtimeDir) {
  const result = spawnSync(
    python,
    [
      "-c",
      "import json, sys, sysconfig; print(json.dumps({'executable': sys.executable, 'prefix': sys.prefix, 'basePrefix': sys.base_prefix, 'purelib': sysconfig.get_path('purelib'), 'platlib': sysconfig.get_path('platlib')}))",
    ],
    { encoding: "utf8", timeout: 30000 },
  );
  if (result.error || result.status !== 0) {
    fail(`Could not verify portable Python root: ${result.error?.message || result.stderr || "unknown error"}`);
  }

  let locations;
  try {
    locations = JSON.parse(String(result.stdout || "").trim());
  } catch {
    fail(`Portable Python root probe returned invalid JSON: ${String(result.stdout || "").trim()}`);
  }
  const realRuntimeDir = fs.realpathSync(runtimeDir);
  for (const [name, value] of Object.entries(locations)) {
    const resolved = typeof value === "string" && fs.existsSync(value) ? fs.realpathSync(value) : String(value || "");
    if (!resolved || !isPathWithin(realRuntimeDir, resolved)) {
      fail(`Portable Python ${name} resolved outside the packaged runtime: ${String(value || "")}`);
    }
  }
}

function portableSitePackages(python, runtimeDir) {
  const sitePackagesResult = spawnSync(
    python,
    ["-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
    { encoding: "utf8", timeout: 30000 },
  );
  if (sitePackagesResult.error || sitePackagesResult.status !== 0) {
    fail(`Could not resolve portable Python site-packages: ${sitePackagesResult.error?.message || sitePackagesResult.stderr || "unknown error"}`);
  }
  const sitePackages = String(sitePackagesResult.stdout || "").trim();
  if (!sitePackages || !isPathWithin(fs.realpathSync(runtimeDir), fs.realpathSync(sitePackages))) {
    fail(`Refusing unexpected portable Python site-packages path: ${sitePackages}`);
  }
  return sitePackages;
}

function installLinuxPyatspi(python, runtimeDir, workDir) {
  const sitePackages = portableSitePackages(python, runtimeDir);

  const archivePath = path.join(workDir, "pyatspi-2.58.2.tar.xz");
  const extractDir = path.join(workDir, "pyatspi2");
  fs.mkdirSync(extractDir, { recursive: true });
  return download(LINUX_PYATSPI_SOURCE.url, archivePath, LINUX_PYATSPI_SOURCE.archive, "pyatspi source archive")
    .then(() => {
      run("tar", ["-xJf", archivePath, "-C", extractDir]);
      const sourceRoot = fs.readdirSync(extractDir, { withFileTypes: true })
        .find((entry) => entry.isDirectory() && fs.existsSync(path.join(extractDir, entry.name, "pyatspi", "__init__.py")));
      if (!sourceRoot) fail(`Pinned pyatspi2 archive did not contain the expected Python package: ${LINUX_PYATSPI_SOURCE.commit}`);
      const sourcePackage = path.join(extractDir, sourceRoot.name, "pyatspi");
      fs.cpSync(sourcePackage, path.join(sitePackages, "pyatspi"), { recursive: true });
      const noticesDir = path.join(runtimeDir, "THIRD_PARTY_NOTICES");
      fs.mkdirSync(noticesDir, { recursive: true });
      fs.copyFileSync(
        path.join(extractDir, sourceRoot.name, "COPYING"),
        path.join(noticesDir, "pyatspi2-COPYING"),
      );
      run(python, ["-c", "import gi; gi.require_version('Atspi', '2.0'); import pyatspi; from gi.repository import Atspi; print('PYATSPI_IMPORT_OK')"]);
    });
}

function updateSqliteVecWheelRecord(sitePackages, installedLibrary) {
  const relativeLibrary = "sqlite_vec/vec0.dylib";
  const recordPath = path.join(
    sitePackages,
    `sqlite_vec-${MACOS_SQLITE_VEC_SOURCE.version}.dist-info`,
    "RECORD",
  );
  if (!fs.existsSync(recordPath)) fail(`Installed sqlite-vec RECORD was not found: ${recordPath}`);

  const library = fs.readFileSync(installedLibrary);
  const digest = createHash("sha256").update(library).digest("base64url");
  const rows = fs.readFileSync(recordPath, "utf8").split(/\r?\n/);
  let updatedRows = 0;
  const updated = rows.map((row) => {
    if (!row.startsWith(`${relativeLibrary},`)) return row;
    updatedRows += 1;
    return `${relativeLibrary},sha256=${digest},${library.length}`;
  });
  if (updatedRows !== 1) fail(`Expected one sqlite-vec RECORD row for ${relativeLibrary}; found ${updatedRows}`);
  fs.writeFileSync(recordPath, updated.join("\n"), "utf8");
}

async function rebuildMacosSqliteVec(python, runtimeDir, workDir, arch) {
  const deploymentTarget = String(process.env.MACOSX_DEPLOYMENT_TARGET || MACOS_MINIMUM_SYSTEM_VERSION).trim();
  if (deploymentTarget !== MACOS_MINIMUM_SYSTEM_VERSION) {
    fail(`macOS sqlite-vec must be built for ${MACOS_MINIMUM_SYSTEM_VERSION}; got ${deploymentTarget}`);
  }

  const sitePackages = portableSitePackages(python, runtimeDir);
  const sqliteVecPackage = path.join(sitePackages, "sqlite_vec");
  const installedLibrary = path.join(sqliteVecPackage, "vec0.dylib");
  if (!fs.existsSync(installedLibrary)) {
    fail(`Installed sqlite-vec ${MACOS_SQLITE_VEC_SOURCE.version} library was not found: ${installedLibrary}`);
  }
  run(python, [
    "-c",
    `import sqlite_vec; assert sqlite_vec.__version__ == '${MACOS_SQLITE_VEC_SOURCE.version}', sqlite_vec.__version__`,
  ]);

  const archivePath = path.join(workDir, `sqlite-vec-${MACOS_SQLITE_VEC_SOURCE.version}-amalgamation.tar.gz`);
  const sourceDir = path.join(workDir, "sqlite-vec-source");
  fs.mkdirSync(sourceDir, { recursive: true });
  await download(MACOS_SQLITE_VEC_SOURCE.url, archivePath, MACOS_SQLITE_VEC_SOURCE.archive, "sqlite-vec source archive");
  run("tar", ["-xzf", archivePath, "-C", sourceDir]);
  const source = path.join(sourceDir, "sqlite-vec.c");
  if (!fs.existsSync(source)) fail(`Pinned sqlite-vec archive did not contain sqlite-vec.c`);

  const targetArch = arch === "x64" ? "x86_64" : "arm64";
  const compiledLibrary = path.join(sqliteVecPackage, "vec0.v8os.dylib");
  run("clang", [
    "-O3",
    "-fPIC",
    "-dynamiclib",
    "-Wl,-undefined,dynamic_lookup",
    "-arch",
    targetArch,
    `-mmacosx-version-min=${deploymentTarget}`,
    source,
    "-o",
    compiledLibrary,
  ]);
  fs.renameSync(compiledLibrary, installedLibrary);
  updateSqliteVecWheelRecord(sitePackages, installedLibrary);

  const noticesDir = path.join(runtimeDir, "THIRD_PARTY_NOTICES");
  fs.mkdirSync(noticesDir, { recursive: true });
  for (const license of MACOS_SQLITE_VEC_SOURCE.licenses) {
    const licensePath = path.join(workDir, `sqlite-vec-${license.name}`);
    await download(license.url, licensePath, license.sha256, `sqlite-vec ${license.name}`);
    fs.copyFileSync(
      licensePath,
      path.join(noticesDir, `sqlite-vec-${MACOS_SQLITE_VEC_SOURCE.version}-${license.name}`),
    );
  }
  fs.writeFileSync(
    path.join(noticesDir, `sqlite-vec-${MACOS_SQLITE_VEC_SOURCE.version}-SOURCE.txt`),
    `V8 Agent OS rebuilds sqlite-vec ${MACOS_SQLITE_VEC_SOURCE.version} from the official amalgamation source for macOS ${deploymentTarget}.\n`
      + `${MACOS_SQLITE_VEC_SOURCE.url}\nSHA256 ${MACOS_SQLITE_VEC_SOURCE.archive}\n`,
    "utf8",
  );
  run(python, [
    "-c",
    `import sqlite3, sqlite_vec; db = sqlite3.connect(':memory:'); db.enable_load_extension(True); sqlite_vec.load(db); version = db.execute('select vec_version()').fetchone()[0]; assert version == 'v${MACOS_SQLITE_VEC_SOURCE.version}', version; print('SQLITE_VEC_MACOS_REBUILD_OK')`,
  ]);
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
    await download(sourceUrl, archivePath, runtime.sha256, "Python runtime");
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
    // The upstream archive contains relative interpreter symlinks such as
    // bin/python3 -> python3.11. Node's default copies resolve those links
    // against the temporary extraction folder, which leaves the package
    // pointing at a directory removed in finally. Preserve the link text.
    fs.cpSync(installRoot, runtimeDir, { recursive: true, verbatimSymlinks: true });

    const python = pythonExecutable(runtimeDir);
    if (!python) fail(`Portable Python executable was not found under ${runtimeDir}`);
    const pythonAlias = path.join(runtimeDir, "bin", "python");
    if (!fs.existsSync(pythonAlias)) fs.symlinkSync("python3", pythonAlias);
    fs.chmodSync(python, 0o755);
    verifyPortablePythonLocation(python, runtimeDir);

    run(python, ["-m", "ensurepip", "--upgrade"]);
    run(python, ["-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--upgrade", "pip", "setuptools", "wheel"]);
    run(python, ["-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--prefer-binary", "-r", requirementsPath]);
    if (runtime.platform === "linux") {
      await installLinuxPyatspi(python, runtimeDir, workDir);
    } else if (runtime.platform === "darwin") {
      await rebuildMacosSqliteVec(python, runtimeDir, workDir, runtime.arch);
    }
    run(python, ["-m", "pip", "check"]);

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
