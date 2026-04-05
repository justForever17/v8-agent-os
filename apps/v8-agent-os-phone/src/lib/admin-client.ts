import type { ChatStreamEvent } from "@/src/types/admin";

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
    } catch {
        return null;
    }
}

export async function parseTextSafe(response: Response) {
    try {
        return await response.text();
    } catch {
        return "";
    }
}

export async function streamNdjson(
    response: Response,
    onEvent: (event: ChatStreamEvent) => void,
) {
    const flushBuffer = (raw: string) => {
        const lines = raw.split("\n");
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
    onEvent: (eventName: string, payload: unknown) => void,
) {
    const flushBuffer = (raw: string) => {
        const chunks = raw.split("\n\n");
        const rest = chunks.pop() || "";

        for (const chunk of chunks) {
            const lines = chunk.split("\n");
            let eventName = "message";
            const dataLines: string[] = [];

            for (const line of lines) {
                const trimmed = line.trim();
                if (!trimmed) continue;
                if (trimmed.startsWith("event:")) {
                    eventName = trimmed.slice("event:".length).trim() || "message";
                    continue;
                }
                if (trimmed.startsWith("data:")) {
                    dataLines.push(trimmed.slice("data:".length).trim());
                }
            }

            if (!dataLines.length) continue;
            const rawPayload = dataLines.join("\n");
            try {
                onEvent(eventName, JSON.parse(rawPayload));
            } catch {
                onEvent(eventName, rawPayload);
            }
        }

        return rest;
    };

    if (!response.body || typeof response.body.getReader !== "function") {
        const fullText = await response.text();
        let trailing = flushBuffer(fullText);
        if (trailing.trim()) {
            trailing = flushBuffer(`${trailing}\n\n`);
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
        flushBuffer(`${buffer}\n\n`);
    }
}
