const CACHE_PREFIX = "v8-agent-os.webConversation.";
const DB_NAME = "v8-agent-os-web";

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
