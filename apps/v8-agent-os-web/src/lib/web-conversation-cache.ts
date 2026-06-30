import type { Message } from "@/store/chat-types";

const CACHE_PREFIX = "v8-agent-os.webConversation.";
const DB_NAME = "v8-agent-os-web";
const DB_VERSION = 1;
const STORE_NAME = "conversationCache";
const MAX_CACHED_MESSAGES = 2000;

export type WebConversationCache = {
    sessionId: string;
    messages: Message[];
    syncCursor: string;
    updatedAt: string;
};

function isBrowser() {
    return typeof window !== "undefined";
}

function cacheKey(sessionId: string) {
    return `${CACHE_PREFIX}${encodeURIComponent(sessionId)}`;
}

function hasIndexedDb() {
    return isBrowser() && Boolean(window.indexedDB);
}

function openWebConversationDb(): Promise<IDBDatabase> {
    return new Promise((resolve, reject) => {
        if (!hasIndexedDb()) {
            reject(new Error("IndexedDB unavailable"));
            return;
        }
        const request = window.indexedDB.open(DB_NAME, DB_VERSION);
        request.onupgradeneeded = () => {
            const db = request.result;
            if (!db.objectStoreNames.contains(STORE_NAME)) {
                db.createObjectStore(STORE_NAME, { keyPath: "sessionId" });
            }
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error || new Error("IndexedDB open failed"));
    });
}

async function readIndexedDbCache(sessionId: string): Promise<WebConversationCache | null> {
    const db = await openWebConversationDb();
    try {
        return await new Promise<WebConversationCache | null>((resolve, reject) => {
            const tx = db.transaction(STORE_NAME, "readonly");
            const store = tx.objectStore(STORE_NAME);
            const request = store.get(sessionId);
            request.onsuccess = () => {
                const parsed = request.result as Partial<WebConversationCache> | undefined;
                if (!parsed || !Array.isArray(parsed.messages)) {
                    resolve(null);
                    return;
                }
                resolve({
                    sessionId,
                    messages: parsed.messages as Message[],
                    syncCursor: String(parsed.syncCursor || ""),
                    updatedAt: String(parsed.updatedAt || ""),
                });
            };
            request.onerror = () => reject(request.error || new Error("IndexedDB read failed"));
        });
    } finally {
        db.close();
    }
}

async function writeIndexedDbCache(payload: WebConversationCache): Promise<void> {
    const db = await openWebConversationDb();
    try {
        await new Promise<void>((resolve, reject) => {
            const tx = db.transaction(STORE_NAME, "readwrite");
            const store = tx.objectStore(STORE_NAME);
            const request = store.put(payload);
            request.onsuccess = () => resolve();
            request.onerror = () => reject(request.error || new Error("IndexedDB write failed"));
        });
    } finally {
        db.close();
    }
}

function readLocalStorageCache(sessionId: string): WebConversationCache | null {
    if (!isBrowser() || !sessionId) return null;
    try {
        if (!window.localStorage) return null;
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

function writeLocalStorageCache(payload: WebConversationCache) {
    if (!isBrowser() || !payload.sessionId) return;
    try {
        if (!window.localStorage) return;
        window.localStorage.setItem(cacheKey(payload.sessionId), JSON.stringify(payload));
    } catch {
        // Local cache is an acceleration layer only.
    }
}

export async function readWebConversationCache(sessionId: string): Promise<WebConversationCache | null> {
    if (!isBrowser() || !sessionId) return null;
    if (hasIndexedDb()) {
        try {
            const indexedDbCache = await readIndexedDbCache(sessionId);
            if (indexedDbCache) return indexedDbCache;
        } catch {
            // Fall through to the legacy cache below.
        }
    }
    return readLocalStorageCache(sessionId);
}

export async function writeWebConversationCache(sessionId: string, messages: Message[], syncCursor?: string): Promise<void> {
    if (!isBrowser() || !sessionId) return;
    const capped = messages.slice(-MAX_CACHED_MESSAGES);
    const existing = syncCursor === undefined ? await readWebConversationCache(sessionId) : null;
    const payload: WebConversationCache = {
        sessionId,
        messages: capped,
        syncCursor: String(syncCursor ?? existing?.syncCursor ?? ""),
        updatedAt: new Date().toISOString(),
    };
    if (hasIndexedDb()) {
        try {
            await writeIndexedDbCache(payload);
            return;
        } catch {
            // Fall through to localStorage as a degraded acceleration layer.
        }
    }
    writeLocalStorageCache(payload);
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
