import crypto from "node:crypto";
import fs from "node:fs";
import { spawn } from "node:child_process";
import path from "node:path";
import { ensureDir } from "./json_file.mjs";
import { COMPONENTS, componentRuntimePorts, configureComponentRuntimePorts, logPathsFor } from "./components.mjs";
import { LOG_DIR, REPO_ROOT, STATE_ROOT } from "./paths.mjs";
import { isPortOpen } from "./ports.mjs";
import { readRuntimePorts, resolveRuntimePorts } from "./runtime_ports.mjs";
import {
  compareAndSwapProcessRecord,
  isPidAlive,
  processRecordIdentity,
  processRecordMatchesIdentity,
  readProcessState,
  withComponentProcessLease,
  withRuntimePortsLease,
} from "./process_state.mjs";

export const WINDOWS_PROCESS_PROBE_TIMEOUT_MS = 10_000;
export const SHELL_TERMINATION_TIMEOUT_MS = 20_000;
export const DESKTOP_PET_TERMINATION_TIMEOUT_MS = 10_000;
export const MANAGED_SHELL_SHUTDOWN_ARG = "--v8os-managed-shutdown";
export const MANAGED_SHELL_SHUTDOWN_TIMEOUT_MS = 30_000;
const RUNTIME_HANDOFF_DIR = path.join(STATE_ROOT, "runtime", "cli", "handoffs");

export function managedStopOptions(componentId, platform = process.platform, options = {}) {
  const forceDesktopPet = componentId === "desktop-pet" && options.force === true;
  return {
    tree: componentId !== "shell",
    timeoutMs: componentId === "shell"
      ? SHELL_TERMINATION_TIMEOUT_MS
      : componentId === "desktop-pet" && !forceDesktopPet ? DESKTOP_PET_TERMINATION_TIMEOUT_MS : undefined,
    // CLI `stop --only shell` is a component restart primitive, not the user-facing
    // V8OS quit flow. On POSIX, SIGTERM enters Electron's governed global shutdown
    // and can wait for an interactive retry dialog. A verified Shell process group
    // must therefore use the same force-stop semantics as Windows taskkill /F.
    signal: platform !== "win32" && (componentId === "shell" || forceDesktopPet) ? "SIGKILL" : "SIGTERM",
  };
}

function componentHasPort(component) {
  return Number.isInteger(component?.port) && component.port > 0;
}

function normalizeProcessText(value) {
  const normalized = String(value || "").replaceAll("/", "\\");
  return process.platform === "win32" ? normalized.toLowerCase() : normalized;
}

function normalizeResolvedProcessPath(value) {
  const resolved = path.resolve(String(value || ""));
  try {
    return normalizeProcessText(fs.realpathSync(resolved));
  } catch {
    return normalizeProcessText(resolved);
  }
}

function positivePid(value) {
  const pid = Number(value);
  return Number.isInteger(pid) && pid > 0 ? pid : null;
}

function runChildCommand(command, args, options = {}) {
  const timeoutMs = Math.max(100, Number(options.timeoutMs) || 2_500);
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(command, args, {
        cwd: options.cwd,
        env: options.env,
        windowsHide: true,
        stdio: ["ignore", "pipe", "pipe"],
      });
    } catch (error) {
      resolve({ status: null, stdout: "", stderr: "", error, timedOut: false });
      return;
    }
    let stdout = "";
    let stderr = "";
    let settled = false;
    let timer = null;
    const settle = (result) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      resolve({ stdout, stderr, error: null, timedOut: false, ...result });
    };
    child.stdout?.on("data", (chunk) => { stdout += chunk; });
    child.stderr?.on("data", (chunk) => { stderr += chunk; });
    child.once("error", (error) => settle({ status: null, error }));
    child.once("close", (status, signal) => settle({ status, signal }));
    timer = setTimeout(() => {
      try {
        child.kill();
      } catch {}
      settle({ status: null, timedOut: true, error: new Error(`Command timed out after ${timeoutMs}ms`) });
    }, timeoutMs);
  });
}

export function runWindowsProcessProbe(script, runner = runChildCommand) {
  return runner("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script], {
    timeoutMs: WINDOWS_PROCESS_PROBE_TIMEOUT_MS,
  });
}

function waitForSpawn(child) {
  return new Promise((resolve, reject) => {
    child.once("spawn", resolve);
    child.once("error", reject);
  });
}

function spawnErrorCode(error) {
  const code = String(error?.code || "").trim();
  return /^[A-Z0-9_]+$/.test(code) ? code : "SPAWN_ERROR";
}

export async function spawnManagedChild(id, commandSpec, logs, spawnImpl = spawn) {
  const out = fs.openSync(logs.out, "a");
  const err = fs.openSync(logs.err, "a");
  try {
    const child = spawnImpl(commandSpec.command, commandSpec.args, {
      cwd: commandSpec.cwd,
      env: { ...process.env, ...commandSpec.env },
      detached: true,
      stdio: ["ignore", out, err],
      windowsHide: true,
    });
    await waitForSpawn(child);
    return { child, failure: null };
  } catch (error) {
    const errorCode = spawnErrorCode(error);
    return {
      child: null,
      failure: {
        id,
        status: "spawn_failed",
        stage: "spawn",
        reason: errorCode.toLowerCase(),
        errorCode,
        exitCode: null,
        signal: null,
        logOut: logs.out,
        logErr: logs.err,
      },
    };
  } finally {
    fs.closeSync(out);
    fs.closeSync(err);
  }
}

export function observeEarlyProcessExit(child, timeoutMs = 350) {
  const existingExitCode = child?.exitCode;
  const existingSignal = child?.signalCode;
  if (existingExitCode !== null && existingExitCode !== undefined) {
    return Promise.resolve({ exited: true, exitCode: existingExitCode, signal: existingSignal || null });
  }
  if (existingSignal) {
    return Promise.resolve({ exited: true, exitCode: null, signal: existingSignal });
  }
  return new Promise((resolve) => {
    let timer = null;
    const finish = (result) => {
      if (timer) clearTimeout(timer);
      child?.off?.("exit", onExit);
      child?.off?.("error", onError);
      resolve(result);
    };
    const onExit = (exitCode, signal) => finish({ exited: true, exitCode, signal: signal || null });
    const onError = (error) => finish({ exited: true, exitCode: null, signal: null, error });
    child?.once?.("exit", onExit);
    child?.once?.("error", onError);
    timer = setTimeout(() => finish({ exited: false, exitCode: null, signal: null }), Math.max(0, timeoutMs));
  });
}

export async function waitForRuntimeComponentHandoff(componentId, child, options = {}) {
  const timeoutMs = Number.isFinite(options.timeoutMs) ? Math.max(1, options.timeoutMs) : 20_000;
  const pollMs = Number.isFinite(options.pollMs) ? Math.max(1, options.pollMs) : 50;
  const readRuntime = options.readRuntimeDescriptor || runtimeProcessDescriptor;
  const readDescriptor = options.readProcessDescriptor || readProcessDescriptor;
  const pidAlive = options.pidIsAlive || isPidAlive;
  const receiptContract = options.receiptContract || null;
  const readReceipt = options.readReceipt || readRuntimeHandoffReceipt;
  const sleep = options.sleep || ((delayMs) => new Promise((resolve) => setTimeout(resolve, delayMs)));
  const deadline = Date.now() + timeoutMs;
  let lastReason = "runtime_descriptor_missing";
  let candidatePid = null;
  while (Date.now() < deadline) {
    const runtimeDescriptor = readRuntime(componentId);
    const runtimePid = positivePid(runtimeDescriptor?.pid);
    if (runtimePid) {
      const descriptorContractValid = componentId !== "desktop-pet"
        || (runtimeDescriptor?.managedByShell === true
          && typeof runtimeDescriptor?.descriptorId === "string"
          && runtimeDescriptor.descriptorId.length > 0);
      const processDescriptor = await readDescriptor(runtimePid);
      if (descriptorContractValid
        && pidAlive(runtimePid)
        && verifiedRuntimeComponentPid(componentId, processDescriptor, runtimeDescriptor) === runtimePid) {
        candidatePid = runtimePid;
        const receipt = receiptContract ? readReceipt(receiptContract) : null;
        if (!receiptContract || receipt?.pid === runtimePid) {
          return { ok: true, pid: runtimePid, runtimeDescriptor, processDescriptor };
        }
        lastReason = receipt ? "runtime_handoff_receipt_mismatch" : "runtime_handoff_receipt_missing";
      } else {
        lastReason = !descriptorContractValid
          ? "runtime_descriptor_invalid"
          : processDescriptor ? "runtime_identity_mismatch" : "runtime_identity_unavailable";
      }
    }
    if (child?.signalCode) {
      return { ok: false, reason: "launcher_signalled", exitCode: null, signal: child.signalCode };
    }
    if (child?.exitCode !== null && child?.exitCode !== undefined && child.exitCode !== 0) {
      return { ok: false, reason: "launcher_exited", exitCode: child.exitCode, signal: null };
    }
    await sleep(pollMs);
  }
  return {
    ok: false,
    reason: lastReason === "runtime_descriptor_missing" ? "runtime_handoff_timeout" : lastReason,
    exitCode: Number.isInteger(child?.exitCode) ? child.exitCode : null,
    signal: child?.signalCode || null,
    candidatePid,
  };
}

function createRuntimeHandoffReceipt(componentId) {
  if (componentId !== "desktop-pet") return null;
  const nonce = crypto.randomUUID();
  ensureDir(RUNTIME_HANDOFF_DIR);
  const filePath = path.join(RUNTIME_HANDOFF_DIR, `${componentId}-${nonce}.json`);
  fs.rmSync(filePath, { force: true });
  return { componentId, nonce, filePath };
}

function readRuntimeHandoffReceipt(contract) {
  if (!contract?.filePath || !fs.existsSync(contract.filePath)) return null;
  try {
    if (fs.statSync(contract.filePath).size > 1_024) return null;
    const payload = JSON.parse(fs.readFileSync(contract.filePath, "utf8"));
    const pid = positivePid(payload?.pid);
    if (payload?.version !== 1
      || payload?.componentId !== contract.componentId
      || payload?.nonce !== contract.nonce
      || !pid) return null;
    return { ...payload, pid };
  } catch {
    return null;
  }
}

export async function cleanupFailedRuntimeHandoff(componentId, child, contract, options = {}) {
  const readReceipt = options.readReceipt || readRuntimeHandoffReceipt;
  const pidAlive = options.pidIsAlive || isPidAlive;
  const describe = options.readProcessDescriptor || readProcessDescriptor;
  const verify = options.verifyRuntimePid || verifiedRuntimeComponentPid;
  const terminate = options.killPid || killPid;
  const removeDescriptor = options.removeRuntimeDescriptor || removeRuntimeDescriptor;
  const runtimeDescriptor = options.runtimeDescriptor || runtimeProcessDescriptor(componentId);
  try {
    const receipt = readReceipt(contract);
    const runtimePids = [...new Set([receipt?.pid, positivePid(options.candidatePid)].filter(Boolean))];
    for (const runtimePid of runtimePids) {
      if (pidAlive(runtimePid)) {
        const descriptor = await describe(runtimePid);
        if (verify(componentId, descriptor, runtimeDescriptor) === runtimePid) {
          await terminate(runtimePid, { tree: true });
          removeDescriptor(componentId, runtimePid);
        }
      }
    }
    const launcherPid = positivePid(child?.pid);
    if (launcherPid && child?.exitCode === null && !child?.signalCode && pidAlive(launcherPid)) {
      await terminate(launcherPid, { tree: true });
    }
  } finally {
    if (contract?.filePath) fs.rmSync(contract.filePath, { force: true });
  }
}

async function waitForPidExit(pid, timeoutMs = 2_500) {
  const deadline = Date.now() + timeoutMs;
  while (isPidAlive(pid) && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 25));
  return !isPidAlive(pid);
}

function readPosixProcessStartToken(pid) {
  try {
    const stat = fs.readFileSync(`/proc/${pid}/stat`, "utf8");
    const fieldsAfterCommand = stat.slice(stat.lastIndexOf(")") + 1).trim().split(/\s+/);
    const startTicks = fieldsAfterCommand[19];
    return startTicks ? `proc:${startTicks}` : null;
  } catch {
    return null;
  }
}

function processExecutableMatchesCommand(candidate, command) {
  const executable = normalizeProcessText(candidate?.executablePath);
  const expected = normalizeProcessText(command);
  if (!executable || !expected) return false;
  if (path.isAbsolute(String(command))) {
    if (path.isAbsolute(String(candidate?.executablePath || ""))
      && normalizeResolvedProcessPath(candidate.executablePath) === normalizeResolvedProcessPath(command)) return true;
    // POSIX `ps -o comm=` exposes only the executable basename on platforms
    // without /proc. Accept that weaker executable evidence only when it is
    // explicitly tagged by our POSIX reader; component signature and cwd
    // checks still have to pass before a PID becomes managed.
    if (candidate?.executablePathKind !== "posix_comm") return false;
  }
  const executableName = path.win32.basename(executable).replace(/\.exe$/i, "");
  const expectedName = path.win32.basename(expected).replace(/\.exe$/i, "");
  return executableName === expectedName;
}

function processCandidateMatchesJavaScriptRuntime(candidate, expectedCommand) {
  if (processExecutableMatchesCommand(candidate, expectedCommand)) return true;
  // The preview CLI launches Next with Node, while the resident Shell imports
  // this module from Electron. Identity must describe the candidate process,
  // not whichever JavaScript host happens to be doing the verification.
  const executableName = path.win32.basename(normalizeProcessText(candidate?.executablePath)).replace(/\.exe$/i, "");
  if (executableName === "node") return true;
  if (executableName !== "electron") return false;
  const executable = normalizeResolvedProcessPath(candidate.executablePath);
  const controlledElectronRoot = normalizeResolvedProcessPath(path.join(
    REPO_ROOT,
    "apps",
    "v8-agent-os-desktop-pet",
    "node_modules",
    "electron",
  ));
  return executable.startsWith(`${controlledElectronRoot}\\`);
}

function processCandidateMatchesEngine(candidate, commandSpec) {
  if (!candidate || typeof candidate !== "object") return false;
  const executable = normalizeProcessText(candidate.executablePath);
  const commandLine = normalizeProcessText(candidate.commandLine);
  const canonicalExecutable = normalizeProcessText(path.resolve(commandSpec.command));
  const canonicalEngineDir = normalizeProcessText(path.resolve(commandSpec.cwd));
  const engineSignature = /(?:^|\s)(?:-m\s+uvicorn\s+main:app|[^\s]*main\.py)(?:\s|$)/i.test(commandLine);
  const ownedRuntime = executable === canonicalExecutable
    || executable.startsWith(`${canonicalEngineDir}\\`)
    || commandLine.includes(canonicalEngineDir);
  return engineSignature && ownedRuntime;
}

function processCandidateMatchesNextApp(componentId, candidate, expectedPort = COMPONENTS[componentId]?.port) {
  if (!candidate || typeof candidate !== "object" || !["admin", "web"].includes(componentId)) return false;
  const commandLine = normalizeProcessText(candidate.commandLine);
  const appDir = normalizeProcessText(path.join(REPO_ROOT, "apps", `v8-agent-os-${componentId}`));
  const port = Number(expectedPort);
  const runtimeMatches = processCandidateMatchesJavaScriptRuntime(
    candidate,
    COMPONENTS[componentId].command({ mode: "start" }).command,
  );
  const standaloneSignature = commandLine.includes(`${appDir}\\.next\\standalone\\server.js`);
  const launcherSignature = commandLine.includes("scripts\\run-next-with-managed-auth.mjs")
    && commandLine.includes(`--app ${componentId}`)
    && commandLine.includes(`--port ${port}`);
  return runtimeMatches && (standaloneSignature || launcherSignature);
}

function packagedRepoRootForExecutable(executablePath, platform = process.platform) {
  if (!path.isAbsolute(String(executablePath || ""))) return null;
  const executableDir = path.dirname(path.resolve(String(executablePath)));
  return platform === "darwin"
    ? path.resolve(executableDir, "..", "Resources", "v8os")
    : path.resolve(executableDir, "resources", "v8os");
}

export function packagedRuntimeDescriptorMatches(componentId, candidate, runtimeDescriptor, options = {}) {
  const governedRepoRoot = path.resolve(options.repoRoot || REPO_ROOT);
  const platform = options.platform || process.platform;
  if (!candidate || !runtimeDescriptor || !["shell", "desktop-pet"].includes(componentId)) return false;
  if (runtimeDescriptor.packaged !== true || runtimeDescriptor.runtimeKind !== componentId) return false;
  if (!positivePid(candidate.pid) || positivePid(runtimeDescriptor.pid) !== positivePid(candidate.pid)) return false;
  if (!path.isAbsolute(String(candidate.executablePath || ""))
    || !path.isAbsolute(String(runtimeDescriptor.executablePath || ""))
    || !path.isAbsolute(String(runtimeDescriptor.repoRoot || ""))) return false;
  const candidateExecutable = normalizeResolvedProcessPath(candidate.executablePath);
  const declaredExecutable = normalizeResolvedProcessPath(runtimeDescriptor.executablePath);
  const declaredRepoRoot = normalizeResolvedProcessPath(runtimeDescriptor.repoRoot);
  const derivedRepoRoot = normalizeResolvedProcessPath(
    packagedRepoRootForExecutable(runtimeDescriptor.executablePath, platform),
  );
  const commandLine = normalizeProcessText(candidate.commandLine);
  const desktopPetMain = normalizeProcessText(path.join(
    governedRepoRoot,
    "apps",
    "v8-agent-os-desktop-pet",
    "electron",
    "main.cjs",
  ));
  const packagedNodeEntries = [
    desktopPetMain,
    normalizeProcessText(path.join(governedRepoRoot, "apps", "v8-agent-os-desktop-pet", "dist", "server.cjs")),
    normalizeProcessText(path.join(governedRepoRoot, "apps", "v8-agent-os-cli", "src", "cli.mjs")),
    normalizeProcessText(path.join(governedRepoRoot, "scripts", "run-next-with-managed-auth.mjs")),
  ];
  const roleMatches = componentId === "desktop-pet"
    ? commandLine.includes(desktopPetMain)
    : !packagedNodeEntries.some((entry) => commandLine.includes(entry))
      && !/(?:^|\s)--type=(?:renderer|gpu-process|utility)(?:\s|$)/i.test(commandLine);
  return candidateExecutable === declaredExecutable
    && declaredRepoRoot === normalizeResolvedProcessPath(governedRepoRoot)
    && derivedRepoRoot === declaredRepoRoot
    && roleMatches;
}

function processCandidateMatchesShell(candidate, runtimeDescriptor = null) {
  if (!candidate || typeof candidate !== "object") return false;
  if (packagedRuntimeDescriptorMatches("shell", candidate, runtimeDescriptor)) return true;
  const commandLine = normalizeProcessText(candidate.commandLine);
  const executable = normalizeProcessText(candidate.executablePath);
  const commandSpec = COMPONENTS.shell.command({ mode: "start" });
  const launcherSignature = commandLine.includes("apps\\v8-agent-os-shell\\scripts\\launch-shell.mjs")
    && processExecutableMatchesCommand(candidate, commandSpec.command);
  const electronRoot = normalizeProcessText(path.join(REPO_ROOT, "apps", "v8-agent-os-desktop-pet", "node_modules", "electron"));
  const shellDir = normalizeProcessText(path.join(REPO_ROOT, "apps", "v8-agent-os-shell"));
  const electronSignature = executable.startsWith(`${electronRoot}\\`)
    && commandLine.includes(shellDir);
  return launcherSignature || electronSignature;
}

function processCandidateMatchesDesktopPet(candidate, runtimeDescriptor = null) {
  if (!candidate || typeof candidate !== "object") return false;
  if (packagedRuntimeDescriptorMatches("desktop-pet", candidate, runtimeDescriptor)) return true;
  const commandLine = normalizeProcessText(candidate.commandLine);
  const executable = normalizeProcessText(candidate.executablePath);
  const commandSpec = COMPONENTS["desktop-pet"].command({ mode: "start" });
  const launcherSignature = commandLine.includes("apps\\v8-agent-os-shell\\scripts\\launch-desktop-pet.mjs")
    && processExecutableMatchesCommand(candidate, commandSpec.command);
  const petDir = normalizeProcessText(path.join(REPO_ROOT, "apps", "v8-agent-os-desktop-pet"));
  const electronRoot = `${petDir}\\node_modules\\electron`;
  const mainEntry = `${petDir}\\electron\\main.cjs`;
  const electronSignature = commandLine.includes(mainEntry)
    && (executable.startsWith(`${electronRoot}\\`) || processExecutableMatchesCommand(candidate, commandSpec.command));
  return launcherSignature || electronSignature;
}

function processCandidateMatchesCybercore(candidate) {
  if (!candidate || typeof candidate !== "object") return false;
  const commandLine = normalizeProcessText(candidate.commandLine);
  const commandSpec = COMPONENTS.cybercore.command({ mode: commandLine.includes("npm run start") ? "start" : "dev" });
  const executableName = path.win32.basename(normalizeProcessText(candidate.executablePath)).replace(/\.exe$/i, "");
  const posixNpmInterpreter = candidate.processDescriptorSource === "posix_ps" && executableName === "node";
  return (processExecutableMatchesCommand(candidate, commandSpec.command) || posixNpmInterpreter)
    && /(?:^|\s)npm\s+run\s+(?:start|dev)(?:\s|$)/i.test(commandLine);
}

function processCandidateMatchesComponent(componentId, candidate, runtimeDescriptor = null, expectedPort = null) {
  if (componentId === "engine") return processCandidateMatchesEngine(candidate, COMPONENTS.engine.command({ mode: "start" }));
  if (componentId === "admin" || componentId === "web") {
    return processCandidateMatchesNextApp(componentId, candidate, expectedPort || COMPONENTS[componentId]?.port);
  }
  if (componentId === "shell") return processCandidateMatchesShell(candidate, runtimeDescriptor);
  if (componentId === "desktop-pet") return processCandidateMatchesDesktopPet(candidate, runtimeDescriptor);
  if (componentId === "cybercore") return processCandidateMatchesCybercore(candidate);
  return false;
}

function recordCommandLine(record) {
  return [record?.command, ...(Array.isArray(record?.args) ? record.args : [])]
    .filter((part) => part !== undefined && part !== null && String(part).length > 0)
    .map(String)
    .join(" ");
}

function expectedComponentCwd(componentId) {
  const component = COMPONENTS[componentId];
  return component?.command({ mode: "start" })?.cwd || component?.cwd || "";
}

function recordMatchesComponent(componentId, record) {
  const component = COMPONENTS[componentId];
  if (!component || !record || typeof record !== "object") return false;
  if (normalizeProcessText(path.resolve(String(record.cwd || ""))) !== normalizeProcessText(path.resolve(expectedComponentCwd(componentId)))) return false;
  return processCandidateMatchesComponent(componentId, {
    pid: positivePid(record.pid),
    executablePath: record.command,
    commandLine: recordCommandLine(record),
    cwd: record.cwd,
  }, null, record.port);
}

export function verifiedManagedComponentPid(componentId, record, descriptor) {
  const pid = positivePid(record?.pid);
  if (!pid || positivePid(descriptor?.pid) !== pid) return null;
  if (!recordMatchesComponent(componentId, record)) return null;
  const descriptorMatchesComponent = processCandidateMatchesComponent(componentId, descriptor, null, record.port);
  if (!descriptorMatchesComponent) return null;
  const executableMatches = processExecutableMatchesCommand(descriptor, record.command);
  const verifiedPosixNpmInterpreter = componentId === "cybercore"
    && descriptor.processDescriptorSource === "posix_ps"
    && Boolean(descriptor.cwd);
  if (!executableMatches && !verifiedPosixNpmInterpreter) return null;
  if (record.processStartToken && descriptor.processStartToken !== record.processStartToken) return null;
  if (descriptor.executablePathKind === "posix_comm" && !descriptor.cwd) return null;
  if (descriptor.cwd
    && normalizeProcessText(path.resolve(String(descriptor.cwd))) !== normalizeProcessText(path.resolve(expectedComponentCwd(componentId)))) return null;
  return pid;
}

export function verifiedRuntimeComponentPid(componentId, descriptor, runtimeDescriptor = null) {
  if (!["shell", "desktop-pet"].includes(componentId)) return null;
  const pid = positivePid(descriptor?.pid);
  return pid && processCandidateMatchesComponent(componentId, descriptor, runtimeDescriptor) ? pid : null;
}

function processDescriptorAt(processDescriptors, pid) {
  if (!pid || !processDescriptors) return null;
  if (processDescriptors instanceof Map) return processDescriptors.get(pid) || null;
  return processDescriptors[pid] || processDescriptors[String(pid)] || null;
}

export function resolveManagedComponentIdentity(componentId, options = {}) {
  const record = options.record || null;
  const runtimeDescriptor = options.runtimeDescriptor || null;
  const processDescriptors = options.processDescriptors || new Map();
  const pidIsAlive = typeof options.pidIsAlive === "function" ? options.pidIsAlive : isPidAlive;
  const recordPid = positivePid(record?.pid);
  const runtimePid = positivePid(runtimeDescriptor?.pid);
  const verifiedRecordPid = verifiedManagedComponentPid(componentId, record, processDescriptorAt(processDescriptors, recordPid));
  const verifiedRuntimePid = verifiedRuntimeComponentPid(
    componentId,
    processDescriptorAt(processDescriptors, runtimePid),
    runtimeDescriptor,
  );
  const verifiedPids = [...new Set([verifiedRecordPid, verifiedRuntimePid].filter(Boolean))];
  const processStartTokens = Object.fromEntries(verifiedPids.map((pid) => [
    String(pid),
    processDescriptorAt(processDescriptors, pid)?.processStartToken || null,
  ]));
  const stalePids = [];
  const unverifiedPids = [];
  for (const pid of [...new Set([recordPid, runtimePid].filter(Boolean))]) {
    if (verifiedPids.includes(pid)) continue;
    if (processDescriptorAt(processDescriptors, pid) || !pidIsAlive(pid)) stalePids.push(pid);
    else unverifiedPids.push(pid);
  }
  return {
    recordPid: verifiedRecordPid,
    runtimePid: verifiedRuntimePid,
    effectivePid: verifiedRuntimePid || verifiedRecordPid || null,
    verifiedPids,
    processStartTokens,
    stalePids,
    unverifiedPids,
  };
}

export function verifiedComponentPortOwner(componentId, descriptor) {
  if (!descriptor || typeof descriptor !== "object") return null;
  const component = COMPONENTS[componentId];
  if (!component || !["engine", "admin", "web"].includes(componentId)) return null;
  const commandSpec = component.command({ mode: "start" });
  const owner = {
    pid: Number(descriptor.pid),
    executablePath: descriptor.executablePath,
    commandLine: descriptor.commandLine,
    processStartToken: descriptor.processStartToken,
  };
  const parent = {
    pid: Number(descriptor.parentPid),
    executablePath: descriptor.parentExecutablePath,
    commandLine: descriptor.parentCommandLine,
    processStartToken: descriptor.parentProcessStartToken,
  };
  const matcher = componentId === "engine"
    ? (candidate) => processCandidateMatchesEngine(candidate, commandSpec)
    : (candidate) => processCandidateMatchesNextApp(componentId, candidate);
  const ownerMatches = Number.isInteger(owner.pid) && owner.pid > 0 && matcher(owner);
  const parentMatches = Number.isInteger(parent.pid) && parent.pid > 0 && matcher(parent);
  if (!ownerMatches && !parentMatches) return null;
  const processStartToken = parentMatches ? parent.processStartToken : owner.processStartToken;
  return {
    ownerPid: owner.pid,
    killPid: parentMatches ? parent.pid : owner.pid,
    matchedBy: parentMatches ? "verified_parent_runtime" : "verified_port_owner",
    ...(processStartToken ? { processStartToken } : {}),
  };
}

async function readWindowsListeningProcessDescriptor(port) {
  if (process.platform !== "win32" || !Number.isInteger(Number(port))) return null;
  const script = [
    "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()",
    `$connection = Get-NetTCPConnection -LocalPort ${Number(port)} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1`,
    "if (-not $connection) { exit 0 }",
    "$process = Get-CimInstance Win32_Process -Filter \"ProcessId = $($connection.OwningProcess)\" -ErrorAction SilentlyContinue",
    "if (-not $process) { exit 0 }",
    "$parent = if ($process.ParentProcessId) { Get-CimInstance Win32_Process -Filter \"ProcessId = $($process.ParentProcessId)\" -ErrorAction SilentlyContinue } else { $null }",
    "$started = if ($process.CreationDate) { $process.CreationDate.ToUniversalTime().ToString('o') } else { '' }",
    "$parentStarted = if ($parent -and $parent.CreationDate) { $parent.CreationDate.ToUniversalTime().ToString('o') } else { '' }",
    "[pscustomobject]@{ pid = [int]$process.ProcessId; parentPid = [int]$process.ParentProcessId; executablePath = [string]$process.ExecutablePath; commandLine = [string]$process.CommandLine; processStartToken = $started; parentExecutablePath = [string]$parent.ExecutablePath; parentCommandLine = [string]$parent.CommandLine; parentProcessStartToken = $parentStarted } | ConvertTo-Json -Compress",
  ].join("; ");
  const result = await runWindowsProcessProbe(script);
  if (result.status !== 0 || !String(result.stdout || "").trim()) return null;
  try {
    return JSON.parse(String(result.stdout).trim());
  } catch {
    return null;
  }
}

async function readProcessDescriptor(pid) {
  const numeric = positivePid(pid);
  if (!numeric) return null;
  if (process.platform === "win32") {
    const script = [
      "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()",
      `$item = Get-CimInstance Win32_Process -Filter \"ProcessId = ${numeric}\" -ErrorAction SilentlyContinue`,
      "if (-not $item) { exit 0 }",
      "$started = if ($item.CreationDate) { $item.CreationDate.ToUniversalTime().ToString('o') } else { '' }",
      "[pscustomobject]@{ pid = [int]$item.ProcessId; executablePath = [string]$item.ExecutablePath; commandLine = [string]$item.CommandLine; processStartToken = $started } | ConvertTo-Json -Compress",
    ].join("; ");
    const result = await runWindowsProcessProbe(script);
    if (result.status !== 0 || !String(result.stdout || "").trim()) return null;
    try {
      return JSON.parse(String(result.stdout).trim());
    } catch {
      return null;
    }
  }
  const result = await runChildCommand("ps", ["-p", String(numeric), "-o", "pid=", "-o", "ppid=", "-o", "comm=", "-o", "args="]);
  const match = String(result.stdout || "").trim().match(/^(\d+)\s+(\d+)\s+(\S+)\s+(.+)$/);
  if (result.status !== 0 || !match) return null;
  let executablePath = match[3];
  let executablePathKind = "posix_comm";
  try {
    executablePath = fs.realpathSync(`/proc/${numeric}/exe`);
    executablePathKind = "exact";
  } catch {}
  let cwd = null;
  try {
    cwd = fs.realpathSync(`/proc/${numeric}/cwd`);
  } catch {}
  if (executablePathKind !== "exact" || !cwd) {
    const lsof = await runChildCommand("lsof", ["-a", "-p", String(numeric), "-d", "cwd,txt", "-Fn"]);
    if (lsof.status === 0) {
      let descriptorKind = "";
      for (const line of String(lsof.stdout || "").split(/\r?\n/)) {
        if (line.startsWith("f")) {
          descriptorKind = line.slice(1);
          continue;
        }
        if (!line.startsWith("n")) continue;
        const candidatePath = line.slice(1).trim();
        if (!candidatePath) continue;
        if (descriptorKind === "cwd" && !cwd) cwd = candidatePath;
        if (descriptorKind === "txt" && executablePathKind !== "exact") {
          executablePath = candidatePath;
          executablePathKind = "exact";
        }
      }
    }
  }
  return {
    pid: Number(match[1]),
    parentPid: Number(match[2]),
    processDescriptorSource: "posix_ps",
    executablePath,
    executablePathKind,
    commandLine: match[4],
    cwd,
    processStartToken: readPosixProcessStartToken(numeric),
  };
}

async function readProcessDescriptors(pids) {
  const uniquePids = [...new Set(pids.map(positivePid).filter(Boolean))];
  if (!uniquePids.length) return new Map();
  if (process.platform !== "win32") {
    const descriptors = await Promise.all(uniquePids.map(async (pid) => [pid, await readProcessDescriptor(pid)]));
    return new Map(descriptors.filter(([, descriptor]) => descriptor));
  }
  const filter = uniquePids.map((pid) => `ProcessId = ${pid}`).join(" OR ");
  const script = [
    "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()",
    `$items = @(Get-CimInstance Win32_Process -Filter \"${filter}\" -ErrorAction SilentlyContinue | ForEach-Object { [pscustomobject]@{ pid = [int]$_.ProcessId; executablePath = [string]$_.ExecutablePath; commandLine = [string]$_.CommandLine; processStartToken = $(if ($_.CreationDate) { $_.CreationDate.ToUniversalTime().ToString('o') } else { '' }) } })`,
    "[pscustomobject]@{ items = $items } | ConvertTo-Json -Compress -Depth 3",
  ].join("; ");
  const result = await runWindowsProcessProbe(script);
  if (result.status !== 0 || !String(result.stdout || "").trim()) return new Map();
  try {
    const payload = JSON.parse(String(result.stdout).trim());
    const items = Array.isArray(payload?.items) ? payload.items : payload?.items ? [payload.items] : [];
    return new Map(items.map((item) => [positivePid(item?.pid), item]).filter(([pid]) => pid));
  } catch {
    return new Map();
  }
}

const DESKTOP_PET_PROCESS_PATH = path.join(STATE_ROOT, "runtime", "desktop-pet.json");
const SHELL_CONTROL_PATH = path.join(STATE_ROOT, "runtime", "shell-control.json");

function readDesktopPetProcessDescriptor() {
  try {
    const descriptor = JSON.parse(fs.readFileSync(DESKTOP_PET_PROCESS_PATH, "utf8"));
    const pid = Number(descriptor?.pid);
    if (!Number.isInteger(pid) || pid <= 0) return null;
    return { ...descriptor, pid };
  } catch {
    return null;
  }
}

function readShellControlDescriptor() {
  try {
    const descriptor = JSON.parse(fs.readFileSync(SHELL_CONTROL_PATH, "utf8"));
    const pid = Number(descriptor?.pid);
    if (!Number.isInteger(pid) || pid <= 0) return null;
    return { ...descriptor, pid };
  } catch {
    return null;
  }
}

function runtimeProcessDescriptor(componentId) {
  if (componentId === "desktop-pet") return readDesktopPetProcessDescriptor();
  if (componentId === "shell") return readShellControlDescriptor();
  return null;
}

async function managedIdentitySnapshot(componentIds, state) {
  const runtimeDescriptors = new Map(componentIds.map((id) => [id, runtimeProcessDescriptor(id)]));
  const pids = componentIds.flatMap((id) => [state.processes[id]?.pid, runtimeDescriptors.get(id)?.pid]);
  return { runtimeDescriptors, processDescriptors: await readProcessDescriptors(pids) };
}

function resolveLiveManagedIdentity(componentId, record, snapshot) {
  const runtimeDescriptor = snapshot.runtimeDescriptors.get(componentId) || null;
  return {
    runtimeDescriptor,
    identity: resolveManagedComponentIdentity(componentId, {
      record,
      runtimeDescriptor,
      processDescriptors: snapshot.processDescriptors,
    }),
  };
}

function removeRuntimeDescriptor(componentId, expectedPid) {
  const filePath = componentId === "desktop-pet"
    ? DESKTOP_PET_PROCESS_PATH
    : componentId === "shell" ? SHELL_CONTROL_PATH : null;
  if (!filePath) return;
  const current = runtimeProcessDescriptor(componentId);
  if (!current || current.pid === expectedPid) fs.rmSync(filePath, { force: true });
}

async function resolveCurrentManagedIdentity(componentId, state = readProcessState()) {
  const snapshot = await managedIdentitySnapshot([componentId], state);
  const record = state.processes[componentId] || null;
  return { record, ...resolveLiveManagedIdentity(componentId, record, snapshot) };
}

async function cleanupConfirmedStaleRecord(componentId, expectedIdentity) {
  if (!expectedIdentity) return false;
  try {
    return await withComponentProcessLease(componentId, async () => {
      const state = readProcessState();
      const record = state.processes[componentId] || null;
      if (!processRecordMatchesIdentity(record, expectedIdentity)) return false;
      const { identity } = await resolveCurrentManagedIdentity(componentId, state);
      if (identity.unverifiedPids.length || !identity.stalePids.includes(expectedIdentity.pid)) return false;
      return (await compareAndSwapProcessRecord(componentId, expectedIdentity, null)).applied;
    }, { timeoutMs: 250 });
  } catch {
    return false;
  }
}

export function getManagedComponentProcessRecordIdentity(componentId) {
  if (!COMPONENTS[componentId]) return null;
  return processRecordIdentity(readProcessState().processes[componentId]);
}

export async function removeManagedComponentProcessRecord(componentId, expectedIdentity) {
  if (!COMPONENTS[componentId] || !expectedIdentity) return false;
  return withComponentProcessLease(componentId, async () => {
    const record = readProcessState().processes[componentId] || null;
    if (!processRecordMatchesIdentity(record, expectedIdentity)) return false;
    return (await compareAndSwapProcessRecord(componentId, expectedIdentity, null)).applied;
  });
}

export async function statusComponents(componentIds = Object.keys(COMPONENTS)) {
  configureComponentRuntimePorts(readRuntimePorts());
  const state = readProcessState();
  const snapshot = await managedIdentitySnapshot(componentIds, state);
  const statuses = [];
  const staleCleanups = [];
  for (const id of componentIds) {
    const component = COMPONENTS[id];
    if (!component) continue;
    const record = state.processes[id] || null;
    const { runtimeDescriptor, identity } = resolveLiveManagedIdentity(id, record, snapshot);
    const recordIdentity = processRecordIdentity(record);
    if (recordIdentity && !identity.unverifiedPids.length && identity.stalePids.includes(recordIdentity.pid)) {
      staleCleanups.push(cleanupConfirmedStaleRecord(id, recordIdentity));
    }
    const effectivePid = identity.effectivePid || identity.unverifiedPids[0] || null;
    const pidAlive = Boolean(identity.effectivePid || identity.unverifiedPids.length);
    const hasPort = componentHasPort(component);
    const portOpen = hasPort ? await isPortOpen(component.port) : false;
    statuses.push({
      id,
      label: component.label,
      port: component.port,
      managed: Boolean(identity.effectivePid),
      pid: effectivePid,
      pidAlive,
      portOpen,
      state: identity.effectivePid
        ? "managed_running"
        : identity.unverifiedPids.length ? "managed_identity_unverified"
          : hasPort && portOpen ? "external_port_in_use" : "stopped",
      startedAt: runtimeDescriptor?.startedAt || record?.startedAt || null,
      logOut: record?.logOut || null,
      logErr: record?.logErr || null,
    });
  }
  await Promise.all(staleCleanups);
  return statuses;
}

async function startComponent(id, options) {
  const component = COMPONENTS[id];
  const ports = options.runtimePorts || componentRuntimePorts();
  const componentPort = ["engine", "admin", "web"].includes(id)
    ? Number(ports[id])
    : component.port;
  const hasPort = Number.isInteger(componentPort) && componentPort > 0;
  return withComponentProcessLease(id, async () => {
    const state = readProcessState();
    const { record, runtimeDescriptor, identity } = await resolveCurrentManagedIdentity(id, state);
    if (identity.effectivePid) {
      return { id, status: "already_running", pid: identity.effectivePid };
    }
    if (identity.unverifiedPids.length) {
      return { id, status: "identity_unavailable", pid: identity.unverifiedPids[0] };
    }
    if (record) {
      const expectedIdentity = processRecordIdentity(record);
      const removed = await compareAndSwapProcessRecord(id, expectedIdentity, null);
      if (!removed.applied) return { id, status: "lifecycle_conflict", reason: "record_replaced_before_start" };
    }
    if (runtimeDescriptor) removeRuntimeDescriptor(id, runtimeDescriptor.pid);
    if (hasPort && await isPortOpen(componentPort)) {
      return { id, status: "port_in_use", port: componentPort };
    }
    const commandSpec = component.command({ ...options, runtimePorts: ports });
    if (!fs.existsSync(commandSpec.cwd)) {
      return { id, status: "missing_cwd", cwd: commandSpec.cwd };
    }
    const handoffReceipt = component.detachedHandoff ? createRuntimeHandoffReceipt(id) : null;
    if (handoffReceipt) {
      commandSpec.env = {
        ...commandSpec.env,
        V8OS_RUNTIME_HANDOFF_PATH: handoffReceipt.filePath,
        V8OS_RUNTIME_HANDOFF_NONCE: handoffReceipt.nonce,
      };
    }
    const logs = logPathsFor(id);
    const { child, failure } = await spawnManagedChild(id, commandSpec, logs);
    if (failure) {
      if (handoffReceipt?.filePath) fs.rmSync(handoffReceipt.filePath, { force: true });
      return failure;
    }
    if (component.detachedHandoff) {
      const handoff = await waitForRuntimeComponentHandoff(id, child, {
        receiptContract: handoffReceipt,
      });
      child.unref();
      if (!handoff.ok) {
        await cleanupFailedRuntimeHandoff(id, child, handoffReceipt, {
          candidatePid: handoff.candidatePid,
        });
        return {
          id,
          status: "startup_exit",
          stage: "runtime_handoff",
          reason: handoff.reason,
          exitCode: handoff.exitCode ?? null,
          signal: handoff.signal || null,
          port: null,
          logOut: logs.out,
          logErr: logs.err,
        };
      }
      fs.rmSync(handoffReceipt.filePath, { force: true });
      return {
        id,
        status: "started",
        pid: handoff.pid,
        port: null,
        logOut: logs.out,
        logErr: logs.err,
      };
    }
    const earlyExit = await observeEarlyProcessExit(child);
    if (earlyExit.exited) {
      child.unref();
      return {
        id,
        status: "startup_exit",
        stage: "post_spawn",
        reason: earlyExit.error
          ? "process_error_after_spawn"
          : earlyExit.signal ? "process_signalled_after_spawn" : "process_exited_after_spawn",
        exitCode: earlyExit.exitCode ?? null,
        signal: earlyExit.signal || null,
        port: hasPort ? componentPort : null,
        logOut: logs.out,
        logErr: logs.err,
      };
    }
    child.unref();
    const spawnedDescriptor = await readProcessDescriptor(child.pid);
    const recordToWrite = {
      managed: true,
      pid: child.pid,
      launchId: crypto.randomUUID(),
      command: commandSpec.command,
      args: commandSpec.args,
      cwd: commandSpec.cwd,
      port: hasPort ? componentPort : null,
      startedAt: new Date().toISOString(),
      logOut: logs.out,
      logErr: logs.err,
      ...(spawnedDescriptor?.processStartToken ? { processStartToken: spawnedDescriptor.processStartToken } : {}),
    };
    let inserted;
    try {
      inserted = await compareAndSwapProcessRecord(id, null, recordToWrite);
    } catch (error) {
      await killPid(child.pid);
      throw error;
    }
    if (!inserted.applied) {
      await killPid(child.pid);
      return { id, status: "lifecycle_conflict", reason: "record_insert_conflict", pid: child.pid };
    }
    return {
      id,
      status: "started",
      pid: child.pid,
      launchId: recordToWrite.launchId,
      port: hasPort ? componentPort : null,
      logOut: logs.out,
      logErr: logs.err,
    };
  });
}

export async function startComponentsWithRuntimePorts(componentIds, options = {}) {
  ensureDir(LOG_DIR);
  const selected = componentIds.filter((id) => COMPONENTS[id]);
  let webResult = null;
  const profile = await withRuntimePortsLease(async () => {
    const state = readProcessState();
    const currentWeb = state.processes.web
      ? await resolveCurrentManagedIdentity("web", state)
      : null;
    const verifiedManagedWebPort = currentWeb?.identity?.effectivePid
      ? Number(currentWeb.record?.port)
      : null;
    const profile = await resolveRuntimePorts({
      verifiedManagedWebPort,
      withLease: (callback) => callback(),
    });
    configureComponentRuntimePorts(profile.ports);
    if (selected.includes("web")) {
      webResult = await startComponent("web", { ...options, runtimePorts: profile.ports });
    }
    return profile;
  });
  configureComponentRuntimePorts(profile.ports);
  const results = await Promise.all(selected.map((id) => id === "web"
    ? webResult
    : startComponent(id, { ...options, runtimePorts: profile.ports })));
  return { profile, results };
}

export async function startComponents(componentIds, options = {}) {
  return (await startComponentsWithRuntimePorts(componentIds, options)).results;
}

async function killPid(pid, options = {}) {
  const numeric = Number(pid);
  if (!Number.isInteger(numeric) || numeric <= 0) return { ok: false, reason: "invalid_pid" };
  if (!isPidAlive(numeric)) return { ok: true, reason: "already_stopped" };
  if (process.platform === "win32") {
    const args = ["/PID", String(numeric)];
    if (options.tree !== false) args.push("/T");
    args.push("/F");
    const result = await runChildCommand("taskkill", args, { timeoutMs: 5_000 });
    if (result.status !== 0 && !isPidAlive(numeric)) {
      return { ok: true, reason: "stopped_during_kill" };
    }
    if (result.status !== 0) {
      return { ok: false, reason: (result.stderr || result.stdout || "taskkill_failed").trim() };
    }
    const stopped = await waitForPidExit(numeric, options.timeoutMs);
    return { ok: stopped, reason: stopped ? "killed" : "termination_timeout" };
  }
  const signal = options.signal === "SIGKILL" ? "SIGKILL" : "SIGTERM";
  try {
    process.kill(-numeric, signal);
  } catch {
    try {
      process.kill(numeric, signal);
    } catch (error) {
      return { ok: false, reason: error instanceof Error ? error.message : "kill_failed" };
    }
  }
  const stopped = await waitForPidExit(numeric, options.timeoutMs);
  return { ok: stopped, reason: stopped ? "killed" : "termination_timeout" };
}

async function revalidateStopTarget(componentId, pid, context) {
  const descriptor = await readProcessDescriptor(pid);
  if (context.expectedProcessStartToken
    && descriptor?.processStartToken !== context.expectedProcessStartToken) {
    return { ok: false, processStartToken: descriptor?.processStartToken || null };
  }
  const currentRecord = readProcessState().processes[componentId] || null;
  if (positivePid(context.record?.pid) === pid
    && processRecordMatchesIdentity(currentRecord, context.expectedIdentity)
    && verifiedManagedComponentPid(componentId, context.record, descriptor) === pid) {
    return { ok: true, processStartToken: descriptor?.processStartToken || null };
  }
  const currentRuntimeDescriptor = runtimeProcessDescriptor(componentId);
  if (positivePid(context.runtimeDescriptor?.pid) === pid
    && positivePid(currentRuntimeDescriptor?.pid) === pid
    && verifiedRuntimeComponentPid(componentId, descriptor, currentRuntimeDescriptor) === pid) {
    return { ok: true, processStartToken: descriptor?.processStartToken || null };
  }
  if (context.verifiedPortOwner?.killPid === pid) {
    const currentPortOwner = verifiedComponentPortOwner(
      componentId,
      await readWindowsListeningProcessDescriptor(COMPONENTS[componentId]?.port),
    );
    const tokenMatches = !context.verifiedPortOwner.processStartToken
      || currentPortOwner?.processStartToken === context.verifiedPortOwner.processStartToken;
    if (currentPortOwner?.killPid === pid
      && currentPortOwner.ownerPid === context.verifiedPortOwner.ownerPid
      && tokenMatches) {
      return { ok: true, processStartToken: currentPortOwner.processStartToken || null };
    }
  }
  return { ok: false, processStartToken: null };
}

export function orderedManagedStopPids(componentId, identity, verifiedPortOwner = null) {
  const verifiedPids = Array.isArray(identity?.verifiedPids) ? identity.verifiedPids : [];
  const preferredPids = componentId === "shell"
    ? [identity?.runtimePid, identity?.recordPid]
    : verifiedPids;
  return [...new Set([
    ...preferredPids,
    ...verifiedPids,
    verifiedPortOwner?.killPid,
  ].filter(Boolean))];
}

const WHOLE_V8OS_SHUTDOWN_COMPONENTS = ["engine", "admin", "web", "desktop-pet", "shell"];

export function requestsManagedShellShutdown(componentIds) {
  const selected = new Set(Array.isArray(componentIds) ? componentIds : []);
  return WHOLE_V8OS_SHUTDOWN_COMPONENTS.every((id) => selected.has(id));
}

export function managedShellShutdownEnvironment(environment = process.env, runtimeDescriptor = {}) {
  const next = { ...environment };
  for (const key of Object.keys(next)) {
    if (["ELECTRON_RUN_AS_NODE", "V8OS_DESKTOP_RUNTIME_MODE"].includes(key.toUpperCase())) delete next[key];
  }
  next.V8OS_SHELL_PACKAGED = "1";
  if (runtimeDescriptor.repoRoot) next.V8_REPO_ROOT = path.resolve(String(runtimeDescriptor.repoRoot));
  return next;
}

export async function requestPackagedShellShutdown(componentIds, options = {}) {
  if (!requestsManagedShellShutdown(componentIds)) return { attempted: false, stopped: false, reason: "partial_stop" };
  const readRuntime = options.readRuntimeDescriptor || runtimeProcessDescriptor;
  const describe = options.readProcessDescriptor || readProcessDescriptor;
  const verify = options.verifyRuntimePid || verifiedRuntimeComponentPid;
  const spawnImpl = options.spawnImpl || spawn;
  const awaitSpawn = options.waitForSpawn || waitForSpawn;
  const waitForExit = options.waitForPidExit || waitForPidExit;
  const runtimeDescriptor = readRuntime("shell");
  const shellPid = positivePid(runtimeDescriptor?.pid);
  const executablePath = String(runtimeDescriptor?.executablePath || "").trim();
  if (!runtimeDescriptor?.packaged || runtimeDescriptor?.runtimeKind !== "shell" || !shellPid || !executablePath) {
    return { attempted: false, stopped: false, reason: "packaged_shell_unavailable" };
  }
  const processDescriptor = await describe(shellPid);
  if (verify("shell", processDescriptor, runtimeDescriptor) !== shellPid) {
    return { attempted: false, stopped: false, reason: "packaged_shell_identity_unverified" };
  }
  try {
    const child = spawnImpl(executablePath, [MANAGED_SHELL_SHUTDOWN_ARG], {
      cwd: path.dirname(executablePath),
      env: managedShellShutdownEnvironment(options.environment || process.env, runtimeDescriptor),
      detached: false,
      stdio: "ignore",
      windowsHide: true,
    });
    await awaitSpawn(child);
    child.unref?.();
  } catch {
    return { attempted: true, stopped: false, reason: "shutdown_request_failed", pid: shellPid };
  }
  const stopped = await waitForExit(shellPid, options.timeoutMs || MANAGED_SHELL_SHUTDOWN_TIMEOUT_MS);
  return {
    attempted: true,
    stopped,
    reason: stopped ? "governed_shutdown" : "governed_shutdown_timeout",
    pid: shellPid,
  };
}

async function stopComponent(id, options) {
  const component = COMPONENTS[id];
  return withComponentProcessLease(id, async () => {
    const state = readProcessState();
    const { record, runtimeDescriptor, identity } = await resolveCurrentManagedIdentity(id, state);
    const expectedIdentity = processRecordIdentity(record);
    if (identity.unverifiedPids.length) {
      return { id, status: "stop_failed", reason: "identity_unavailable", pid: identity.unverifiedPids[0] };
    }
    const mayStopVerifiedPortOwner = Array.isArray(options.stopVerifiedPortOwners)
      && options.stopVerifiedPortOwners.includes(id)
      && componentHasPort(component)
      && !identity.recordPid;
    const verifiedPortOwner = mayStopVerifiedPortOwner
      ? verifiedComponentPortOwner(id, await readWindowsListeningProcessDescriptor(component.port))
      : null;
    if (!record && !runtimeDescriptor && !verifiedPortOwner) {
      return { id, status: "not_managed" };
    }
    const targetPids = orderedManagedStopPids(id, identity, verifiedPortOwner);
    // Windows keeps the original parent process relationship even for detached
    // children. Killing the whole Shell tree can therefore terminate the managed
    // desktop pet. Stop the Shell browser and launcher PIDs exactly; Electron
    // tears down its own renderer/GPU children when the browser process exits.
    const killResults = [];
    for (const pid of targetPids) {
      const revalidated = await revalidateStopTarget(id, pid, {
        record,
        expectedIdentity,
        runtimeDescriptor,
        verifiedPortOwner,
        expectedProcessStartToken: identity.processStartTokens[String(pid)]
          || (verifiedPortOwner?.killPid === pid ? verifiedPortOwner.processStartToken : null),
      });
      if (!revalidated.ok) {
        if (!isPidAlive(pid)) {
          killResults.push({ pid, ok: true, reason: "already_stopped" });
          continue;
        }
        killResults.push({ pid, ok: false, reason: "identity_changed_before_kill" });
        break;
      }
      killResults.push({
        pid,
        ...await killPid(pid, managedStopOptions(id, process.platform, {
          force: options.forceDesktopPet === true,
        })),
      });
    }
    const failed = killResults.find((item) => !item.ok);
    if (!failed) {
      if (record) {
        const removed = await compareAndSwapProcessRecord(id, expectedIdentity, null);
        if (!removed.applied) {
          return { id, status: "stop_conflict", reason: "record_replaced_after_stop", pid: expectedIdentity?.pid || null };
        }
      }
      if (runtimeDescriptor) removeRuntimeDescriptor(id, runtimeDescriptor.pid);
    }
    return {
      id,
      status: failed ? "stop_failed" : targetPids.length ? "stopped" : "stale_state_removed",
      reason: failed?.reason || killResults.map((item) => item.reason).join(",") || "already_stopped",
      pid: identity.effectivePid || positivePid(runtimeDescriptor?.pid) || positivePid(record?.pid) || verifiedPortOwner?.ownerPid || null,
      verifiedPortOwner: Boolean(verifiedPortOwner),
      matchedBy: verifiedPortOwner?.matchedBy || null,
    };
  });
}

export async function stopComponents(componentIds = Object.keys(COMPONENTS), options = {}) {
  configureComponentRuntimePorts(readRuntimePorts());
  const selected = componentIds.filter((id) => COMPONENTS[id]);
  const governedShutdown = options.skipManagedShellShutdown === true
    ? { attempted: false, stopped: false, reason: "disabled" }
    : await requestPackagedShellShutdown(selected, options.managedShellShutdown || {});
  const stopOptions = governedShutdown.attempted && !governedShutdown.stopped
    ? { ...options, forceDesktopPet: true }
    : options;
  return Promise.all(selected.map((id) => stopComponent(id, stopOptions)));
}
