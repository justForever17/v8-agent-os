import fs from "fs";
import os from "os";
import path from "path";
import {
    readCanonicalAdminRuntimeConfig,
    readCanonicalBridge,
    type CanonicalConfig,
} from "@/lib/server/bridge-config";
import { readOrCreateInstanceIdentity } from "@/lib/server/instance-identity";

type DesktopLiveConfig = {
    enabled?: boolean;
    maxWidth?: number;
    maxHeight?: number;
    targetFps?: number;
    idleReleaseSeconds?: number;
    keepWarmStandby?: boolean;
    autoWarmOnStatus?: boolean;
    captureDisplay?: string;
    singleViewerOnly?: boolean;
    audioEnabled?: boolean;
    audioSource?: string;
    audioSampleRate?: number;
    audioChannels?: number;
    iceServers?: Array<{
        urls?: string | string[];
        username?: string;
        credential?: string;
    }>;
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
const NON_ROUTABLE_CLIENT_HOSTS = new Set([
    "0.0.0.0",
    "127.0.0.1",
    "localhost",
    "[::]",
    "::",
    "[::1]",
    "::1",
]);

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

function buildRemoteLinkContext(requestOrigin?: string) {
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
    return {
        requestAdminBaseUrl: adminBaseUrl,
        engineBaseUrl,
        profiles,
        activeProfile,
        activeProfileId,
        transportKind,
        remoteLink,
    };
}

function isTailscaleIpv4(address: string) {
    return /^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./.test(address);
}

function resolveActiveRemoteLinkAdminBaseUrl(requestOrigin?: string) {
    const context = buildRemoteLinkContext(requestOrigin);
    const activeProfile = context.activeProfile || {};
    if (activeProfile.enabled === false) {
        return "";
    }
    const configured = resolveReachableClientSurfaceOrigin(stripApiSuffix(activeProfile.adminBaseUrl || ""));
    if (configured) {
        return configured;
    }
    const kind = normalizeTransportKind(activeProfile.kind);
    if (kind === "lan" || kind === "wireguard" || kind === "tailscale" || kind === "headscale" || kind === "custom_vpn") {
        return resolveLocalNetworkAdminOrigin(requestOrigin, kind);
    }
    return "";
}

export function buildAdminLinkManifest(requestOrigin?: string) {
    const identity = readOrCreateInstanceIdentity();
    const context = buildRemoteLinkContext(requestOrigin);
    const adminBaseUrl = resolveActiveRemoteLinkAdminBaseUrl(requestOrigin) || context.requestAdminBaseUrl;
    const engineBaseUrl = context.engineBaseUrl;
    const profiles = context.profiles;
    const activeProfile = context.activeProfile;
    const activeProfileId = context.activeProfileId;
    const transportKind = context.transportKind;
    const warnings = [
        adminBaseUrl.match(/^https?:\/\/(127\.|localhost|\[::1\]|::1)/i) ? "admin_loopback_not_reachable_from_phone" : "",
        engineBaseUrl.match(/^https?:\/\/(127\.|localhost|\[::1\]|::1)/i) ? "engine_loopback_not_reachable_from_phone" : "",
    ].filter(Boolean);
    return {
        ok: true,
        kind: "v8_link_manifest",
        version: "1",
        instanceId: identity.instanceId,
        ownerMode: "single_owner",
        clientGateway: "admin_bff",
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
            pairing: true,
            publicRegistration: false,
            phoneUpload: true,
            artifactPreview: true,
            runtimeEvents: true,
            networkSupervisorPeers: true,
        },
        meshProviders: (context.remoteLink.meshProviders || []).map((provider) => ({
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

export function buildClientLinkManifest(requestOrigin?: string) {
    const manifest = buildAdminLinkManifest(requestOrigin);
    return {
        ok: true,
        kind: "v8_client_link_manifest",
        version: manifest.version,
        serverId: manifest.instanceId,
        instanceId: manifest.instanceId,
        ownerMode: manifest.ownerMode,
        clientGateway: manifest.clientGateway,
        transportKind: manifest.transportKind,
        activeProfileId: manifest.activeProfileId,
        admin: {
            baseUrl: manifest.admin.baseUrl,
            apiBaseUrl: manifest.admin.apiBaseUrl,
        },
        profiles: manifest.profiles.map((profile) => ({
            id: profile.id,
            kind: profile.kind,
            label: profile.label,
            enabled: profile.enabled,
            adminBaseUrl: profile.adminBaseUrl,
        })),
        capabilities: manifest.capabilities,
        diagnostics: manifest.diagnostics,
        warnings: manifest.warnings,
    };
}

export function isReachableClientSurfaceOrigin(baseUrl: string) {
    const normalized = String(baseUrl || "").trim();
    if (!normalized) {
        return false;
    }
    try {
        const parsed = new URL(normalized);
        return !NON_ROUTABLE_CLIENT_HOSTS.has(parsed.hostname || "");
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

export function resolveRequestOrigin(request: { headers?: Headers | HeadersInit | null; url?: string | null }) {
    const requestUrl = String(request?.url || "").trim();
    const fallback = (() => {
        try {
            return new URL(requestUrl).origin;
        } catch {
            return "";
        }
    })();
    const headers = new Headers(request?.headers || undefined);
    const forwardedHost = pickForwardedHeaderValue(headers.get("x-forwarded-host"));
    const host = forwardedHost || pickForwardedHeaderValue(headers.get("host"));
    if (!host) return fallback;
    const fallbackProtocol = fallback.startsWith("https://") ? "https" : "http";
    const protocol = normalizeForwardedProtocol(headers.get("x-forwarded-proto"), fallbackProtocol);
    return `${protocol}://${host}`;
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

function isPrivateIpv4(address: string) {
    if (/^10\./.test(address) || /^192\.168\./.test(address)) {
        return true;
    }
    const match = address.match(/^172\.(\d+)\./);
    if (match) {
        const second = Number(match[1]);
        return second >= 16 && second <= 31;
    }
    return false;
}

function scoreLocalIpv4(address: string, preferredKind?: string) {
    const normalizedKind = normalizeTransportKind(preferredKind);
    if ((normalizedKind === "tailscale" || normalizedKind === "headscale") && isTailscaleIpv4(address)) {
        return 200;
    }
    if (normalizedKind === "lan" && isPrivateIpv4(address)) {
        return 200;
    }
    if (isPrivateIpv4(address)) return 100;
    if (isTailscaleIpv4(address)) return 80;
    if (/^198\.(1[89])\./.test(address)) return 20;
    return 50;
}

function resolveLocalNetworkAdminOrigin(requestOrigin?: string, preferredKind?: string) {
    let protocol = "http:";
    let port = "9528";
    try {
        const parsed = new URL(String(requestOrigin || resolveAdminPublicBaseUrl() || ""));
        protocol = parsed.protocol || protocol;
        port = parsed.port || (parsed.protocol === "https:" ? "443" : "80");
    } catch {
        // Keep defaults.
    }

    const candidates = Object.values(os.networkInterfaces())
        .flat()
        .filter((entry): entry is os.NetworkInterfaceInfo => Boolean(entry))
        .filter((entry) => entry.family === "IPv4" && !entry.internal)
        .map((entry) => entry.address)
        .filter((address) => address && !address.startsWith("169.254."))
        .sort((left, right) => scoreLocalIpv4(right, preferredKind) - scoreLocalIpv4(left, preferredKind));

    const selected = candidates[0];
    if (!selected) {
        return "";
    }
    const suffix = port && !["80", "443"].includes(port) ? `:${port}` : "";
    return `${protocol}//${selected}${suffix}`;
}

export function resolvePairingAdminBaseUrlFromRequest(
    request:
        | {
            headers?: Headers | HeadersInit | null;
            url?: string | null;
        }
        | string,
) {
    const requestOrigin = resolveRequestOrigin(typeof request === "string" ? { url: request } : request);
    return (
        resolveActiveRemoteLinkAdminBaseUrl(requestOrigin)
        ||
        resolveClientSurfaceOriginFromRequest(request, { allowTrustedHeader: true })
        || resolveReachableAdminPublicBaseUrl()
        || resolveLocalNetworkAdminOrigin(requestOrigin)
        || stripApiSuffix(requestOrigin || resolveAdminPublicBaseUrl())
    ).replace(/\/$/, "");
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
