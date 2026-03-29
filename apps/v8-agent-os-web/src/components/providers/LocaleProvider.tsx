"use client";

import React from "react";

import { LOCALE_COOKIE_NAME, Locale, LocalizedText, pickLocalizedText } from "@/lib/locale";

type LocaleContextValue = {
    locale: Locale;
    setLocale: (locale: Locale) => void;
    t: (value: LocalizedText | string) => string;
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
        (value: LocalizedText | string) => pickLocalizedText(locale, value),
        [locale],
    );

    const contextValue = React.useMemo(
        () => ({
            locale,
            setLocale,
            t,
        }),
        [locale, setLocale, t],
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
