import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "..", "..");
const crateRoot = path.join(repoRoot, "apps", "v8-agent-os-engine", "native", "v8-sandbox-host");
const manifestPath = path.join(crateRoot, "Cargo.toml");
const outputRoot = path.join(repoRoot, "apps", "v8-agent-os-engine", "bin");
const maxBytes = 5 * 1024 * 1024;

function parseArgs(argv) {
  const result = { target: "", force: false, check: false };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--target") result.target = String(argv[++index] || "").trim();
    else if (value === "--force") result.force = true;
    else if (value === "--check") result.check = true;
    else throw new Error(`Unknown argument: ${value}`);
  }
  return result;
}

function executableName(target = "") {
  return target.includes("windows") || (!target && process.platform === "win32")
    ? "v8-sandbox-host.exe"
    : "v8-sandbox-host";
}

function sourcePath(target = "") {
  return target
    ? path.join(crateRoot, "target", target, "release", executableName(target))
    : path.join(crateRoot, "target", "release", executableName());
}

function latestSourceMtime() {
  const candidates = [
    manifestPath,
    path.join(crateRoot, "Cargo.lock"),
    path.join(crateRoot, "src", "main.rs"),
  ];
  return Math.max(...candidates.map((item) => fs.statSync(item).mtimeMs));
}

function verifyBinary(filePath) {
  if (!fs.existsSync(filePath)) throw new Error(`Sandbox host is missing: ${filePath}`);
  const size = fs.statSync(filePath).size;
  if (size <= 0 || size > maxBytes) {
    throw new Error(`Sandbox host size ${size} is outside the allowed 1..${maxBytes} byte range.`);
  }
  if (process.platform !== "win32") fs.chmodSync(filePath, 0o755);
  return size;
}

export function buildSandboxHost(options = {}) {
  const target = String(options.target || "").trim();
  const destination = path.join(outputRoot, executableName(target));
  const isFresh = fs.existsSync(destination) && fs.statSync(destination).mtimeMs >= latestSourceMtime();
  if (options.check) {
    return { destination, size: verifyBinary(destination), built: false, target: target || null };
  }
  if (!options.force && isFresh) {
    return { destination, size: verifyBinary(destination), built: false, target: target || null };
  }
  const cargo = process.platform === "win32" ? "cargo.exe" : "cargo";
  const args = ["build", "--locked", "--release", "--manifest-path", manifestPath];
  if (target) args.push("--target", target);
  const result = spawnSync(cargo, args, {
    cwd: repoRoot,
    stdio: "inherit",
    windowsHide: true,
    shell: false,
  });
  if (result.error?.code === "ENOENT") {
    throw new Error("Rust/Cargo is required to build the source-tree sandbox host. Packaged V8OS includes the prebuilt helper.");
  }
  if (result.status !== 0) throw new Error(`Sandbox host build failed with exit code ${result.status}.`);
  const source = sourcePath(target);
  verifyBinary(source);
  fs.mkdirSync(outputRoot, { recursive: true });
  fs.copyFileSync(source, destination);
  const size = verifyBinary(destination);
  return { destination, size, built: true, target: target || null };
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  try {
    const result = buildSandboxHost(parseArgs(process.argv.slice(2)));
    process.stdout.write(`${JSON.stringify(result)}\n`);
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  }
}
