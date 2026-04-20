import enCatalog from "@/src/i18n/locales/en.json";
import zhCNCatalog from "@/src/i18n/locales/zh-CN.json";

export type LocaleCode = "zh-CN" | "en";
export type TranslationParams = Record<string, string | number>;

export const TRANSLATION_CATALOG = {
    "zh-CN": zhCNCatalog,
    en: enCatalog,
} as const;

export type TranslationKey = keyof typeof zhCNCatalog & string;

const TRANSLATION_KEYS = new Set<TranslationKey>(Object.keys(zhCNCatalog) as TranslationKey[]);
const PLACEHOLDER_PATTERN = /\{([a-zA-Z0-9_]+)\}/g;

let activeLocale: LocaleCode = "zh-CN";

function failOrWarn(message: string) {
    if (typeof __DEV__ !== "undefined" && __DEV__) {
        throw new Error(message);
    }
    console.warn(message);
}

function resolveMessage(locale: LocaleCode, key: TranslationKey) {
    const catalog = TRANSLATION_CATALOG[locale] as Record<string, string | undefined>;
    const fallbackCatalog = TRANSLATION_CATALOG["zh-CN"] as Record<string, string | undefined>;
    const message = catalog[key] ?? fallbackCatalog[key];
    if (typeof message === "string") {
        return message;
    }
    failOrWarn(`[phone-i18n] Missing translation key "${key}" for locale "${locale}".`);
    return key;
}

function applyParams(key: TranslationKey, template: string, params?: TranslationParams) {
    return template.replace(PLACEHOLDER_PATTERN, (_, name: string) => {
        if (params && Object.prototype.hasOwnProperty.call(params, name)) {
            return String(params[name]);
        }
        failOrWarn(`[phone-i18n] Missing interpolation param "${name}" for key "${key}".`);
        return key;
    });
}

export function parseLocale(value: string | null | undefined): LocaleCode | null {
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

export function isTranslationKey(value: string): value is TranslationKey {
    return TRANSLATION_KEYS.has(value as TranslationKey);
}

export function createTranslator(locale: LocaleCode) {
    return (key: TranslationKey, params?: TranslationParams) =>
        applyParams(key, resolveMessage(locale, key), params);
}

export function resolveText(locale: LocaleCode, value: string, params?: TranslationParams) {
    if (!value) {
        return value;
    }
    if (isTranslationKey(value)) {
        return createTranslator(locale)(value, params);
    }
    if (/^[a-z0-9]+(?:[._-][a-z0-9]+)+$/i.test(value)) {
        failOrWarn(`[phone-i18n] Unknown translation key "${value}" for locale "${locale}".`);
    }
    return value;
}

export function setActiveLocale(locale: LocaleCode) {
    activeLocale = locale;
}

export function getActiveLocale() {
    return activeLocale;
}

export function translateCurrent(value: TranslationKey | string, params?: TranslationParams) {
    return resolveText(activeLocale, String(value), params);
}
