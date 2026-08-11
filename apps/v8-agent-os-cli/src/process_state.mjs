import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { PROCESS_STATE_PATH, REPO_ROOT } from "./paths.mjs";
import { ensureDir, readJsonFile } from "./json_file.mjs";

const PROCESS_LEASE_DIR = path.join(path.dirname(PROCESS_STATE_PATH), "leases");
const STATE_WRITE_LEASE_PATH = path.join(PROCESS_LEASE_DIR, "state-write.lease");
const RUNTIME_PORTS_LEASE_PATH = path.join(PROCESS_LEASE_DIR, "runtime-ports.lease");

function wait(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function leasePathForComponent(componentId) {
  if (!/^[a-z0-9-]+$/.test(String(componentId || ""))) {
    throw new Error(`Invalid V8OS component id: ${componentId}`);
  }
  return path.join(PROCESS_LEASE_DIR, `${componentId}.lease`);
}

function leaseQueuePath(filePath) {
  return `${filePath}.queue`;
}

function leaseEntryPath(queuePath, kind, leaseId) {
  return path.join(queuePath, `${kind}-${leaseId}.json`);
}

async function publishLeaseEntry(queuePath, kind, leaseId, payload) {
  ensureDir(queuePath);
  const temporaryPath = path.join(queuePath, `.tmp-${kind}-${leaseId}-${crypto.randomBytes(4).toString("hex")}`);
  const finalPath = leaseEntryPath(queuePath, kind, leaseId);
  try {
    await fs.promises.writeFile(temporaryPath, `${JSON.stringify(payload)}\n`, { encoding: "utf8", flag: "wx" });
    await fs.promises.rename(temporaryPath, finalPath);
    return finalPath;
  } finally {
    await fs.promises.rm(temporaryPath, { force: true }).catch(() => undefined);
  }
}

async function readLeaseEntries(queuePath) {
  let names;
  try {
    names = await fs.promises.readdir(queuePath);
  } catch (error) {
    if (error?.code === "ENOENT") return [];
    throw error;
  }
  return Promise.all(names
    .filter((name) => /^(?:choosing|ticket)-.+\.json$/.test(name))
    .map(async (name) => {
      const filePath = path.join(queuePath, name);
      let payload = null;
      let stats = null;
      try {
        const [text, currentStats] = await Promise.all([
          fs.promises.readFile(filePath, "utf8"),
          fs.promises.stat(filePath),
        ]);
        stats = currentStats;
        payload = JSON.parse(text);
      } catch {
        try {
          stats = await fs.promises.stat(filePath);
        } catch {}
      }
      return {
        filePath,
        kind: name.startsWith("choosing-") ? "choosing" : "ticket",
        payload,
        stats,
      };
    }));
}

function leaseEntryIsActive(entry, staleAfterMs) {
  const ageMs = entry.stats ? Date.now() - entry.stats.mtimeMs : 0;
  const ownerPid = Number(entry.payload?.ownerPid);
  if (!entry.payload || !Number.isInteger(ownerPid) || ownerPid <= 0) return ageMs < staleAfterMs;
  return isPidAlive(ownerPid) || ageMs < staleAfterMs;
}

async function removeInactiveLeaseEntries(entries, staleAfterMs) {
  await Promise.all(entries.map(async (entry) => {
    if (leaseEntryIsActive(entry, staleAfterMs)) return;
    // Entry names include a UUID and are never reused, so cleanup cannot remove
    // a later owner's lease even when several reclaimers run concurrently.
    await fs.promises.rm(entry.filePath, { force: true }).catch(() => undefined);
  }));
}

async function withFileLease(filePath, callback, options = {}) {
  ensureDir(path.dirname(filePath));
  const queuePath = leaseQueuePath(filePath);
  const leaseId = crypto.randomUUID();
  const timeoutMs = Math.max(100, Number(options.timeoutMs) || 10_000);
  const requestedStaleAfterMs = Number(options.staleAfterMs);
  const staleAfterMs = Number.isFinite(requestedStaleAfterMs) ? Math.max(0, requestedStaleAfterMs) : 1_000;
  const retryMs = Math.max(5, Number(options.retryMs) || 20);
  const deadline = Date.now() + timeoutMs;
  const basePayload = {
    version: 2,
    leaseId,
    ownerPid: process.pid,
    createdAt: new Date().toISOString(),
  };
  let choosingPath = null;
  let ticketPath = null;
  try {
    choosingPath = await publishLeaseEntry(queuePath, "choosing", leaseId, basePayload);
    const initialEntries = await readLeaseEntries(queuePath);
    await removeInactiveLeaseEntries(initialEntries, staleAfterMs);
    const ticketNumber = initialEntries.reduce((maximum, entry) => {
      if (entry.kind !== "ticket" || !leaseEntryIsActive(entry, staleAfterMs)) return maximum;
      const candidate = Number(entry.payload?.ticketNumber);
      return Number.isSafeInteger(candidate) && candidate > maximum ? candidate : maximum;
    }, 0) + 1;
    ticketPath = await publishLeaseEntry(queuePath, "ticket", leaseId, { ...basePayload, ticketNumber });
    await fs.promises.rm(choosingPath, { force: true });
    choosingPath = null;

    while (true) {
      const entries = await readLeaseEntries(queuePath);
      await removeInactiveLeaseEntries(entries, staleAfterMs);
      const activeChoosing = entries.some((entry) => entry.kind === "choosing"
        && entry.payload?.leaseId !== leaseId
        && leaseEntryIsActive(entry, staleAfterMs));
      const activePriorTicket = entries.some((entry) => {
        if (entry.kind !== "ticket" || entry.payload?.leaseId === leaseId || !leaseEntryIsActive(entry, staleAfterMs)) return false;
        const candidateNumber = Number(entry.payload?.ticketNumber);
        if (!Number.isSafeInteger(candidateNumber) || candidateNumber <= 0) return true;
        if (candidateNumber !== ticketNumber) return candidateNumber < ticketNumber;
        return String(entry.payload?.leaseId || "") < leaseId;
      });
      if (!activeChoosing && !activePriorTicket) break;
      if (Date.now() >= deadline) throw new Error(`Timed out waiting for V8OS lease: ${path.basename(filePath)}`);
      await wait(retryMs);
    }
    return await callback({ leaseId, filePath });
  } finally {
    await Promise.all([
      choosingPath ? fs.promises.rm(choosingPath, { force: true }).catch(() => undefined) : undefined,
      ticketPath ? fs.promises.rm(ticketPath, { force: true }).catch(() => undefined) : undefined,
    ]);
    const remaining = await readLeaseEntries(queuePath).catch(() => []);
    await removeInactiveLeaseEntries(remaining, staleAfterMs);
  }
}

export async function withComponentProcessLease(componentId, callback, options = {}) {
  return withFileLease(leasePathForComponent(componentId), callback, options);
}

export async function withRuntimePortsLease(callback, options = {}) {
  return withFileLease(RUNTIME_PORTS_LEASE_PATH, callback, { timeoutMs: 30_000, ...options });
}

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

function writeProcessState(state) {
  const payload = {
    version: 1,
    repoRoot: REPO_ROOT,
    updatedAt: new Date().toISOString(),
    processes: state.processes || {},
  };
  ensureDir(path.dirname(PROCESS_STATE_PATH));
  const temporaryPath = `${PROCESS_STATE_PATH}.${process.pid}.${crypto.randomBytes(6).toString("hex")}.tmp`;
  try {
    fs.writeFileSync(temporaryPath, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
    fs.renameSync(temporaryPath, PROCESS_STATE_PATH);
  } finally {
    fs.rmSync(temporaryPath, { force: true });
  }
}

export function processRecordIdentity(record) {
  const pid = Number(record?.pid);
  if (!Number.isInteger(pid) || pid <= 0) return null;
  return {
    pid,
    launchId: typeof record?.launchId === "string" && record.launchId ? record.launchId : null,
  };
}

export function processRecordMatchesIdentity(record, expectedIdentity) {
  const current = processRecordIdentity(record);
  if (!expectedIdentity) return current === null;
  return current?.pid === Number(expectedIdentity.pid)
    && current?.launchId === (expectedIdentity.launchId || null);
}

export async function compareAndSwapProcessRecord(componentId, expectedIdentity, nextRecord) {
  leasePathForComponent(componentId);
  return withFileLease(STATE_WRITE_LEASE_PATH, async () => {
    const state = readProcessState();
    const current = state.processes[componentId] || null;
    if (!processRecordMatchesIdentity(current, expectedIdentity)) {
      return { applied: false, current, currentIdentity: processRecordIdentity(current) };
    }
    if (nextRecord) state.processes[componentId] = nextRecord;
    else delete state.processes[componentId];
    writeProcessState(state);
    return {
      applied: true,
      current,
      currentIdentity: processRecordIdentity(current),
      nextIdentity: processRecordIdentity(nextRecord),
    };
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
