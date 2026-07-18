const CACHE_PREFIX = "v8-agent-os.webConversation.";
const DB_NAME = "v8-agent-os-web";
const SESSION_INDEX_PREFIX = "v8-agent-os.sessionIndex.v1.";
const SESSION_INDEX_VERSION = 1;

function isBrowser() {
    return typeof window !== "undefined";
}

async function deleteLegacyIndexedDb() {
    if (!isBrowser() || !window.indexedDB) {
        return;
    }

    await new Promise<void>((resolve) => {
        const request = window.indexedDB.deleteDatabase(DB_NAME);
        request.onsuccess = () => resolve();
        request.onerror = () => resolve();
        request.onblocked = () => resolve();
    });
}

function clearLegacyLocalStorage() {
    if (!isBrowser() || !window.localStorage) {
        return;
    }
    const keys: string[] = [];
    for (let index = 0; index < window.localStorage.length; index += 1) {
        const key = window.localStorage.key(index);
        if (key && key.startsWith(CACHE_PREFIX)) {
            keys.push(key);
        }
    }
    keys.forEach((key) => window.localStorage.removeItem(key));
}

export async function clearLegacyWebConversationCache(): Promise<void> {
    if (!isBrowser()) {
        return;
    }
    clearLegacyLocalStorage();
    await deleteLegacyIndexedDb();
}

function sessionIndexKey(ownerKey: string) {
    return `${SESSION_INDEX_PREFIX}${encodeURIComponent(ownerKey.trim().toLowerCase() || "local")}`;
}

export function readWebSessionIndexCache<T>(ownerKey: string): T[] {
    if (!isBrowser() || !window.localStorage) {
        return [];
    }
    try {
        const raw = window.localStorage.getItem(sessionIndexKey(ownerKey));
        if (!raw) return [];
        const payload = JSON.parse(raw) as { version?: number; sessions?: unknown[] };
        if (payload.version !== SESSION_INDEX_VERSION || !Array.isArray(payload.sessions)) {
            return [];
        }
        return payload.sessions as T[];
    } catch {
        return [];
    }
}

export function writeWebSessionIndexCache<T>(ownerKey: string, sessions: T[]): void {
    if (!isBrowser() || !window.localStorage) {
        return;
    }
    try {
        window.localStorage.setItem(sessionIndexKey(ownerKey), JSON.stringify({
            version: SESSION_INDEX_VERSION,
            updatedAt: new Date().toISOString(),
            sessions,
        }));
    } catch {
        // The Engine quick index remains authoritative when browser storage is unavailable.
    }
}

export function clearWebSessionIndexCache(ownerKey: string): void {
    if (!isBrowser() || !window.localStorage) {
        return;
    }
    try {
        window.localStorage.removeItem(sessionIndexKey(ownerKey));
    } catch {
        // Best effort only.
    }
}
