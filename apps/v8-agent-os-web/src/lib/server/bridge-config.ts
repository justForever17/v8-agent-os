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

export function resolveCanonicalConfigPath(environment: NodeJS.ProcessEnv = process.env) {
    const explicitHome = String(environment.V8_AGENT_OS_HOME || "").trim();
    const canonicalHome = explicitHome ? path.resolve(explicitHome) : path.join(os.homedir(), ".v8-agent-os");
    return path.join(canonicalHome, "config.json");
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

export function readCanonicalConfig() {
    return readJsonConfig(resolveCanonicalConfigPath());
}

export function readCanonicalBridge() {
    return readCanonicalConfig().systemBase?.bridge || {};
}
