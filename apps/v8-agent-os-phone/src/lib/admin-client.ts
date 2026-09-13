import type { ChatStreamEvent } from "@/src/types/admin";
import { translateCurrent } from "@/src/lib/locale";

type StreamEventHandler = (eventName: string, payload: unknown) => void;

function attachSseEventId(payload: unknown, eventId: string) {
    const normalizedEventId = String(eventId || "").trim();
    if (!normalizedEventId || !payload || typeof payload !== "object" || Array.isArray(payload)) {
        return payload;
    }
    const record = payload as Record<string, unknown>;
    const diagnostics = record._diagnostics && typeof record._diagnostics === "object" && !Array.isArray(record._diagnostics)
        ? record._diagnostics as Record<string, unknown>
        : {};
    return {
        ...record,
        _diagnostics: {
            ...diagnostics,
            sseEventId: normalizedEventId,
        },
    };
}

export function normalizeAdminBaseUrl(value: string) {
    const trimmed = String(value || "").trim();
    if (!trimmed) return "";
    const noTrailingSlash = trimmed.replace(/\/+$/, "");
    return noTrailingSlash.endsWith("/api") ? noTrailingSlash.slice(0, -4) : noTrailingSlash;
}

export function buildAdminApiUrl(baseUrl: string, path: string) {
    const normalizedBase = normalizeAdminBaseUrl(baseUrl);
    const normalizedPath = path.startsWith("/") ? path : `/${path}`;
    return `${normalizedBase}${normalizedPath}`;
}

const LOOPBACK_PATTERN = /^(https?:\/\/)(?:127(?:\.\d{1,3}){3}|localhost|\[::1\])(?::\d+)?(\/.*)?$/i;

export function resolveAdminAssetUrl(baseUrl: string, value?: string | null) {
    const raw = String(value || "").trim();
    if (!raw) return "";

    const normalizedBase = normalizeAdminBaseUrl(baseUrl);

    if (raw.startsWith("/")) {
        return `${normalizedBase}${raw}`;
    }

    const loopbackMatch = raw.match(LOOPBACK_PATTERN);
    if (loopbackMatch) {
        return `${normalizedBase}${loopbackMatch[2] || ""}`;
    }

    return raw;
}

export async function parseJsonSafe<T>(response: Response): Promise<T | null> {
    try {
        return (await response.json()) as T;
    } catch (error) {
        if (error instanceof Error && error.name === "AbortError") throw error;
        return null;
    }
}

export async function parseTextSafe(response: Response) {
    try {
        return await response.text();
    } catch (error) {
        if (error instanceof Error && error.name === "AbortError") throw error;
        return "";
    }
}

export async function streamNdjson(
    response: Response,
    onEvent: (event: ChatStreamEvent) => void,
) {
    const flushBuffer = (raw: string) => {
        const lines = raw.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
        const rest = lines.pop() || "";
        for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed) continue;
            try {
                onEvent(JSON.parse(trimmed) as ChatStreamEvent);
            } catch {
                // Ignore malformed line fragments from upstream stream noise.
            }
        }
        return rest;
    };

    if (!response.body || typeof response.body.getReader !== "function") {
        const fullText = await response.text();
        let trailing = flushBuffer(fullText);
        if (trailing.trim()) {
            try {
                onEvent(JSON.parse(trailing.trim()) as ChatStreamEvent);
            } catch {
                // Ignore trailing parse failures.
            }
        }
        return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        buffer = flushBuffer(buffer);
    }
    if (buffer.trim()) {
        try {
            onEvent(JSON.parse(buffer.trim()) as ChatStreamEvent);
        } catch {
            // Ignore trailing parse failures.
        }
    }
}

export async function streamSse(
    response: Response,
    onEvent: StreamEventHandler,
    signal?: AbortSignal,
) {
    if (!response.body || typeof response.body.getReader !== "function") {
        throw new Error("Realtime transport requires a streaming response body");
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let partialLine = "";
    let eventName = "message";
    let eventId = "";
    let dataLines: string[] = [];
    const consumeLine = (line: string) => {
        if (!line) {
            if (dataLines.length && !signal?.aborted) {
                const raw = dataLines.join("\n");
                let payload: unknown = raw;
                try { payload = JSON.parse(raw); } catch { /* SSE also permits plain text. */ }
                onEvent(eventName, attachSseEventId(payload, eventId));
            }
            eventName = "message";
            eventId = "";
            dataLines = [];
            return;
        }
        if (line.startsWith(":")) return;
        const divider = line.indexOf(":");
        const field = divider < 0 ? line : line.slice(0, divider);
        let value = divider < 0 ? "" : line.slice(divider + 1);
        if (value.startsWith(" ")) value = value.slice(1);
        if (field === "event") eventName = value || "message";
        else if (field === "id" && !value.includes("\0")) eventId = value;
        else if (field === "data") dataLines.push(value);
    };
    const cancel = () => { void reader.cancel().catch(() => undefined); };
    signal?.addEventListener("abort", cancel, { once: true });
    try {
        while (!signal?.aborted) {
            const { done, value } = await reader.read();
            if (signal?.aborted) break;
            partialLine += done ? decoder.decode() : decoder.decode(value, { stream: true });
            let start = 0;
            for (let index = 0; index < partialLine.length; index += 1) {
                const char = partialLine[index];
                if (char !== "\r" && char !== "\n") continue;
                // Keep a trailing CR until we know whether the next byte is LF.
                if (!done && char === "\r" && index === partialLine.length - 1) break;
                consumeLine(partialLine.slice(start, index));
                if (signal?.aborted) break;
                if (char === "\r" && partialLine[index + 1] === "\n") index += 1;
                start = index + 1;
            }
            partialLine = partialLine.slice(start);
            if (done) break;
        }
        // A closed/canceled transport never promotes half an SSE event to a
        // complete event. The existing reconnect owner recovers its snapshot.
    } finally {
        signal?.removeEventListener("abort", cancel);
        await reader.cancel().catch(() => undefined);
        reader.releaseLock();
    }
}
