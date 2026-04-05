import * as SecureStore from "expo-secure-store";

const KEYS = {
    adminBaseUrl: "v8.phone.adminBaseUrl",
    accessToken: "v8.phone.accessToken",
    refreshToken: "v8.phone.refreshToken",
    user: "v8.phone.user",
    activeConversationId: "v8.phone.activeConversationId",
    locale: "v8.phone.locale",
    themeMode: "v8.phone.themeMode",
    voiceEnabled: "v8.phone.voiceEnabled",
} as const;

export async function getStoredValue(key: keyof typeof KEYS) {
    return SecureStore.getItemAsync(KEYS[key]);
}

export async function setStoredValue(key: keyof typeof KEYS, value: string) {
    await SecureStore.setItemAsync(KEYS[key], value);
}

export async function removeStoredValue(key: keyof typeof KEYS) {
    await SecureStore.deleteItemAsync(KEYS[key]);
}

export async function clearSessionStorage() {
    await Promise.all([
        removeStoredValue("accessToken"),
        removeStoredValue("refreshToken"),
        removeStoredValue("user"),
        removeStoredValue("activeConversationId"),
    ]);
}
