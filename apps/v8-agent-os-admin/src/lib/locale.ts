import enCatalog from "@/i18n/locales/en.json";
import zhCNCatalog from "@/i18n/locales/zh-CN.json";

export const LOCALE_COOKIE_NAME = "v8-agent-os-locale";

export type Locale = "zh-CN" | "en";
export type TranslationParams = Record<string, string | number>;

export const TRANSLATION_CATALOG = {
    "zh-CN": zhCNCatalog,
    en: enCatalog,
} as const;

export type TranslationKey = keyof typeof zhCNCatalog & string;

const TRANSLATION_KEYS = new Set<TranslationKey>(Object.keys(zhCNCatalog) as TranslationKey[]);
const PLACEHOLDER_PATTERN = /\{([a-zA-Z0-9_]+)\}/g;

function failOrWarn(message: string) {
    if (process.env.NODE_ENV !== "production") {
        throw new Error(message);
    }
    console.warn(message);
}

function resolveMessage(locale: Locale, key: TranslationKey) {
    const catalog = TRANSLATION_CATALOG[locale] as Record<string, string | undefined>;
    const fallbackCatalog = TRANSLATION_CATALOG["zh-CN"] as Record<string, string | undefined>;
    const message = catalog[key] ?? fallbackCatalog[key];
    if (typeof message === "string") {
        return message;
    }
    failOrWarn(`[admin-i18n] Missing translation key "${key}" for locale "${locale}".`);
    return key;
}

function applyParams(key: TranslationKey, template: string, params?: TranslationParams) {
    return template.replace(PLACEHOLDER_PATTERN, (_, name: string) => {
        if (params && Object.prototype.hasOwnProperty.call(params, name)) {
            return String(params[name]);
        }
        failOrWarn(`[admin-i18n] Missing interpolation param "${name}" for key "${key}".`);
        return key;
    });
}

export function isTranslationKey(value: string): value is TranslationKey {
    return TRANSLATION_KEYS.has(value as TranslationKey);
}

export function createTranslator(locale: Locale) {
    return (key: TranslationKey, params?: TranslationParams) =>
        applyParams(key, resolveMessage(locale, key), params);
}

export function resolveClientLocale(): Locale {
    if (typeof document !== "undefined") {
        const docLocale = parseLocale(document.documentElement.lang);
        if (docLocale) {
            return docLocale;
        }
    }
    if (typeof navigator !== "undefined") {
        const navLocale = parseLocale(navigator.language);
        if (navLocale) {
            return navLocale;
        }
    }
    return "zh-CN";
}

export function translateCurrentClient(key: TranslationKey, params?: TranslationParams) {
    return createTranslator(resolveClientLocale())(key, params);
}

export function resolveText(
    locale: Locale,
    value: string,
    params?: TranslationParams,
) {
    if (!value) {
        return value;
    }
    if (isTranslationKey(value)) {
        return createTranslator(locale)(value, params);
    }
    if (/^[a-z0-9]+(?:[._-][a-z0-9]+)+$/i.test(value)) {
        failOrWarn(`[admin-i18n] Unknown translation key "${value}" for locale "${locale}".`);
    }
    return value;
}

export function getTranslationVariants(value: string) {
    if (!value) {
        return [];
    }
    if (!isTranslationKey(value)) {
        return [value];
    }
    const zh = resolveMessage("zh-CN", value);
    const en = resolveMessage("en", value);
    return zh === en ? [zh] : [zh, en];
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
