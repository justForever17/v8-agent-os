export type AdminConnection = {
    adminBaseUrl: string;
    adminApiBaseUrl?: string;
    engineBaseUrl?: string;
    engineWsBaseUrl?: string;
    bridgeMode?: string;
    reachable?: boolean;
    version?: string;
};

export function normalizeAdminBaseUrl(rawValue: string) {
    const normalized = String(rawValue || "").trim().replace(/\/$/, "");
    if (!normalized) return "";
    return normalized.endsWith("/api") ? normalized.slice(0, -4) : normalized;
}

export function deriveAdminApiBaseUrl(rawValue: string) {
    const normalized = normalizeAdminBaseUrl(rawValue);
    return normalized ? `${normalized}/api` : "";
}

export function serializeAdminConnection(connection: AdminConnection) {
    return encodeURIComponent(JSON.stringify(connection));
}

export function parseAdminConnection(rawValue: string | null | undefined): AdminConnection | null {
    try {
        const parsed = JSON.parse(decodeURIComponent(String(rawValue || ""))) as AdminConnection;
        if (!parsed || typeof parsed !== "object") {
            return null;
        }
        if (!parsed.adminBaseUrl) {
            return null;
        }
        parsed.adminBaseUrl = normalizeAdminBaseUrl(parsed.adminBaseUrl);
        parsed.adminApiBaseUrl = deriveAdminApiBaseUrl(parsed.adminApiBaseUrl || parsed.adminBaseUrl);
        return parsed;
    } catch {
        return null;
    }
}

export function deriveAdminConnectionLabel(adminBaseUrl: string) {
    const normalized = normalizeAdminBaseUrl(adminBaseUrl);
    if (!normalized) return "未命名连接";
    try {
        const url = new URL(normalized);
        return url.host;
    } catch {
        return normalized;
    }
}
