import type { SessionRuntimeEventTarget, SessionRuntimeId } from "./contract.js";

export type RuntimeProjectionSurface =
  | "admin"
  | "phone"
  | "web";

export type RuntimeProjectionMatrixEntry = {
  runtimeId: SessionRuntimeId;
  eventPrefix: string;
  surfaces: RuntimeProjectionSurface[];
  targets: SessionRuntimeEventTarget[];
  governanceOnly?: boolean;
  notes?: string;
};

export const RUNTIME_PROJECTION_MATRIX: RuntimeProjectionMatrixEntry[] = [
  {
    runtimeId: "chat",
    eventPrefix: "chat.",
    surfaces: ["admin", "phone", "web"],
    targets: ["message", "runtime_card", "hud"],
  },
  {
    runtimeId: "engineering",
    eventPrefix: "engineering.",
    surfaces: ["admin", "phone", "web"],
    targets: ["runtime_card", "runtime_timeline", "hud"],
  },
  {
    runtimeId: "creative_media",
    eventPrefix: "creative_media.",
    surfaces: ["admin", "phone", "web"],
    targets: ["runtime_card", "artifact", "process"],
  },
  {
    runtimeId: "computer_use",
    eventPrefix: "computer_use.",
    surfaces: ["admin", "phone", "web"],
    targets: ["message", "runtime_card", "terminal", "process"],
  },
  {
    runtimeId: "network_supervisor",
    eventPrefix: "network_supervisor.",
    surfaces: ["admin", "phone", "web"],
    targets: ["runtime_card", "process"],
  },
  {
    runtimeId: "automation",
    eventPrefix: "automation.",
    surfaces: ["admin", "phone", "web"],
    targets: ["runtime_card", "hud", "process"],
  },
  {
    runtimeId: "memory",
    eventPrefix: "memory.",
    surfaces: ["admin"],
    targets: ["runtime_card"],
    governanceOnly: true,
    notes: "Memory log/map details are supervisor/governance scoped and are not auto-projected to subagent/runtime cards.",
  },
  {
    runtimeId: "chat",
    eventPrefix: "context.",
    surfaces: ["admin", "phone", "web"],
    targets: ["runtime_card", "hud", "context"],
  },
  {
    runtimeId: "desktop_live",
    eventPrefix: "desktop_live.",
    surfaces: ["admin"],
    targets: [],
    governanceOnly: true,
    notes: "Desktop live is a manual remote-view surface, not an agent orchestration runtime.",
  },
];

export function findRuntimeProjectionMatrixEntry(runtimeId: SessionRuntimeId, eventPrefix?: string | null) {
  const prefix = String(eventPrefix || "").trim().toLowerCase();
  return (
    RUNTIME_PROJECTION_MATRIX.find((entry) => {
      if (entry.runtimeId !== runtimeId) return false;
      return !prefix || prefix.startsWith(entry.eventPrefix);
    }) || null
  );
}
