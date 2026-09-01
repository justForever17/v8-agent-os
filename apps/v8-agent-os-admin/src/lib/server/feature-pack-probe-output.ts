export function normalizeFeaturePackProbeOutput(value: string): string {
    return String(value || "")
        .replace(/\u0000/g, "")
        .replace(/\r\n?/g, "\n");
}

export function parseFeaturePackProbeMarker(
    output: string,
    prefix: string,
): Record<string, unknown> | null {
    for (const rawLine of normalizeFeaturePackProbeOutput(output).split("\n")) {
        const line = rawLine.replace(/^[\uFEFF\uFFFD]+/, "");
        const markerIndex = line.indexOf(prefix);
        if (markerIndex < 0 || line.slice(0, markerIndex).trim()) continue;
        try {
            const payload: unknown = JSON.parse(line.slice(markerIndex + prefix.length));
            if (payload && typeof payload === "object" && !Array.isArray(payload)) {
                return payload as Record<string, unknown>;
            }
        } catch {
            return null;
        }
    }
    return null;
}
