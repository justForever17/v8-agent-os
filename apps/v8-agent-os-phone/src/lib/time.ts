import { createTranslator } from "@/src/lib/locale";
import type { LocaleCode } from "@/src/providers/ui-prefs";

export function formatRelativeTime(
    value?: string | number | null,
    locale: LocaleCode = "zh-CN",
    nowMs = Date.now(),
) {
    const t = createTranslator(locale);
    const english = locale === "en";
    if (!value) return t("shared.time.just_now");
    const timestamp = typeof value === "number" ? value : Date.parse(value);
    if (Number.isNaN(timestamp)) return t("shared.time.just_now");
    const delta = Math.max(0, nowMs - timestamp);
    const minutes = Math.floor(delta / 60000);
    if (minutes < 1) return t("shared.time.just_now");
    if (minutes < 60) return t("shared.time.minutes_ago", { count: minutes });
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return t("shared.time.hours_ago", { count: hours });
    const days = Math.floor(hours / 24);
    if (days < 30) return t("shared.time.days_ago", { count: days });
    return new Date(timestamp).toLocaleDateString(english ? "en-US" : "zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
    });
}

export function formatClock(
    value?: string | number | null,
    locale: LocaleCode = "zh-CN",
) {
    const english = locale === "en";
    if (!value) return "";
    const timestamp = typeof value === "number" ? value : Date.parse(value);
    if (Number.isNaN(timestamp)) return "";
    return new Date(timestamp).toLocaleString(english ? "en-US" : "zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
    });
}

export function getDayGreeting(
    locale: LocaleCode = "zh-CN",
    now = new Date(),
) {
    const t = createTranslator(locale);
    const hour = now.getHours();
    if (hour < 11) return t("shared.time.good_morning");
    if (hour < 13) return t("shared.time.good_noon");
    if (hour < 18) return t("shared.time.good_afternoon");
    return t("shared.time.good_evening");
}
