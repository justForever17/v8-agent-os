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

export class SupervisorRuntimeModeValidationError extends Error {
    readonly code = "invalid_supervisor_runtime_mode";
    readonly field = "supervisorRuntimeMode";
    readonly allowedValues = [...ADMIN_SUPERVISOR_RUNTIME_MODES];

    constructor() {
        super("supervisorRuntimeMode is invalid");
        this.name = "SupervisorRuntimeModeValidationError";
    }
}

export function serializeSupervisorRuntimeModeValidationError(
    error: SupervisorRuntimeModeValidationError,
) {
    return {
        error: error.message,
        errorCode: error.code,
        detail: {
            field: error.field,
            allowedValues: [...error.allowedValues],
        },
    };
}

export function isSupervisorRuntimeMode(value: unknown): value is SupervisorRuntimeMode {
    return typeof value === "string" && ADMIN_SUPERVISOR_RUNTIME_MODE_SET.has(value);
}
