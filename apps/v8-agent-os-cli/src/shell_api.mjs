import { ALL_COMPONENTS } from "./components.mjs";
import {
  getManagedComponentProcessRecordIdentity,
  startComponents,
  startComponentsWithRuntimePorts,
  statusComponents,
  stopComponents,
} from "./process_manager.mjs";
import { compareAndSwapProcessRecord } from "./process_state.mjs";

export async function shellStatus(componentIds = ALL_COMPONENTS) {
  return statusComponents(componentIds);
}

export async function shellStart(componentIds, options = {}) {
  return startComponents(componentIds, {
    mode: options.mode || "start",
  });
}

export async function shellStartWithRuntimePorts(componentIds, options = {}) {
  return startComponentsWithRuntimePorts(componentIds, {
    mode: options.mode || "start",
  });
}

export async function shellStop(componentIds = ALL_COMPONENTS, options = {}) {
  return stopComponents(componentIds, options);
}

export function getShellProcessRecordIdentity() {
  return getManagedComponentProcessRecordIdentity("shell");
}

export async function removeShellProcessRecord(expectedIdentity) {
  if (!expectedIdentity) return false;
  // Shell record removal can race an external lifecycle action that already
  // owns shell.lease. The exact pid+launchId CAS avoids re-entering that lease
  // while still preventing a stale Shell from deleting a replacement record.
  return (await compareAndSwapProcessRecord("shell", expectedIdentity, null)).applied;
}
