"use client";

import React from "react";

import {
    LOCALE_COOKIE_NAME,
    Locale,
    resolveText,
    type TranslationKey,
    type TranslationParams,
} from "@/lib/locale";

type LocaleContextValue = {
    locale: Locale;
    setLocale: (locale: Locale) => void;
    t: (key: TranslationKey | string, params?: TranslationParams) => string;
    resolveText: (value: string, params?: TranslationParams) => string;
};

const LocaleContext = React.createContext<LocaleContextValue | null>(null);

export function LocaleProvider({
    initialLocale,
    children,
}: {
    initialLocale: Locale;
    children: React.ReactNode;
}) {
    const [locale, setLocaleState] = React.useState<Locale>(initialLocale);

    React.useEffect(() => {
        document.documentElement.lang = locale;
        document.cookie = `${LOCALE_COOKIE_NAME}=${locale}; Path=/; Max-Age=31536000; SameSite=Lax`;
    }, [locale]);

    const setLocale = React.useCallback((nextLocale: Locale) => {
        setLocaleState(nextLocale);
    }, []);

    const t = React.useCallback(
        (value: TranslationKey | string, params?: TranslationParams) => {
            if (typeof value === "string" && !value) {
                return value;
            }
            return resolveText(locale, String(value), params);
        },
        [locale],
    );
    const resolveTextValue = React.useCallback(
        (value: string, params?: TranslationParams) => resolveText(locale, value, params),
        [locale],
    );

    const contextValue = React.useMemo(
        () => ({
            locale,
            setLocale,
            t,
            resolveText: resolveTextValue,
        }),
        [locale, setLocale, t, resolveTextValue],
    );

    return <LocaleContext.Provider value={contextValue}>{children}</LocaleContext.Provider>;
}

export function useLocale() {
    const context = React.useContext(LocaleContext);
    if (!context) {
        throw new Error("useLocale must be used within LocaleProvider");
    }
    return context;
}

export function useT() {
    return useLocale().t;
}

export function useResolveText() {
    return useLocale().resolveText;
}
