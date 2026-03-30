import fs from "fs";
import os from "os";
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
};

const DEFAULT_ENGINE_BASE_URL = "http://127.0.0.1:9530/v1";
const DEFAULT_ADMIN_BASE_URL = "http://127.0.0.1:9528/api";
const DEFAULT_DESKTOP_LIVE_BRIDGE_BASE_URL = "http://127.0.0.1:8011/v1";

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
