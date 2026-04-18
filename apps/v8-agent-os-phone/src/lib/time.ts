import type { LocaleCode } from "@/src/providers/ui-prefs";

function isEnglishLocale(locale: LocaleCode | string | null | undefined) {
    return String(locale || "").toLowerCase().startsWith("en");
}

export function formatRelativeTime(
    value?: string | number | null,
    locale: LocaleCode = "zh-CN",
    nowMs = Date.now(),
) {
    if (!value) return isEnglishLocale(locale) ? "Just now" : "刚刚";
    const timestamp = typeof value === "number" ? value : Date.parse(value);
    if (Number.isNaN(timestamp)) return isEnglishLocale(locale) ? "Just now" : "刚刚";
    const delta = Math.max(0, nowMs - timestamp);
    const minutes = Math.floor(delta / 60000);
    if (minutes < 1) return isEnglishLocale(locale) ? "Just now" : "刚刚";
    if (minutes < 60) return isEnglishLocale(locale) ? `${minutes}m ago` : `${minutes} 分钟前`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return isEnglishLocale(locale) ? `${hours}h ago` : `${hours} 小时前`;
    const days = Math.floor(hours / 24);
    if (days < 30) return isEnglishLocale(locale) ? `${days}d ago` : `${days} 天前`;
    return new Date(timestamp).toLocaleDateString(isEnglishLocale(locale) ? "en-US" : "zh-CN", {
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
    if (!value) return "";
    const timestamp = typeof value === "number" ? value : Date.parse(value);
    if (Number.isNaN(timestamp)) return "";
    return new Date(timestamp).toLocaleString(isEnglishLocale(locale) ? "en-US" : "zh-CN", {
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
    const hour = now.getHours();
    if (isEnglishLocale(locale)) {
        if (hour < 12) return "Good morning";
        if (hour < 18) return "Good afternoon";
        return "Good evening";
    }
    if (hour < 11) return "早上好";
    if (hour < 13) return "中午好";
    if (hour < 18) return "下午好";
    return "晚上好";
}
