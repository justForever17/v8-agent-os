import { Platform } from "react-native";
import * as FileSystem from "expo-file-system/legacy";

import { getStoredValue, setStoredValue } from "@/src/lib/mobile-storage";

type AvatarCacheRecord = {
    source: string;
    localUri: string;
};

function stableHash(value: string) {
    let hash = 2166136261;
    for (let index = 0; index < value.length; index += 1) {
        hash ^= value.charCodeAt(index);
        hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0).toString(16).padStart(8, "0");
}

function parseStoredRecord(value: string | null): AvatarCacheRecord | null {
    if (!value) return null;
    try {
        const parsed = JSON.parse(value) as Partial<AvatarCacheRecord>;
        const source = String(parsed.source || "").trim();
        const localUri = String(parsed.localUri || "").trim();
        return source && localUri ? { source, localUri } : null;
    } catch {
        return null;
    }
}

async function localFileExists(uri: string) {
    const info = await FileSystem.getInfoAsync(uri).catch(() => null);
    return Boolean(info?.exists && !info.isDirectory);
}

/**
 * Resolve an immutable remote avatar URL to a local file. Avatar uploads use a
 * new filename, so the source URL is also the cache version and never needs a
 * stale-time heuristic.
 */
export async function cacheProfileAvatar(source: string): Promise<string> {
    const normalizedSource = String(source || "").trim();
    if (!normalizedSource || Platform.OS === "web") return normalizedSource;
    const root = FileSystem.cacheDirectory || FileSystem.documentDirectory;
    if (!root || !/^https?:\/\//i.test(normalizedSource)) return normalizedSource;

    const stored = parseStoredRecord(await getStoredValue("userAvatarCache"));
    if (stored?.source === normalizedSource && await localFileExists(stored.localUri)) {
        return stored.localUri;
    }

    const directory = `${root}v8-profile-avatar/`;
    const localUri = `${directory}avatar-${stableHash(normalizedSource)}.webp`;
    await FileSystem.makeDirectoryAsync(directory, { intermediates: true }).catch(() => undefined);
    if (!await localFileExists(localUri)) {
        const temporaryUri = `${localUri}.download`;
        await FileSystem.deleteAsync(temporaryUri, { idempotent: true }).catch(() => undefined);
        try {
            const result = await FileSystem.downloadAsync(normalizedSource, temporaryUri);
            if (result.status < 200 || result.status >= 300) {
                throw new Error(`Avatar download failed with status ${result.status}`);
            }
            await FileSystem.moveAsync({ from: temporaryUri, to: localUri });
        } catch {
            await FileSystem.deleteAsync(temporaryUri, { idempotent: true }).catch(() => undefined);
            return normalizedSource;
        }
    }

    await setStoredValue("userAvatarCache", JSON.stringify({ source: normalizedSource, localUri } satisfies AvatarCacheRecord));
    const files = await FileSystem.readDirectoryAsync(directory).catch(() => [] as string[]);
    await Promise.all(files
        .filter((name) => name !== localUri.slice(directory.length))
        .map((name) => FileSystem.deleteAsync(`${directory}${name}`, { idempotent: true }).catch(() => undefined)));
    return localUri;
}
