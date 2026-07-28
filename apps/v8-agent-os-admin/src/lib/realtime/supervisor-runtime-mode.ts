import type { SupervisorRuntimeMode } from "@v8/session-realtime";

export const ADMIN_SUPERVISOR_RUNTIME_MODES = [
    "auto",
    "engineering",
    "research",
    "creative_media",
    "computer_use",
    "rpa",
] as const satisfies readonly SupervisorRuntimeMode[];

const ADMIN_SUPERVISOR_RUNTIME_MODE_SET = new Set<string>(ADMIN_SUPERVISOR_RUNTIME_MODES);

export function isSupervisorRuntimeMode(value: unknown): value is SupervisorRuntimeMode {
    return typeof value === "string" && ADMIN_SUPERVISOR_RUNTIME_MODE_SET.has(value);
}
