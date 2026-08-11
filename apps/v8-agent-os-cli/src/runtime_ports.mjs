import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { DEFAULT_PORTS, RUNTIME_PORTS_PATH } from "./paths.mjs";
import { isPortOpen } from "./ports.mjs";
import { withRuntimePortsLease } from "./process_state.mjs";

export const WEB_FALLBACK_PORT_START = 19_527;
export const WEB_FALLBACK_PORT_END = 19_546;

function validPort(value) {
  const port = Number(value);
  return Number.isInteger(port) && port > 0 && port <= 65_535 ? port : null;
}

function validWebPort(value) {
  const port = validPort(value);
  return port === DEFAULT_PORTS.web
    || (port >= WEB_FALLBACK_PORT_START && port <= WEB_FALLBACK_PORT_END)
    ? port
    : null;
}

function normalizedProfile(payload) {
  if (!payload || payload.version !== 1 || payload.policy !== "web-fallback-v1") return null;
  const web = validWebPort(payload.ports?.web);
  if (!web
    || Number(payload.ports?.engine) !== DEFAULT_PORTS.engine
    || Number(payload.ports?.admin) !== DEFAULT_PORTS.admin) return null;
  return {
    version: 1,
    policy: "web-fallback-v1",
    ports: {
      engine: DEFAULT_PORTS.engine,
      admin: DEFAULT_PORTS.admin,
      web,
    },
    selectedAt: typeof payload.selectedAt === "string" ? payload.selectedAt : null,
    reservationExpiresAt: typeof payload.reservationExpiresAt === "string" ? payload.reservationExpiresAt : null,
    reason: typeof payload.reason === "string" ? payload.reason : null,
  };
}

export function readRuntimePortProfile(options = {}) {
  const profilePath = options.profilePath || RUNTIME_PORTS_PATH;
  try {
    return normalizedProfile(JSON.parse(fs.readFileSync(profilePath, "utf8")));
  } catch {
    return null;
  }
}

export function readRuntimePorts(options = {}) {
  return readRuntimePortProfile(options)?.ports || {
    engine: DEFAULT_PORTS.engine,
    admin: DEFAULT_PORTS.admin,
    web: DEFAULT_PORTS.web,
  };
}

function writeRuntimePortProfile(profile, profilePath) {
  fs.mkdirSync(path.dirname(profilePath), { recursive: true });
  const temporaryPath = `${profilePath}.${process.pid}.${crypto.randomBytes(6).toString("hex")}.tmp`;
  try {
    fs.writeFileSync(temporaryPath, `${JSON.stringify(profile, null, 2)}\n`, {
      encoding: "utf8",
      mode: 0o600,
    });
    try {
      fs.renameSync(temporaryPath, profilePath);
    } catch {
      fs.rmSync(profilePath, { force: true });
      fs.renameSync(temporaryPath, profilePath);
    }
  } finally {
    fs.rmSync(temporaryPath, { force: true });
  }
}

export async function resolveRuntimePorts(options = {}) {
  const profilePath = options.profilePath || RUNTIME_PORTS_PATH;
  const probePort = options.probePort || ((port) => isPortOpen(port));
  const lease = options.withLease || withRuntimePortsLease;
  return lease(async () => {
    const existing = readRuntimePortProfile({ profilePath });
    const managedWebPort = validWebPort(options.verifiedManagedWebPort);
    let selectedWebPort = managedWebPort;
    let reason = managedWebPort ? "managed_process" : null;

    if (!selectedWebPort) {
      const candidates = [
        existing?.ports.web,
        DEFAULT_PORTS.web,
        ...Array.from(
          { length: WEB_FALLBACK_PORT_END - WEB_FALLBACK_PORT_START + 1 },
          (_unused, index) => WEB_FALLBACK_PORT_START + index,
        ),
      ].filter((port, index, items) => validWebPort(port) && items.indexOf(port) === index);
      for (const candidate of candidates) {
        if (!await probePort(candidate)) {
          selectedWebPort = candidate;
          reason = candidate === existing?.ports.web && candidate !== DEFAULT_PORTS.web
            ? "persisted_fallback_available"
            : candidate === DEFAULT_PORTS.web
              ? "default_available"
              : "default_conflict";
          break;
        }
      }
    }

    if (!selectedWebPort) {
      const error = new Error("No governed Web port is available for V8OS.");
      error.code = "V8OS_WEB_PORT_RANGE_EXHAUSTED";
      throw error;
    }

    const profile = {
      version: 1,
      policy: "web-fallback-v1",
      ports: {
        engine: DEFAULT_PORTS.engine,
        admin: DEFAULT_PORTS.admin,
        web: selectedWebPort,
      },
      selectedAt: new Date().toISOString(),
      reservationExpiresAt: null,
      reason,
    };
    writeRuntimePortProfile(profile, profilePath);
    return profile;
  });
}
