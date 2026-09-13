export type TerminalOutputCursor = { cursor: number; generation: string; output: string; reset: boolean };
export function initialTerminalOutputCursor(): TerminalOutputCursor { return { cursor: 0, generation: "", output: "", reset: false }; }
export function mergeTerminalOutput(current: TerminalOutputCursor, requestedCursor: number, payload: {
    output?: string; outputCursor?: number; outputGeneration?: string | number; outputReset?: boolean;
}): TerminalOutputCursor {
    const generation = String(payload.outputGeneration ?? "");
    if ((payload.outputReset || (current.generation && current.generation !== generation)) && requestedCursor !== 0) {
        return { cursor: 0, generation, output: "", reset: true };
    }
    if (requestedCursor !== current.cursor) return current;
    const cursor = Number(payload.outputCursor);
    if (!Number.isSafeInteger(cursor) || cursor < requestedCursor) return { ...current, cursor: 0, generation, reset: true };
    if (!payload.outputReset && cursor === current.cursor && current.generation === generation) return current;
    return { cursor, generation, output: `${payload.outputReset ? "" : current.output}${payload.output || ""}`.slice(-32_000), reset: false };
}
