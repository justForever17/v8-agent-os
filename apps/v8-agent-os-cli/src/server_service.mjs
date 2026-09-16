import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { DEFAULT_PORTS, STATE_ROOT } from "./paths.mjs";
import { withFileLease } from "./process_state.mjs";

const execFileAsync = promisify(execFile);
export const SERVER_SERVICE_NAME = "v8os-server.service";
const ENGINE_PATH = "apps/v8-agent-os-engine";
const CLI_PATH = "apps/v8-agent-os-cli/bin/v8os.mjs";
const digest = (text) => crypto.createHash("sha256").update(text).digest("hex");

function absolutePath(value, label) {
  if (!value || !path.isAbsolute(value) || /[\x00-\x1f\x7f]/u.test(value)) {
    throw new Error(`${label} must be an absolute path without control characters`);
  }
  return path.resolve(value);
}

function quoteUnit(value, command = false) {
  let text = String(value);
  if (/[\x00-\x1f\x7f]/u.test(text)) throw new Error("systemd values must not contain control characters");
  text = text.replaceAll("\\", "\\\\").replaceAll('"', '\\"').replaceAll("%", "%%");
  if (command) text = text.replaceAll("$", () => "$$");
  return `"${text}"`;
}

export function inspectServerBundle(bundleRoot, { arch = process.arch } = {}) {
  const root = fs.realpathSync(absolutePath(bundleRoot, "--bundle"));
  let manifest;
  try { manifest = JSON.parse(fs.readFileSync(path.join(root, "server-manifest.json"), "utf8")); }
  catch { throw new Error("--bundle must contain a valid server-manifest.json from the independent server distribution"); }
  if (manifest.schema !== 1 || manifest.profile !== "server" || manifest.platform !== "linux"
      || manifest.arch !== arch || !/^\d{4}\.\d{2}\.\d{2}\.\d+$/u.test(manifest.version || "")
      || manifest.engine !== ENGINE_PATH || manifest.cli !== CLI_PATH) {
    throw new Error(`Unsupported server bundle manifest (expected schema 1, server, linux/${arch})`);
  }
  const engineDir = path.join(root, ENGINE_PATH);
  const python = path.join(engineDir, ".venv", "bin", "python3");
  for (const entry of [path.join(engineDir, "main.py"), path.join(root, CLI_PATH), python]) {
    try { if (!fs.statSync(entry).isFile()) throw new Error(); }
    catch { throw new Error(`Server bundle is not installed: missing ${path.relative(root, entry)}. Run its install.sh first.`); }
  }
  fs.accessSync(python, fs.constants.X_OK);
  return { bundleRoot: root, version: manifest.version, engineDir, python };
}

export function renderServerServiceUnit(record, { nodePath = process.execPath } = {}) {
  const engineDir = path.join(record.bundleRoot, ENGINE_PATH);
  const environment = {
    V8_AGENT_OS_HOME: record.stateRoot,
    V8_REPO_ROOT: record.bundleRoot,
    V8_ENGINE_DIR: engineDir,
    V8_AGENT_OS_CREDENTIAL_KEY_FILE: record.keyFile,
    ENGINE_INSTALL_PROFILE: "server",
    ENGINE_INSTALL_PLATFORM: "linux",
    ENGINE_STARTUP_PROFILE: "server",
    ENGINE_HOST: "127.0.0.1",
    ENGINE_PORT: String(record.port),
    ENGINE_RELOAD: "0",
    PYTHONUTF8: "1",
    PYTHONIOENCODING: "utf-8",
    PYTHONUNBUFFERED: "1",
    PLAYWRIGHT_BROWSERS_PATH: path.join(engineDir, ".playwright-browsers"),
    PATH: `${path.join(engineDir, ".venv", "bin")}:${path.dirname(nodePath)}:/usr/local/bin:/usr/bin:/bin`,
  };
  return [
    "# Managed by v8os service. Configuration and credentials remain owned by Engine.",
    "[Unit]", "Description=V8 Agent OS Linux server", "StartLimitIntervalSec=120", "StartLimitBurst=3", "",
    // WorkingDirectory is a single path, not an ExecStart-style quoted word list.
    "[Service]", "Type=exec", `WorkingDirectory=${engineDir.replaceAll("%", "%%")}`,
    // systemd does not expand environment variables in the executable word.
    `ExecStart=${quoteUnit(path.join(engineDir, ".venv", "bin", "python3"))} -X utf8 ${quoteUnit(path.join(engineDir, "main.py"), true)}`,
    ...Object.entries(environment).map(([key, value]) => `Environment=${quoteUnit(`${key}=${value}`)}`),
    "UnsetEnvironment=DISPLAY WAYLAND_DISPLAY ELECTRON_RUN_AS_NODE PYTHONPATH PYTHONHOME",
    "UMask=0077", "Restart=on-failure", "RestartSec=5", "TimeoutStopSec=45", "KillMode=control-group",
    "StandardOutput=journal", "StandardError=journal", "", "[Install]", "WantedBy=default.target", "",
  ].join("\n");
}

function readOwnedFile(filename) {
  try {
    if (!fs.lstatSync(filename).isFile()) throw new Error(`Refusing non-regular installation file: ${filename}`);
    return fs.readFileSync(filename, "utf8");
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

function atomicWrite(filename, text) {
  fs.mkdirSync(path.dirname(filename), { recursive: true, mode: 0o700 });
  const temporary = `${filename}.${process.pid}.${crypto.randomUUID()}.tmp`;
  try {
    fs.writeFileSync(temporary, text, { mode: 0o600, flag: "wx" });
    fs.renameSync(temporary, filename);
  } finally { fs.rmSync(temporary, { force: true }); }
}

function readReceipt(filename) {
  const text = readOwnedFile(filename);
  if (text === null) return null;
  let value;
  try { value = JSON.parse(text); } catch { throw new Error(`Invalid service installation receipt: ${filename}`); }
  if (value.schema !== 1 || !["ready", "pending"].includes(value.phase) || !value.current?.unit
      || digest(value.current.unit) !== value.current.unitSha256) {
    throw new Error(`Invalid service installation receipt: ${filename}`);
  }
  for (const record of [value.current, value.previous].filter(Boolean)) {
    if (digest(record.unit || "") !== record.unitSha256) throw new Error(`Invalid rollback unit in ${filename}`);
    for (const key of ["bundleRoot", "stateRoot", "keyFile"]) absolutePath(record[key], key);
  }
  return value;
}

function verifyKeyFile(keyFile, uid) {
  const filename = absolutePath(keyFile, "--key-file (or V8_AGENT_OS_CREDENTIAL_KEY_FILE)");
  let key;
  let directory;
  try { key = fs.lstatSync(filename); directory = fs.lstatSync(path.dirname(filename)); }
  catch { throw new Error("Server credential key is missing. Initialize it with v8os config credentials init --key-file <absolute-path> before installing the service."); }
  if (!key.isFile() || key.size !== 32 || !directory.isDirectory()
      || (process.platform === "linux" && ((key.mode & 0o777) !== 0o600 || (directory.mode & 0o777) !== 0o700
        || key.uid !== uid || directory.uid !== uid))) {
    throw new Error("Server credential key must be an owned 32-byte regular file with mode 0600 in an owned 0700 directory; its contents are never read by the CLI.");
  }
  return filename;
}

async function defaultRun(command, args) {
  try {
    const result = await execFileAsync(command, args, { encoding: "utf8", timeout: 60_000, maxBuffer: 128 * 1024 });
    return { code: 0, stdout: result.stdout, stderr: result.stderr };
  } catch (error) {
    return { code: error.code ?? 1, stdout: error.stdout || "", stderr: error.stderr || error.message };
  }
}

function commandError(command, result) {
  const details = String(result.stderr || result.stdout || `exit ${result.code}`).trim().replace(/[\x00-\x1f\x7f]/gu, " ").slice(0, 1000);
  return new Error(`${command} failed: ${details}. Diagnostics: journalctl --user -u ${SERVER_SERVICE_NAME} --no-pager -n 50`);
}

function processOwnsListeningPort(pid, port) {
  try {
    const inodes = new Set(fs.readdirSync(`/proc/${pid}/fd`).map((fd) => {
      try { return fs.readlinkSync(`/proc/${pid}/fd/${fd}`).match(/^socket:\[(\d+)\]$/u)?.[1]; }
      catch { return null; }
    }).filter(Boolean));
    return fs.readFileSync("/proc/net/tcp", "utf8").split("\n").slice(1).some((line) => {
      const columns = line.trim().split(/\s+/u);
      return columns[1] === `0100007F:${port.toString(16).toUpperCase().padStart(4, "0")}`
        && columns[3] === "0A" && inodes.has(columns[9]);
    });
  } catch { return false; }
}

export function createServerServiceManager(options = {}) {
  const platform = options.platform || process.platform;
  const uid = options.uid ?? process.getuid?.();
  const configHome = absolutePath(options.configHome || process.env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config"), "XDG_CONFIG_HOME");
  const stateRoot = absolutePath(options.stateRoot || STATE_ROOT, "V8_AGENT_OS_HOME");
  const unitPath = path.join(configHome, "systemd", "user", SERVER_SERVICE_NAME);
  const receiptPath = path.join(configHome, "v8-agent-os", "server-service.json");
  const lockPath = `${receiptPath}.lock`;
  const run = options.run || defaultRun;
  const fetchHealth = options.fetchHealth || (async (port) => {
    const response = await fetch(`http://127.0.0.1:${port}/health`, { signal: AbortSignal.timeout(2_000), redirect: "error" });
    return response.ok ? response.json() : null;
  });
  const pause = options.pause || ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  const ownsPort = options.ownsPort || processOwnsListeningPort;
  const now = options.now || Date.now;
  const timeoutMs = options.timeoutMs ?? 90_000;
  const ctl = async (...args) => {
    const result = await run("systemctl", ["--user", "--no-pager", "--no-ask-password", ...args]);
    if (result.code !== 0) throw commandError(`systemctl --user ${args[0]}`, result);
    return String(result.stdout || "");
  };
  const save = (receipt) => atomicWrite(receiptPath, `${JSON.stringify(receipt, null, 2)}\n`);
  const journal = `journalctl --user -u ${SERVER_SERVICE_NAME} --no-pager -n 50`;

  async function requireManager({ linger = false } = {}) {
    if (platform !== "linux") throw new Error("v8os service requires Linux with a systemd user manager");
    if (uid === 0) throw new Error("Install the server service as a regular Linux user; do not run v8os service through sudo");
    await ctl("show", "--property=Version");
    if (linger) {
      const result = await run("loginctl", ["show-user", String(uid), "--property=Linger", "--value"]);
      if (result.code !== 0 || String(result.stdout).trim() !== "yes") {
        throw new Error(`Persistent user service requires linger. Ask the server administrator to run: loginctl enable-linger ${uid}. Then retry; no service or credential was changed.`);
      }
    }
  }

  async function inspectUnit() {
    const output = await ctl("show", SERVER_SERVICE_NAME,
      "--property=LoadState,ActiveState,SubState,MainPID,UnitFileState,FragmentPath,DropInPaths,Result");
    return Object.fromEntries(output.trim().split("\n").filter((line) => line.includes("=")).map((line) => {
      const index = line.indexOf("=");
      return [line.slice(0, index), line.slice(index + 1)];
    }));
  }

  function ownedReceipt({ allowPending = false } = {}) {
    const receipt = readReceipt(receiptPath);
    const unit = readOwnedFile(unitPath);
    if (!receipt && unit !== null) throw new Error(`Refusing unmanaged unit ${unitPath}; no matching V8OS installation receipt exists`);
    if (!receipt) return null;
    if (receipt.current.stateRoot !== stateRoot) throw new Error(`Service belongs to a different V8_AGENT_OS_HOME: ${receipt.current.stateRoot}`);
    const allowedHashes = [receipt.current, receipt.phase === "pending" ? receipt.previous : null].filter(Boolean).map((record) => record.unitSha256);
    if (unit !== null && !allowedHashes.includes(digest(unit))) throw new Error(`Service unit was modified outside v8os: ${unitPath}. Reconcile it before continuing.`);
    if (unit === null && receipt.phase !== "pending") throw new Error(`Service unit is missing: ${unitPath}; restore it from the installation receipt before continuing`);
    if (receipt.phase === "pending" && !allowPending) throw new Error("An interrupted service installation is pending. Run v8os service rollback to restore the previous installation.");
    return receipt;
  }

  function verifyLoadedUnit(unit) {
    if (unit.FragmentPath !== unitPath || unit.DropInPaths) {
      throw new Error(`systemd resolved another unit or an external override for ${SERVER_SERVICE_NAME}; no lifecycle action was applied`);
    }
  }

  async function ready(record) {
    const deadline = now() + timeoutMs;
    let lastState = "starting";
    while (now() < deadline) {
      const state = await inspectUnit();
      verifyLoadedUnit(state);
      lastState = `${state.ActiveState}/${state.SubState}`;
      if (["failed", "inactive"].includes(state.ActiveState)) throw new Error(`Server exited before readiness (${lastState}; ${state.Result || "unknown"}). See ${journal}`);
      if (state.ActiveState === "active" && Number(state.MainPID) > 0) {
        let health;
        try { health = await fetchHealth(record.port); } catch { /* Engine may still be importing or binding. */ }
        if (health?.status === "ok" && health.service === "v8-agent-os-engine" && health.startupProfile === "server"
            && health.engineRuntime?.managedRuntimeRoot === path.join(record.bundleRoot, ENGINE_PATH, ".venv")
            && health.engineRuntime?.reload === false) {
          const settled = await inspectUnit();
          if (settled.ActiveState === "active" && settled.MainPID === state.MainPID
              && await ownsPort(Number(settled.MainPID), record.port)) return settled;
        }
      }
      await pause(250);
    }
    throw new Error(`Server readiness timed out (${lastState}); /health must identify this server bundle and its non-reloading runtime. See ${journal}`);
  }

  async function stop() {
    await ctl("stop", SERVER_SERVICE_NAME);
    const state = await inspectUnit();
    if (Number(state.MainPID) !== 0 || !["inactive", "failed"].includes(state.ActiveState) || state.Result === "timeout") {
      throw new Error(`Server stop was not clean (${state.ActiveState}, ${state.Result}, pid=${state.MainPID}); installation and data retained. See ${journal}`);
    }
  }

  async function restore(receipt) {
    const currentUnit = readOwnedFile(unitPath);
    if (currentUnit !== null) {
      await ctl("daemon-reload");
      const loaded = await inspectUnit();
      verifyLoadedUnit(loaded);
      if (Number(loaded.MainPID) > 0 || !["inactive", "failed"].includes(loaded.ActiveState)) await stop();
    }
    if (receipt.previous) {
      atomicWrite(unitPath, receipt.previous.unit);
      await ctl("daemon-reload");
      await ctl(receipt.previous.enabled ? "enable" : "disable", SERVER_SERVICE_NAME);
      if (receipt.previous.active) {
        await ctl("reset-failed", SERVER_SERVICE_NAME);
        await ctl("start", SERVER_SERVICE_NAME);
        await ready(receipt.previous);
      }
      save({ schema: 1, phase: "ready", current: receipt.previous, previous: null });
      return "previous_service_restored";
    }
    if (currentUnit !== null) {
      await ctl("disable", SERVER_SERVICE_NAME);
      fs.unlinkSync(unitPath);
      await ctl("daemon-reload");
    }
    fs.rmSync(receiptPath, { force: true });
    return "installation_removed";
  }

  async function perform(action, input = {}) {
    if (!["install", "upgrade", "start", "stop", "restart", "status", "uninstall", "rollback"].includes(action)) throw new Error(`Unknown service action: ${action}`);
    await requireManager({ linger: ["install", "upgrade", "start", "restart"].includes(action) });
    if (action === "status") {
      const receipt = ownedReceipt({ allowPending: true });
      if (!receipt) return { status: "not_installed", unit: SERVER_SERVICE_NAME, stateRoot };
      const state = await inspectUnit();
      if (receipt.phase !== "pending" || state.LoadState !== "not-found") verifyLoadedUnit(state);
      const linger = await run("loginctl", ["show-user", String(uid), "--property=Linger", "--value"]);
      return { status: receipt.phase === "pending" ? "recovery_required" : state.ActiveState,
        version: receipt.current.version, bundleRoot: receipt.current.bundleRoot, stateRoot,
        unit: SERVER_SERVICE_NAME, mainPid: Number(state.MainPID) || null, enabled: state.UnitFileState === "enabled",
        persistentAfterLogout: linger.code === 0 && String(linger.stdout).trim() === "yes", journal };
    }
    return withFileLease(lockPath, async () => {
      const receipt = ownedReceipt({ allowPending: action === "rollback" });
      if (["install", "upgrade"].includes(action)) {
        const bundle = inspectServerBundle(input.bundleRoot, { arch: options.arch || process.arch });
        if (action === "install" && receipt) {
          if (receipt.current.bundleRoot !== bundle.bundleRoot) throw new Error("Server service is already installed; use v8os service upgrade --bundle <new-version-directory>");
          if (receipt.current.version !== bundle.version) throw new Error("Installed bundle was replaced in place; restore it and install new releases in separate directories");
          if ((input.keyFile && path.resolve(input.keyFile) !== receipt.current.keyFile)
              || (input.port !== undefined && input.port !== receipt.current.port)) throw new Error("Service is already installed with different options; use service upgrade with a new bundle directory");
          return { status: "already_installed", version: receipt.current.version, stateRoot };
        }
        if (action === "upgrade" && !receipt) throw new Error("Server service is not installed; run v8os service install first");
        if (receipt?.current.bundleRoot === bundle.bundleRoot) {
          if (receipt.current.version !== bundle.version) throw new Error("In-place bundle replacement cannot be rolled back. Extract and install the new release in a separate directory.");
          if ((input.keyFile && path.resolve(input.keyFile) !== receipt.current.keyFile)
              || (input.port !== undefined && input.port !== receipt.current.port)) throw new Error("Service upgrade requires a separate bundle directory to change installation options");
          return { status: "already_current", version: bundle.version, stateRoot };
        }
        const keyFile = verifyKeyFile(input.keyFile || receipt?.current.keyFile || process.env.V8_AGENT_OS_CREDENTIAL_KEY_FILE, uid);
        if (receipt && keyFile !== receipt.current.keyFile) throw new Error("Service upgrade cannot change the credential key; use the existing Engine credential owner for key changes");
        const port = input.port ?? receipt?.current.port ?? DEFAULT_PORTS.engine;
        if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("--port must be an integer from 1 to 65535");
        const current = { bundleRoot: bundle.bundleRoot, version: bundle.version, stateRoot, keyFile, port };
        current.unit = renderServerServiceUnit(current, { nodePath: options.nodePath || process.execPath });
        current.unitSha256 = digest(current.unit);
        let previous = null;
        const state = await inspectUnit();
        if (receipt) {
          verifyLoadedUnit(state);
          if (!["active", "inactive", "failed"].includes(state.ActiveState)) throw new Error(`Service is busy (${state.ActiveState}); retry when its current systemd job completes`);
          previous = { ...receipt.current, active: state.ActiveState === "active", enabled: state.UnitFileState === "enabled" };
        } else if (state.LoadState !== "not-found") {
          throw new Error(`An unmanaged ${SERVER_SERVICE_NAME} is already loaded; refusing to replace it`);
        }
        const pending = { schema: 1, phase: "pending", current, previous };
        save(pending);
        try {
          if (previous) await stop();
          atomicWrite(unitPath, current.unit);
          await ctl("daemon-reload");
          verifyLoadedUnit(await inspectUnit());
          await ctl("enable", SERVER_SERVICE_NAME);
          await ctl("reset-failed", SERVER_SERVICE_NAME);
          await ctl("start", SERVER_SERVICE_NAME);
          const running = await ready(current);
          save({ ...pending, phase: "ready" });
          return { status: action === "install" ? "installed" : "upgraded", version: current.version,
            bundleRoot: current.bundleRoot, stateRoot, mainPid: Number(running.MainPID), persistentAfterLogout: true,
            rollbackAvailable: Boolean(previous), journal };
        } catch (error) {
          let recovery;
          try {
            recovery = await restore(pending);
          } catch (recoveryError) {
            throw new Error(`${action} failed: ${error.message}. Rollback failed: ${recoveryError.message}. Receipt retained; run v8os service rollback after resolving the fault.`);
          }
          throw new Error(`${action} failed: ${error.message}. Recovery: ${recovery}; configuration, credentials and bundle directories preserved.`);
        }
      }
      if (!receipt) {
        if (action === "uninstall" || action === "rollback") return { status: "not_installed", stateRoot };
        throw new Error("Server service is not installed; run v8os service install first");
      }
      if (action === "rollback") {
        if (receipt.phase === "ready" && !receipt.previous) throw new Error("No previous service version is available for rollback");
        save({ ...receipt, phase: "pending" });
        return { status: await restore(receipt), stateRoot, dataPreserved: true };
      }
      const existingState = await inspectUnit();
      verifyLoadedUnit(existingState);
      if (action === "stop" || action === "uninstall") {
        if (action === "uninstall") save({ ...receipt, phase: "pending", previous: {
          ...receipt.current, active: existingState.ActiveState === "active", enabled: existingState.UnitFileState === "enabled",
        } });
        await stop();
        if (action === "uninstall") {
          await ctl("disable", SERVER_SERVICE_NAME);
          fs.unlinkSync(unitPath);
          await ctl("daemon-reload");
          fs.unlinkSync(receiptPath);
        }
        return { status: action === "stop" ? "stopped" : "uninstalled", stateRoot, dataPreserved: true, bundlesPreserved: true };
      }
      verifyKeyFile(receipt.current.keyFile, uid);
      inspectServerBundle(receipt.current.bundleRoot, { arch: options.arch || process.arch });
      await ctl("reset-failed", SERVER_SERVICE_NAME);
      await ctl(action, SERVER_SERVICE_NAME);
      const state = await ready(receipt.current);
      return { status: action === "start" ? "started" : "restarted", stateRoot, mainPid: Number(state.MainPID), journal };
    });
  }
  return { perform, unitPath, receiptPath };
}

export async function commandServerService(args) {
  const explicitAction = args[0] && !args[0].startsWith("--");
  const action = explicitAction ? args[0] : "status";
  const json = args.includes("--json");
  try {
    const input = {};
    for (let index = explicitAction ? 1 : 0; index < args.length; index += 1) {
      const flag = args[index];
      if (flag === "--json") continue;
      const key = { "--bundle": "bundleRoot", "--key-file": "keyFile", "--port": "port" }[flag];
      if (!key || !args[index + 1] || args[index + 1].startsWith("--")) throw new Error(`Invalid service option: ${flag}`);
      input[key] = key === "port" ? Number(args[++index]) : args[++index];
    }
    if (Object.keys(input).length && !["install", "upgrade"].includes(action)) throw new Error("--bundle, --key-file and --port only apply to service install/upgrade");
    const result = await createServerServiceManager().perform(action, input);
    if (json) console.log(JSON.stringify(result, null, 2));
    else {
      console.log(`${SERVER_SERVICE_NAME}: ${result.status}${result.version ? ` (${result.version})` : ""}`);
      console.log(`Engine state: ${result.stateRoot}`);
      if (result.dataPreserved) console.log("Configuration, credentials, data and extracted bundles were preserved.");
      if (result.persistentAfterLogout === false) console.log("Linger is disabled; this user service is not guaranteed to survive logout.");
      if (result.journal) console.log(`Logs: ${result.journal}`);
    }
    if (["failed", "recovery_required"].includes(result.status)) process.exitCode = 1;
    return result;
  } catch (error) {
    if (!json) throw error;
    console.log(JSON.stringify({ status: "failed", action, error: error.message }, null, 2));
    process.exitCode = 1;
    return { status: "failed" };
  }
}
