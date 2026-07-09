import fs from "fs";
import os from "os";
import path from "path";
import { spawn } from "child_process";

import {
    canonicalConfigPath,
    readCanonicalAdminRuntimeConfig,
    writeCanonicalConfig,
    type CanonicalConfig,
} from "@/lib/server/bridge-config";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";

export type FeaturePackStatus = "installed" | "not_installed" | "installing" | "failed";

export type RuntimeFeaturePack = {
    id: string;
    productName: string;
    shortName: string;
    description: string;
    hover: string;
    recommendedOrder: number;
    runtimeFamilies: string[];
    requirementsFile: string;
    targetDir: string;
    status: FeaturePackStatus;
    installed: boolean;
    restartRequired: boolean;
    logRef: string | null;
    lastError: string | null;
    updatedAt: string | null;
    installable: boolean;
};

export type RuntimeFeaturePackState = {
    engineAvailable: boolean;
    packs: RuntimeFeaturePack[];
    summary: {
        total: number;
        installed: number;
        missing: number;
        installing: number;
        failed: number;
    };
    installRoot: string;
    logRoot: string;
    configPath: string;
};

type FeaturePackDefinition = {
    id: string;
    productName: string;
    shortName: string;
    description: string;
    hover: string;
    recommendedOrder: number;
    runtimeFamilies: string[];
    requirementsFile: string;
};

const FEATURE_PACK_DEFINITIONS: FeaturePackDefinition[] = [
    {
        id: "computer_use_desktop",
        productName: "桌面操作能力包",
        shortName: "桌面操作",
        description: "启用桌面截图、窗口识别、点击输入和桌面直播采集。",
        hover: "安装后接入桌面操作与 Desktop Live；适合需要 V8OS 观察并操作本机应用的场景。",
        recommendedOrder: 1,
        runtimeFamilies: ["computer_use", "desktop_live"],
        requirementsFile: "computer-use-desktop.txt",
    },
    {
        id: "rpa_automation",
        productName: "自动流程能力包",
        shortName: "自动流程",
        description: "启用 Robot Framework / RPA 流程执行和录制辅助能力。",
        hover: "安装后接入自动流程 runtime；适合重复性业务操作、脚本化流程和可复用自动化。",
        recommendedOrder: 2,
        runtimeFamilies: ["rpa"],
        requirementsFile: "rpa-automation.txt",
    },
    {
        id: "local_asr_ocr",
        productName: "本地识别增强包",
        shortName: "本地识别",
        description: "为高性能本机提供本地语音转写、OCR 和媒体理解增强。",
        hover: "适合电脑性能较高且不想依赖云供应商的用户；安装后按需接入本地语音转写、OCR、媒体/附件理解增强。",
        recommendedOrder: 3,
        runtimeFamilies: [],
        requirementsFile: "local-asr-ocr.txt",
    },
];

const FEATURE_PACK_BY_ID = new Map(FEATURE_PACK_DEFINITIONS.map((definition) => [definition.id, definition]));

function v8Home() {
    return process.env.V8_AGENT_OS_HOME || path.join(os.homedir(), ".v8-agent-os");
}

function resolveRepoRoot() {
    const candidates = [
        process.env.V8_AGENT_OS_REPO_ROOT || "",
        process.cwd(),
        path.resolve(process.cwd(), ".."),
        path.resolve(process.cwd(), "..", ".."),
    ].filter(Boolean);
    const detected = candidates.find((candidate) => fs.existsSync(
        path.join(candidate, "apps", "v8-agent-os-engine", "requirements", "feature-packs"),
    ));
    return detected ? path.resolve(detected) : path.resolve(process.cwd(), "..", "..");
}

function resolveEngineRoot() {
    const explicit = String(process.env.V8_ENGINE_DIR || "").trim();
    if (explicit && fs.existsSync(explicit)) return explicit;
    return path.join(resolveRepoRoot(), "apps", "v8-agent-os-engine");
}

function featurePackRoot() {
    return path.join(v8Home(), "runtime-data", "feature-packs");
}

function featurePackLogRoot() {
    return path.join(v8Home(), "logs", "feature-packs");
}

function targetDirFor(packId: string) {
    return path.join(featurePackRoot(), packId, "python");
}

function requirementsPathFor(definition: FeaturePackDefinition) {
    return path.join(resolveEngineRoot(), "requirements", "feature-packs", definition.requirementsFile);
}

function normalizeStatus(value: unknown): FeaturePackStatus {
    const normalized = String(value || "").trim();
    if (normalized === "installed" || normalized === "installing" || normalized === "failed") return normalized;
    return "not_installed";
}

function nowIso() {
    return new Date().toISOString();
}

function normalizeFeaturePackFromConfig(definition: FeaturePackDefinition, config: CanonicalConfig): RuntimeFeaturePack {
    const raw = config.runtimeRegistry?.featurePacks?.[definition.id] || {};
    const targetDir = String(raw.targetDir || targetDirFor(definition.id));
    const status = normalizeStatus(raw.status);
    const targetExists = fs.existsSync(targetDir);
    const installed = status === "installed" && targetExists;
    return {
        ...definition,
        requirementsFile: requirementsPathFor(definition),
        targetDir,
        status: installed ? "installed" : status === "installed" ? "not_installed" : status,
        installed,
        restartRequired: Boolean(raw.restartRequired ?? installed),
        logRef: raw.logRef || null,
        lastError: raw.lastError || null,
        updatedAt: raw.updatedAt || null,
        installable: fs.existsSync(requirementsPathFor(definition)),
    };
}

function normalizeFeaturePackFromEngine(definition: FeaturePackDefinition, raw: Record<string, unknown>): RuntimeFeaturePack {
    const status = normalizeStatus(raw.status);
    return {
        ...definition,
        requirementsFile: String(raw.requirementsFile || requirementsPathFor(definition)),
        targetDir: String(raw.targetDir || targetDirFor(definition.id)),
        status,
        installed: Boolean(raw.installed ?? status === "installed"),
        restartRequired: Boolean(raw.restartRequired ?? status === "installed"),
        logRef: raw.logRef ? String(raw.logRef) : null,
        lastError: raw.lastError ? String(raw.lastError) : null,
        updatedAt: raw.updatedAt ? String(raw.updatedAt) : null,
        installable: fs.existsSync(requirementsPathFor(definition)),
    };
}

function summarize(packs: RuntimeFeaturePack[]) {
    return {
        total: packs.length,
        installed: packs.filter((pack) => pack.status === "installed").length,
        missing: packs.filter((pack) => pack.status === "not_installed").length,
        installing: packs.filter((pack) => pack.status === "installing").length,
        failed: packs.filter((pack) => pack.status === "failed").length,
    };
}

async function readEngineFeaturePacks() {
    try {
        const response = await fetch(`${resolveEngineOrigin()}/health`, { cache: "no-store" });
        if (!response.ok) return { engineAvailable: false, packs: null as RuntimeFeaturePack[] | null };
        const payload = await response.json().catch(() => ({}));
        const rawPacks = Array.isArray(payload.featurePacks) ? payload.featurePacks : null;
        if (!rawPacks) return { engineAvailable: true, packs: null as RuntimeFeaturePack[] | null };
        const rawById = new Map<string, Record<string, unknown>>();
        for (const pack of rawPacks) {
            if (!pack || typeof pack !== "object") continue;
            const raw = pack as Record<string, unknown>;
            rawById.set(String(raw.id || ""), raw);
        }
        return {
            engineAvailable: true,
            packs: FEATURE_PACK_DEFINITIONS.map((definition) =>
                normalizeFeaturePackFromEngine(definition, rawById.get(definition.id) || ({} as Record<string, unknown>)),
            ).sort((a, b) => a.recommendedOrder - b.recommendedOrder),
        };
    } catch {
        return { engineAvailable: false, packs: null as RuntimeFeaturePack[] | null };
    }
}

export async function getRuntimeFeaturePackState(): Promise<RuntimeFeaturePackState> {
    const config = readCanonicalAdminRuntimeConfig();
    const engineState = await readEngineFeaturePacks();
    const packs = (
        engineState.packs
            || FEATURE_PACK_DEFINITIONS.map((definition) => normalizeFeaturePackFromConfig(definition, config))
    ).sort((a, b) => a.recommendedOrder - b.recommendedOrder);
    return {
        engineAvailable: engineState.engineAvailable,
        packs,
        summary: summarize(packs),
        installRoot: featurePackRoot(),
        logRoot: featurePackLogRoot(),
        configPath: canonicalConfigPath(),
    };
}

function updateFeaturePackConfig(packId: string, patch: Record<string, unknown>) {
    const config = readCanonicalAdminRuntimeConfig();
    const runtimeRegistry = {
        ...(config.runtimeRegistry || {}),
        featurePacks: {
            ...(config.runtimeRegistry?.featurePacks || {}),
        },
    };
    const existing = runtimeRegistry.featurePacks?.[packId] || {};
    runtimeRegistry.featurePacks = {
        ...(runtimeRegistry.featurePacks || {}),
        [packId]: {
            ...existing,
            ...patch,
            updatedAt: nowIso(),
        },
    };
    writeCanonicalConfig({
        ...config,
        runtimeRegistry,
    });
}

function resolvePythonExecutable(config: CanonicalConfig) {
    const configured = String(config.systemBase?.channels?.enginePython || "").trim();
    if (configured && fs.existsSync(configured)) return configured;
    const engineRoot = resolveEngineRoot();
    const windowsPython = path.join(engineRoot, ".venv", "Scripts", "python.exe");
    if (fs.existsSync(windowsPython)) return windowsPython;
    const posixPython = path.join(engineRoot, ".venv", "bin", "python");
    if (fs.existsSync(posixPython)) return posixPython;
    return process.platform === "win32" ? "python.exe" : "python3";
}

export async function triggerFeaturePackInstall(packId: string, dryRun = false) {
    const definition = FEATURE_PACK_BY_ID.get(String(packId || ""));
    if (!definition) {
        throw new Error(`Unknown feature pack: ${packId}`);
    }
    const config = readCanonicalAdminRuntimeConfig();
    const requirementsFile = requirementsPathFor(definition);
    if (!fs.existsSync(requirementsFile)) {
        throw new Error(`Feature pack requirements file not found: ${requirementsFile}`);
    }
    const targetDir = targetDirFor(definition.id);
    const pythonExe = resolvePythonExecutable(config);
    const args = [
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--prefer-binary",
        "--target",
        targetDir,
        "-r",
        requirementsFile,
    ];
    const commandSummary = `${pythonExe} ${args.map((item) => (item.includes(" ") ? `"${item}"` : item)).join(" ")}`;
    if (dryRun) {
        return {
            status: "dry_run",
            packId: definition.id,
            commandSummary,
            targetDir,
            requirementsFile,
            restartRequired: true,
        };
    }

    fs.mkdirSync(targetDir, { recursive: true });
    fs.mkdirSync(featurePackLogRoot(), { recursive: true });
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    const logRef = path.join(featurePackLogRoot(), `${definition.id}-${timestamp}.log`);
    const output = fs.createWriteStream(logRef, { flags: "a", encoding: "utf-8" });
    output.write(`[V8OS Feature Pack] ${definition.productName}\n`);
    output.write(`[Command] ${commandSummary}\n\n`);

    updateFeaturePackConfig(definition.id, {
        status: "installing",
        targetDir,
        logRef,
        lastError: null,
        restartRequired: true,
    });

    const child = spawn(pythonExe, args, {
        cwd: resolveRepoRoot(),
        windowsHide: true,
        stdio: ["ignore", "pipe", "pipe"],
        env: {
            ...process.env,
            PYTHONUTF8: "1",
            PYTHONIOENCODING: "utf-8",
        },
    });
    child.stdout?.pipe(output, { end: false });
    child.stderr?.pipe(output, { end: false });
    child.on("error", (error) => {
        output.write(`\n[Install error] ${error.message}\n`);
        output.end();
        updateFeaturePackConfig(definition.id, {
            status: "failed",
            targetDir,
            logRef,
            lastError: error.message,
            restartRequired: true,
        });
    });
    child.on("exit", (code) => {
        const ok = code === 0;
        output.write(`\n[Exit code] ${code ?? "unknown"}\n`);
        output.end();
        updateFeaturePackConfig(definition.id, {
            status: ok ? "installed" : "failed",
            targetDir,
            logRef,
            lastError: ok ? null : `pip exited with code ${code ?? "unknown"}. See logRef for details.`,
            restartRequired: true,
        });
    });

    return {
        status: "started",
        packId: definition.id,
        commandSummary,
        targetDir,
        requirementsFile,
        logRef,
        restartRequired: true,
        message: "能力包安装已开始。安装完成后如状态提示需要重启，请重启 Engine 后继续使用。",
    };
}
