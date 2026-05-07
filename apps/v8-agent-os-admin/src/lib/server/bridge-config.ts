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

const CANONICAL_CONFIG_PATH = path.join(os.homedir(), ".v8-agent-os", "config.json");

function readJsonConfig(configPath: string): CanonicalConfig {
    try {
        if (!fs.existsSync(configPath)) {
            return {};
        }
        const raw = fs.readFileSync(configPath, "utf-8");
        const parsed = JSON.parse(raw) as CanonicalConfig;
        return parsed && typeof parsed === "object" ? parsed : {};
    } catch {
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
    return readJsonConfig(CANONICAL_CONFIG_PATH);
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
