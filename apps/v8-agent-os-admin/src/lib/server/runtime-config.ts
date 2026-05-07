import fs from "fs";
import path from "path";
import {
    readCanonicalAdminRuntimeConfig,
    readCanonicalBridge,
    type CanonicalConfig,
} from "@/lib/server/bridge-config";

type DesktopLiveConfig = {
    enabled?: boolean;
    maxWidth?: number;
    maxHeight?: number;
    targetFps?: number;
    idleReleaseSeconds?: number;
    captureDisplay?: string;
    singleViewerOnly?: boolean;
};

type SystemBaseConfig = {
    bridge?: {
        engineBaseUrl?: string;
        engineWsBaseUrl?: string;
        adminBaseUrl?: string;
        desktopLiveBridgeBaseUrl?: string;
        internalSecret?: string;
    };
    channels?: {
        enginePython?: string;
    };
    desktopLive?: DesktopLiveConfig;
    remoteLink?: RemoteLinkConfig;
};

type RemoteLinkProfile = {
    id?: string;
    kind?: string;
    label?: string;
    enabled?: boolean;
    adminBaseUrl?: string;
    engineBaseUrl?: string;
    peerBaseUrl?: string;
};

type RemoteLinkConfig = {
    enabled?: boolean;
    activeProfileId?: string;
    transportProfiles?: RemoteLinkProfile[];
    meshProviders?: Array<{
        id?: string;
        kind?: string;
        enabled?: boolean;
        mode?: string;
        controlUrl?: string;
        namespace?: string;
        allowRouteMutation?: boolean;
    }>;
};

const DEFAULT_ENGINE_BASE_URL = "http://127.0.0.1:9530/v1";
const DEFAULT_ADMIN_BASE_URL = "http://127.0.0.1:9528/api";
const DEFAULT_DESKTOP_LIVE_BRIDGE_BASE_URL = "http://127.0.0.1:8011/v1";
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]", "::1"]);

function inferEnginePythonPath() {
    const repoRoots = [
        path.resolve(process.cwd(), "..", "v8-agent-os-engine"),
        path.resolve(process.cwd(), "..", "engine"),
    ];
    const candidates = repoRoots.flatMap((repoRoot) => (
        process.platform === "win32"
            ? [
                path.join(repoRoot, ".venv", "Scripts", "python.exe"),
                path.join(repoRoot, "venv", "Scripts", "python.exe"),
            ]
            : [
                path.join(repoRoot, ".venv", "bin", "python"),
                path.join(repoRoot, "venv", "bin", "python"),
            ]
    ));

    const detected = candidates.find((candidate) => fs.existsSync(candidate));
    return detected || "";
}

export function resolveConfigDomain<T>(domain: keyof CanonicalConfig, fallback: T): T {
    const config = readCanonicalAdminRuntimeConfig();
    const payload = config[domain];
    if (payload && typeof payload === "object") {
        return payload as T;
    }
    return fallback;
}

function normalizeUrl(value: unknown, fallback: string) {
    const normalized = String(value || "").trim() || fallback;
    return normalized.replace(/\/$/, "");
}

function getBridge() {
    return readCanonicalBridge();
}

export function resolveEngineBaseUrl() {
    return normalizeUrl(getBridge().engineBaseUrl, DEFAULT_ENGINE_BASE_URL);
}

export function resolveEngineOrigin() {
    return resolveEngineBaseUrl().replace(/\/v1$/, "");
}

export function resolveEngineWsBaseUrl() {
    const explicit = String(getBridge().engineWsBaseUrl || "").trim();
    if (explicit) {
        return explicit.replace(/\/$/, "");
    }
    const engineBase = resolveEngineBaseUrl();
    if (engineBase.startsWith("https://")) return engineBase.replace(/^https:\/\//, "wss://");
    if (engineBase.startsWith("http://")) return engineBase.replace(/^http:\/\//, "ws://");
    return engineBase;
}

export function resolveAdminApiBaseUrl() {
    return normalizeUrl(getBridge().adminBaseUrl, DEFAULT_ADMIN_BASE_URL);
}

export function resolveDesktopLiveBridgeBaseUrl() {
    return normalizeUrl(getBridge().desktopLiveBridgeBaseUrl, DEFAULT_DESKTOP_LIVE_BRIDGE_BASE_URL);
}

export function resolveAdminPublicBaseUrl() {
    const apiBaseUrl = resolveAdminApiBaseUrl();
    return apiBaseUrl.endsWith("/api") ? apiBaseUrl.slice(0, -4) : apiBaseUrl;
}

function stripApiSuffix(value: unknown) {
    const raw = String(value || "").trim().replace(/\/$/, "");
    if (raw.endsWith("/api")) return raw.slice(0, -4);
    if (raw.endsWith("/v1")) return raw.slice(0, -3);
    return raw;
}

function withApiSuffix(value: unknown, suffix: string) {
    const base = stripApiSuffix(value);
    return base ? `${base}/${suffix.replace(/^\/+/, "")}` : "";
}

function normalizeTransportKind(value: unknown) {
    const normalized = String(value || "").trim().toLowerCase().replace(/-/g, "_");
    return ["manual_url", "lan", "wireguard", "tailscale", "headscale", "custom_vpn"].includes(normalized)
        ? normalized
        : "manual_url";
}

export function buildAdminLinkManifest(requestOrigin?: string) {
    const config = readCanonicalAdminRuntimeConfig();
    const systemBase = (config.systemBase || {}) as SystemBaseConfig;
    const remoteLink = systemBase.remoteLink || {};
    const adminBaseUrl = stripApiSuffix(requestOrigin || resolveAdminApiBaseUrl());
    const engineBaseUrl = stripApiSuffix(resolveEngineBaseUrl());
    const defaultProfiles: RemoteLinkProfile[] = [
        { id: "manual-local", kind: "manual_url", label: "Manual / Local", enabled: true, adminBaseUrl, engineBaseUrl, peerBaseUrl: engineBaseUrl },
        { id: "lan", kind: "lan", label: "LAN", enabled: true },
        { id: "wireguard", kind: "wireguard", label: "WireGuard", enabled: true },
        { id: "tailscale", kind: "tailscale", label: "Tailscale", enabled: true },
        { id: "headscale", kind: "headscale", label: "Headscale", enabled: true },
        { id: "custom-vpn", kind: "custom_vpn", label: "Custom VPN", enabled: true },
    ];
    const profilesById = new Map<string, RemoteLinkProfile>();
    defaultProfiles.forEach((profile) => profile.id && profilesById.set(profile.id, profile));
    (remoteLink.transportProfiles || []).forEach((profile) => {
        const id = String(profile?.id || "").trim();
        if (!id) return;
        const merged = { ...(profilesById.get(id) || {}), ...profile };
        merged.kind = normalizeTransportKind(merged.kind);
        merged.adminBaseUrl = stripApiSuffix(merged.adminBaseUrl || "");
        merged.engineBaseUrl = stripApiSuffix(merged.engineBaseUrl || "");
        merged.peerBaseUrl = stripApiSuffix(merged.peerBaseUrl || "");
        profilesById.set(id, merged);
    });
    const profiles = Array.from(profilesById.values());
    const activeProfileId = String(remoteLink.activeProfileId || "manual-local");
    const activeProfile = profiles.find((profile) => profile.id === activeProfileId) || profiles[0] || {};
    const transportKind = normalizeTransportKind(activeProfile.kind);
    const warnings = [
        adminBaseUrl.match(/^https?:\/\/(127\.|localhost|\[::1\]|::1)/i) ? "admin_loopback_not_reachable_from_phone" : "",
        engineBaseUrl.match(/^https?:\/\/(127\.|localhost|\[::1\]|::1)/i) ? "engine_loopback_not_reachable_from_phone" : "",
    ].filter(Boolean);
    return {
        ok: true,
        kind: "v8_link_manifest",
        version: "1",
        transportKind,
        activeProfileId: activeProfile.id || activeProfileId,
        admin: {
            baseUrl: adminBaseUrl,
            apiBaseUrl: withApiSuffix(adminBaseUrl, "api"),
            configuredApiBaseUrl: resolveAdminApiBaseUrl(),
        },
        engine: {
            baseUrl: engineBaseUrl,
            apiBaseUrl: withApiSuffix(engineBaseUrl, "v1"),
            directExposure: false,
        },
        profiles: profiles.map((profile) => ({
            id: profile.id || "",
            kind: normalizeTransportKind(profile.kind),
            label: profile.label || profile.id || "",
            enabled: profile.enabled !== false,
            adminBaseUrl: stripApiSuffix(profile.adminBaseUrl || ""),
            engineBaseUrl: stripApiSuffix(profile.engineBaseUrl || ""),
            peerBaseUrl: stripApiSuffix(profile.peerBaseUrl || ""),
        })),
        capabilities: {
            adminProxy: true,
            phoneUpload: true,
            artifactPreview: true,
            runtimeEvents: true,
            networkSupervisorPeers: true,
        },
        meshProviders: (remoteLink.meshProviders || []).map((provider) => ({
            id: provider.id || provider.kind || "",
            kind: provider.kind || provider.id || "",
            enabled: provider.enabled !== false,
            mode: provider.mode || "detect_only",
            allowRouteMutation: false,
        })),
        diagnostics: {
            readOnly: true,
            warnings,
        },
        warnings,
    };
}

export function isReachableClientSurfaceOrigin(baseUrl: string) {
    const normalized = String(baseUrl || "").trim();
    if (!normalized) {
        return false;
    }
    try {
        const parsed = new URL(normalized);
        return !LOOPBACK_HOSTS.has(parsed.hostname || "");
    } catch {
        return false;
    }
}

export function resolveReachableClientSurfaceOrigin(candidate: string) {
    const normalized = String(candidate || "").trim().replace(/\/$/, "");
    return isReachableClientSurfaceOrigin(normalized) ? normalized : "";
}

function pickForwardedHeaderValue(value: string | null | undefined) {
    const normalized = String(value || "").trim();
    if (!normalized) {
        return "";
    }
    return normalized.split(",")[0]?.trim() || "";
}

function normalizeForwardedProtocol(value: string | null | undefined, fallback = "http") {
    const normalized = pickForwardedHeaderValue(value).toLowerCase();
    if (normalized === "https" || normalized === "http") {
        return normalized;
    }
    return fallback;
}

export function resolveReachableClientSurfaceOriginFromRequest(requestUrl: string) {
    try {
        return resolveReachableClientSurfaceOrigin(new URL(String(requestUrl || "")).origin);
    } catch {
        return "";
    }
}

export function resolveClientSurfaceOriginFromRequest(
    request:
        | {
            headers?: Headers | HeadersInit | null;
            url?: string | null;
        }
        | string,
    options?: {
        allowTrustedHeader?: boolean;
    },
) {
    const requestUrl = typeof request === "string"
        ? request
        : String(request?.url || "").trim();
    const fallbackProtocol = (() => {
        try {
            const parsed = new URL(String(requestUrl || ""));
            return parsed.protocol === "https:" ? "https" : "http";
        } catch {
            return "http";
        }
    })();

    const requestHeaders = typeof request === "string"
        ? null
        : new Headers(request?.headers || undefined);

    if (requestHeaders) {
        if (options?.allowTrustedHeader !== false) {
            const trustedOrigin = resolveReachableClientSurfaceOrigin(
                pickForwardedHeaderValue(requestHeaders.get("x-v8-client-surface-origin")),
            );
            if (trustedOrigin) {
                return trustedOrigin;
            }
        }

        const forwardedHost = pickForwardedHeaderValue(requestHeaders.get("x-forwarded-host"));
        if (forwardedHost) {
            const forwardedProtocol = normalizeForwardedProtocol(
                requestHeaders.get("x-forwarded-proto"),
                fallbackProtocol,
            );
            const forwardedOrigin = resolveReachableClientSurfaceOrigin(`${forwardedProtocol}://${forwardedHost}`);
            if (forwardedOrigin) {
                return forwardedOrigin;
            }
        }

        const host = pickForwardedHeaderValue(requestHeaders.get("host"));
        if (host) {
            const hostProtocol = normalizeForwardedProtocol(
                requestHeaders.get("x-forwarded-proto"),
                fallbackProtocol,
            );
            const hostOrigin = resolveReachableClientSurfaceOrigin(`${hostProtocol}://${host}`);
            if (hostOrigin) {
                return hostOrigin;
            }
        }
    }

    return resolveReachableClientSurfaceOriginFromRequest(requestUrl);
}

export function resolveReachableAdminPublicBaseUrl() {
    const publicBase = resolveAdminPublicBaseUrl();
    return resolveReachableClientSurfaceOrigin(publicBase);
}

export function resolveInternalSecret() {
    return String(getBridge().internalSecret || "").trim();
}

export function resolveEnginePythonPath() {
    const config = readCanonicalAdminRuntimeConfig();
    const explicit = String(config.systemBase?.channels?.enginePython || "").trim();
    return explicit || inferEnginePythonPath();
}

export function resolveDesktopLiveConfig(): DesktopLiveConfig {
    const config = readCanonicalAdminRuntimeConfig();
    return config.systemBase?.desktopLive || {};
}
