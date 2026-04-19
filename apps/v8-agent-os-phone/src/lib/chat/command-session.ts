function extractMode(candidate: unknown): string {
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) {
        return "";
    }
    const record = candidate as Record<string, unknown>;
    return String(record.mode || "").trim().toLowerCase();
}

export function isCommandSessionTool(toolName: unknown): boolean {
    const normalized = typeof toolName === "string" ? toolName.trim() : "";
    return normalized === "run_system_command"
        || normalized === "start_background_command"
        || normalized === "command_session_broker";
}

export function isCommandSessionStartTool(toolName: unknown, args?: unknown, result?: unknown): boolean {
    const normalized = typeof toolName === "string" ? toolName.trim() : "";
    if (normalized === "command_session_broker") {
        const mode = extractMode(args) || extractMode(result);
        return mode === "start";
    }
    return normalized === "run_system_command" || normalized === "start_background_command";
}

export function isBackgroundCommandTraceTool(toolName: unknown, args?: unknown, result?: unknown): boolean {
    const normalized = typeof toolName === "string" ? toolName.trim() : "";
    if (normalized === "command_session_broker") {
        const mode = extractMode(args) || extractMode(result);
        return mode === "observe" || mode === "input" || mode === "terminate";
    }
    return normalized === "read_background_output"
        || normalized === "send_background_input"
        || normalized === "terminate_background_command";
}
