function getLocaleAwareFallback(fallback?: string) {
    if (fallback) return fallback;
    const locale = typeof navigator !== "undefined"
        ? navigator.language
        : Intl.DateTimeFormat().resolvedOptions().locale;
    return locale.toLowerCase().startsWith("zh") ? "刚刚" : "Just now";
}

function normalizeTimestampInput(value: string) {
    const normalized = String(value || "").trim();
    if (!normalized) return "";
    if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(normalized)) {
        return `${normalized.replace(" ", "T")}Z`;
    }
    if (
        /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(normalized)
        && !/(?:Z|[+-]\d{2}:\d{2})$/i.test(normalized)
    ) {
        return `${normalized}Z`;
    }
    return normalized;
}

export function formatLocalDateTime(
    value?: string,
    options?: {
        includeYear?: boolean;
        includeSeconds?: boolean;
        fallback?: string;
    }
) {
    const fallback = getLocaleAwareFallback(options?.fallback);
    if (!value) {
        return fallback;
    }
    const date = new Date(normalizeTimestampInput(value));
    if (Number.isNaN(date.getTime())) {
        return value;
    }
    return new Intl.DateTimeFormat(undefined, {
        year: options?.includeYear === false ? undefined : "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: options?.includeSeconds === false ? undefined : "2-digit",
        hour12: false,
    }).format(date);
}
