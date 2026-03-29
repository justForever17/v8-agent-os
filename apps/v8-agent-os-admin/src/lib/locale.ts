export const LOCALE_COOKIE_NAME = "v8-agent-os-locale";

export type Locale = "zh-CN" | "en";

export type LocalizedText = {
    "zh-CN": string;
    en: string;
};

export function lt(zhCN: string, en: string): LocalizedText {
    return {
        "zh-CN": zhCN,
        en,
    };
}

export function parseLocale(value: string | null | undefined): Locale | null {
    if (!value) return null;
    const normalized = value.trim().toLowerCase();
    if (normalized === "en" || normalized.startsWith("en-")) {
        return "en";
    }
    if (normalized === "zh-cn" || normalized === "zh" || normalized.startsWith("zh-")) {
        return "zh-CN";
    }
    return null;
}

export function resolveInitialLocale(
    cookieValue?: string | null,
    acceptLanguage?: string | null,
): Locale {
    return parseLocale(cookieValue) || parseLocale(acceptLanguage) || "zh-CN";
}

export function pickLocalizedText(locale: Locale, value: LocalizedText | string): string {
    return typeof value === "string" ? value : value[locale];
}
