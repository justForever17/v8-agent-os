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

export function readCanonicalConfig() {
    return readJsonConfig(CANONICAL_CONFIG_PATH);
}

export function readCanonicalBridge() {
    return readCanonicalConfig().systemBase?.bridge || {};
}
