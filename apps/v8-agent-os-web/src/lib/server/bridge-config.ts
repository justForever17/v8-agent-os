import fs from "fs";
import os from "os";
import path from "path";

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

type SupervisorConfig = {
    profile?: {
        avatar?: string;
    };
};

type CanonicalConfig = {
    systemBase?: {
        bridge?: BridgeConfig;
    };
    supervisor?: SupervisorConfig;
    networkSupervisorRuntime?: {
        node?: {
            advertisedBaseUrl?: string;
            advertisedWsUrl?: string;
        };
    };
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

function migrateLegacyLocalConfig(configPath: string, config: CanonicalConfig): CanonicalConfig {
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

    return next;
}

export function readCanonicalConfig() {
    let selected: CanonicalConfig = {};
    for (const configPath of resolveCanonicalConfigPaths()) {
        const migrated = migrateLegacyLocalConfig(configPath, readJsonConfig(configPath));
        if (!selected.systemBase?.bridge && Object.keys(migrated || {}).length) {
            selected = migrated;
        }
    }
    return selected;
}

export function readCanonicalBridge() {
    return readCanonicalConfig().systemBase?.bridge || {};
}
