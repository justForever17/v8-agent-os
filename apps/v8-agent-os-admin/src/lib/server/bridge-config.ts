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

const LEGACY_LOCAL_ENGINE_BASES = new Map<string, string>([
    ["http://127.0.0.1:8000/v1", "http://127.0.0.1:9530/v1"],
    ["http://localhost:8000/v1", "http://127.0.0.1:9530/v1"],
]);

const LEGACY_LOCAL_ENGINE_WS_BASES = new Map<string, string>([
    ["ws://127.0.0.1:8000/v1", "ws://127.0.0.1:9530/v1"],
    ["ws://localhost:8000/v1", "ws://127.0.0.1:9530/v1"],
]);

const LEGACY_LOCAL_ADMIN_BASES = new Map<string, string>([
    ["http://127.0.0.1:5001/api", "http://127.0.0.1:9528/api"],
    ["http://localhost:5001/api", "http://127.0.0.1:9528/api"],
]);

const LEGACY_NETWORK_BASES = new Map<string, string>([
    ["http://127.0.0.1:8000", "http://127.0.0.1:9530"],
    ["http://localhost:8000", "http://127.0.0.1:9530"],
]);

const LEGACY_NETWORK_WS_BASES = new Map<string, string>([
    ["ws://127.0.0.1:8000", "ws://127.0.0.1:9530/v1/network-supervisor/peer/ws"],
    ["ws://localhost:8000", "ws://127.0.0.1:9530/v1/network-supervisor/peer/ws"],
    ["ws://127.0.0.1:8000/v1/network-supervisor/peer/ws", "ws://127.0.0.1:9530/v1/network-supervisor/peer/ws"],
    ["ws://localhost:8000/v1/network-supervisor/peer/ws", "ws://127.0.0.1:9530/v1/network-supervisor/peer/ws"],
    ["ws://127.0.0.1:9530", "ws://127.0.0.1:9530/v1/network-supervisor/peer/ws"],
    ["ws://localhost:9530", "ws://127.0.0.1:9530/v1/network-supervisor/peer/ws"],
]);

const LEGACY_ADMIN_ORIGINS = [
    "http://127.0.0.1:5001",
    "http://localhost:5001",
];

const CURRENT_ADMIN_ORIGIN = "http://127.0.0.1:9528";

function normalizeUrl(value: unknown) {
    return String(value || "").trim().replace(/\/$/, "");
}

function resolveCanonicalConfigPaths() {
    return [
        path.join(os.homedir(), ".v8-agent-os", "config.json"),
        path.join(os.homedir(), ".v8chat", "config.json"),
    ];
}

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

function maybeReplace(value: unknown, replacements: Map<string, string>) {
    const normalized = normalizeUrl(value);
    return replacements.get(normalized) || normalized;
}

function maybeReplaceAvatar(value: unknown) {
    const normalized = String(value || "").trim();
    if (!normalized) {
        return normalized;
    }
    const matchedOrigin = LEGACY_ADMIN_ORIGINS.find((origin) => normalized.startsWith(origin));
    if (!matchedOrigin) {
        return normalized;
    }
    const suffix = normalized.slice(matchedOrigin.length);
    if (!suffix.startsWith("/Avatar/")) {
        return normalized;
    }
    return `${CURRENT_ADMIN_ORIGIN}${suffix}`;
}

function migrateLegacyLocalConfig(configPath: string, config: CanonicalConfig): { config: CanonicalConfig; changed: boolean } {
    const next = JSON.parse(JSON.stringify(config || {})) as CanonicalConfig;
    let changed = false;

    const bridge = next.systemBase?.bridge;
    if (bridge) {
        const engineBaseUrl = maybeReplace(bridge.engineBaseUrl, LEGACY_LOCAL_ENGINE_BASES);
        const engineWsBaseUrl = maybeReplace(bridge.engineWsBaseUrl, LEGACY_LOCAL_ENGINE_WS_BASES);
        const adminBaseUrl = maybeReplace(bridge.adminBaseUrl, LEGACY_LOCAL_ADMIN_BASES);
        if (engineBaseUrl && engineBaseUrl !== normalizeUrl(bridge.engineBaseUrl)) {
            bridge.engineBaseUrl = engineBaseUrl;
            changed = true;
        }
        if (engineWsBaseUrl && engineWsBaseUrl !== normalizeUrl(bridge.engineWsBaseUrl)) {
            bridge.engineWsBaseUrl = engineWsBaseUrl;
            changed = true;
        }
        if (adminBaseUrl && adminBaseUrl !== normalizeUrl(bridge.adminBaseUrl)) {
            bridge.adminBaseUrl = adminBaseUrl;
            changed = true;
        }
    }

    const networkNode = next.networkSupervisorRuntime?.node;
    if (networkNode) {
        const advertisedBaseUrl = maybeReplace(networkNode.advertisedBaseUrl, LEGACY_NETWORK_BASES);
        const advertisedWsUrl = maybeReplace(networkNode.advertisedWsUrl, LEGACY_NETWORK_WS_BASES);
        if (advertisedBaseUrl && advertisedBaseUrl !== normalizeUrl(networkNode.advertisedBaseUrl)) {
            networkNode.advertisedBaseUrl = advertisedBaseUrl;
            changed = true;
        }
        if (advertisedWsUrl && advertisedWsUrl !== normalizeUrl(networkNode.advertisedWsUrl)) {
            networkNode.advertisedWsUrl = advertisedWsUrl;
            changed = true;
        }
    }

    const avatar = maybeReplaceAvatar(next.supervisor?.profile?.avatar);
    if (avatar && avatar !== String(next.supervisor?.profile?.avatar || "").trim()) {
        next.supervisor = next.supervisor || {};
        next.supervisor.profile = next.supervisor.profile || {};
        next.supervisor.profile.avatar = avatar;
        changed = true;
    }

    if (changed) {
        fs.writeFileSync(configPath, `${JSON.stringify(next, null, 2)}\n`, "utf-8");
    }

    return { config: next, changed };
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
    let selected: CanonicalConfig = {};
    for (const configPath of resolveCanonicalConfigPaths()) {
        const { config } = migrateLegacyLocalConfig(configPath, readJsonConfig(configPath));
        if (!selected.systemBase?.bridge && Object.keys(config || {}).length) {
            selected = config;
        }
    }
    return selected;
}

export function readCanonicalConfigDiagnostics(): CanonicalConfigDiagnostics {
    let selectedConfigPath: string | null = null;
    let selectedHasContent = false;
    const migratedConfigPaths: string[] = [];

    for (const configPath of resolveCanonicalConfigPaths()) {
        const rawConfig = readJsonConfig(configPath);
        const { config, changed } = migrateLegacyLocalConfig(configPath, rawConfig);
        if (!selectedHasContent && Object.keys(config || {}).length) {
            selectedConfigPath = configPath;
            selectedHasContent = true;
        }
        if (changed) {
            migratedConfigPaths.push(configPath);
        }
    }

    const notices: LegacyPortNotice[] = [
        ...migratedConfigPaths.map((configPath) => ({ code: "config_migrated" as const, path: configPath })),
        ...resolveLocalEnvCandidates().flatMap((item) => {
            if (!fs.existsSync(item.path)) {
                return [];
            }
            try {
                const raw = fs.readFileSync(item.path, "utf-8");
                return hasLegacyLocalPorts(raw) ? [{ code: item.code, path: item.path }] : [];
            } catch {
                return [];
            }
        }),
    ];

    return {
        selectedConfigPath,
        migratedConfigPaths,
        notices,
    };
}

export function readCanonicalBridge(): BridgeConfig {
    return readCanonicalConfig().systemBase?.bridge || {};
}

export function readCanonicalAdminRuntimeConfig(): CanonicalConfig {
    return readCanonicalConfig();
}
