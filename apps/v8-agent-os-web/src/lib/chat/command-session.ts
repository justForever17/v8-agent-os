export function isCommandSessionTool(toolName: unknown): boolean {
    const normalized = typeof toolName === "string" ? toolName.trim() : "";
    return normalized === "run_system_command" || normalized === "start_background_command";
}
