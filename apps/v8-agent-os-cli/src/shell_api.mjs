import { ALL_COMPONENTS } from "./components.mjs";
import { startComponents, statusComponents, stopComponents } from "./process_manager.mjs";
import { readProcessState, writeProcessState } from "./process_state.mjs";

export async function shellStatus(componentIds = ALL_COMPONENTS) {
  return statusComponents(componentIds);
}

export async function shellStart(componentIds, options = {}) {
  return startComponents(componentIds, {
    mode: options.mode || "start",
  });
}

export function shellStop(componentIds = ALL_COMPONENTS, options = {}) {
  return stopComponents(componentIds, options);
}

export function removeShellProcessRecord() {
  const state = readProcessState();
  if (state.processes?.shell) {
    delete state.processes.shell;
    writeProcessState(state);
  }
}
