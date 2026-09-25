// Core Base lifecycle entry shared by terminal, desktop and installed bundles.
// Process records, port leases and OS identity checks stay in process_manager.
import path from "node:path";
import { ALL_COMPONENTS } from "./components.mjs";
import desktopPetPlatform from "./desktop_pet_platform.cjs";
import {
  getManagedComponentProcessRecordIdentity,
  requestPackagedShellShutdown,
  startComponentsWithRuntimePorts,
  statusComponents,
  stopComponents,
} from "./process_manager.mjs";
import { compareAndSwapProcessRecord, processRecordMatchesIdentity } from "./process_state.mjs";
import { STATE_ROOT } from "./paths.mjs";
import { engineResponse, engineTargetOrigin } from "./engine_client.mjs";
import { fetchJson } from "./http.mjs";
import { readJsonFile } from "./json_file.mjs";
import { createServerServiceManager, discoverServerServiceReceipt } from "./server_service.mjs";
import { isPortOpen } from "./ports.mjs";
export { engineJson, engineTargetOrigin } from "./engine_client.mjs";
export { initializeServerCredentials, defaultCredentialKeyPath } from "./credentials_commands.mjs";
export { discoverServerServiceReceipt } from "./server_service.mjs";
export { resolveProductOrigin } from "./product_origin.mjs";

export const { desktopPetAvailability } = desktopPetPlatform;

function managedService(options) {
  // Foreground surfaces own the Engine lifetime.  An installed systemd unit is
  // only consulted when the caller explicitly opts into the persistent
  // service plane (or supplies a test/embedded manager).  This prevents a
  // desktop/TUI launch from silently starting a daemon that survives exit.
  // A foreground surface must never attach to a service manager implicitly.
  // `useManagedService: true` is reserved for an explicit control-plane call
  // such as `v8os service ...`; passing a test manager alone is not an opt-in.
  if (options.lifecycle === "desktop" && options.useManagedService !== true) return null;
  if (options.useManagedService === false) return null;
  if (options.serverService) return options.serverService;
  const receipt = discoverServerServiceReceipt();
  return receipt ? { receipt, manager: createServerServiceManager() } : null;
}

function servicePort(service) {
  const port = Number(service.receipt.port);
  const target = new URL(engineTargetOrigin());
  if (target.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(target.hostname)
    || Number(target.port || 80) !== port) throw new Error("engine_service_port_mismatch");
  return port;
}

export async function statusCoreComponents(componentIds = ALL_COMPONENTS, options = {}) {
  const service = componentIds.includes("engine") ? managedService(options) : null;
  const statuses = await statusComponents(service ? componentIds.filter(id => id !== "engine") : componentIds);
  if (service) {
    const port = servicePort(service);
    const state = await service.manager.perform("status");
    const running = state.status === "active" && Number(state.mainPid) > 0;
    statuses.push({ id: "engine", label: "Engine", port, managed: true, pid: state.mainPid || null,
      pidAlive: running, portOpen: await isPortOpen(port), state: running ? "managed_running" : state.status,
      lifecycle: "daemon", manager: "systemd", recordIdentity: null, journal: state.journal });
    statuses.sort((a, b) => componentIds.indexOf(a.id) - componentIds.indexOf(b.id));
  }
  if (!options.expectedIdentities) return statuses;
  return statuses.map(item => ({ ...item,
    ownership: options.expectedIdentities[item.id]
      && processRecordMatchesIdentity(item.recordIdentity, options.expectedIdentities[item.id])
      ? "owned" : "detached",
  }));
}

export async function startCoreComponents(componentIds, options = {}) {
  return (await startCoreComponentsWithRuntimePorts(componentIds, options)).results;
}

export async function startCoreComponentsWithRuntimePorts(componentIds, options = {}) {
  const lifecycle = options.lifecycle || "daemon";
  if (!["daemon", "desktop"].includes(lifecycle)) throw new Error("Invalid Core lifecycle");
  const service = componentIds.includes("engine") ? managedService(options) : null;
  let serviceResult;
  if (service) {
    const port = servicePort(service);
    const before = await service.manager.perform("status");
    const result = await service.manager.perform("start");
    serviceResult = { id: "engine", status: before.status === "active" ? "already_running" : "started",
      pid: result.mainPid, port, lifecycle: "daemon", manager: "systemd", recordIdentity: null };
  }
  const started = await startComponentsWithRuntimePorts(service ? componentIds.filter(id => id !== "engine") : componentIds,
    { ...options, mode: options.mode || "start", lifecycle });
  if (serviceResult) {
    started.results.push(serviceResult);
    started.results.sort((a, b) => componentIds.indexOf(a.id) - componentIds.indexOf(b.id));
  }
  return started;
}

export async function stopCoreComponents(componentIds = ALL_COMPONENTS, options = {}) {
  const service = componentIds.includes("engine") ? managedService(options) : null;
  if (service) servicePort(service);
  // Preserve the whole-product Shell handshake before handing Engine's stop
  // to systemd; removing Engine from the component list must not bypass it.
  const shutdown = service && !options.expectedIdentities && options.skipManagedShellShutdown !== true
    ? await requestPackagedShellShutdown(componentIds, options.managedShellShutdown || {}) : null;
  const stopOptions = shutdown?.attempted
    ? { ...options, skipManagedShellShutdown: true } : options;
  const results = await stopComponents(service ? componentIds.filter(id => id !== "engine") : componentIds, stopOptions);
  if (service) {
    const result = options.expectedIdentities ? { status: "not_owned", reason: "daemon_retained" }
      : await service.manager.perform("stop");
    results.push({ ...result, id: "engine", lifecycle: "daemon", manager: "systemd" });
    results.sort((a, b) => componentIds.indexOf(a.id) - componentIds.indexOf(b.id));
  }
  return results;
}

// A desktop-created process may be transferred across Shell surface restarts.
// Merely connecting to a daemon never transfers its ownership to the desktop.
export function desktopOwnedIdentities(results, previous = {}) {
  const next = { ...previous };
  for (const item of results) {
    if (["started", "already_running"].includes(item.status)
      && item.lifecycle === "desktop" && item.recordIdentity?.launchId) {
      next[item.id] = item.recordIdentity;
    }
  }
  return next;
}

export function getShellProcessRecordIdentity() {
  return getManagedComponentProcessRecordIdentity("shell");
}

export async function removeShellProcessRecord(expectedIdentity) {
  if (!expectedIdentity) return false;
  // A stopper can already own shell.lease. Exact CAS avoids re-entering it,
  // and prevents an old Shell from deleting a replacement process record.
  return (await compareAndSwapProcessRecord("shell", expectedIdentity, null)).applied;
}

export async function waitForCoreReadiness({ timeoutMs = 180_000, signal, onProgress } = {}) {
  const deadline = Date.now() + timeoutMs;
  const origin = engineTargetOrigin();
  let lastReason = "engine_starting";
  while (Date.now() < deadline) {
    signal?.throwIfAborted();
    if (engineTargetOrigin() !== origin) throw new Error("engine_target_changed");
    try {
      const ready = await fetchJson(`${origin}/readyz`, { timeoutMs: Math.min(2500, deadline - Date.now()), signal });
      if (ready.ok && ready.data?.service === "v8-agent-os-engine" && ready.data?.ready === true) {
        const identity = await engineResponse("/v1/client-identity/instance", { expectedOrigin: origin, signal });
        const local = readJsonFile(path.join(STATE_ROOT, "runtime", "instance.json"), {});
        const expectedId = String(local.instanceId || "");
        const actualId = String(identity.data?.instanceId || "");
        if (!identity.ok || !expectedId || actualId !== expectedId) throw new Error("engine_instance_mismatch");
        return { ready: true, origin, instanceId: actualId };
      }
      lastReason = ready.data?.service === "v8-agent-os-engine" ? "engine_not_ready" : "engine_unavailable";
    } catch (error) {
      if (error?.message === "engine_instance_mismatch" || signal?.aborted) throw error;
      lastReason = "engine_unavailable";
    }
    onProgress?.({ ready: false, reason: lastReason });
    await new Promise(resolve => setTimeout(resolve, Math.min(250, Math.max(0, deadline - Date.now()))));
  }
  throw Object.assign(new Error(`Engine did not become ready: ${lastReason}`), { code: "V8OS_ENGINE_READINESS_TIMEOUT" });
}
