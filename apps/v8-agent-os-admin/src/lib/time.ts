export function formatLocalDateTime(
    value?: string,
    options?: {
        includeYear?: boolean;
        includeSeconds?: boolean;
        fallback?: string;
    }
) {
    const fallback = options?.fallback ?? "刚刚";
    if (!value) {
        return fallback;
    }
    const date = new Date(value);
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
