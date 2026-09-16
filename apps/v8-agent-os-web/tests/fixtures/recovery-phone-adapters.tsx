import React from "react";
import catalog from "../../../v8-agent-os-phone/src/i18n/locales/en.json";
export const MaterialCommunityIcons = () => <span />;
export function useAppSession() {
    return { authorizedFetch: (path: string, init?: RequestInit) => fetch(path, init),
        setActiveConversationId: async (id: string) => { (window as any).lastNavigation = "/chat?id=" + id; } };
}
export function useUiPrefs() {
    return { colors: { text: "#18181b", textMuted: "#71717a", border: "#d4d4d8" },
        t: (key: string, params: Record<string, unknown> = {}) => String((catalog as Record<string, string>)[key] || key).replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? "")) };
}
// The draft owner/hook are production; only native SQLite I/O is adapted.
export async function readMetadata(key: string) { return localStorage.getItem(key); }
export async function writeMetadata(key: string, value: string) { localStorage.setItem(key, value); }
