import { ALL_COMPONENTS } from "./components.mjs";
import {
  getManagedComponentProcessRecordIdentity,
  startComponents,
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

export async function shellStop(componentIds = ALL_COMPONENTS, options = {}) {
  return stopComponents(componentIds, options);
}

export function getShellProcessRecordIdentity() {
  return getManagedComponentProcessRecordIdentity("shell");
}

export async function removeShellProcessRecord(expectedIdentity) {
  if (!expectedIdentity) return false;
  // An external `stop --only shell` owns shell.lease while Electron performs
  // its governed shutdown. Re-entering that lease here would deadlock the
  // stopper and the Shell. The exact pid+launchId CAS still prevents a stale
  // Shell from deleting a replacement process record.
  return (await compareAndSwapProcessRecord("shell", expectedIdentity, null)).applied;
}
