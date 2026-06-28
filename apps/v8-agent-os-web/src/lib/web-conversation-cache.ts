import type { Message } from "@/store/chat-types";

const CACHE_PREFIX = "v8-agent-os.webConversation.";
const MAX_CACHED_MESSAGES = 240;

export type WebConversationCache = {
    sessionId: string;
    messages: Message[];
    syncCursor: string;
    updatedAt: string;
};

function isBrowser() {
    return typeof window !== "undefined" && Boolean(window.localStorage);
}

function cacheKey(sessionId: string) {
    return `${CACHE_PREFIX}${encodeURIComponent(sessionId)}`;
}

export function readWebConversationCache(sessionId: string): WebConversationCache | null {
    if (!isBrowser() || !sessionId) return null;
    try {
        const raw = window.localStorage.getItem(cacheKey(sessionId));
        if (!raw) return null;
        const parsed = JSON.parse(raw) as Partial<WebConversationCache>;
        if (!Array.isArray(parsed.messages)) return null;
        return {
            sessionId,
            messages: parsed.messages as Message[],
            syncCursor: String(parsed.syncCursor || ""),
            updatedAt: String(parsed.updatedAt || ""),
        };
    } catch {
        return null;
    }
}

export function writeWebConversationCache(sessionId: string, messages: Message[], syncCursor?: string) {
    if (!isBrowser() || !sessionId) return;
    const capped = messages.slice(-MAX_CACHED_MESSAGES);
    const payload: WebConversationCache = {
        sessionId,
        messages: capped,
        syncCursor: String(syncCursor || new Date().toISOString()),
        updatedAt: new Date().toISOString(),
    };
    try {
        window.localStorage.setItem(cacheKey(sessionId), JSON.stringify(payload));
    } catch {
        // Local cache is an acceleration layer only.
    }
}

export function mergeWebConversationSync(
    currentMessages: Message[],
    incomingMessages: Message[],
    deletions: unknown[] = [],
) {
    const deletedIds = new Set(deletions.map((item) => String(item || "")).filter(Boolean));
    const order: string[] = [];
    const byId = new Map<string, Message>();

    for (const message of currentMessages) {
        if (!message?.id || deletedIds.has(message.id)) continue;
        order.push(message.id);
        byId.set(message.id, message);
    }

    for (const message of incomingMessages) {
        if (!message?.id || deletedIds.has(message.id)) continue;
        if (!byId.has(message.id)) {
            order.push(message.id);
        }
        byId.set(message.id, message);
    }

    return order
        .filter((id) => byId.has(id))
        .map((id) => byId.get(id) as Message)
        .slice(-MAX_CACHED_MESSAGES);
}
