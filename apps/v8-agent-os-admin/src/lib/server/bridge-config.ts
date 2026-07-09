import fs from "fs";
import os from "os";
import path from "path";

export type BridgeConfig = {
    engineBaseUrl?: string;
    engineWsBaseUrl?: string;
    adminBaseUrl?: string;
    desktopLiveBridgeBaseUrl?: string;
    internalSecret?: string;
};

type SupervisorConfig = {
    profile?: {
        avatar?: string;
    };
};

export type CanonicalConfig = {
    runtimeRegistry?: {
        installProfile?: string;
        installPlatform?: string;
        installedRuntimeFamilies?: string[];
        featurePacks?: Record<string, {
            status?: string;
            targetDir?: string;
            logRef?: string | null;
            lastError?: string | null;
            updatedAt?: string | null;
            restartRequired?: boolean;
        }>;
        bootstrapManaged?: boolean;
        lastUpgradeAt?: string;
        startupProfile?: string;
    };
    systemBase?: {
        bridge?: BridgeConfig;
        channels?: {
            enginePython?: string;
        };
        desktopLive?: {
            enabled?: boolean;
            maxWidth?: number;
            maxHeight?: number;
            targetFps?: number;
            idleReleaseSeconds?: number;
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
        remoteLink?: {
            enabled?: boolean;
            activeProfileId?: string;
            transportProfiles?: Array<{
                id?: string;
                kind?: string;
                label?: string;
                enabled?: boolean;
                adminBaseUrl?: string;
                engineBaseUrl?: string;
                peerBaseUrl?: string;
            }>;
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
    };
    workspace?: {
        agent_workspace_path?: string;
    };
    supervisor?: SupervisorConfig;
    networkSupervisorRuntime?: {
        node?: {
            advertisedBaseUrl?: string;
            advertisedWsUrl?: string;
        };
    };
};

export type LegacyPortNotice = {
    code: "config_migrated" | "admin_env_legacy_ports" | "web_env_legacy_ports";
    path: string;
};

export type CanonicalConfigDiagnostics = {
    selectedConfigPath: string | null;
    migratedConfigPaths: string[];
    notices: LegacyPortNotice[];
};

const CANONICAL_CONFIG_PATH = path.join(
    process.env.V8_AGENT_OS_HOME || path.join(os.homedir(), ".v8-agent-os"),
    "config.json",
);

type JsonConfigCacheEntry = {
    mtimeMs: number;
    size: number;
    data: CanonicalConfig;
};

const jsonConfigCache = new Map<string, JsonConfigCacheEntry>();

export function invalidateCanonicalConfigCache(configPath = CANONICAL_CONFIG_PATH) {
    jsonConfigCache.delete(path.resolve(configPath));
}

export function canonicalConfigPath() {
    return CANONICAL_CONFIG_PATH;
}

function cloneConfig(data: CanonicalConfig): CanonicalConfig {
    return JSON.parse(JSON.stringify(data)) as CanonicalConfig;
}

function readJsonConfig(configPath: string): CanonicalConfig {
    const normalizedPath = path.resolve(configPath);
    try {
        const stat = fs.statSync(normalizedPath);
        const cached = jsonConfigCache.get(normalizedPath);
        if (cached && cached.mtimeMs === stat.mtimeMs && cached.size === stat.size) {
            return cloneConfig(cached.data);
        }
        const raw = fs.readFileSync(normalizedPath, "utf-8");
        const parsed = JSON.parse(raw) as CanonicalConfig;
        const data = parsed && typeof parsed === "object" ? parsed : {};
        jsonConfigCache.set(normalizedPath, {
            mtimeMs: stat.mtimeMs,
            size: stat.size,
            data: cloneConfig(data),
        });
        return cloneConfig(data);
    } catch {
        jsonConfigCache.delete(normalizedPath);
        return {};
    }
}

function resolveLocalEnvCandidates() {
    const adminRoot = process.cwd();
    const webRoot = path.resolve(adminRoot, "..", "v8-agent-os-web");
    return [
        { code: "admin_env_legacy_ports" as const, path: path.join(adminRoot, ".env") },
        { code: "admin_env_legacy_ports" as const, path: path.join(adminRoot, ".env.local") },
        { code: "web_env_legacy_ports" as const, path: path.join(webRoot, ".env") },
        { code: "web_env_legacy_ports" as const, path: path.join(webRoot, ".env.local") },
    ];
}

function hasLegacyLocalPorts(raw: string) {
    return /(127\.0\.0\.1|localhost):(8000|5001)\b/.test(String(raw || ""));
}

export function readCanonicalConfig(): CanonicalConfig {
    if (process.env.V8_NEXT_BUILD === "1") {
        return {};
    }
    return readJsonConfig(CANONICAL_CONFIG_PATH);
}

export function writeCanonicalConfig(config: CanonicalConfig) {
    const targetPath = CANONICAL_CONFIG_PATH;
    fs.mkdirSync(path.dirname(targetPath), { recursive: true });
    fs.writeFileSync(targetPath, `${JSON.stringify(config || {}, null, 2)}\n`, "utf-8");
    invalidateCanonicalConfigCache(targetPath);
}

export function readCanonicalConfigDiagnostics(): CanonicalConfigDiagnostics {
    const config = readJsonConfig(CANONICAL_CONFIG_PATH);
    const selectedConfigPath = Object.keys(config || {}).length ? CANONICAL_CONFIG_PATH : null;
    const notices: LegacyPortNotice[] = resolveLocalEnvCandidates().flatMap((item) => {
        if (!fs.existsSync(item.path)) {
            return [];
        }
        try {
            const raw = fs.readFileSync(item.path, "utf-8");
            return hasLegacyLocalPorts(raw) ? [{ code: item.code, path: item.path }] : [];
        } catch {
            return [];
        }
    });

    return {
        selectedConfigPath,
        migratedConfigPaths: [],
        notices,
    };
}

export function readCanonicalBridge(): BridgeConfig {
    return readCanonicalConfig().systemBase?.bridge || {};
}

export function readCanonicalAdminRuntimeConfig(): CanonicalConfig {
    return readCanonicalConfig();
}
