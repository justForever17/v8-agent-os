import { cookies } from "next/headers";
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
const DEFAULT_ADMIN_BASE_URL = "http://127.0.0.1:9528/api";
export const ADMIN_CONNECTION_COOKIE = "v8-agent-os_admin_connection";

function normalizeUrl(value: unknown, fallback: string) {
    const normalized = String(value || "").trim() || fallback;
    return normalized.replace(/\/$/, "");
}

function getBridge(): BridgeConfig {
    return readCanonicalBridge();
}

async function readRequestScopedBridge(): Promise<BridgeConfig> {
    try {
        const cookieStore = await cookies();
        const raw = cookieStore.get(ADMIN_CONNECTION_COOKIE)?.value || "";
        if (!raw) return {};
        const parsed = JSON.parse(decodeURIComponent(raw)) as BridgeConfig;
        return parsed && typeof parsed === "object" ? parsed : {};
    } catch {
        return {};
    }
}

async function getResolvedBridge(): Promise<BridgeConfig> {
    const localBridge = getBridge();
    const requestBridge = await readRequestScopedBridge();
    return {
        ...localBridge,
        ...requestBridge,
    };
}

export async function resolveEngineBaseUrl() {
    const bridge = await getResolvedBridge();
    return normalizeUrl(bridge.engineBaseUrl, DEFAULT_ENGINE_BASE_URL);
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
    const bridge = await getResolvedBridge();
    const explicitApiBase = String(bridge.adminApiBaseUrl || "").trim();
    if (explicitApiBase) {
        return normalizeUrl(explicitApiBase, DEFAULT_ADMIN_BASE_URL);
    }

    const adminBase = String(bridge.adminBaseUrl || "").trim();
    if (adminBase) {
        const normalized = adminBase.endsWith("/api") ? adminBase : `${adminBase}/api`;
        return normalizeUrl(normalized, DEFAULT_ADMIN_BASE_URL);
    }

    return DEFAULT_ADMIN_BASE_URL;
}

export async function resolveInternalSecret() {
    const bridge = await getResolvedBridge();
    return String(bridge.internalSecret || "").trim();
}

export async function getAdminProxyConfig() {
    return {
        adminApiBaseUrl: await resolveAdminApiBaseUrl(),
        internalSecret: await resolveInternalSecret(),
    };
}

export async function getEngineProxyConfig() {
    return {
        engineBaseUrl: await resolveEngineBaseUrl(),
        engineWsBaseUrl: await resolveEngineWsBaseUrl(),
        internalSecret: await resolveInternalSecret(),
    };
}

