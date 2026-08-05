export type BoundedResponseTextOptions = {
    maxBytes: number;
    maxChars: number;
};

export class BoundedResponseTextError extends Error {
    readonly code: "too_large" | "stream_unavailable";

    constructor(code: "too_large" | "stream_unavailable") {
        super(code);
        this.name = "BoundedResponseTextError";
        this.code = code;
    }
}

function normalizeLimit(value: number) {
    if (!Number.isFinite(value) || value <= 0) {
        throw new TypeError("Text preview limits must be positive finite numbers.");
    }
    return Math.floor(value);
}

export async function readBoundedResponseText(
    response: Response,
    options: BoundedResponseTextOptions,
): Promise<{ text: string; truncated: boolean }> {
    const maxBytes = normalizeLimit(options.maxBytes);
    const maxChars = normalizeLimit(options.maxChars);
    const announcedSize = Number(response.headers.get("Content-Length") || 0);
    if (Number.isFinite(announcedSize) && announcedSize > maxBytes) {
        throw new BoundedResponseTextError("too_large");
    }

    const reader = response.body && typeof response.body.getReader === "function"
        ? response.body.getReader()
        : null;
    if (!reader) {
        throw new BoundedResponseTextError("stream_unavailable");
    }

    const decoder = new TextDecoder();
    let byteLength = 0;
    let text = "";
    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                text += decoder.decode();
                return {
                    text: text.slice(0, maxChars),
                    truncated: text.length > maxChars,
                };
            }
            byteLength += value.byteLength;
            if (byteLength > maxBytes) {
                await reader.cancel().catch(() => undefined);
                throw new BoundedResponseTextError("too_large");
            }
            text += decoder.decode(value, { stream: true });
            if (text.length > maxChars) {
                await reader.cancel().catch(() => undefined);
                return { text: text.slice(0, maxChars), truncated: true };
            }
        }
    } finally {
        reader.releaseLock();
    }
}
