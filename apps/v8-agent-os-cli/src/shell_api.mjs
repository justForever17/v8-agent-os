import { ALL_COMPONENTS } from "./components.mjs";
import {
  getManagedComponentProcessRecordIdentity,
  removeManagedComponentProcessRecord,
  startComponents,
  statusComponents,
  stopComponents,
} from "./process_manager.mjs";

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
  return removeManagedComponentProcessRecord("shell", expectedIdentity);
}
