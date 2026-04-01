export interface CommandSessionPayload {
    commandId: string;
    sessionId: string;
    mode?: string;
    interactive?: boolean;
    tty?: string;
    runId?: string | null;
    reason?: string | null;
    initialOutput?: string | null;
    source: "json" | "legacy";
}

function extractLegacyCommandId(text: string): string | null {
    const match = text.match(/ID:\s*([a-z0-9-]+)/i);
    return match ? match[1] : null;
}

export function extractCommandSessionPayload(result: unknown): CommandSessionPayload | null {
    const payload =
        typeof result === "string"
            ? (() => {
                  const trimmed = result.trim();
                  if (!trimmed) return null;
                  try {
                      return JSON.parse(trimmed) as Record<string, unknown>;
                  } catch {
                      return null;
                  }
              })()
            : (result && typeof result === "object" ? (result as Record<string, unknown>) : null);

    if (payload) {
        const mode = typeof payload.mode === "string" ? payload.mode.trim().toLowerCase() : "";
        const commandId = typeof payload.commandId === "string" && payload.commandId.trim()
            ? payload.commandId.trim()
            : (typeof payload.sessionId === "string" ? payload.sessionId.trim() : "");
        if (mode === "session" && commandId) {
            return {
                commandId,
                sessionId: typeof payload.sessionId === "string" && payload.sessionId.trim() ? payload.sessionId.trim() : commandId,
                mode,
                interactive: typeof payload.interactive === "boolean" ? payload.interactive : undefined,
                tty: typeof payload.tty === "string" ? payload.tty : undefined,
                runId: typeof payload.runId === "string" && payload.runId.trim() ? payload.runId.trim() : null,
                reason: typeof payload.reason === "string" && payload.reason.trim() ? payload.reason.trim() : null,
                initialOutput: typeof payload.initialOutput === "string" && payload.initialOutput.trim() ? payload.initialOutput : null,
                source: "json",
            };
        }
    }

    if (typeof result !== "string") {
        return null;
    }
    const trimmed = result.trim();
    if (!trimmed) {
        return null;
    }

    const legacyId = extractLegacyCommandId(trimmed);
    if (!legacyId) {
        return null;
    }
    return {
        commandId: legacyId,
        sessionId: legacyId,
        source: "legacy",
    };
}

export function isCommandSessionTool(toolName: unknown): boolean {
    const normalized = typeof toolName === "string" ? toolName.trim() : "";
    return normalized === "run_system_command" || normalized === "start_background_command";
}
