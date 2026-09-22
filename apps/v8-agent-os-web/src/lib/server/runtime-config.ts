import { resolveProductOrigin } from "./product-origin";
import { readCanonicalBridge } from "@/lib/server/bridge-config";

type BridgeConfig = {
    engineBaseUrl?: string;
    engineWsBaseUrl?: string;
    adminBaseUrl?: string;
    adminApiBaseUrl?: string;
    internalSecret?: string;
    allowedOrigins?: string[];
    version?: string;
    reachable?: boolean;
};

const DEFAULT_ENGINE_BASE_URL = "http://127.0.0.1:9530/v1";
export const ADMIN_CONNECTION_COOKIE = "v8-agent-os_admin_connection";

function normalizeUrl(value: unknown, fallback: string) {
    const normalized = String(value || "").trim() || fallback;
    return normalized.replace(/\/$/, "");
}

function getBridge(): BridgeConfig {
    return readCanonicalBridge();
}

async function getResolvedBridge(): Promise<BridgeConfig> {
    // Browser cookies must not choose a destination for the local service key.
    return getBridge();
}

export async function resolveEngineBaseUrl() {
    const bridge = await getResolvedBridge();
    const value = normalizeUrl(process.env.V8_ENGINE_BASE_URL || bridge.engineBaseUrl, DEFAULT_ENGINE_BASE_URL);
    const url = new URL(value);
    if (url.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)
        || url.username || url.password || url.search || url.hash || !["", "/", "/v1"].includes(url.pathname)) {
        throw new Error("Local Engine URL must be a loopback origin");
    }
    return `${url.origin}/v1`;
}

export async function resolveEngineRootUrl() {
    const engineBaseUrl = await resolveEngineBaseUrl();
    try {
        const url = new URL(engineBaseUrl);
        const normalizedPath = url.pathname.replace(/\/+$/, "").replace(/\/v1$/, "");
        return normalizedPath ? `${url.origin}${normalizedPath}` : url.origin;
    } catch {
        return engineBaseUrl.replace(/\/+$/, "").replace(/\/v1$/, "");
    }
}

export async function resolveWorkspaceAssetBaseUrl() {
    return `${await resolveEngineRootUrl()}/workspace`;
}

export async function resolveEngineWsBaseUrl() {
    const bridge = await getResolvedBridge();
    const explicit = String(bridge.engineWsBaseUrl || "").trim();
    if (explicit) {
        return explicit.replace(/\/$/, "");
    }
    const engineBase = await resolveEngineBaseUrl();
    if (engineBase.startsWith("https://")) return engineBase.replace(/^https:\/\//, "wss://");
    if (engineBase.startsWith("http://")) return engineBase.replace(/^http:\/\//, "ws://");
    return engineBase;
}

export async function resolveAdminApiBaseUrl() {
    return `${resolveProductOrigin()}/api/admin`;
}

export async function resolveAdminRootUrl() {
    return resolveProductOrigin();
}

export function resolveLocalAdminRootUrl() {
    return resolveProductOrigin();
}

export async function resolveInternalSecret() {
    const bridge = await getResolvedBridge();
    return String(bridge.internalSecret || "").trim();
}

export async function getEngineProxyConfig() {
    return {
        engineBaseUrl: await resolveEngineBaseUrl(),
        engineWsBaseUrl: await resolveEngineWsBaseUrl(),
        internalSecret: await resolveInternalSecret(),
    };
}

export async function getClientProxyConfig() {
    return {
        engineBaseUrl: await resolveEngineBaseUrl(),
        clientApiBaseUrl: `${await resolveEngineRootUrl()}/api/client`,
        internalSecret: await resolveInternalSecret(),
    };
}

export async function resolveClientApiBaseUrl() {
    return `${await resolveEngineRootUrl()}/api/client`;
}

