import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { EventEmitter } from "node:events";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import desktopPetPlatform from "../src/desktop_pet_platform.cjs";
import {
  ALL_COMPONENTS,
  COMPONENTS,
  DEFAULT_START_COMPONENTS,
  componentRuntimePorts,
  configureComponentRuntimePorts,
  parseComponentSelection,
} from "../src/components.mjs";
import {
  assistantTerminalFailure,
  buildChatSubmitPayload,
  extractMessageText,
  normalizeSafetyApprovalMode,
  resolveChatWorkspaceSelection,
} from "../src/chat_commands.mjs";
import { requireOk } from "../src/client_api.mjs";
import { buildMcpInstallPayload, extractModelRoles } from "../src/config_commands.mjs";
import { filterPendingInboxItems } from "../src/inbox_commands.mjs";
import { backupFile, readJsonFile, writeJsonFile } from "../src/json_file.mjs";
import { getPortOwners, isPortOpen } from "../src/ports.mjs";
import { DEFAULT_PORTS } from "../src/paths.mjs";
import {
  cleanupFailedRuntimeHandoff,
  DESKTOP_PET_TERMINATION_TIMEOUT_MS,
  MANAGED_SHELL_RESTART_ARG,
  MANAGED_SHELL_RESTART_TIMEOUT_MS,
  MANAGED_SHELL_SHUTDOWN_ARG,
  MANAGED_SHELL_SHUTDOWN_TIMEOUT_MS,
  managedStopOptions,
  managedShellShutdownEnvironment,
  observeEarlyProcessExit,
  orderedManagedStopPids,
  packagedRuntimeDescriptorMatches,
  requestPackagedShellRestart,
  requestPackagedShellShutdown,
  requestsManagedShellRestart,
  requestsManagedShellShutdown,
  resolveManagedComponentIdentity,
  runWindowsProcessProbe,
  SHELL_TERMINATION_TIMEOUT_MS,
  spawnManagedChild,
  stopComponents,
  waitForRuntimeComponentHandoff,
  verifiedComponentPortOwner,
  verifiedManagedComponentPid,
  verifiedRuntimeComponentPid,
  WINDOWS_PROCESS_PROBE_TIMEOUT_MS,
} from "../src/process_manager.mjs";
import { processRecordMatchesIdentity } from "../src/process_state.mjs";
import { buildLocalRepairPlan, runDoctor } from "../src/doctor.mjs";
import {
  createShellRestartLease,
  isNextBuildPresent,
  nextBuildIdPath,
  previewBuildLogPaths,
  previewRebuildStopComponentIds,
  removeOwnedShellRestartLease,
  validateComponentStartResults,
  validateShellControlDescriptor,
  waitForShellControlDescriptor,
} from "../src/preview_commands.mjs";
import { currentWorkspaceBinding, currentWorkspacePath, inspectWorkspace, resolveWorkspacePath } from "../src/workspace_commands.mjs";
import { commandResultsHaveFailures, main as runCli } from "../src/cli.mjs";
import { renderStartResults, renderStatus } from "../src/render.mjs";
import { shellDesktopPetAvailability } from "../src/shell_api.mjs";

const {
  desktopPetAvailability,
  LINUX_DESKTOP_PET_UNAVAILABLE_REASON,
} = desktopPetPlatform;

const currentFile = fileURLToPath(import.meta.url);
const cliRoot = path.resolve(path.dirname(currentFile), "..");
const repoRoot = path.resolve(cliRoot, "..", "..");

function runWindowsCommand(command, args, options = {}) {
  return spawnSync(command, args, {
    cwd: repoRoot,
    encoding: "utf8",
    timeout: options.timeoutMs || 10000,
  });
}

function powershellLiteral(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}

function runIsolatedModuleScript(script, env = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ["--input-type=module", "-e", script], {
      cwd: repoRoot,
      env: { ...process.env, ...env },
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", reject);
    child.on("exit", (code) => code === 0
      ? resolve(stdout)
      : reject(new Error(stderr || stdout || `child exited ${code}`)));
  });
}

test("default start components exclude CyberCore", () => {
  assert.deepEqual(DEFAULT_START_COMPONENTS, ["engine", "admin", "web"]);
  assert.deepEqual(parseComponentSelection([]), ["engine", "admin", "web"]);
  assert.equal(DEFAULT_START_COMPONENTS.includes("shell"), false);
  assert.equal(DEFAULT_START_COMPONENTS.includes("desktop-pet"), false);
});

test("--with adds optional components without replacing defaults", () => {
  assert.deepEqual(parseComponentSelection(["--with", "cybercore"]), ["engine", "admin", "web", "cybercore"]);
});

test("--only narrows component set", () => {
  assert.deepEqual(parseComponentSelection(["--only", "engine,admin"]), ["engine", "admin"]);
});

test("--all returns all components", () => {
  assert.deepEqual(parseComponentSelection(["--all"]), ALL_COMPONENTS);
  assert.ok(ALL_COMPONENTS.includes("shell"));
  assert.ok(ALL_COMPONENTS.includes("desktop-pet"));
});

test("desktop pet capability is fail-closed only on Linux", () => {
  const linux = desktopPetAvailability("linux");
  assert.equal(LINUX_DESKTOP_PET_UNAVAILABLE_REASON, "linux_desktop_pet_input_passthrough_unreliable");
  assert.deepEqual(linux, {
    componentId: "desktop-pet",
    platform: "linux",
    available: false,
    status: "unavailable",
    reasonCode: LINUX_DESKTOP_PET_UNAVAILABLE_REASON,
    message: "Desktop Pet is unavailable on Linux because its current full-screen interactive window has no verified safe click-through contract. Core V8OS interfaces (Engine, Admin, Web, and Shell) are unaffected.",
  });
  for (const platform of ["win32", "darwin"]) {
    const availability = desktopPetAvailability(platform);
    assert.equal(availability.available, true, platform);
    assert.equal(availability.status, "available", platform);
    assert.equal(availability.reasonCode, null, platform);
  }
  assert.deepEqual(shellDesktopPetAvailability("linux"), linux);
});

test("Linux desktop pet selection remains explicit for --only, --with, and --all", () => {
  const selections = [
    parseComponentSelection(["--only", "desktop-pet"]),
    parseComponentSelection(["--with", "desktop-pet"]),
    parseComponentSelection(["--all"]),
  ];
  for (const selected of selections) {
    assert.ok(selected.includes("desktop-pet"));
    assert.equal(commandResultsHaveFailures("start", [{
      id: "desktop-pet",
      ...desktopPetAvailability("linux"),
    }]), true);
  }
  assert.equal(parseComponentSelection([]).includes("desktop-pet"), false);
});

test("Linux desktop pet human output explains that core interfaces are unaffected", () => {
  const lines = [];
  const originalLog = console.log;
  console.log = (...args) => lines.push(args.join(" "));
  try {
    const availability = desktopPetAvailability("linux");
    renderStartResults([{ id: "desktop-pet", ...availability }]);
    renderStatus([{
      id: "desktop-pet",
      label: "Desktop Pet",
      state: "managed_running",
      pid: 44001,
      pidAlive: true,
      ...availability,
    }]);
  } finally {
    console.log = originalLog;
  }
  const output = lines.join("\n");
  assert.match(output, /Core V8OS interfaces \(Engine, Admin, Web, and Shell\) are unaffected\./);
  assert.match(output, /residual process detected; stop remains available/);
});

test("subcommand help is read-only and does not execute preview", async () => {
  const lines = [];
  const originalLog = console.log;
  console.log = (...args) => lines.push(args.join(" "));
  try {
    await runCli(["preview", "--help"]);
  } finally {
    console.log = originalLog;
  }
  assert.match(lines.join("\n"), /v8os preview \[--rebuild\|--no-build\]/);
});

test("local repair plan proposes safe state root creation", () => {
  const plan = buildLocalRepairPlan([{ id: "state_root", status: "warning" }]);
  assert.equal(plan.actions[0].id, "create_state_root");
  assert.equal(plan.actions[0].safe, true);
});

test("local repair plan treats runtime installation as explicit action", () => {
  const plan = buildLocalRepairPlan([{ id: "python", status: "warning" }]);
  assert.equal(plan.actions[0].id, "install_python");
  assert.equal(plan.actions[0].safe, false);
});

test("local repair plan can safely refresh missing admin auth secret", () => {
  const plan = buildLocalRepairPlan([{ id: "admin_auth_secret", status: "warning", path: "secret" }]);
  assert.equal(plan.actions[0].id, "refresh_admin_auth_secret");
  assert.equal(plan.actions[0].safe, true);
});

test("port probe detects a listening local port", async () => {
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  try {
    assert.equal(await isPortOpen(address.port), true);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

test("port owner probe is safe for invalid or unopened ports", () => {
  assert.deepEqual(getPortOwners(0), []);
});

test("CLI lifecycle result contracts distinguish idempotent success from actionable failure", () => {
  assert.equal(commandResultsHaveFailures("start", [
    { status: "started" },
    { status: "already_running" },
  ]), false);
  assert.equal(commandResultsHaveFailures("start", [{ status: "startup_exit" }]), true);
  assert.equal(commandResultsHaveFailures("start", [{ status: "identity_unavailable" }]), true);
  assert.equal(commandResultsHaveFailures("stop", [
    { status: "stopped" },
    { status: "not_managed" },
    { status: "stale_state_removed" },
  ]), false);
  assert.equal(commandResultsHaveFailures("stop", [{ status: "stop_failed" }]), true);
  assert.equal(commandResultsHaveFailures("stop", [{ status: "stop_conflict" }]), true);
});

test("CLI prints structured start failures before exiting nonzero", (t) => {
  const fakeRepo = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-cli-start-failure-repo-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-cli-start-failure-state-"));
  t.after(() => {
    fs.rmSync(fakeRepo, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  });
  const cliUrl = new URL("../src/cli.mjs", import.meta.url).href;
  const child = spawnSync(process.execPath, [
    "--input-type=module",
    "-e",
    `const { main } = await import(${JSON.stringify(cliUrl)}); await main(["start", "--only", "shell", "--mode", "start", "--json"]);`,
  ], {
    cwd: repoRoot,
    env: {
      ...process.env,
      V8_REPO_ROOT: fakeRepo,
      V8_SHELL_DIR: path.join(fakeRepo, "apps", "v8-agent-os-shell"),
      V8_AGENT_OS_HOME: stateRoot,
    },
    encoding: "utf8",
    windowsHide: true,
    timeout: 10_000,
  });
  assert.equal(child.status, 1, child.stderr);
  assert.equal(JSON.parse(child.stdout)[0].status, "startup_exit");
});

test("Linux explicit desktop pet starts return unavailable and exit nonzero", (t) => {
  const fakeRepo = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-cli-linux-pet-repo-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-cli-linux-pet-state-"));
  t.after(() => {
    fs.rmSync(fakeRepo, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  });
  const cliUrl = new URL("../src/cli.mjs", import.meta.url).href;
  const cases = [
    ["--only", "desktop-pet"],
    ["--with", "desktop-pet"],
    ["--all"],
  ];
  for (const args of cases) {
    const child = spawnSync(process.execPath, [
      "--input-type=module",
      "-e",
      `Object.defineProperty(process, "platform", { value: "linux" }); const { main } = await import(${JSON.stringify(cliUrl)}); await main(${JSON.stringify(["start", ...args, "--mode", "start", "--json"])});`,
    ], {
      cwd: repoRoot,
      env: {
        ...process.env,
        V8_REPO_ROOT: fakeRepo,
        V8_AGENT_OS_HOME: stateRoot,
      },
      encoding: "utf8",
      windowsHide: true,
      timeout: 10_000,
    });
    assert.equal(child.status, 1, `${args.join(" ")}: ${child.stderr}`);
    const desktopPet = JSON.parse(child.stdout).find((item) => item.id === "desktop-pet");
    assert.ok(desktopPet, args.join(" "));
    assert.equal(desktopPet.componentId, "desktop-pet");
    assert.equal(desktopPet.status, "unavailable");
    assert.equal(desktopPet.available, false);
    assert.equal(desktopPet.reasonCode, LINUX_DESKTOP_PET_UNAVAILABLE_REASON);
    assert.equal(desktopPet.pid, undefined);
  }
});

test("Linux desktop pet status reports capability while stop remains available", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-cli-linux-pet-lifecycle-"));
  const processManagerUrl = new URL("../src/process_manager.mjs", import.meta.url).href;
  const script = [
    `Object.defineProperty(process, "platform", { value: "linux" });`,
    `const { statusComponents, stopComponents } = await import(${JSON.stringify(processManagerUrl)});`,
    `const status = await statusComponents(["desktop-pet"]);`,
    `const stop = await stopComponents(["desktop-pet"]);`,
    `process.stdout.write(JSON.stringify({ status, stop }));`,
  ].join("\n");
  try {
    const payload = JSON.parse(await runIsolatedModuleScript(script, {
      V8_AGENT_OS_HOME: stateRoot,
      V8_REPO_ROOT: repoRoot,
    }));
    assert.equal(payload.status[0].state, "stopped");
    assert.equal(payload.status[0].pidAlive, false);
    assert.equal(payload.status[0].available, false);
    assert.equal(payload.status[0].status, "unavailable");
    assert.equal(payload.status[0].reasonCode, LINUX_DESKTOP_PET_UNAVAILABLE_REASON);
    assert.deepEqual(payload.stop, [{ id: "desktop-pet", status: "not_managed" }]);
  } finally {
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("Windows process ownership probes pass the governed cold-start timeout to the runner", async () => {
  assert.ok(WINDOWS_PROCESS_PROBE_TIMEOUT_MS >= 10_000);
  const calls = [];
  const result = await runWindowsProcessProbe("Get-CimInstance Win32_Process", async (...args) => {
    calls.push(args);
    return { status: 0, stdout: '{"items":[]}', stderr: "" };
  });

  assert.equal(result.status, 0);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], "powershell.exe");
  assert.deepEqual(calls[0][1].slice(0, 3), ["-NoProfile", "-NonInteractive", "-Command"]);
  assert.equal(calls[0][1][3], "Get-CimInstance Win32_Process");
  assert.equal(calls[0][2].timeoutMs, WINDOWS_PROCESS_PROBE_TIMEOUT_MS);
});

test("managed process identity rejects reused PIDs for every preview component", () => {
  const components = ["engine", "admin", "web", "shell", "desktop-pet"];
  for (const [index, id] of components.entries()) {
    const pid = 41000 + index;
    const spec = COMPONENTS[id].command({ mode: "start" });
    const record = {
      pid,
      command: spec.command,
      args: spec.args,
      cwd: spec.cwd,
    };
    const valid = {
      pid,
      executablePath: spec.command,
      commandLine: [spec.command, ...spec.args].join(" "),
      cwd: spec.cwd,
    };
    const reused = {
      pid,
      executablePath: "C:\\Windows\\System32\\unrelated.exe",
      commandLine: "unrelated.exe --background",
    };
    assert.equal(verifiedManagedComponentPid(id, record, valid), pid, `${id} valid identity`);
    assert.equal(verifiedManagedComponentPid(id, {
      ...record,
      processStartToken: "launch-a",
    }, {
      ...valid,
      processStartToken: "launch-b",
    }), null, `${id} reused PID creation token`);
    assert.equal(verifiedManagedComponentPid(id, record, reused), null, `${id} reused PID`);
    assert.equal(verifiedManagedComponentPid(id, { ...record, cwd: path.join(os.tmpdir(), "other") }, valid), null, `${id} cwd`);
  }
});

test("process record CAS identity requires both PID and launchId", () => {
  const record = { pid: 46692, launchId: "launch-admin-a" };
  assert.equal(processRecordMatchesIdentity(record, { pid: 46692, launchId: "launch-admin-a" }), true);
  assert.equal(processRecordMatchesIdentity(record, { pid: 46692, launchId: "launch-admin-b" }), false);
  assert.equal(processRecordMatchesIdentity(record, { pid: 47012, launchId: "launch-admin-a" }), false);
  assert.equal(processRecordMatchesIdentity(null, null), true);
});

test("Shell exit uses exact process-state CAS instead of re-entering its component lease", () => {
  const source = fs.readFileSync(path.join(cliRoot, "src", "shell_api.mjs"), "utf8");

  assert.match(source, /compareAndSwapProcessRecord\("shell", expectedIdentity, null\)/);
  assert.doesNotMatch(source, /removeManagedComponentProcessRecord/);
  assert.doesNotMatch(source, /readProcessState|writeProcessState/);
});

test("Shell self-removal completes while an external stopper owns shell.lease", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-shell-self-remove-"));
  const statePath = path.join(stateRoot, "runtime", "cli", "processes.json");
  const expectedIdentity = { pid: 48720, launchId: "shell-governed-exit" };
  fs.mkdirSync(path.dirname(statePath), { recursive: true });
  fs.writeFileSync(statePath, `${JSON.stringify({
    version: 1,
    repoRoot,
    processes: { shell: expectedIdentity },
  }, null, 2)}\n`, "utf8");
  const processStateUrl = new URL("../src/process_state.mjs", import.meta.url).href;
  const shellApiUrl = new URL("../src/shell_api.mjs", import.meta.url).href;
  const script = [
    `const stateApi = await import(${JSON.stringify(processStateUrl)});`,
    `const shellApi = await import(${JSON.stringify(shellApiUrl)});`,
    `const expected = ${JSON.stringify(expectedIdentity)};`,
    `const startedAt = Date.now();`,
    `const removed = await stateApi.withComponentProcessLease("shell", () => shellApi.removeShellProcessRecord(expected));`,
    `process.stdout.write(JSON.stringify({ removed, elapsedMs: Date.now() - startedAt, state: stateApi.readProcessState() }));`,
  ].join("\n");

  try {
    const payload = JSON.parse(await runIsolatedModuleScript(script, {
      V8_AGENT_OS_HOME: stateRoot,
      V8_REPO_ROOT: repoRoot,
    }));
    assert.equal(payload.removed, true);
    assert.equal(payload.state.processes.shell, undefined);
    assert.ok(payload.elapsedMs < 1_000, `Shell self-removal took ${payload.elapsedMs}ms`);
  } finally {
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("Shell shutdown stops the verified Electron browser before its launcher", () => {
  assert.deepEqual(orderedManagedStopPids("shell", {
    runtimePid: 2202,
    recordPid: 1101,
    verifiedPids: [1101, 2202],
  }), [2202, 1101]);
  assert.deepEqual(orderedManagedStopPids("web", {
    runtimePid: null,
    recordPid: 3303,
    verifiedPids: [3303],
  }, { killPid: 4404 }), [3303, 4404]);
});

test("POSIX component restart force-stops only the verified Shell process group", () => {
  assert.equal(DESKTOP_PET_TERMINATION_TIMEOUT_MS, 10_000);
  assert.deepEqual(managedStopOptions("shell", "linux"), {
    tree: false,
    timeoutMs: SHELL_TERMINATION_TIMEOUT_MS,
    signal: "SIGKILL",
  });
  assert.deepEqual(managedStopOptions("shell", "darwin"), {
    tree: false,
    timeoutMs: SHELL_TERMINATION_TIMEOUT_MS,
    signal: "SIGKILL",
  });
  assert.deepEqual(managedStopOptions("shell", "win32"), {
    tree: false,
    timeoutMs: SHELL_TERMINATION_TIMEOUT_MS,
    signal: "SIGTERM",
  });
  assert.deepEqual(managedStopOptions("engine", "linux"), {
    tree: true,
    timeoutMs: undefined,
    signal: "SIGTERM",
  });
  assert.deepEqual(managedStopOptions("desktop-pet", "linux"), {
    tree: true,
    timeoutMs: DESKTOP_PET_TERMINATION_TIMEOUT_MS,
    signal: "SIGTERM",
  });
  assert.deepEqual(managedStopOptions("desktop-pet", "linux", { force: true }), {
    tree: true,
    timeoutMs: undefined,
    signal: "SIGKILL",
  });
});

test("packaged stop-all asks the verified Shell to run governed V8OS shutdown", async () => {
  assert.equal(requestsManagedShellShutdown(ALL_COMPONENTS), true);
  assert.equal(requestsManagedShellShutdown(["shell"]), false);
  const runtimeDescriptor = {
    packaged: true,
    runtimeKind: "shell",
    pid: 4201,
    executablePath: path.join("C:\\Program Files", "V8 Agent OS", "V8 Agent OS.exe"),
    repoRoot: path.join("C:\\Program Files", "V8 Agent OS", "resources", "v8os"),
  };
  let spawned = null;
  const child = new EventEmitter();
  child.unref = () => { child.unrefCalled = true; };
  const result = await requestPackagedShellShutdown(ALL_COMPONENTS, {
    readRuntimeDescriptor: () => runtimeDescriptor,
    readProcessDescriptor: async () => ({ pid: 4201 }),
    verifyRuntimePid: () => 4201,
    spawnImpl(command, args, options) {
      spawned = { command, args, options };
      queueMicrotask(() => child.emit("spawn"));
      return child;
    },
    waitForPidExit: async (pid, timeoutMs) => pid === 4201 && timeoutMs === MANAGED_SHELL_SHUTDOWN_TIMEOUT_MS,
    environment: {
      ELECTRON_RUN_AS_NODE: "1",
      V8OS_DESKTOP_RUNTIME_MODE: "desktop-pet",
      DISPLAY: ":99",
    },
  });
  assert.deepEqual(result, {
    attempted: true,
    stopped: true,
    reason: "governed_shutdown",
    pid: 4201,
  });
  assert.equal(spawned.command, runtimeDescriptor.executablePath);
  assert.deepEqual(spawned.args, [MANAGED_SHELL_SHUTDOWN_ARG]);
  assert.equal(spawned.options.windowsHide, true);
  assert.equal(spawned.options.env.ELECTRON_RUN_AS_NODE, undefined);
  assert.equal(spawned.options.env.V8OS_DESKTOP_RUNTIME_MODE, undefined);
  assert.equal(spawned.options.env.DISPLAY, ":99");
  assert.equal(spawned.options.env.V8OS_SHELL_PACKAGED, "1");
  assert.equal(spawned.options.env.V8_REPO_ROOT, path.resolve(runtimeDescriptor.repoRoot));
  assert.equal(child.unrefCalled, true);
  assert.deepEqual(managedShellShutdownEnvironment({ electron_run_as_node: "1" }, runtimeDescriptor), {
    V8OS_SHELL_PACKAGED: "1",
    V8_REPO_ROOT: path.resolve(runtimeDescriptor.repoRoot),
  });
});

test("packaged Shell-only stop asks Electron to release its profile before restart", async () => {
  assert.equal(requestsManagedShellRestart(["shell"]), true);
  assert.equal(requestsManagedShellRestart(["shell", "web"]), false);
  assert.equal(requestsManagedShellRestart([]), false);
  const runtimeDescriptor = {
    packaged: true,
    runtimeKind: "shell",
    pid: 4301,
    executablePath: path.join("C:\\Program Files", "V8 Agent OS", "V8 Agent OS.exe"),
    repoRoot: path.join("C:\\Program Files", "V8 Agent OS", "resources", "v8os"),
  };
  let spawned = null;
  const child = new EventEmitter();
  child.unref = () => { child.unrefCalled = true; };
  const result = await requestPackagedShellRestart(["shell"], {
    readRuntimeDescriptor: () => runtimeDescriptor,
    readProcessDescriptor: async () => ({ pid: 4301 }),
    verifyRuntimePid: () => 4301,
    spawnImpl(command, args, options) {
      spawned = { command, args, options };
      queueMicrotask(() => child.emit("spawn"));
      return child;
    },
    waitForPidExit: async (pid, timeoutMs) => pid === 4301 && timeoutMs === MANAGED_SHELL_RESTART_TIMEOUT_MS,
  });
  assert.deepEqual(result, {
    attempted: true,
    stopped: true,
    reason: "governed_shell_restart",
    pid: 4301,
  });
  assert.equal(spawned.command, runtimeDescriptor.executablePath);
  assert.deepEqual(spawned.args, [MANAGED_SHELL_RESTART_ARG]);
  assert.equal(spawned.options.windowsHide, true);
  assert.equal(child.unrefCalled, true);
});

test("Shell-only stop reports governed restart without entering the force-kill fallback", async () => {
  const runtimeDescriptor = {
    packaged: true,
    runtimeKind: "shell",
    pid: 4302,
    executablePath: path.join("C:\\Program Files", "V8 Agent OS", "V8 Agent OS.exe"),
  };
  const child = new EventEmitter();
  child.unref = () => undefined;
  const results = await stopComponents(["shell"], {
    managedShellRestart: {
      readRuntimeDescriptor: () => runtimeDescriptor,
      readProcessDescriptor: async () => ({ pid: 4302 }),
      verifyRuntimePid: () => 4302,
      spawnImpl() {
        queueMicrotask(() => child.emit("spawn"));
        return child;
      },
      waitForPidExit: async () => true,
    },
  });
  assert.deepEqual(results, [{
    id: "shell",
    status: "stopped",
    reason: "governed_shell_restart",
    pid: 4302,
  }]);
});

test("managed startup observes an immediate child exit before recording success", async () => {
  const child = new EventEmitter();
  child.exitCode = null;
  child.signalCode = null;
  setImmediate(() => {
    child.exitCode = 1;
    child.emit("exit", 1, null);
  });

  assert.deepEqual(await observeEarlyProcessExit(child, 100), {
    exited: true,
    exitCode: 1,
    signal: null,
  });

  const running = new EventEmitter();
  running.exitCode = null;
  running.signalCode = null;
  assert.deepEqual(await observeEarlyProcessExit(running, 5), {
    exited: false,
    exitCode: null,
    signal: null,
  });
});

test("managed spawn failures retain structured stage and log references", async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-cli-spawn-failure-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const logs = {
    out: path.join(root, "engine.out.log"),
    err: path.join(root, "engine.err.log"),
  };

  const result = await spawnManagedChild("engine", {
    command: path.join(root, "missing-engine-runtime"),
    args: [],
    cwd: root,
    env: {},
  }, logs);

  assert.equal(result.child, null);
  assert.equal(result.failure?.id, "engine");
  assert.equal(result.failure?.status, "spawn_failed");
  assert.equal(result.failure?.stage, "spawn");
  assert.equal(result.failure?.errorCode, "ENOENT");
  assert.equal(result.failure?.logOut, logs.out);
  assert.equal(result.failure?.logErr, logs.err);
  assert.equal(fs.existsSync(logs.out), true);
  assert.equal(fs.existsSync(logs.err), true);
});

test("desktop pet declares its intentional detached launcher handoff", () => {
  assert.equal(COMPONENTS["desktop-pet"].detachedHandoff, true);
  assert.equal(COMPONENTS.shell.detachedHandoff, undefined);
});

test("desktop pet startup waits for a verified runtime handoff instead of recording the launcher", async () => {
  const pid = 43123;
  const child = new EventEmitter();
  child.exitCode = 0;
  child.signalCode = null;
  let reads = 0;
  const mainEntry = path.join(repoRoot, "apps", "v8-agent-os-desktop-pet", "electron", "main.cjs");
  const receiptContract = { componentId: "desktop-pet", nonce: "delayed-unit", filePath: "ignored" };
  let receiptReads = 0;
  const result = await waitForRuntimeComponentHandoff("desktop-pet", child, {
    timeoutMs: 50,
    pollMs: 1,
    readRuntimeDescriptor: () => (++reads < 2 ? null : { pid, managedByShell: true, descriptorId: "pet-unit" }),
    readProcessDescriptor: async () => ({
      pid,
      executablePath: process.execPath,
      commandLine: `${process.execPath} ${mainEntry}`,
    }),
    pidIsAlive: () => true,
    receiptContract,
    readReceipt: () => (++receiptReads < 3 ? null : { pid }),
  });
  assert.equal(result.ok, true);
  assert.equal(result.pid, pid);
  assert.ok(receiptReads >= 3, "descriptor may arrive before the nonce receipt");

  const failed = await waitForRuntimeComponentHandoff("desktop-pet", {
    exitCode: 1,
    signalCode: null,
  }, {
    timeoutMs: 20,
    pollMs: 1,
    readRuntimeDescriptor: () => null,
  });
  assert.deepEqual(failed, {
    ok: false,
    reason: "launcher_exited",
    exitCode: 1,
    signal: null,
  });
});

test("desktop pet launcher writes a nonce-bound runtime handoff receipt", () => {
  const launcherSource = fs.readFileSync(path.join(repoRoot, "apps", "v8-agent-os-shell", "scripts", "electron-launcher.mjs"), "utf8");
  const managerSource = fs.readFileSync(path.join(cliRoot, "src", "process_manager.mjs"), "utf8");
  assert.match(launcherSource, /V8OS_RUNTIME_HANDOFF_PATH/);
  assert.match(launcherSource, /V8OS_RUNTIME_HANDOFF_NONCE/);
  assert.match(launcherSource, /desktop-pet-\$\{nonce\}\.json/);
  assert.match(managerSource, /runtime_handoff_receipt_mismatch/);
  assert.match(managerSource, /cleanupFailedRuntimeHandoff/);
});

test("failed desktop pet handoff terminates verified runtime and launcher PIDs", async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-pet-handoff-cleanup-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const contract = { componentId: "desktop-pet", nonce: "unit-nonce", filePath: path.join(root, "receipt.json") };
  fs.writeFileSync(contract.filePath, "{}", "utf8");
  const killed = [];
  const removed = [];

  await cleanupFailedRuntimeHandoff("desktop-pet", {
    pid: 44002,
    exitCode: null,
    signalCode: null,
  }, contract, {
    readReceipt: () => null,
    candidatePid: 44001,
    pidIsAlive: () => true,
    readProcessDescriptor: async (pid) => ({ pid }),
    verifyRuntimePid: (_componentId, descriptor) => descriptor.pid,
    killPid: async (pid, options) => {
      killed.push({ pid, options });
      return { ok: true };
    },
    removeRuntimeDescriptor: (componentId, pid) => removed.push({ componentId, pid }),
  });

  assert.deepEqual(killed, [
    { pid: 44001, options: { tree: true } },
    { pid: 44002, options: { tree: true } },
  ]);
  assert.deepEqual(removed, [{ componentId: "desktop-pet", pid: 44001 }]);
  assert.equal(fs.existsSync(contract.filePath), false);
});

test("independent CLI hosts serialize scoped process-state mutations", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-cli-process-race-"));
  const statePath = path.join(stateRoot, "runtime", "cli", "processes.json");
  fs.mkdirSync(path.dirname(statePath), { recursive: true });
  const initialProcesses = {
    engine: { pid: 38472, launchId: "engine-live" },
    admin: { pid: 544, launchId: "admin-live" },
    web: { pid: 38544, launchId: "web-live" },
    shell: { pid: 37040, launchId: "shell-old" },
    "desktop-pet": { pid: 25248, launchId: "pet-old" },
  };
  fs.writeFileSync(statePath, `${JSON.stringify({ version: 1, repoRoot, processes: initialProcesses }, null, 2)}\n`, "utf8");
  const processManagerUrl = new URL("../src/process_manager.mjs", import.meta.url).href;
  const startAt = Date.now() + 1_000;
  const childScript = [
    `const { removeManagedComponentProcessRecord } = await import(${JSON.stringify(processManagerUrl)});`,
    `await new Promise((resolve) => setTimeout(resolve, Math.max(0, Number(process.env.V8_TEST_START_AT) - Date.now())));`,
    `await removeManagedComponentProcessRecord(process.env.V8_TEST_COMPONENT, JSON.parse(process.env.V8_TEST_EXPECTED));`,
  ].join("\n");
  const runMutation = (componentId) => new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ["--input-type=module", "-e", childScript], {
      cwd: repoRoot,
      env: {
        ...process.env,
        V8_AGENT_OS_HOME: stateRoot,
        V8_REPO_ROOT: repoRoot,
        V8_TEST_COMPONENT: componentId,
        V8_TEST_EXPECTED: JSON.stringify({
          pid: initialProcesses[componentId].pid,
          launchId: initialProcesses[componentId].launchId,
        }),
        V8_TEST_START_AT: String(startAt),
      },
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    let stderr = "";
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", reject);
    child.on("exit", (code) => code === 0 ? resolve() : reject(new Error(stderr || `child exited ${code}`)));
  });

  try {
    await Promise.all([runMutation("shell"), runMutation("desktop-pet")]);
    const finalState = JSON.parse(fs.readFileSync(statePath, "utf8"));
    assert.deepEqual(finalState.processes, {
      engine: initialProcesses.engine,
      admin: initialProcesses.admin,
      web: initialProcesses.web,
    });
    const leaseRoot = path.join(path.dirname(statePath), "leases");
    for (const entry of fs.readdirSync(leaseRoot, { withFileTypes: true })) {
      assert.equal(entry.isDirectory(), true);
      assert.deepEqual(fs.readdirSync(path.join(leaseRoot, entry.name)), []);
    }
  } finally {
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("independent CLI hosts never overlap the same component lease", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-cli-cross-host-lease-"));
  const markerPath = path.join(stateRoot, "active.marker");
  const processStateUrl = new URL("../src/process_state.mjs", import.meta.url).href;
  const startAt = Date.now() + 1_000;
  const script = [
    `const fs = await import("node:fs");`,
    `const { withComponentProcessLease } = await import(${JSON.stringify(processStateUrl)});`,
    `await new Promise((resolve) => setTimeout(resolve, Math.max(0, Number(process.env.V8_TEST_START_AT) - Date.now())));`,
    `await withComponentProcessLease("admin", async () => {`,
    `  const handle = fs.openSync(process.env.V8_TEST_ACTIVE_MARKER, "wx"); fs.closeSync(handle);`,
    `  try { await new Promise((resolve) => setTimeout(resolve, 75)); } finally { fs.rmSync(process.env.V8_TEST_ACTIVE_MARKER, { force: true }); }`,
    `});`,
  ].join("\n");
  const runHost = () => runIsolatedModuleScript(script, {
    V8_AGENT_OS_HOME: stateRoot,
    V8_REPO_ROOT: repoRoot,
    V8_TEST_START_AT: String(startAt),
    V8_TEST_ACTIVE_MARKER: markerPath,
  });

  try {
    await Promise.all([runHost(), runHost(), runHost(), runHost()]);
    assert.equal(fs.existsSync(markerPath), false);
    const queuePath = path.join(stateRoot, "runtime", "cli", "leases", "admin.lease.queue");
    assert.deepEqual(fs.readdirSync(queuePath), []);
  } finally {
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("same-component start/start and stop/start lifecycles serialize with launchId CAS", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-cli-lifecycle-race-"));
  const processStateUrl = new URL("../src/process_state.mjs", import.meta.url).href;
  const processManagerUrl = new URL("../src/process_manager.mjs", import.meta.url).href;
  const script = [
    `const fs = await import("node:fs");`,
    `const path = await import("node:path");`,
    `const stateApi = await import(${JSON.stringify(processStateUrl)});`,
    `const processApi = await import(${JSON.stringify(processManagerUrl)});`,
    `const statePath = path.join(process.env.V8_AGENT_OS_HOME, "runtime", "cli", "processes.json");`,
    `fs.mkdirSync(path.dirname(statePath), { recursive: true });`,
    `const write = (processes) => fs.writeFileSync(statePath, JSON.stringify({ version: 1, processes }), "utf8");`,
    `const start = (launchId, pid) => stateApi.withComponentProcessLease("admin", async () => {`,
    `  const current = stateApi.readProcessState().processes.admin || null;`,
    `  if (current) return "already_running";`,
    `  const result = await stateApi.compareAndSwapProcessRecord("admin", null, { pid, launchId });`,
    `  return result.applied ? "started" : "conflict";`,
    `});`,
    `write({});`,
    `const starts = await Promise.all([start("admin-a", 501), start("admin-b", 502)]);`,
    `const startedRecord = stateApi.readProcessState().processes.admin;`,
    `let releaseStop; let stopEnteredResolve;`,
    `const stopEntered = new Promise((resolve) => { stopEnteredResolve = resolve; });`,
    `const oldShell = { pid: 601, launchId: "shell-old" };`,
    `write({ admin: startedRecord, shell: oldShell });`,
    `const stop = stateApi.withComponentProcessLease("shell", async () => {`,
    `  const expected = stateApi.processRecordIdentity(stateApi.readProcessState().processes.shell);`,
    `  stopEnteredResolve();`,
    `  await new Promise((resolve) => { releaseStop = resolve; });`,
    `  return stateApi.compareAndSwapProcessRecord("shell", expected, null);`,
    `});`,
    `await stopEntered;`,
    `const replacement = stateApi.withComponentProcessLease("shell", async () => {`,
    `  const inserted = await stateApi.compareAndSwapProcessRecord("shell", null, { pid: 602, launchId: "shell-new" });`,
    `  return inserted.applied;`,
    `});`,
    `await new Promise((resolve) => setTimeout(resolve, 30));`,
    `releaseStop();`,
    `const [stopped, replaced] = await Promise.all([stop, replacement]);`,
    `const staleCleanup = await processApi.removeManagedComponentProcessRecord("shell", oldShell);`,
    `const casMismatch = await stateApi.compareAndSwapProcessRecord("shell", oldShell, null);`,
    `process.stdout.write(JSON.stringify({ starts, startedRecord, stopped: stopped.applied, replaced, staleCleanup, casMismatch: casMismatch.applied, final: stateApi.readProcessState().processes }));`,
  ].join("\n");

  try {
    const payload = JSON.parse(await runIsolatedModuleScript(script, {
      V8_AGENT_OS_HOME: stateRoot,
      V8_REPO_ROOT: repoRoot,
    }));
    assert.deepEqual([...payload.starts].sort(), ["already_running", "started"]);
    assert.ok(["admin-a", "admin-b"].includes(payload.startedRecord.launchId));
    assert.equal(payload.stopped, true);
    assert.equal(payload.replaced, true);
    assert.equal(payload.staleCleanup, false, "old Shell cleanup must not delete its replacement");
    assert.equal(payload.casMismatch, false, "old pid+launchId cannot delete a replacement");
    assert.deepEqual(payload.final.shell, { pid: 602, launchId: "shell-new" });
  } finally {
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("a process-state commit failure terminates the spawned component without recording it", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-cli-orphan-rollback-state-"));
  const fakeRepo = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-cli-orphan-rollback-repo-"));
  const markerPath = path.join(stateRoot, "spawned-pid.txt");
  const launcherPath = path.join(fakeRepo, "apps", "v8-agent-os-shell", "scripts", "launch-shell.mjs");
  const blockerQueue = path.join(stateRoot, "runtime", "cli", "leases", "state-write.lease.queue");
  fs.mkdirSync(path.dirname(launcherPath), { recursive: true });
  fs.mkdirSync(blockerQueue, { recursive: true });
  fs.writeFileSync(launcherPath, [
    `import fs from "node:fs";`,
    `fs.writeFileSync(process.env.V8_TEST_CHILD_PID_PATH, String(process.pid), "utf8");`,
    `setInterval(() => undefined, 1_000);`,
  ].join("\n"), "utf8");
  fs.writeFileSync(path.join(blockerQueue, "ticket-live-blocker.json"), JSON.stringify({
    version: 2,
    leaseId: "live-blocker",
    ownerPid: process.pid,
    ticketNumber: 1,
  }), "utf8");
  const processManagerUrl = new URL("../src/process_manager.mjs", import.meta.url).href;
  const script = [
    `const fs = await import("node:fs");`,
    `const { startComponents } = await import(${JSON.stringify(processManagerUrl)});`,
    `let errorMessage = "";`,
    `try { await startComponents(["shell"], { mode: "start" }); } catch (error) { errorMessage = error?.message || String(error); }`,
    `const pid = Number(fs.readFileSync(process.env.V8_TEST_CHILD_PID_PATH, "utf8"));`,
    `await new Promise((resolve) => setTimeout(resolve, 100));`,
    `let alive = true; try { process.kill(pid, 0); } catch { alive = false; }`,
    `const statePath = process.env.V8_AGENT_OS_HOME + "/runtime/cli/processes.json";`,
    `const recorded = fs.existsSync(statePath) ? Boolean(JSON.parse(fs.readFileSync(statePath, "utf8")).processes?.shell) : false;`,
    `process.stdout.write(JSON.stringify({ errorMessage, pid, alive, recorded }));`,
  ].join("\n");

  try {
    const payload = JSON.parse(await runIsolatedModuleScript(script, {
      V8_AGENT_OS_HOME: stateRoot,
      V8_REPO_ROOT: fakeRepo,
      V8_TEST_CHILD_PID_PATH: markerPath,
    }));
    assert.match(payload.errorMessage, /Timed out waiting for V8OS lease/);
    assert.equal(payload.alive, false);
    assert.equal(payload.recorded, false);
  } finally {
    if (fs.existsSync(markerPath)) {
      const pid = Number(fs.readFileSync(markerPath, "utf8"));
      try {
        if (process.platform === "win32") {
          spawnSync("taskkill", ["/PID", String(pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" });
        } else {
          process.kill(-pid, "SIGKILL");
        }
      } catch {}
    }
    fs.rmSync(stateRoot, { recursive: true, force: true });
    fs.rmSync(fakeRepo, { recursive: true, force: true });
  }
});

test("component leases recover dead owners without blocking the event loop or other components", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-cli-lease-behavior-"));
  const processStateUrl = new URL("../src/process_state.mjs", import.meta.url).href;
  const script = [
    `const fs = await import("node:fs");`,
    `const path = await import("node:path");`,
    `const { withComponentProcessLease } = await import(${JSON.stringify(processStateUrl)});`,
    `let releaseAdmin; let adminEnteredResolve;`,
    `const adminEntered = new Promise((resolve) => { adminEnteredResolve = resolve; });`,
    `const firstAdmin = withComponentProcessLease("admin", async () => {`,
    `  adminEnteredResolve();`,
    `  await new Promise((resolve) => { releaseAdmin = resolve; });`,
    `});`,
    `await adminEntered;`,
    `let timerTicked = false; let secondAdminEntered = false;`,
    `setTimeout(() => { timerTicked = true; }, 10);`,
    `const secondAdmin = withComponentProcessLease("admin", async () => { secondAdminEntered = true; });`,
    `const webStartedAt = Date.now();`,
    `await withComponentProcessLease("web", async () => undefined);`,
    `const webElapsedMs = Date.now() - webStartedAt;`,
    `await new Promise((resolve) => setTimeout(resolve, 60));`,
    `const observedWhileWaiting = { timerTicked, secondAdminEntered, webElapsedMs };`,
    `releaseAdmin();`,
    `await Promise.all([firstAdmin, secondAdmin]);`,
    `const leaseDir = path.join(process.env.V8_AGENT_OS_HOME, "runtime", "cli", "leases");`,
    `fs.mkdirSync(leaseDir, { recursive: true });`,
    `const staleQueue = path.join(leaseDir, "engine.lease.queue");`,
    `fs.mkdirSync(staleQueue, { recursive: true });`,
    `const stalePath = path.join(staleQueue, "ticket-dead-owner.json");`,
    `const damagedPath = path.join(staleQueue, "ticket-damaged-owner.json");`,
    `fs.writeFileSync(stalePath, JSON.stringify({ leaseId: "dead-owner", ownerPid: 2147483647, ticketNumber: 1 }), "utf8");`,
    `fs.writeFileSync(damagedPath, "", "utf8");`,
    `const old = new Date(Date.now() - 5_000); fs.utimesSync(stalePath, old, old); fs.utimesSync(damagedPath, old, old);`,
    `let active = 0; let maxActive = 0;`,
    `await Promise.all(Array.from({ length: 6 }, () => withComponentProcessLease("engine", async () => {`,
    `  active += 1; maxActive = Math.max(maxActive, active);`,
    `  await new Promise((resolve) => setTimeout(resolve, 10));`,
    `  active -= 1;`,
    `}, { staleAfterMs: 0, timeoutMs: 1_500, retryMs: 5 })));`,
    `const remaining = fs.readdirSync(leaseDir, { withFileTypes: true }).flatMap((entry) => entry.isDirectory() ? fs.readdirSync(path.join(leaseDir, entry.name)).map((name) => path.join(entry.name, name)) : [entry.name]);`,
    `process.stdout.write(JSON.stringify({ observedWhileWaiting, recovered: maxActive === 1, maxActive, remaining }));`,
  ].join("\n");

  try {
    const payload = JSON.parse(await runIsolatedModuleScript(script, {
      V8_AGENT_OS_HOME: stateRoot,
      V8_REPO_ROOT: repoRoot,
    }));
    assert.equal(payload.observedWhileWaiting.timerTicked, true, "lease wait must yield to the Electron event loop");
    assert.equal(payload.observedWhileWaiting.secondAdminEntered, false, "same-component lifecycle must remain serialized");
    assert.ok(payload.observedWhileWaiting.webElapsedMs < 500, `different component blocked for ${payload.observedWhileWaiting.webElapsedMs}ms`);
    assert.equal(payload.recovered, true);
    assert.equal(payload.maxActive, 1, "concurrent stale reclaimers must never enter the callback together");
    assert.deepEqual(payload.remaining, []);
  } finally {
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("status confirms and CAS-removes a stale record once", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-cli-stale-status-"));
  const statePath = path.join(stateRoot, "runtime", "cli", "processes.json");
  fs.mkdirSync(path.dirname(statePath), { recursive: true });
  fs.writeFileSync(statePath, JSON.stringify({
    version: 1,
    processes: {
      "desktop-pet": {
        pid: 2147483647,
        launchId: "dead-pet-launch",
        command: process.execPath,
        args: ["apps/v8-agent-os-shell/scripts/launch-desktop-pet.mjs"],
        cwd: repoRoot,
      },
    },
  }), "utf8");
  const processManagerUrl = new URL("../src/process_manager.mjs", import.meta.url).href;
  const script = [
    `const { statusComponents } = await import(${JSON.stringify(processManagerUrl)});`,
    `const fs = await import("node:fs");`,
    `const path = await import("node:path");`,
    `let timerTicked = false; setTimeout(() => { timerTicked = true; }, 10);`,
    `const first = await statusComponents(["desktop-pet"]);`,
    `const statePath = path.join(process.env.V8_AGENT_OS_HOME, "runtime", "cli", "processes.json");`,
    `const afterFirst = JSON.parse(fs.readFileSync(statePath, "utf8"));`,
    `const second = await statusComponents(["desktop-pet"]);`,
    `process.stdout.write(JSON.stringify({ first, second, afterFirst, timerTicked }));`,
  ].join("\n");

  try {
    const payload = JSON.parse(await runIsolatedModuleScript(script, {
      V8_AGENT_OS_HOME: stateRoot,
      V8_REPO_ROOT: repoRoot,
    }));
    assert.equal(payload.first[0].managed, false);
    assert.equal(payload.first[0].state, "stopped");
    assert.equal(payload.timerTicked, true, "status process probing must yield to the Electron event loop");
    assert.equal(payload.afterFirst.processes["desktop-pet"], undefined);
    assert.equal(payload.second[0].pid, null);
  } finally {
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("Shell uses immediate event refresh plus low-frequency process reconciliation", () => {
  const source = fs.readFileSync(path.join(repoRoot, "apps", "v8-agent-os-shell", "electron", "main.cjs"), "utf8");
  assert.match(source, /setInterval\(\(\) => \{ void refreshStatus\(\); \}, 10_000\)/);
  assert.match(source, /if \(statusRefreshPromise\) return statusRefreshPromise/);
  assert.match(source, /statusRefreshPromise = refreshStatusOnce\(\)\.finally/);
  assert.match(source, /await shellStop\(\['desktop-pet'\]\)/);
  assert.match(source, /await removeShellProcessRecord\(shellProcessRecordIdentity\)/);
});

test("Windows Next launcher state and standalone listener resolve as one managed chain", () => {
  const launcherPid = 46692;
  const standalonePid = 25764;
  const spec = COMPONENTS.admin.command({ mode: "start" });
  const launcherCommandLine = `${spec.command} ${spec.args.join(" ")}`;
  const launcherRecord = {
    pid: launcherPid,
    command: spec.command,
    args: spec.args,
    cwd: spec.cwd,
  };
  const launcherDescriptor = {
    pid: launcherPid,
    executablePath: spec.command,
    commandLine: launcherCommandLine,
  };
  const standaloneDescriptor = {
    pid: standalonePid,
    parentPid: launcherPid,
    executablePath: spec.command,
    commandLine: `${spec.command} ${path.join(repoRoot, "apps", "v8-agent-os-admin", ".next", "standalone", "server.js")}`,
    parentExecutablePath: spec.command,
    parentCommandLine: launcherCommandLine,
  };

  assert.equal(verifiedManagedComponentPid("admin", launcherRecord, launcherDescriptor), launcherPid);
  assert.deepEqual(verifiedComponentPortOwner("admin", standaloneDescriptor), {
    ownerPid: standalonePid,
    killPid: launcherPid,
    matchedBy: "verified_parent_runtime",
  });
});

test("resident Electron host preserves Node-started Next launcher identity", () => {
  const electronHost = path.join(repoRoot, "apps", "v8-agent-os-desktop-pet", "node_modules", "electron", "dist", "electron.exe");
  const childScript = [
    `const nodeExecutable = ${JSON.stringify(process.execPath)};`,
    `process.execPath = ${JSON.stringify(electronHost)};`,
    `process.versions.electron = "test-electron";`,
    `const { verifiedManagedComponentPid } = await import(${JSON.stringify(new URL("../src/process_manager.mjs", import.meta.url).href)});`,
    `const args = ["scripts/run-next-with-managed-auth.mjs", "--app", "admin", "--mode", "start", "--port", "9528"];`,
    `const record = { pid: 46692, command: nodeExecutable, args, cwd: ${JSON.stringify(repoRoot)} };`,
    `const descriptor = { pid: 46692, executablePath: nodeExecutable, commandLine: [nodeExecutable, ...args].join(" ") };`,
    `process.stdout.write(String(verifiedManagedComponentPid("admin", record, descriptor)));`,
  ].join("\n");
  const result = spawnSync(process.execPath, ["--input-type=module", "-e", childScript], {
    cwd: repoRoot,
    encoding: "utf8",
    windowsHide: true,
  });

  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.equal(result.stdout, "46692");
});

test("Node CLI recognizes only the project-controlled Electron Next launcher", () => {
  const pid = 38544;
  const args = ["scripts/run-next-with-managed-auth.mjs", "--app", "web", "--mode", "start", "--port", "9527"];
  const controlledElectron = path.join(
    repoRoot,
    "apps",
    "v8-agent-os-desktop-pet",
    "node_modules",
    "electron",
    "dist",
    process.platform === "win32" ? "electron.exe" : "electron",
  );
  const record = { pid, command: controlledElectron, args, cwd: repoRoot };
  const descriptor = {
    pid,
    executablePath: controlledElectron,
    commandLine: [controlledElectron, ...args].join(" "),
  };

  assert.equal(verifiedManagedComponentPid("web", record, descriptor), pid);
  const unrelatedElectron = path.join(os.tmpdir(), process.platform === "win32" ? "electron.exe" : "electron");
  assert.equal(verifiedManagedComponentPid("web", {
    ...record,
    command: unrelatedElectron,
  }, {
    ...descriptor,
    executablePath: unrelatedElectron,
    commandLine: [unrelatedElectron, ...args].join(" "),
  }), null, "an unrelated Electron executable must not become a managed Next runtime");
  assert.equal(verifiedManagedComponentPid("web", record, {
    ...descriptor,
    executablePath: process.execPath,
  }), null, "record and live descriptor executables must still agree");
  assert.equal(verifiedManagedComponentPid("web", record, {
    ...descriptor,
    commandLine: [controlledElectron, ...args.slice(0, -1), "9999"].join(" "),
  }), null, "the exact managed port remains mandatory");
  assert.equal(verifiedManagedComponentPid("web", {
    ...record,
    cwd: path.join(os.tmpdir(), "other-repo"),
  }, descriptor), null, "the repository cwd remains mandatory");
});

test("managed process identity accepts tagged POSIX ps comm basename with full component proof", () => {
  const pid = 41991;
  const spec = COMPONENTS.admin.command({ mode: "start" });
  const record = {
    pid,
    command: spec.command,
    args: spec.args,
    cwd: spec.cwd,
  };
  const psCommDescriptor = {
    pid,
    executablePath: path.basename(spec.command).replace(/\.exe$/i, ""),
    executablePathKind: "posix_comm",
    commandLine: [spec.command, ...spec.args].join(" "),
    cwd: spec.cwd,
  };

  assert.equal(verifiedManagedComponentPid("admin", record, psCommDescriptor), pid);
  assert.equal(
    verifiedManagedComponentPid("admin", record, { ...psCommDescriptor, executablePathKind: undefined }),
    null,
    "an untagged Windows/CIM-style basename must remain fail-closed",
  );
  assert.equal(
    verifiedManagedComponentPid("admin", record, { ...psCommDescriptor, commandLine: "node unrelated.mjs" }),
    null,
    "the component command signature remains mandatory",
  );
  assert.equal(
    verifiedManagedComponentPid("admin", record, { ...psCommDescriptor, cwd: path.join(os.tmpdir(), "other") }),
    null,
    "the component cwd remains mandatory when the OS exposes it",
  );
  assert.equal(
    verifiedManagedComponentPid("admin", record, { ...psCommDescriptor, cwd: null }),
    null,
    "a basename-only POSIX descriptor without cwd proof must remain fail-closed",
  );
});

test("POSIX process identity keeps repository path comparisons case-sensitive", { skip: process.platform === "win32" }, () => {
  const pid = 41993;
  const spec = COMPONENTS.admin.command({ mode: "start" });
  const record = { pid, command: spec.command, args: spec.args, cwd: spec.cwd };
  const serverPath = path.join(repoRoot, "apps", "v8-agent-os-admin", ".next", "standalone", "server.js");
  const descriptor = {
    pid,
    executablePath: spec.command,
    commandLine: `${spec.command} ${serverPath}`,
    cwd: spec.cwd,
  };
  assert.equal(verifiedManagedComponentPid("admin", record, descriptor), pid);
  assert.equal(verifiedManagedComponentPid("admin", record, {
    ...descriptor,
    commandLine: `${spec.command} ${serverPath.replace("v8-agent-os-admin", "V8-AGENT-OS-ADMIN")}`,
  }), null);
});

test("managed process identity recognizes POSIX npm through its Node interpreter only with cwd proof", () => {
  const pid = 41992;
  const spec = COMPONENTS.cybercore.command({ mode: "dev" });
  const record = { pid, command: spec.command, args: spec.args, cwd: spec.cwd };
  const descriptor = {
    pid,
    processDescriptorSource: "posix_ps",
    executablePath: "/usr/bin/node",
    executablePathKind: "exact",
    commandLine: "npm run dev",
    cwd: spec.cwd,
  };

  assert.equal(verifiedManagedComponentPid("cybercore", record, descriptor), pid);
  assert.equal(verifiedManagedComponentPid("cybercore", record, { ...descriptor, cwd: null }), null);
  assert.equal(verifiedManagedComponentPid("cybercore", record, { ...descriptor, processDescriptorSource: undefined }), null);
  assert.equal(verifiedManagedComponentPid("cybercore", record, { ...descriptor, commandLine: "node unrelated.mjs" }), null);
});

test("stale recorded Engine PID never enters the verified stop target set", () => {
  const pid = 19676;
  const spec = COMPONENTS.engine.command({ mode: "start" });
  const identity = resolveManagedComponentIdentity("engine", {
    record: { pid, command: spec.command, args: spec.args, cwd: spec.cwd },
    processDescriptors: new Map([[pid, {
      pid,
      executablePath: "C:\\Program Files\\WindowsApps\\Microsoft.WidgetsPlatformRuntime\\WidgetService.exe",
      commandLine: "WidgetService.exe -RegisterProcessAsComServer -Embedding",
    }]]),
    pidIsAlive: () => true,
  });
  assert.deepEqual(identity.verifiedPids, []);
  assert.deepEqual(identity.stalePids, [pid]);
  assert.deepEqual(identity.unverifiedPids, []);
});

test("identity probing fails closed when a live PID cannot be described", () => {
  const pid = 42000;
  const spec = COMPONENTS.engine.command({ mode: "start" });
  const identity = resolveManagedComponentIdentity("engine", {
    record: { pid, command: spec.command, args: spec.args, cwd: spec.cwd },
    processDescriptors: new Map(),
    pidIsAlive: () => true,
  });
  assert.deepEqual(identity.verifiedPids, []);
  assert.deepEqual(identity.unverifiedPids, [pid]);
});

test("Shell and desktop pet runtime descriptors require their Electron entry identity", () => {
  const electron = path.join(repoRoot, "apps", "v8-agent-os-desktop-pet", "node_modules", "electron", "dist", "electron.exe");
  const shellPid = 43000;
  const petPid = 43001;
  assert.equal(verifiedRuntimeComponentPid("shell", {
    pid: shellPid,
    executablePath: electron,
    commandLine: `${electron} ${path.join(repoRoot, "apps", "v8-agent-os-shell")}`,
  }), shellPid);
  assert.equal(verifiedRuntimeComponentPid("desktop-pet", {
    pid: petPid,
    executablePath: electron,
    commandLine: `${electron} ${path.join(repoRoot, "apps", "v8-agent-os-desktop-pet", "electron", "main.cjs")}`,
  }), petPid);
  assert.equal(verifiedRuntimeComponentPid("desktop-pet", {
    pid: petPid,
    executablePath: electron,
    commandLine: `${electron} C:\\other-app\\main.cjs`,
  }), null);
});

test("packaged Shell and desktop pet descriptors bind the runtime to the governed resource root", () => {
  const packageRoot = path.join(os.tmpdir(), "v8os-packaged-identity");
  const executable = process.platform === "darwin"
    ? path.join(packageRoot, "V8 Agent OS.app", "Contents", "MacOS", "V8 Agent OS")
    : path.join(packageRoot, process.platform === "win32" ? "V8 Agent OS.exe" : "v8-agent-os-shell");
  const packagedRepoRoot = process.platform === "darwin"
    ? path.join(packageRoot, "V8 Agent OS.app", "Contents", "Resources", "v8os")
    : path.join(packageRoot, "resources", "v8os");
  const candidate = { pid: 45001, executablePath: executable, commandLine: executable };
  const shellDescriptor = {
    pid: 45001,
    packaged: true,
    runtimeKind: "shell",
    executablePath: executable,
    repoRoot: packagedRepoRoot,
  };

  assert.equal(packagedRuntimeDescriptorMatches("shell", candidate, shellDescriptor, {
    repoRoot: packagedRepoRoot,
  }), true);
  assert.equal(packagedRuntimeDescriptorMatches("desktop-pet", candidate, shellDescriptor, {
    repoRoot: packagedRepoRoot,
  }), false, "the packaged runtime kind cannot be reused across components");
  assert.equal(packagedRuntimeDescriptorMatches("shell", {
    ...candidate,
    executablePath: path.join(packageRoot, "unrelated.exe"),
  }, shellDescriptor, { repoRoot: packagedRepoRoot }), false);
  assert.equal(packagedRuntimeDescriptorMatches("shell", {
    ...candidate,
    pid: 45002,
  }, shellDescriptor, { repoRoot: packagedRepoRoot }), false, "a reused PID must not inherit a stale Shell descriptor");
  const desktopPetMain = path.join(packagedRepoRoot, "apps", "v8-agent-os-desktop-pet", "electron", "main.cjs");
  const petCandidate = {
    ...candidate,
    pid: 45002,
    commandLine: `${executable} ${desktopPetMain}`,
  };
  const petDescriptor = {
    ...shellDescriptor,
    pid: 45002,
    runtimeKind: "desktop-pet",
  };
  assert.equal(packagedRuntimeDescriptorMatches("desktop-pet", petCandidate, petDescriptor, {
    repoRoot: packagedRepoRoot,
  }), true);
  assert.equal(packagedRuntimeDescriptorMatches("shell", petCandidate, {
    ...shellDescriptor,
    pid: 45002,
  }, { repoRoot: packagedRepoRoot }), false, "a packaged desktop pet must not satisfy Shell identity");
  assert.equal(packagedRuntimeDescriptorMatches("desktop-pet", candidate, {
    ...shellDescriptor,
    runtimeKind: "desktop-pet",
  }, { repoRoot: packagedRepoRoot }), false, "the Shell browser must not satisfy desktop pet identity");
  assert.equal(packagedRuntimeDescriptorMatches("shell", candidate, {
    ...shellDescriptor,
    repoRoot: path.join(packageRoot, "other", "v8os"),
  }, { repoRoot: packagedRepoRoot }), false);
});

test("json backup writes timestamped backup without changing source", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-cli-json-"));
  const file = path.join(dir, "config.json");
  writeJsonFile(file, { ok: true });
  const backup = backupFile(file, "unit");
  assert.deepEqual(readJsonFile(file), { ok: true });
  assert.deepEqual(readJsonFile(backup), { ok: true });
  assert.match(path.basename(backup), /^config\.json\.unit\./);
});

test("admin and web commands use managed auth launcher", () => {
  const admin = COMPONENTS.admin.command({ mode: "dev" });
  const web = COMPONENTS.web.command({ mode: "start" });
  assert.equal(admin.command, process.execPath);
  assert.equal(web.command, process.execPath);
  assert.ok(admin.args.some((part) => part.endsWith("run-next-with-managed-auth.mjs")));
  assert.ok(web.args.some((part) => part.endsWith("run-next-with-managed-auth.mjs")));
  assert.ok(admin.args.includes("admin"));
  assert.ok(web.args.includes("web"));
  assert.ok(admin.args.includes("9528"));
  assert.ok(web.args.includes("9527"));
});

test("component commands project the selected Web fallback into every local surface", () => {
  try {
    const runtimePorts = { engine: 9530, admin: 9528, web: 19527 };
    configureComponentRuntimePorts(DEFAULT_PORTS);
    const web = COMPONENTS.web.command({ mode: "start", runtimePorts });
    const admin = COMPONENTS.admin.command({ mode: "start", runtimePorts });
    const engine = COMPONENTS.engine.command({ mode: "start", runtimePorts });
    const shell = COMPONENTS.shell.command({ mode: "start", runtimePorts });
    const pet = COMPONENTS["desktop-pet"].command({ mode: "start", runtimePorts });
    assert.equal(COMPONENTS.web.port, DEFAULT_PORTS.web);
    assert.ok(web.args.includes("19527"));
    assert.equal(web.env.V8_WEB_BASE_URL, "http://127.0.0.1:19527");
    assert.equal(admin.env.V8_WEB_BASE_URL, "http://127.0.0.1:19527");
    assert.equal(engine.env.V8_WEB_BASE_URL, "http://127.0.0.1:19527");
    assert.equal(shell.env.V8_WEB_BASE_URL, "http://127.0.0.1:19527");
    assert.equal(pet.env.V8_WEB_BASE_URL, "http://127.0.0.1:19527");
    assert.deepEqual(componentRuntimePorts(), DEFAULT_PORTS);
  } finally {
    configureComponentRuntimePorts(DEFAULT_PORTS);
  }
});

test("component start holds the governed port lease only through Web process record insertion", () => {
  const processManager = fs.readFileSync(
    path.join(cliRoot, "src", "process_manager.mjs"),
    "utf8",
  );
  const runtimePorts = fs.readFileSync(
    path.join(cliRoot, "src", "runtime_ports.mjs"),
    "utf8",
  );
  const processState = fs.readFileSync(
    path.join(cliRoot, "src", "process_state.mjs"),
    "utf8",
  );
  const startBlock = processManager.slice(
    processManager.indexOf("export async function startComponentsWithRuntimePorts"),
    processManager.indexOf("async function killPid"),
  );
  const outsideLeaseIndex = startBlock.indexOf("\n  configureComponentRuntimePorts(profile.ports);");
  assert.ok(outsideLeaseIndex > 0);
  const leaseBlock = startBlock.slice(0, outsideLeaseIndex);
  const outsideLeaseBlock = startBlock.slice(outsideLeaseIndex);
  assert.match(leaseBlock, /const profile = await withRuntimePortsLease\(async \(\) => \{/);
  assert.match(leaseBlock, /resolveCurrentManagedIdentity\("web", state\)/);
  assert.match(leaseBlock, /verifiedManagedWebPort/);
  assert.match(leaseBlock, /webResult = await startComponent\("web"/);
  assert.doesNotMatch(leaseBlock, /Promise\.all/);
  assert.match(outsideLeaseBlock, /const results = await Promise\.all/);
  assert.match(outsideLeaseBlock, /startComponent\(id, \{ \.\.\.options, runtimePorts: profile\.ports \}\)/);
  assert.match(startBlock, /return \{ profile, results \}/);
  assert.doesNotMatch(runtimePorts, /readProcessState|isPidAlive/);
  assert.match(processState, /withRuntimePortsLease[\s\S]{0,180}timeoutMs: 30_000/);
});

test("preview shell and desktop pet are no-port managed components", () => {
  assert.equal(COMPONENTS.shell.port, null);
  assert.equal(COMPONENTS["desktop-pet"].port, null);
  const shell = COMPONENTS.shell.command();
  const pet = COMPONENTS["desktop-pet"].command();
  assert.equal(shell.command, process.execPath);
  assert.equal(pet.command, process.execPath);
  assert.ok(shell.args.some((part) => part.endsWith("launch-shell.mjs")));
  assert.ok(pet.args.some((part) => part.endsWith("launch-desktop-pet.mjs")));
  assert.equal(pet.env.V8_DESKTOP_PET_MANAGED_BY_SHELL, "1");
});

test("desktop pet managed mode suppresses its own tray", () => {
  const main = fs.readFileSync(path.join(repoRoot, "apps", "v8-agent-os-desktop-pet", "electron", "main.cjs"), "utf8");
  assert.match(main, /V8_DESKTOP_PET_MANAGED_BY_SHELL/);
  assert.match(main, /MANAGED_BY_SHELL/);
  assert.match(main, /if \(!MANAGED_BY_SHELL\) createTray\(\)/);
});

test("Windows browser and workspace openers never flash a command window", () => {
  for (const relativePath of ["cli.mjs", "workspace_commands.mjs", "session_commands.mjs"]) {
    const source = fs.readFileSync(path.join(cliRoot, "src", relativePath), "utf8");
    assert.match(source, /spawn\(command, commandArgs, \{ detached: true, stdio: "ignore", windowsHide: true \}\)\.unref\(\)/);
  }
});

test("Windows diagnostic and Electron helper processes never flash a command window", () => {
  const doctor = fs.readFileSync(path.join(cliRoot, "src", "doctor.mjs"), "utf8");
  const ports = fs.readFileSync(path.join(cliRoot, "src", "ports.mjs"), "utf8");
  const electronLauncher = fs.readFileSync(
    path.join(repoRoot, "apps", "v8-agent-os-shell", "scripts", "electron-launcher.mjs"),
    "utf8",
  );

  assert.match(doctor, /spawnSync\(command, args, \{ encoding: "utf8", timeout: 2500, windowsHide: true \}\)/);
  assert.match(doctor, /shell: true,\s+windowsHide: true,/);
  assert.match(ports, /spawnSync\("powershell\.exe"[\s\S]*?windowsHide: true,/);
  assert.match(electronLauncher, /spawnSync\(process\.execPath[\s\S]*?windowsHide: true,/);
  assert.match(electronLauncher, /spawn\(process\.execPath[\s\S]*?windowsHide: true,/);
});

test("desktop pet survives Shell replacement through detached handoff and exact Shell termination", () => {
  const launcher = fs.readFileSync(
    path.join(repoRoot, "apps", "v8-agent-os-shell", "scripts", "launch-desktop-pet.mjs"),
    "utf8",
  );
  const interposer = fs.readFileSync(
    path.join(repoRoot, "apps", "v8-agent-os-shell", "scripts", "spawn-detached-electron.mjs"),
    "utf8",
  );
  const processManager = fs.readFileSync(
    path.join(repoRoot, "apps", "v8-agent-os-cli", "src", "process_manager.mjs"),
    "utf8",
  );
  assert.match(launcher, /launchDetachedElectron/);
  assert.match(interposer, /detached:\s*true/);
  assert.match(interposer, /child\.unref\(\)/);
  assert.match(processManager, /desktop-pet\.json/);
  assert.match(processManager, /resolveLiveManagedIdentity/);
  assert.match(processManager, /shell-control\.json/);
  assert.equal(SHELL_TERMINATION_TIMEOUT_MS, 20_000);
  assert.match(processManager, /killPid\(pid, managedStopOptions\(id, process\.platform,/);
  assert.match(processManager, /stopped_during_kill/);
  assert.match(processManager, /await runChildCommand\("taskkill", args, \{ timeoutMs: 5_000 \}\)/);
  assert.doesNotMatch(processManager, /spawnSync/);
});

test("preview build check is based on Next BUILD_ID", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-preview-build-"));
  assert.equal(isNextBuildPresent(dir), false);
  fs.mkdirSync(path.dirname(nextBuildIdPath(dir)), { recursive: true });
  fs.writeFileSync(nextBuildIdPath(dir), "unit-build\n", "utf8");
  assert.equal(isNextBuildPresent(dir), true);
});

test("preview build logs are written to CLI logs", () => {
  const logs = previewBuildLogPaths({ app: "web" });
  assert.match(logs.out, /web\.build\.out\.log$/);
  assert.match(logs.err, /web\.build\.err\.log$/);
});

test("preview startup validation rejects missing and failed components", () => {
  assert.deepEqual(validateComponentStartResults([
    { id: "engine", status: "started" },
    { id: "admin", status: "already_running" },
    { id: "web", status: "port_in_use" },
  ], ["engine", "admin", "web", "shell"]), {
    ok: false,
    rejected: [
      { id: "web", status: "port_in_use" },
      { id: "shell", status: "missing" },
    ],
  });
  assert.equal(validateComponentStartResults([
    { id: "engine", status: "started" },
    { id: "admin", status: "already_running" },
  ], ["engine", "admin"]).ok, true);
});

test("preview waits for a fresh live Shell control descriptor", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-shell-control-ready-"));
  const descriptorPath = path.join(root, "shell-control.json");
  const nowMs = Date.now();
  const descriptor = {
    version: 1,
    endpoint: "test-shell-control",
    pid: 4242,
    token: "a".repeat(64),
    createdAt: new Date(nowMs).toISOString(),
    surfaceReady: true,
    surfaceKind: "web",
    surfaceReadyAt: new Date(nowMs).toISOString(),
  };
  try {
    fs.writeFileSync(descriptorPath, JSON.stringify(descriptor), "utf8");
    assert.equal(validateShellControlDescriptor(descriptor, {
      nowMs,
      notBeforeMs: nowMs - 1,
      pidIsAlive: (pid) => pid === 4242,
    }), true);
    assert.equal(validateShellControlDescriptor(descriptor, {
      nowMs,
      notBeforeMs: nowMs + 1,
      pidIsAlive: () => true,
    }), false);
    assert.equal(validateShellControlDescriptor({ ...descriptor, surfaceReady: false }, {
      nowMs,
      notBeforeMs: nowMs - 1,
      pidIsAlive: () => true,
    }), false);
    assert.equal(validateShellControlDescriptor({ ...descriptor, pid: 5252 }, {
      nowMs,
      notBeforeMs: nowMs - 1,
      expectedPid: 4242,
      pidIsAlive: () => true,
    }), false);
    assert.equal(validateShellControlDescriptor(descriptor, {
      nowMs,
      notBeforeMs: nowMs - 1,
      expectedPid: 4242,
      pidIsAlive: () => true,
    }), true);
    assert.equal(validateShellControlDescriptor({ ...descriptor, surfaceKind: "admin-login" }, {
      nowMs,
      notBeforeMs: nowMs - 1,
      pidIsAlive: () => true,
    }), true);
    assert.equal(validateShellControlDescriptor({ ...descriptor, surfaceKind: "startup" }, {
      nowMs,
      notBeforeMs: nowMs - 1,
      pidIsAlive: () => true,
    }), false);
    assert.deepEqual(await waitForShellControlDescriptor({
      descriptorPath,
      timeoutMs: 20,
      nowMs,
      notBeforeMs: nowMs - 1,
      pidIsAlive: () => true,
    }), descriptor);
    assert.equal(await waitForShellControlDescriptor({
      descriptorPath,
      timeoutMs: 20,
      nowMs,
      notBeforeMs: nowMs + 1,
      pidIsAlive: () => true,
    }), null);
    fs.writeFileSync(descriptorPath, JSON.stringify({
      ...descriptor,
      surfaceReady: false,
      surfaceKind: null,
      surfaceReadyAt: null,
    }), "utf8");
    assert.equal(await waitForShellControlDescriptor({
      descriptorPath,
      timeoutMs: 20,
      nowMs,
      notBeforeMs: nowMs - 1,
      pidIsAlive: () => true,
    }), null);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("preview rebuild restarts shell and adopts verified Next/Engine port owners before verification", () => {
  assert.deepEqual(previewRebuildStopComponentIds({ rebuild: true }), ["shell", "admin", "web", "engine"]);
  assert.deepEqual(previewRebuildStopComponentIds({ rebuild: false }), []);
  const previewSource = fs.readFileSync(path.join(cliRoot, "src", "preview_commands.mjs"), "utf8");
  assert.match(previewSource, /stopVerifiedPortOwners:\s*\["admin", "web", "engine"\]/);
  assert.match(previewSource, /assertStarted\(serviceResults, \["engine", "admin", "web"\]/);
  assert.match(previewSource, /assertStarted\(shellResults, \["shell"\]/);
  assert.match(previewSource, /waitForShellControlDescriptor\(\{/);
  assert.match(previewSource, /timeoutMs:\s*30_000/);
  assert.match(previewSource, /stopComponents\(\[\.\.\.new Set\(startedByThisAttempt\)\]\)/);
});

test("preview rebuild adopts only a verified current-repo Engine port owner", () => {
  const engineDir = COMPONENTS.engine.cwd;
  const verified = verifiedComponentPortOwner("engine", {
    pid: 41000,
    parentPid: 40999,
    executablePath: "D:\\Program Files\\python\\python.exe",
    commandLine: '"D:\\Program Files\\python\\python.exe" -m uvicorn main:app --port 9530',
    parentExecutablePath: path.join(engineDir, ".venv", "Scripts", "python.exe"),
    parentCommandLine: `"${path.join(engineDir, ".venv", "Scripts", "python.exe")}" -m uvicorn main:app --port 9530`,
  });
  const unrelated = verifiedComponentPortOwner("engine", {
    pid: 42000,
    parentPid: 0,
    executablePath: "C:\\Python\\python.exe",
    commandLine: 'python -m http.server 9530',
  });

  assert.deepEqual(verified, {
    ownerPid: 41000,
    killPid: 40999,
    matchedBy: "verified_parent_runtime",
  });
  assert.equal(unrelated, null);
  assert.equal(verifiedComponentPortOwner("admin", { pid: 41000 }), null);
});

test("shutdown can reconcile only verified current-repo Admin and Web port owners", () => {
  const adminDir = path.join(repoRoot, "apps", "v8-agent-os-admin");
  const verifiedAdmin = verifiedComponentPortOwner("admin", {
    pid: 43001,
    parentPid: 43000,
    executablePath: "D:\\Program Files\\node.exe",
    commandLine: `"D:\\Program Files\\node.exe" ${path.join(adminDir, ".next", "standalone", "server.js")}`,
    parentExecutablePath: "D:\\Program Files\\node.exe",
    parentCommandLine: 'node scripts/run-next-with-managed-auth.mjs --app admin --mode start --port 9528',
  });
  const unrelated = verifiedComponentPortOwner("web", {
    pid: 44001,
    parentPid: 44000,
    executablePath: "D:\\Program Files\\node.exe",
    commandLine: 'node C:\\other-app\\server.js --port 9527',
    parentExecutablePath: "D:\\Program Files\\node.exe",
    parentCommandLine: 'node scripts/run-next-with-managed-auth.mjs --app unrelated --mode start --port 9527',
  });

  assert.deepEqual(verifiedAdmin, {
    ownerPid: 43001,
    killPid: 43000,
    matchedBy: "verified_parent_runtime",
  });
  assert.equal(unrelated, null);
});

test("preview rebuild lease is atomic, bounded, and removable only by its owner", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-shell-restart-lease-"));
  const filePath = path.join(root, "shell-restart.json");
  try {
    const lease = createShellRestartLease({ filePath, now: 1_000, ttlMs: 30_000 });
    const persisted = JSON.parse(fs.readFileSync(filePath, "utf8"));
    assert.equal(persisted.id, lease.id);
    assert.equal(persisted.reason, "preview_rebuild");
    assert.equal(persisted.expiresAt, 31_000);
    assert.equal(removeOwnedShellRestartLease({ ...lease, id: "not-owner" }), false);
    assert.equal(fs.existsSync(filePath), true);
    assert.equal(removeOwnedShellRestartLease(lease), true);
    assert.equal(fs.existsSync(filePath), false);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("Windows root wrappers reach the CLI help without entering the app directory", { skip: process.platform !== "win32" }, () => {
  const cmdPath = path.join(repoRoot, "v8os.cmd");
  const ps1Path = path.join(repoRoot, "v8os.ps1");

  const cmd = runWindowsCommand("powershell", [
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-Command",
    `& ${powershellLiteral(cmdPath)} --help`,
  ]);
  assert.equal(cmd.status, 0, cmd.stderr || cmd.stdout);
  assert.match(cmd.stdout, /v8os start/);
  assert.match(cmd.stdout, /v8os config phone/);

  const ps1 = runWindowsCommand("powershell", [
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    ps1Path,
    "--help",
  ]);
  assert.equal(ps1.status, 0, ps1.stderr || ps1.stdout);
  assert.match(ps1.stdout, /v8os start/);
  assert.match(ps1.stdout, /v8os config phone/);
});

test("Windows PATH helper defaults to a non-mutating dry run", { skip: process.platform !== "win32" }, () => {
  const helperPath = path.join(repoRoot, "scripts", "install-v8os-path.ps1");
  const result = runWindowsCommand("powershell", [
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    helperPath,
  ]);
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.match(result.stdout, /V8OS PATH helper|already on the user PATH/);
  if (!/already on the user PATH/.test(result.stdout)) {
    assert.match(result.stdout, /Dry run only/);
    assert.match(result.stdout, /-Apply/);
  }
});

test("doctor fallback returns local checks without requiring engine", async () => {
  const result = await runDoctor({ preferEngine: false });
  assert.equal(result.source, "local_fallback");
  assert.ok(result.summary.total >= 4);
  assert.ok(result.checks.some((check) => check.id === "config.json"));
});

test("mcp install payload builds stdio config without inline secrets", () => {
  const payload = buildMcpInstallPayload([
    "sqlite",
    "--type",
    "stdio",
    "--command",
    "npx",
    "--arg",
    "-y",
    "--arg",
    "@modelcontextprotocol/server-sqlite",
  ]);
  assert.deepEqual(payload, {
    mcpServers: {
      sqlite: {
        type: "stdio",
        command: "npx",
        args: ["-y", "@modelcontextprotocol/server-sqlite"],
      },
    },
  });
});

test("mcp install payload builds http config without inline secrets", () => {
  const payload = buildMcpInstallPayload([
    "remote",
    "--type",
    "http",
    "--url",
    "http://127.0.0.1:3000/mcp",
  ]);
  assert.equal(payload.mcpServers.remote.type, "http");
  assert.equal(payload.mcpServers.remote.url, "http://127.0.0.1:3000/mcp");
  assert.equal(payload.mcpServers.remote.headers, undefined);
});

test("mcp install payload rejects secrets in process arguments", () => {
  assert.throws(
    () => buildMcpInstallPayload(["remote", "--type", "http", "--url", "https://example.test/mcp", "--header", "Authorization=secret"]),
    /secure action card/,
  );
  assert.throws(
    () => buildMcpInstallPayload(["remote", "--type", "stdio", "--command", "npx", "--arg", "--token=secret"]),
    /secure action card/,
  );
});

test("model role extractor accepts registry payload shape", () => {
  assert.deepEqual(extractModelRoles({ payload: { data: { roles: { default: "provider:model" } } } }), {
    default: "provider:model",
  });
});

test("chat payload preserves session, workspace, project, and spec mode", () => {
  const payload = buildChatSubmitPayload({
    sessionId: "session-a",
    message: "你好",
    workspacePath: "E:/Projects/demo",
    workspaceId: "demo-workspace",
    projectId: "demo",
    specMode: true,
    safetyApprovalMode: "minimal",
  });
  assert.equal(payload.session_id, "session-a");
  assert.equal(payload.conversationId, "session-a");
  assert.equal(payload.messages[0].content, "你好");
  assert.equal(payload.data.workspacePath, "E:/Projects/demo");
  assert.equal(payload.data.workspaceId, "demo-workspace");
  assert.equal(payload.data.projectId, "demo");
  assert.equal(payload.data.specMode, true);
  assert.equal(payload.data.safetyApprovalMode, "minimal");
});

test("explicit chat workspace paths never inherit stale workspace identities", () => {
  const storedBinding = {
    path: "C:\\Users\\OldMachine\\.v8-agent-os\\workspace",
    workspaceId: "old-workspace",
    projectId: "old-project",
  };
  assert.deepEqual(resolveChatWorkspaceSelection({
    requestedWorkspacePath: "C:\\current\\workspace",
    storedBinding,
  }), {
    workspacePath: "C:\\current\\workspace",
    workspaceId: "",
    projectId: "",
  });
  assert.deepEqual(resolveChatWorkspaceSelection({ storedBinding }), {
    workspacePath: storedBinding.path,
    workspaceId: storedBinding.workspaceId,
    projectId: storedBinding.projectId,
  });
  assert.deepEqual(resolveChatWorkspaceSelection({
    requestedWorkspaceId: "current-workspace",
    storedBinding,
  }), {
    workspacePath: "",
    workspaceId: "current-workspace",
    projectId: "",
  });
});

test("chat safety approval mode defaults to reduced for local trusted clients", () => {
  assert.equal(normalizeSafetyApprovalMode("manual"), "manual");
  assert.equal(normalizeSafetyApprovalMode("reduced"), "reduced");
  assert.equal(normalizeSafetyApprovalMode("minimal"), "minimal");
  assert.equal(normalizeSafetyApprovalMode("unknown"), "reduced");
  assert.equal(buildChatSubmitPayload({
    sessionId: "session-a",
    message: "你好",
  }).data.safetyApprovalMode, "reduced");
});

test("client API errors surface workspace trust guidance", () => {
  assert.throws(
    () => requireOk({
      ok: false,
      status: 400,
      data: { detail: { error: "workspace_side_effect_blocked", summary: "先选择并信任项目工作区" } },
    }, "提交消息"),
    /v8os workspace create <path> --select/,
  );
});

test("chat text extractor handles string and rich parts", () => {
  assert.equal(extractMessageText({ content: "plain" }), "plain");
  assert.equal(extractMessageText({ content: [{ text: "a" }, { content: "b" }] }), "a\nb");
  assert.equal(extractMessageText({ parts: ["x", { text: "y" }] }), "x\ny");
});

test("chat terminal failures stop waiting even when the assistant has no text", () => {
  assert.deepEqual(assistantTerminalFailure({
    role: "assistant",
    state: "failed",
    content: "",
    metadata: { terminalReason: "auth_error" },
  }), {
    state: "failed",
    reason: "auth_error",
    message: "主理人运行已失败（auth_error）。",
  });
  assert.equal(assistantTerminalFailure({ role: "assistant", state: "streaming" }), null);
  assert.equal(assistantTerminalFailure({ role: "user", state: "failed" }), null);
});

test("inbox pending filter excludes closed and empty items", () => {
  assert.deepEqual(filterPendingInboxItems([
    { id: "a", status: "pending" },
    { id: "b", status: "resolved" },
    { id: "", status: "pending" },
    { id: "c", status: "waiting" },
  ]).map((item) => item.id), ["a", "c"]);
});

test("workspace helpers resolve and inspect local directories without touching config", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-cli-workspace-"));
  fs.mkdirSync(path.join(dir, ".agents", "rules"), { recursive: true });
  fs.writeFileSync(path.join(dir, ".agents", "rules", "AGENTS.md"), "# Rules\n", "utf8");
  assert.equal(resolveWorkspacePath(dir), path.resolve(dir));
  assert.equal(currentWorkspacePath({ workspace: { agent_workspace_path: dir } }), path.resolve(dir));
  assert.deepEqual(currentWorkspaceBinding({
    workspace: {
      agent_workspace_path: dir,
      projectId: "project-demo",
      workspaceId: "workspace-demo",
      workspaceTrustState: "trusted",
      workspaceTrustSource: "cli_user_confirmed",
    },
  }), {
    path: path.resolve(dir),
    projectId: "project-demo",
    workspaceId: "workspace-demo",
    workspaceTrustState: "trusted",
    workspaceTrustSource: "cli_user_confirmed",
  });
  const report = inspectWorkspace(dir);
  assert.equal(report.path, path.resolve(dir));
  assert.ok(report.checks.some((check) => check.id === "agents_rules" && check.status === "ok"));
});
