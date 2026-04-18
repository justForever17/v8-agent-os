export const ENGINE_NOW_HEADER = "x-v8-engine-now";

export function parseEngineNowHeader(value?: string | null) {
    const text = String(value || "").trim();
    if (!text) {
        return null;
    }
    const timestamp = Date.parse(text);
    if (Number.isNaN(timestamp)) {
        return null;
    }
    return timestamp;
}

export function toEngineClockOffsetMs(engineNowHeader?: string | null, localNowMs = Date.now()) {
    const parsed = parseEngineNowHeader(engineNowHeader);
    if (parsed === null) {
        return null;
    }
    return parsed - localNowMs;
}

export function getEngineNowMs(offsetMs: number) {
    return Date.now() + offsetMs;
}
