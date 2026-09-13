import * as SecureStore from "expo-secure-store";
import Storage from "expo-sqlite/kv-store";
import { Platform } from "react-native";

const KEYS = {
    adminBaseUrl: "v8.phone.adminBaseUrl",
    accessToken: "v8.phone.accessToken",
    refreshToken: "v8.phone.refreshToken",
    user: "v8.phone.user",
    activeConversationId: "v8.phone.activeConversationId",
    adminConnectionProfiles: "v8.phone.adminConnectionProfiles",
    activeAdminConnectionProfileId: "v8.phone.activeAdminConnectionProfileId",
    locale: "v8.phone.locale",
    themeMode: "v8.phone.themeMode",
    voiceEnabled: "v8.phone.voiceEnabled",
    userAvatarCache: "v8.phone.userAvatarCache",
    userBackgroundCache: "v8.phone.userBackgroundCache",
    safetyApprovalMode: "v8.phone.safetyApprovalMode",
} as const;

function webStorage() {
    if (!globalThis.localStorage) throw new Error("Local storage is unavailable");
    return globalThis.localStorage;
}

// Never log native exceptions: a storage implementation can include its value.
export async function readSecureItem(key: string): Promise<string | null> {
    try {
        return Platform.OS === "web" ? webStorage().getItem(key) : await SecureStore.getItemAsync(key);
    } catch { throw new Error("Secure storage could not be read. Unlock the phone and retry."); }
}

export async function writeSecureItem(key: string, value: string): Promise<void> {
    try {
        if (Platform.OS === "web") webStorage().setItem(key, value);
        else await SecureStore.setItemAsync(key, value);
    } catch { throw new Error("Credentials were not saved. Free storage or unlock the phone and retry."); }
}

export async function deleteSecureItem(key: string): Promise<void> {
    try {
        if (Platform.OS === "web") webStorage().removeItem(key);
        else await SecureStore.deleteItemAsync(key);
    } catch { throw new Error("Credentials could not be removed. Please retry."); }
}

export async function readMetadata(key: string): Promise<string | null> {
    return Platform.OS === "web" ? webStorage().getItem(key) : Storage.getItem(key);
}

export async function writeMetadata(key: string, value: string): Promise<void> {
    if (Platform.OS === "web") webStorage().setItem(key, value);
    else await Storage.setItem(key, value);
}

export async function deleteMetadata(key: string): Promise<void> {
    if (Platform.OS === "web") webStorage().removeItem(key);
    else await Storage.removeItem(key);
}

// Legacy keys remain readable for profile migration. New credentials use small
// secure items; non-secret metadata and preferences live in SQLite KV.
export async function getStoredValue(key: keyof typeof KEYS) {
    const value = await readMetadata(KEYS[key]);
    return value ?? readSecureItem(KEYS[key]);
}

export async function setStoredValue(key: keyof typeof KEYS, value: string) {
    if (key === "accessToken" || key === "refreshToken" || key === "adminConnectionProfiles") {
        return writeSecureItem(KEYS[key], value);
    }
    await writeMetadata(KEYS[key], value);
}

export async function removeStoredValue(key: keyof typeof KEYS) {
    await deleteMetadata(KEYS[key]);
    await deleteSecureItem(KEYS[key]);
}

export async function clearSessionStorage() {
    for (const key of ["accessToken", "refreshToken", "user", "activeConversationId", "userAvatarCache", "userBackgroundCache"] as const) {
        await removeStoredValue(key);
    }
}
