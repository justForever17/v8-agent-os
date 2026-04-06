export function isCommandSessionTool(toolName: unknown): boolean {
    const normalized = typeof toolName === "string" ? toolName.trim() : "";
    return normalized === "run_system_command" || normalized === "start_background_command";
}

export function isBackgroundCommandTraceTool(toolName: unknown): boolean {
    const normalized = typeof toolName === "string" ? toolName.trim() : "";
    return normalized === "read_background_output"
        || normalized === "send_background_input"
        || normalized === "terminate_background_command";
}
