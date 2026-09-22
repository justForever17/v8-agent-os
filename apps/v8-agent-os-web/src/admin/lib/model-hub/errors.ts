

export function extractErrorText(value: unknown, fallback: string): string {
    if (typeof value === "string" && value.trim())
        return value;
    if (Array.isArray(value)) {
        const joined = value
            .map((item) => extractErrorText(item, ""))
            .filter(Boolean)
            .join(" · ");
        return joined || fallback;
    }
    if (value && typeof value === "object") {
        const record = value as Record<string, unknown>;
        return (extractErrorText(record.error, "")
            || extractErrorText(record.message, "")
            || extractErrorText(record.detail, "")
            || fallback);
    }
    return fallback;
}

export async function readJsonErrorMessage(response: Response, fallback: string) {
    const data = await response.json().catch(() => null);
    const detail = extractErrorText(data?.detail, "") || extractErrorText(data?.error, "");
    return detail || fallback;
}
