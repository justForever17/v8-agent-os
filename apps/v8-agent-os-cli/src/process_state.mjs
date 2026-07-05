import { PROCESS_STATE_PATH, REPO_ROOT } from "./paths.mjs";
import { readJsonFile, writeJsonFile } from "./json_file.mjs";

export function readProcessState() {
  const payload = readJsonFile(PROCESS_STATE_PATH, null);
  if (!payload || typeof payload !== "object") {
    return { version: 1, repoRoot: REPO_ROOT, processes: {} };
  }
  return {
    version: 1,
    repoRoot: payload.repoRoot || REPO_ROOT,
    processes: payload.processes && typeof payload.processes === "object" ? payload.processes : {},
  };
}

export function writeProcessState(state) {
  writeJsonFile(PROCESS_STATE_PATH, {
    version: 1,
    repoRoot: REPO_ROOT,
    updatedAt: new Date().toISOString(),
    processes: state.processes || {},
  });
}

export function isPidAlive(pid) {
  const numeric = Number(pid);
  if (!Number.isInteger(numeric) || numeric <= 0) return false;
  try {
    process.kill(numeric, 0);
    return true;
  } catch {
    return false;
  }
}
