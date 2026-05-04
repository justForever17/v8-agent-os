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
) {
    const flushBuffer = (raw: string) => {
        const chunks = raw.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n\n");
        const rest = chunks.pop() || "";

        for (const chunk of chunks) {
            const lines = chunk.split("\n");
            let eventName = "message";
            let eventId = "";
            const dataLines: string[] = [];

            for (const line of lines) {
                const trimmed = line.trim();
                if (!trimmed) continue;
                if (trimmed.startsWith("id:")) {
                    eventId = trimmed.slice("id:".length).trim();
                    continue;
                }
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
                onEvent(eventName, attachSseEventId(JSON.parse(rawPayload), eventId));
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

function buildStreamError(message: string, status?: number) {
    const error = new Error(message) as Error & { status?: number };
    if (typeof status === "number" && Number.isFinite(status) && status > 0) {
        error.status = status;
    }
    return error;
}

export async function streamSseWithXmlHttpRequest(options: {
    url: string;
    headers?: Record<string, string>;
    signal?: AbortSignal;
    onEvent: StreamEventHandler;
    onHeaders?: (headers: Record<string, string>) => void;
}) {
    if (typeof XMLHttpRequest !== "function") {
        throw buildStreamError(translateCurrent("src.lib.admin_client.text"));
    }

    const { url, headers, signal, onEvent, onHeaders } = options;

    const flushBuffer = (raw: string) => {
        const chunks = raw.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n\n");
        const rest = chunks.pop() || "";

        for (const chunk of chunks) {
            const lines = chunk.split("\n");
            let eventName = "message";
            let eventId = "";
            const dataLines: string[] = [];

            for (const line of lines) {
                const trimmed = line.trim();
                if (!trimmed) continue;
                if (trimmed.startsWith("id:")) {
                    eventId = trimmed.slice("id:".length).trim();
                    continue;
                }
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
                onEvent(eventName, attachSseEventId(JSON.parse(rawPayload), eventId));
            } catch {
                onEvent(eventName, rawPayload);
            }
        }

        return rest;
    };

    await new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        let settled = false;
        let aborted = false;
        let headersEmitted = false;
        let responseCursor = 0;
        let buffer = "";

        const cleanup = () => {
            if (signal) {
                signal.removeEventListener("abort", handleAbort);
            }
            xhr.onreadystatechange = null;
            xhr.onprogress = null;
            xhr.onerror = null;
            xhr.ontimeout = null;
            xhr.onabort = null;
        };

        const finish = (callback: () => void) => {
            if (settled) {
                return;
            }
            settled = true;
            cleanup();
            callback();
        };

        const pumpResponseText = () => {
            const responseText = typeof xhr.responseText === "string" ? xhr.responseText : "";
            if (responseText.length <= responseCursor) {
                return;
            }
            buffer += responseText.slice(responseCursor);
            responseCursor = responseText.length;
            buffer = flushBuffer(buffer);
        };

        const emitHeaders = () => {
            if (headersEmitted || !onHeaders || xhr.readyState < XMLHttpRequest.HEADERS_RECEIVED) {
                return;
            }
            headersEmitted = true;
            const raw = xhr.getAllResponseHeaders() || "";
            const normalized: Record<string, string> = {};
            for (const line of raw.split(/\r?\n/)) {
                const divider = line.indexOf(":");
                if (divider <= 0) {
                    continue;
                }
                const key = line.slice(0, divider).trim().toLowerCase();
                const value = line.slice(divider + 1).trim();
                if (!key || !value) {
                    continue;
                }
                normalized[key] = value;
            }
            onHeaders(normalized);
        };

        const handleAbort = () => {
            aborted = true;
            try {
                xhr.abort();
            } catch {
                // noop
            }
            finish(resolve);
        };

        if (signal?.aborted) {
            handleAbort();
            return;
        }

        xhr.open("GET", url, true);
        xhr.setRequestHeader("Accept", "text/event-stream");
        xhr.setRequestHeader("Cache-Control", "no-cache");
        if (headers) {
            for (const [key, value] of Object.entries(headers)) {
                if (!value) continue;
                xhr.setRequestHeader(key, value);
            }
        }

        xhr.onreadystatechange = () => {
            emitHeaders();
            if (xhr.readyState >= XMLHttpRequest.LOADING) {
                pumpResponseText();
            }
            if (xhr.readyState !== XMLHttpRequest.DONE) {
                return;
            }

            pumpResponseText();

            if (aborted) {
                finish(resolve);
                return;
            }

            const status = Number(xhr.status || 0);
            if (status >= 200 && status < 300) {
                if (buffer.trim()) {
                    flushBuffer(`${buffer}\n\n`);
                    buffer = "";
                }
                finish(resolve);
                return;
            }

            finish(() => reject(buildStreamError(
                xhr.responseText?.trim() || translateCurrent("src.lib.admin_client.realtime_stream_failed_with_status", {
                    status: status || "unknown",
                }),
                status || undefined,
            )));
        };

        xhr.onprogress = pumpResponseText;
        xhr.onerror = () => {
            if (aborted) {
                finish(resolve);
                return;
            }
            finish(() => reject(buildStreamError(translateCurrent("src.lib.admin_client.text_2"), Number(xhr.status || 0) || undefined)));
        };
        xhr.ontimeout = () => {
            if (aborted) {
                finish(resolve);
                return;
            }
            finish(() => reject(buildStreamError(translateCurrent("src.lib.admin_client.text_3"), Number(xhr.status || 0) || undefined)));
        };
        xhr.onabort = () => {
            finish(resolve);
        };

        if (signal) {
            signal.addEventListener("abort", handleAbort, { once: true });
        }

        try {
            xhr.send();
        } catch (error) {
            finish(() => reject(error instanceof Error ? error : new Error(translateCurrent("src.providers.app_session.text_7"))));
        }
    });
}
