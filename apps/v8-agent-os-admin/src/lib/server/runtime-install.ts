import fs from "fs";
import path from "path";
import { spawn } from "child_process";

import { readCanonicalAdminRuntimeConfig } from "@/lib/server/bridge-config";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";

export type RuntimeInstallState = {
    installProfile: "minimal" | "desktop";
    installPlatform: "windows" | "macos" | "linux";
    installedRuntimeFamilies: string[];
    bootstrapManaged: boolean;
    lastUpgradeAt: string | null;
    engineAvailable: boolean;
    canInstallDesktop: boolean;
    canAutoRestart: boolean;
};

function normalizeInstallProfile(value: unknown): "minimal" | "desktop" {
    const normalized = String(value || "").trim().toLowerCase();
    if (normalized === "desktop") return "desktop";
    return "minimal";
}

function normalizePlatform(value: unknown): "windows" | "macos" | "linux" {
    const normalized = String(value || "").trim().toLowerCase();
    if (normalized === "windows" || normalized === "macos" || normalized === "linux") {
        return normalized;
    }
    if (process.platform === "win32") return "windows";
    if (process.platform === "darwin") return "macos";
    return "linux";
}

function normalizeFamilies(value: unknown): string[] {
    const seen = new Set<string>();
    const result: string[] = [];
    for (const item of Array.isArray(value) ? value : []) {
        const normalized = String(item || "").trim();
        if (!normalized || seen.has(normalized)) continue;
        seen.add(normalized);
        result.push(normalized);
    }
    return result;
}

function defaultFamilies(profile: "minimal" | "desktop") {
    if (profile === "desktop") {
        return ["chat", "memory", "extensions", "automation", "network_supervisor", "engineering", "creative_media", "computer_use", "rpa", "desktop_live"];
    }
    return ["chat", "memory", "extensions", "automation", "network_supervisor", "creative_media"];
}

async function readEngineInstallState(): Promise<Partial<RuntimeInstallState> & { engineAvailable: boolean }> {
    try {
        const res = await fetch(`${resolveEngineOrigin()}/health`, { cache: "no-store" });
        if (!res.ok) {
            return { engineAvailable: false };
        }
        const payload = await res.json().catch(() => ({}));
        return {
            installProfile: normalizeInstallProfile(payload.installProfile || payload.startupProfile),
            installPlatform: normalizePlatform(payload.installPlatform),
            installedRuntimeFamilies: normalizeFamilies(payload.installedRuntimeFamilies),
            bootstrapManaged: Boolean(payload.bootstrapManaged),
            lastUpgradeAt: String(payload.lastUpgradeAt || "").trim() || null,
            engineAvailable: true,
        };
    } catch {
        return { engineAvailable: false };
    }
}

export async function getRuntimeInstallState(): Promise<RuntimeInstallState> {
    const config = readCanonicalAdminRuntimeConfig();
    const registry = config.runtimeRegistry || {};
    const engineState = await readEngineInstallState();
    const installProfile = normalizeInstallProfile(engineState.installProfile || registry.installProfile || registry.startupProfile);
    const installPlatform = normalizePlatform(engineState.installPlatform || registry.installPlatform);
    const installedRuntimeFamilies = normalizeFamilies(engineState.installedRuntimeFamilies || registry.installedRuntimeFamilies);
    const bootstrapManaged = Boolean(engineState.bootstrapManaged ?? registry.bootstrapManaged ?? false);
    const lastUpgradeAt = engineState.lastUpgradeAt ?? (String(registry.lastUpgradeAt || "").trim() || null);
    return {
        installProfile,
        installPlatform,
        installedRuntimeFamilies: installedRuntimeFamilies.length ? installedRuntimeFamilies : defaultFamilies(installProfile),
        bootstrapManaged,
        lastUpgradeAt,
        engineAvailable: Boolean(engineState.engineAvailable),
        canInstallDesktop: installProfile !== "desktop",
        canAutoRestart: bootstrapManaged,
    };
}

function resolveRepoRoot() {
    return path.resolve(process.cwd(), "..", "..");
}

function resolveBootstrapScript(platform: "windows" | "macos" | "linux") {
    const repoRoot = resolveRepoRoot();
    const candidate = platform === "windows" ? path.join(repoRoot, "bootstrap.ps1") : path.join(repoRoot, "bootstrap.sh");
    if (!fs.existsSync(candidate)) {
        throw new Error(`Bootstrap script not found: ${candidate}`);
    }
    return candidate;
}

export async function triggerDesktopInstall(requestedPlatform?: string) {
    const state = await getRuntimeInstallState();
    const targetPlatform = normalizePlatform(requestedPlatform || state.installPlatform);
    const scriptPath = resolveBootstrapScript(targetPlatform);
    const repoRoot = resolveRepoRoot();
    const restartEngine = state.bootstrapManaged ? "1" : "0";
    const installOnly = state.bootstrapManaged ? "0" : "1";

    const file = targetPlatform === "windows" ? "powershell.exe" : "bash";
    const args =
        targetPlatform === "windows"
            ? ["-ExecutionPolicy", "Bypass", "-File", scriptPath, "--profile", "desktop", "--services", "engine", "--platform", targetPlatform]
            : [scriptPath, "--profile", "desktop", "--services", "engine", "--platform", targetPlatform];

    const child = spawn(file, args, {
        cwd: repoRoot,
        detached: true,
        stdio: "ignore",
        env: {
            ...process.env,
            V8_AGENT_OS_BOOTSTRAP_RESTART_ENGINE: restartEngine,
            V8_AGENT_OS_BOOTSTRAP_INSTALL_ONLY: installOnly,
            V8_AGENT_OS_BOOTSTRAP_MANAGED: state.bootstrapManaged ? "1" : "0",
        },
    });
    child.unref();

    return {
        installProfile: state.installProfile,
        targetProfile: "desktop" as const,
        installPlatform: targetPlatform,
        bootstrapManaged: state.bootstrapManaged,
        autoRestart: state.bootstrapManaged,
        message: state.bootstrapManaged
            ? "已开始安装桌面依赖，并将在完成后重启 engine。"
            : "已开始安装桌面依赖。当前环境不是 bootstrap-managed，请在安装完成后手动重启 engine。",
    };
}
