import zhCatalog from "@/i18n/locales/zh-CN.json";
import { INTERNAL_READABLE } from "@/i18n/internal-readable";
import type { TranslationKey, TranslationParams } from "@/lib/locale";

export type TranslateFn = (key: TranslationKey, params?: TranslationParams) => string;

const GENERATED_PREFIX = "admin.generated.";
const zhEntries = Object.entries(zhCatalog) as Array<[TranslationKey, string]>;
const reverseCatalog = new Map<string, TranslationKey[]>();

for (const [key, message] of zhEntries) {
    const existing = reverseCatalog.get(message) || [];
    existing.push(key);
    reverseCatalog.set(message, existing);
}

function preferKey(keys: TranslationKey[]) {
    return [...keys].sort((left, right) => {
        const leftScore = left.startsWith("admin.generated.") ? 2 : 0;
        const rightScore = right.startsWith("admin.generated.") ? 2 : 0;
        return leftScore - rightScore || left.localeCompare(right);
    })[0];
}

export function ag(id: string): TranslationKey {
    return `${GENERATED_PREFIX}${String(id || "").trim()}` as TranslationKey;
}

export function tg(t: TranslateFn, id: string, params?: TranslationParams) {
    return t(ag(id), params);
}

export function ik(id: keyof typeof INTERNAL_READABLE): TranslationKey {
    const message = INTERNAL_READABLE[id];
    const keys = reverseCatalog.get(message);
    if (!keys?.length) {
        throw new Error(`[admin-i18n] INTERNAL_READABLE "${String(id)}" has no matching translation key.`);
    }
    return preferKey(keys);
}

export function ti(
    t: TranslateFn,
    id: keyof typeof INTERNAL_READABLE,
    params?: TranslationParams,
) {
    return t(ik(id), params);
}
