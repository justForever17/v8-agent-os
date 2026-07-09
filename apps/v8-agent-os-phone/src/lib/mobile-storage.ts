import * as SecureStore from "expo-secure-store";
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
    safetyApprovalMode: "v8.phone.safetyApprovalMode",
} as const;

export async function getStoredValue(key: keyof typeof KEYS) {
    if (Platform.OS === "web" || typeof SecureStore.getItemAsync !== "function") {
        try {
            return globalThis.localStorage?.getItem(KEYS[key]) ?? null;
        } catch {
            return null;
        }
    }
    try {
        return await SecureStore.getItemAsync(KEYS[key]);
    } catch (error) {
        console.warn(`[mobile-storage] Failed to get secure item for ${key}:`, error);
        return null;
    }
}

export async function setStoredValue(key: keyof typeof KEYS, value: string) {
    if (Platform.OS === "web" || typeof SecureStore.setItemAsync !== "function") {
        try {
            globalThis.localStorage?.setItem(KEYS[key], value);
        } catch {
            // Best-effort web fallback.
        }
        return;
    }
    try {
        await SecureStore.setItemAsync(KEYS[key], value);
    } catch (error) {
        console.warn(`[mobile-storage] Failed to set secure item for ${key}:`, error);
    }
}

export async function removeStoredValue(key: keyof typeof KEYS) {
    if (Platform.OS === "web" || typeof SecureStore.deleteItemAsync !== "function") {
        try {
            globalThis.localStorage?.removeItem(KEYS[key]);
        } catch {
            // Best-effort web fallback.
        }
        return;
    }
    try {
        await SecureStore.deleteItemAsync(KEYS[key]);
    } catch (error) {
        console.warn(`[mobile-storage] Failed to delete secure item for ${key}:`, error);
    }
}

export async function clearSessionStorage() {
    await Promise.all([
        removeStoredValue("accessToken"),
        removeStoredValue("refreshToken"),
        removeStoredValue("user"),
        removeStoredValue("activeConversationId"),
    ]);
}
