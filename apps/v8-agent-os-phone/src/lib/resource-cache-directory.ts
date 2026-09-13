import { readMetadata, writeMetadata } from "@/src/lib/mobile-storage";

const pending = new Map<string, Promise<string>>();
let sequence = 0;

/** Full identity stays in SQLite. Filesystem names remain short on Android/iOS. */
export async function resourceCacheDirectory(root: string, kind: string, identity: string) {
    if (!identity) throw new Error("Missing resource identity");
    const key = `v8.phone.cacheDirectory.${JSON.stringify([kind, identity])}`;
    if (pending.has(key)) return pending.get(key)!;
    const operation = (async () => {
        const stored = await readMetadata(key);
        if (stored && /^[a-z0-9-]+$/.test(stored)) return `${root}v8-cache/${stored}/`;
        const name = `r-${Date.now().toString(36)}-${(++sequence).toString(36)}-${Math.random().toString(36).slice(2)}`;
        await writeMetadata(key, name);
        return `${root}v8-cache/${name}/`;
    })();
    pending.set(key, operation);
    try { return await operation; } finally { if (pending.get(key) === operation) pending.delete(key); }
}
