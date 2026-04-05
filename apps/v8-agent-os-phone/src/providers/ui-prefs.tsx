import React from "react";

import { getStoredValue, setStoredValue } from "@/src/lib/mobile-storage";
import { getThemeColors, type ThemeColors, type ThemeMode } from "@/src/theme/tokens";

export type LocaleCode = "zh-CN" | "en";

type UiPrefsContextValue = {
    locale: LocaleCode;
    themeMode: ThemeMode;
    voiceEnabled: boolean;
    colors: ThemeColors;
    hydrated: boolean;
    setLocale: (next: LocaleCode) => Promise<void>;
    toggleLocale: () => Promise<void>;
    setThemeMode: (next: ThemeMode) => Promise<void>;
    toggleThemeMode: () => Promise<void>;
    setVoiceEnabled: (next: boolean) => Promise<void>;
    toggleVoiceEnabled: () => Promise<void>;
    t: (zh: string, en?: string) => string;
};

const UiPrefsContext = React.createContext<UiPrefsContextValue | null>(null);

function normalizeLocale(value: string | null | undefined): LocaleCode {
    return String(value || "").toLowerCase().startsWith("en") ? "en" : "zh-CN";
}

function normalizeTheme(value: string | null | undefined): ThemeMode {
    return String(value || "").toLowerCase() === "dark" ? "dark" : "light";
}

function normalizeVoice(value: string | null | undefined): boolean {
    if (value === "false" || value === "0") return false;
    return true;
}

export function UiPrefsProvider({ children }: { children: React.ReactNode }) {
    const [locale, setLocaleState] = React.useState<LocaleCode>("zh-CN");
    const [themeMode, setThemeModeState] = React.useState<ThemeMode>("light");
    const [voiceEnabled, setVoiceEnabledState] = React.useState(true);
    const [hydrated, setHydrated] = React.useState(false);

    React.useEffect(() => {
        let disposed = false;
        void (async () => {
            const [storedLocale, storedTheme, storedVoice] = await Promise.all([
                getStoredValue("locale"),
                getStoredValue("themeMode"),
                getStoredValue("voiceEnabled"),
            ]);
            if (disposed) return;
            setLocaleState(normalizeLocale(storedLocale));
            setThemeModeState(normalizeTheme(storedTheme));
            setVoiceEnabledState(normalizeVoice(storedVoice));
            setHydrated(true);
        })();
        return () => {
            disposed = true;
        };
    }, []);

    const setLocale = React.useCallback(async (next: LocaleCode) => {
        setLocaleState(next);
        await setStoredValue("locale", next);
    }, []);

    const toggleLocale = React.useCallback(async () => {
        const next = locale === "zh-CN" ? "en" : "zh-CN";
        setLocaleState(next);
        await setStoredValue("locale", next);
    }, [locale]);

    const setThemeMode = React.useCallback(async (next: ThemeMode) => {
        setThemeModeState(next);
        await setStoredValue("themeMode", next);
    }, []);

    const toggleThemeMode = React.useCallback(async () => {
        const next = themeMode === "light" ? "dark" : "light";
        setThemeModeState(next);
        await setStoredValue("themeMode", next);
    }, [themeMode]);

    const setVoiceEnabled = React.useCallback(async (next: boolean) => {
        setVoiceEnabledState(next);
        await setStoredValue("voiceEnabled", next ? "true" : "false");
    }, []);

    const toggleVoiceEnabled = React.useCallback(async () => {
        const next = !voiceEnabled;
        setVoiceEnabledState(next);
        await setStoredValue("voiceEnabled", next ? "true" : "false");
    }, [voiceEnabled]);

    const translate = React.useCallback(
        (zh: string, en?: string) => (locale === "en" ? (en || zh) : zh),
        [locale],
    );

    const value = React.useMemo<UiPrefsContextValue>(() => ({
        locale,
        themeMode,
        voiceEnabled,
        colors: getThemeColors(themeMode),
        hydrated,
        setLocale,
        toggleLocale,
        setThemeMode,
        toggleThemeMode,
        setVoiceEnabled,
        toggleVoiceEnabled,
        t: translate,
    }), [
        hydrated,
        locale,
        setLocale,
        setThemeMode,
        setVoiceEnabled,
        themeMode,
        toggleLocale,
        toggleThemeMode,
        toggleVoiceEnabled,
        translate,
        voiceEnabled,
    ]);

    return <UiPrefsContext.Provider value={value}>{children}</UiPrefsContext.Provider>;
}

export function useUiPrefs() {
    const context = React.useContext(UiPrefsContext);
    if (!context) {
        throw new Error("useUiPrefs 必须在 UiPrefsProvider 内使用");
    }
    return context;
}
