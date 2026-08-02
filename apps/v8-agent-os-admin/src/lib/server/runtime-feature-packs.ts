import fs from "fs";
import crypto from "crypto";
import os from "os";
import path from "path";
import { spawn } from "child_process";
import { Readable } from "stream";
import { pipeline } from "stream/promises";

import {
    canonicalConfigPath,
    readCanonicalAdminRuntimeConfig,
    writeCanonicalConfig,
    type CanonicalConfig,
} from "@/lib/server/bridge-config";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";

export type FeaturePackStatus = "installed" | "not_installed" | "installing" | "failed";

type PipSource = {
    id: string;
    label: string;
    indexUrl: string | null;
};

type PipAttemptSummary = {
    sourceId: string;
    sourceLabel: string;
    exitCode: number | null;
    recoverable: boolean;
    error: string | null;
};

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
    version?: string | null;
    assetRoot?: string | null;
    receiptRef?: string | null;
    executionProvider?: string | null;
    gpuAdapters?: string[];
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
    assetManifestFile?: string;
};

type FeaturePackAsset = {
    id: string;
    target: string;
    url: string;
    size: number;
    sha256: string;
};

type FeaturePackAssetManifest = {
    id: string;
    version: string;
    license?: { name?: string; source?: string };
    smokeCheck?: {
        kind: "onnx" | "mediapipe_task";
        task?: "holistic_landmarker";
        preferGpu?: boolean;
    };
    assets: FeaturePackAsset[];
};

type FeaturePackInstallEnvironment = {
    platform: NodeJS.Platform;
    architecture: string;
    pythonVersion: string;
    pythonImplementation: string;
    gpuAdapters: string[];
    gpuDetected: boolean;
};

type FeaturePackConfigRecord = {
    status?: string;
    targetDir?: string;
    logRef?: string | null;
    lastError?: string | null;
    updatedAt?: string | null;
    restartRequired?: boolean;
    version?: string | null;
    assetRoot?: string | null;
    receiptRef?: string | null;
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
        productName: "可选本地识别包",
        shortName: "本地识别",
        description: "为高性能本机提供本地语音转写、OCR 和媒体理解增强。",
        hover: "适合电脑性能较高且不想依赖云供应商的用户；安装后按需接入本地语音转写、OCR、媒体/附件理解增强。",
        recommendedOrder: 3,
        runtimeFamilies: [],
        requirementsFile: "local-asr-ocr.txt",
    },
    {
        id: "creative_media_image_analysis",
        productName: "图像分析增强包",
        shortName: "图像分析",
        description: "为多媒体创作提供本地主体分割、透明度核验和跨图构图比较。",
        hover: "安装后可离线复用已验签的 IS-Net 模型；仅在复杂不透明背景需要主体分割时使用。",
        recommendedOrder: 4,
        runtimeFamilies: [],
        requirementsFile: "creative-media-image-analysis.txt",
        assetManifestFile: "creative-media-image-analysis.manifest.json",
    },
    {
        id: "creative_media_motion_capture",
        productName: "动作采集能力包",
        shortName: "动作采集",
        description: "为多媒体创作提供单人视频动作提取、骨架预览和动作质量核验。",
        hover: "安装后可离线使用已验签的 MediaPipe Holistic 模型；首版仅支持单人视频或摄像头录制文件。",
        recommendedOrder: 5,
        runtimeFamilies: [],
        requirementsFile: "creative-media-motion-capture.txt",
        assetManifestFile: "creative-media-motion-capture.manifest.json",
    },
];

const FEATURE_PACK_BY_ID = new Map(FEATURE_PACK_DEFINITIONS.map((definition) => [definition.id, definition]));

export const PIP_SOURCE_STRATEGY: PipSource[] = [
    { id: "official", label: "Official PyPI", indexUrl: null },
    { id: "tuna", label: "TUNA mirror", indexUrl: "https://pypi.tuna.tsinghua.edu.cn/simple" },
    { id: "ustc", label: "USTC mirror", indexUrl: "https://pypi.mirrors.ustc.edu.cn/simple" },
    { id: "aliyun", label: "Aliyun mirror", indexUrl: "https://mirrors.aliyun.com/pypi/simple" },
];

const TERMINAL_PIP_FAILURE_PATTERNS = [
    /Could not find a version that satisfies/i,
    /No matching distribution found/i,
    /Invalid requirement/i,
    /is not a valid editable requirement/i,
    /Directory ['"].+['"] is not installable/i,
    /Permission denied/i,
    /Access is denied/i,
];

const RECOVERABLE_PIP_FAILURE_PATTERNS = [
    /Could not fetch URL/i,
    /Read timed out/i,
    /\btimed out\b/i,
    /Temporary failure in name resolution/i,
    /Failed to establish a new connection/i,
    /NameResolutionError/i,
    /Network is unreachable/i,
    /Connection(?: aborted| reset| refused| error| broken)/i,
    /Remote end closed connection/i,
    /ProxyError/i,
    /SSLError/i,
    /CERTIFICATE_VERIFY_FAILED/i,
    /\b50[234]\b/,
    /Bad Gateway/i,
    /Service Unavailable/i,
    /Gateway Timeout/i,
];

const FEATURE_PACK_ASSET_HOSTS = new Set([
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "storage.googleapis.com",
]);

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

function assetManifestPathFor(definition: FeaturePackDefinition) {
    return definition.assetManifestFile
        ? path.join(resolveEngineRoot(), "requirements", "feature-packs", definition.assetManifestFile)
        : null;
}

function readAssetManifest(definition: FeaturePackDefinition): FeaturePackAssetManifest | null {
    const manifestPath = assetManifestPathFor(definition);
    if (!manifestPath || !fs.existsSync(manifestPath)) return null;
    const payload = JSON.parse(fs.readFileSync(manifestPath, "utf-8")) as FeaturePackAssetManifest;
    if (payload.id !== definition.id || !payload.version || !Array.isArray(payload.assets)) {
        throw new Error(`Invalid feature pack asset manifest: ${manifestPath}`);
    }
    return payload;
}

function definitionInstallable(definition: FeaturePackDefinition) {
    const manifestPath = assetManifestPathFor(definition);
    return fs.existsSync(requirementsPathFor(definition)) && (!manifestPath || fs.existsSync(manifestPath));
}

function normalizeStatus(value: unknown): FeaturePackStatus {
    const normalized = String(value || "").trim();
    if (normalized === "installed" || normalized === "installing" || normalized === "failed") return normalized;
    return "not_installed";
}

function readReceiptRuntimeSummary(receiptRef: unknown) {
    const receiptPath = String(receiptRef || "").trim();
    if (!receiptPath || !fs.existsSync(receiptPath)) {
        return { executionProvider: null, gpuAdapters: [] as string[] };
    }
    try {
        const receipt = JSON.parse(fs.readFileSync(receiptPath, "utf-8")) as Record<string, unknown>;
        const environment = receipt.environment && typeof receipt.environment === "object"
            ? receipt.environment as Record<string, unknown>
            : {};
        const smokeCheck = receipt.smokeCheck && typeof receipt.smokeCheck === "object"
            ? receipt.smokeCheck as Record<string, unknown>
            : {};
        return {
            executionProvider: String(smokeCheck.selectedExecutionProvider || "").trim() || null,
            gpuAdapters: Array.isArray(environment.gpuAdapters)
                ? environment.gpuAdapters.map(String).map((value) => value.trim()).filter(Boolean)
                : [],
        };
    } catch {
        return { executionProvider: null, gpuAdapters: [] as string[] };
    }
}

function nowIso() {
    return new Date().toISOString();
}

function normalizeFeaturePackFromConfig(definition: FeaturePackDefinition, config: CanonicalConfig): RuntimeFeaturePack {
    const raw = (config.runtimeRegistry?.featurePacks?.[definition.id] || {}) as FeaturePackConfigRecord;
    const targetDir = String(raw.targetDir || targetDirFor(definition.id));
    const status = normalizeStatus(raw.status);
    const targetExists = fs.existsSync(targetDir);
    const assetRoot = raw.assetRoot ? String(raw.assetRoot) : path.join(featurePackRoot(), definition.id, "assets");
    const manifest = readAssetManifest(definition);
    const assetsExist = !manifest || manifest.assets.every((asset) => fs.existsSync(path.join(assetRoot, asset.target)));
    const installed = status === "installed" && targetExists && assetsExist;
    const receiptSummary = readReceiptRuntimeSummary(raw.receiptRef);
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
        installable: definitionInstallable(definition),
        version: raw.version ? String(raw.version) : null,
        assetRoot: manifest ? assetRoot : null,
        receiptRef: raw.receiptRef ? String(raw.receiptRef) : null,
        ...receiptSummary,
    };
}

function normalizeFeaturePackFromEngine(definition: FeaturePackDefinition, raw: Record<string, unknown>): RuntimeFeaturePack {
    const status = normalizeStatus(raw.status);
    const receiptSummary = readReceiptRuntimeSummary(raw.receiptRef);
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
        installable: definitionInstallable(definition),
        version: raw.version ? String(raw.version) : null,
        assetRoot: raw.assetRoot ? String(raw.assetRoot) : null,
        receiptRef: raw.receiptRef ? String(raw.receiptRef) : null,
        ...receiptSummary,
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

function formatCommandSummary(pythonExe: string, args: string[]) {
    return `${pythonExe} ${args.map((item) => (item.includes(" ") ? `"${item}"` : item)).join(" ")}`;
}

function pipSourceStrategy(locale: string) {
    const normalized = String(locale || "").trim().toLowerCase();
    if (!normalized.startsWith("zh")) return PIP_SOURCE_STRATEGY;
    const official = PIP_SOURCE_STRATEGY.filter((source) => source.id === "official");
    const mirrors = PIP_SOURCE_STRATEGY.filter((source) => source.id !== "official");
    return [...mirrors, ...official];
}

function sourceStrategyForResponse(sources: PipSource[]) {
    return sources.map((source) => ({
        id: source.id,
        label: source.label,
        indexUrl: source.indexUrl,
    }));
}

function buildPipInstallArgs(targetDir: string, requirementsFile: string, source: PipSource) {
    const args = [
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--prefer-binary",
        "--retries",
        "2",
        "--timeout",
        "30",
        "--target",
        targetDir,
        "-r",
        requirementsFile,
    ];
    if (source.indexUrl) {
        args.push("--index-url", source.indexUrl);
    }
    return args;
}

function isTerminalPipFailure(output: string) {
    return TERMINAL_PIP_FAILURE_PATTERNS.some((pattern) => pattern.test(output));
}

function isRecoverablePipFailure(output: string, source: PipSource) {
    if (
        source.id !== "official"
        && /(?:Could not find a version that satisfies|No matching distribution found)/i.test(output)
    ) {
        return true;
    }
    if (isTerminalPipFailure(output)) return false;
    return RECOVERABLE_PIP_FAILURE_PATTERNS.some((pattern) => pattern.test(output));
}

function buildInstallFailureMessage(attempts: PipAttemptSummary[]) {
    const terminal = attempts.find((attempt) => !attempt.recoverable);
    if (terminal?.error) {
        return "Feature pack install failed before package download completed. See logRef for details.";
    }
    if (terminal) {
        return "pip reported a package or environment error. See logRef for details.";
    }
    if (attempts.some((attempt) => attempt.recoverable) && attempts.every((attempt) => attempt.exitCode !== 0)) {
        return "Package download failed from the configured sources. See logRef for details.";
    }
    return "pip reported a package or environment error. See logRef for details.";
}

function runPipAttempt(
    pythonExe: string,
    args: string[],
    output: fs.WriteStream,
): Promise<{ exitCode: number | null; outputPreview: string; error: string | null }> {
    return new Promise((resolve) => {
        let settled = false;
        let outputPreview = "";
        const appendOutput = (chunk: Buffer | string) => {
            const text = Buffer.isBuffer(chunk) ? chunk.toString("utf8") : String(chunk);
            if (outputPreview.length < 120000) {
                outputPreview += text.slice(0, 120000 - outputPreview.length);
            }
            output.write(text);
        };
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
        child.stdout?.on("data", appendOutput);
        child.stderr?.on("data", appendOutput);
        child.on("error", (error) => {
            if (settled) return;
            settled = true;
            output.write(`\n[Install error] ${error.message}\n`);
            resolve({ exitCode: null, outputPreview, error: error.message });
        });
        child.on("exit", (code) => {
            if (settled) return;
            settled = true;
            resolve({ exitCode: code, outputPreview, error: null });
        });
    });
}

async function installPipDependencies(
    pythonExe: string,
    targetDir: string,
    requirementsFile: string,
    output: fs.WriteStream,
    sources: PipSource[],
) {
    const attempts: PipAttemptSummary[] = [];
    for (const [index, source] of sources.entries()) {
        const args = buildPipInstallArgs(targetDir, requirementsFile, source);
        output.write(`\n[Source] ${source.label}\n[Command] ${formatCommandSummary(pythonExe, args)}\n\n`);
        const result = await runPipAttempt(pythonExe, args, output);
        const ok = result.exitCode === 0;
        const recoverable = ok ? false : isRecoverablePipFailure(result.outputPreview, source);
        attempts.push({
            sourceId: source.id,
            sourceLabel: source.label,
            exitCode: result.exitCode,
            recoverable,
            error: result.error,
        });
        output.write(`\n[Exit code] ${result.exitCode ?? "unknown"}\n`);
        if (ok) return attempts;
        if (!recoverable || index === sources.length - 1) break;
        output.write(`[Fallback] Retrying via ${sources[index + 1].label}.\n`);
    }
    throw new Error(buildInstallFailureMessage(attempts));
}

function sha256File(filePath: string) {
    const hash = crypto.createHash("sha256");
    const descriptor = fs.openSync(filePath, "r");
    const buffer = Buffer.allocUnsafe(1024 * 1024);
    try {
        let bytesRead = 0;
        do {
            bytesRead = fs.readSync(descriptor, buffer, 0, buffer.length, null);
            if (bytesRead > 0) hash.update(buffer.subarray(0, bytesRead));
        } while (bytesRead > 0);
    } finally {
        fs.closeSync(descriptor);
    }
    return hash.digest("hex");
}

function assertTrustedFeaturePackAssetUrl(rawUrl: string) {
    const parsed = new URL(rawUrl);
    if (parsed.protocol !== "https:" || !FEATURE_PACK_ASSET_HOSTS.has(parsed.hostname.toLowerCase())) {
        throw new Error(`Feature pack asset host is not allowed: ${parsed.hostname || "unknown"}`);
    }
}

async function downloadFeaturePackAsset(asset: FeaturePackAsset, modelRoot: string, output: fs.WriteStream) {
    assertTrustedFeaturePackAssetUrl(asset.url);
    const target = path.resolve(modelRoot, asset.target);
    const root = path.resolve(modelRoot);
    if (target !== root && !target.startsWith(`${root}${path.sep}`)) {
        throw new Error(`Asset target escapes feature pack root: ${asset.target}`);
    }
    fs.mkdirSync(path.dirname(target), { recursive: true });
    const partial = `${target}.part`;
    let offset = fs.existsSync(partial) ? fs.statSync(partial).size : 0;
    if (offset > asset.size) {
        fs.rmSync(partial, { force: true });
        offset = 0;
    }
    const request = async (resumeOffset: number) => fetch(asset.url, {
        headers: resumeOffset > 0 ? { Range: `bytes=${resumeOffset}-` } : undefined,
        redirect: "follow",
        cache: "no-store",
    });
    let response = await request(offset);
    if (offset > 0 && response.status !== 206) {
        fs.rmSync(partial, { force: true });
        offset = 0;
        response = await request(0);
    }
    if (!response.ok || !response.body) {
        throw new Error(`Asset download failed (${response.status}): ${asset.id}`);
    }
    assertTrustedFeaturePackAssetUrl(response.url);
    output.write(`[Asset] ${asset.id} ${offset ? `resuming at ${offset}` : "starting"}\n`);
    await pipeline(
        Readable.fromWeb(response.body as never),
        fs.createWriteStream(partial, { flags: offset > 0 ? "a" : "w" }),
    );
    const actualSize = fs.statSync(partial).size;
    if (actualSize !== asset.size) {
        throw new Error(`Asset size mismatch for ${asset.id}: expected ${asset.size}, received ${actualSize}`);
    }
    const actualHash = sha256File(partial);
    if (actualHash.toLowerCase() !== asset.sha256.toLowerCase()) {
        throw new Error(`Asset SHA-256 mismatch for ${asset.id}`);
    }
    fs.renameSync(partial, target);
    output.write(`[Asset verified] ${asset.id} sha256=${actualHash}\n`);
    return { ...asset, path: target, verifiedSha256: actualHash };
}

function runProbeProcess(command: string, args: string[], timeoutMs = 20_000) {
    return new Promise<{ code: number | null; output: string }>((resolve) => {
        let settled = false;
        let output = "";
        const child = spawn(command, args, {
            windowsHide: true,
            stdio: ["ignore", "pipe", "pipe"],
            env: { ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" },
        });
        const append = (chunk: Buffer | string) => {
            if (output.length < 64_000) output += String(chunk).slice(0, 64_000 - output.length);
        };
        child.stdout?.on("data", append);
        child.stderr?.on("data", append);
        const timeout = setTimeout(() => {
            if (settled) return;
            settled = true;
            child.kill();
            resolve({ code: null, output: `${output}\nprobe timed out` });
        }, timeoutMs);
        child.on("error", (error) => {
            if (settled) return;
            settled = true;
            clearTimeout(timeout);
            resolve({ code: null, output: `${output}\n${error.message}` });
        });
        child.on("exit", (code) => {
            if (settled) return;
            settled = true;
            clearTimeout(timeout);
            resolve({ code, output });
        });
    });
}

function hardwareGpuAdapters(adapters: string[]) {
    const virtualAdapter = /(?:virtual|remote|basic display|basic render|indirect display|parsec|spacedesk)/i;
    return adapters.filter((adapter) => adapter && !virtualAdapter.test(adapter));
}

async function detectGpuAdapters() {
    if (process.platform === "win32") {
        const powershell = path.join(
            process.env.SystemRoot || "C:\\Windows",
            "System32",
            "WindowsPowerShell",
            "v1.0",
            "powershell.exe",
        );
        const script = "$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); @(Get-CimInstance Win32_VideoController | ForEach-Object {$_.Name}) | ConvertTo-Json -Compress";
        const result = await runProbeProcess(powershell, ["-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script]);
        if (result.code !== 0) return [];
        try {
            const value = JSON.parse(result.output.trim());
            return hardwareGpuAdapters(
                (Array.isArray(value) ? value : [value]).map(String).map((item) => item.trim()).filter(Boolean),
            );
        } catch {
            return [];
        }
    }
    if (process.platform === "darwin") {
        const result = await runProbeProcess("system_profiler", ["SPDisplaysDataType", "-json"]);
        if (result.code !== 0) return [];
        try {
            const payload = JSON.parse(result.output) as { SPDisplaysDataType?: Array<Record<string, unknown>> };
            return (payload.SPDisplaysDataType || []).map((item) => String(item.sppci_model || item._name || "").trim()).filter(Boolean);
        } catch {
            return [];
        }
    }
    const nvidia = await runProbeProcess("nvidia-smi", ["--query-gpu=name", "--format=csv,noheader"]);
    if (nvidia.code === 0) return nvidia.output.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
    const pci = await runProbeProcess("lspci", ["-mm"]);
    return pci.code === 0
        ? pci.output.split(/\r?\n/).filter((line) => /(?:VGA compatible controller|3D controller)/i.test(line)).map((line) => line.trim())
        : [];
}

async function detectFeaturePackInstallEnvironment(pythonExe: string, output: fs.WriteStream): Promise<FeaturePackInstallEnvironment> {
    const script = "import json,platform,sys; print(json.dumps({'pythonVersion':platform.python_version(),'pythonImplementation':platform.python_implementation(),'architecture':platform.machine() or platform.architecture()[0]}))";
    const [python, gpuAdapters] = await Promise.all([
        runProbeProcess(pythonExe, ["-c", script]),
        detectGpuAdapters(),
    ]);
    if (python.code !== 0) throw new Error("Feature pack Python environment probe failed. See logRef for details.");
    const pythonPayload = JSON.parse(python.output.trim()) as Record<string, unknown>;
    const environment = {
        platform: process.platform,
        architecture: String(pythonPayload.architecture || process.arch),
        pythonVersion: String(pythonPayload.pythonVersion || "unknown"),
        pythonImplementation: String(pythonPayload.pythonImplementation || "unknown"),
        gpuAdapters: [...new Set(gpuAdapters)],
        gpuDetected: gpuAdapters.length > 0,
    };
    output.write(`[Environment] ${JSON.stringify(environment)}\n`);
    return environment;
}

function assetSmokeScript(kind: "onnx" | "mediapipe_task") {
    if (kind === "onnx") {
        return [
            "import json,sys",
            "sys.path.insert(0, sys.argv[1])",
            "import onnxruntime as ort",
            "providers=ort.get_available_providers()",
            "gpu=[p for p in providers if p not in ('CPUExecutionProvider','AzureExecutionProvider')]",
            "selected=(gpu[0] if sys.argv[3]=='GPU' and gpu else 'CPUExecutionProvider')",
            "ordered=[selected]+([ 'CPUExecutionProvider' ] if selected!='CPUExecutionProvider' else [])",
            "session=ort.InferenceSession(sys.argv[2], providers=ordered)",
            "assert session.get_inputs() and session.get_outputs()",
            "print('__V8_SMOKE__'+json.dumps({'kind':'onnx','availableProviders':providers,'selectedExecutionProvider':selected}))",
        ].join("; ");
    }
    return [
        "import json,sys",
        "sys.path.insert(0, sys.argv[1])",
        "import mediapipe as mp",
        "def open_task(delegate):",
        " options=mp.tasks.vision.HolisticLandmarkerOptions(base_options=mp.tasks.BaseOptions(model_asset_path=sys.argv[2],delegate=delegate),running_mode=mp.tasks.vision.RunningMode.IMAGE)",
        " task=mp.tasks.vision.HolisticLandmarker.create_from_options(options)",
        " task.close()",
        "selected=('GPU' if sys.argv[3]=='GPU' else 'CPU')",
        "delegate=(mp.tasks.BaseOptions.Delegate.GPU if selected=='GPU' else mp.tasks.BaseOptions.Delegate.CPU)",
        "open_task(delegate)",
        "print('__V8_SMOKE__'+json.dumps({'kind':'mediapipe_task','task':'holistic_landmarker','selectedExecutionProvider':selected,'mediapipeVersion':getattr(mp,'__version__','unknown')}))",
    ].join("\n");
}

async function runAssetSmokeCheck(
    pythonExe: string,
    pythonRoot: string,
    modelPath: string,
    smokeCheck: NonNullable<FeaturePackAssetManifest["smokeCheck"]>,
    environment: FeaturePackInstallEnvironment,
    output: fs.WriteStream,
) {
    const execute = async (provider: "CPU" | "GPU") => {
        const result = await runProbeProcess(
            pythonExe,
            ["-c", assetSmokeScript(smokeCheck.kind), pythonRoot, modelPath, provider],
            60_000,
        );
        output.write(`\n[${provider} smoke]\n${result.output}`);
        const marker = result.output.split(/\r?\n/).find((line) => line.startsWith("__V8_SMOKE__"));
        return {
            result,
            payload: result.code === 0 && marker
                ? JSON.parse(marker.slice("__V8_SMOKE__".length)) as Record<string, unknown>
                : null,
        };
    };
    let gpuFailure: string | null = null;
    if (environment.gpuDetected && smokeCheck.preferGpu) {
        const gpu = await execute("GPU");
        if (gpu.payload) return gpu.payload;
        gpuFailure = gpu.result.output.trim().slice(-500) || `exit=${gpu.result.code ?? "timeout"}`;
        output.write("\n[GPU fallback] The detected adapter has no working provider for this feature pack; validating CPU in a fresh process.\n");
    }
    const cpu = await execute("CPU");
    if (!cpu.payload) {
        throw new Error(`Feature pack ${smokeCheck.kind} validation failed (${cpu.result.code ?? "timeout"}). See logRef for details.`);
    }
    return gpuFailure ? { ...cpu.payload, gpuProbeError: gpuFailure } : cpu.payload;
}

async function runTransactionalAssetPackInstall(input: {
    definition: FeaturePackDefinition;
    manifest: FeaturePackAssetManifest;
    pythonExe: string;
    targetDir: string;
    requirementsFile: string;
    logRef: string;
    output: fs.WriteStream;
    sources: PipSource[];
}) {
    const { definition, manifest, pythonExe, targetDir, requirementsFile, logRef, output, sources } = input;
    const packRoot = path.dirname(targetDir);
    const stagingBase = path.join(featurePackRoot(), ".staging");
    const stagingRoot = path.join(stagingBase, `${definition.id}-${Date.now()}`);
    const stagingPython = path.join(stagingRoot, "python");
    const stagingModels = path.join(stagingRoot, "models");
    const backupRoot = `${packRoot}.backup-${Date.now()}`;
    const previousReceiptPath = path.join(packRoot, "receipt.json");
    const hadInstalledPack = fs.existsSync(targetDir) && fs.existsSync(previousReceiptPath);
    let previousReceipt: Record<string, unknown> = {};
    if (hadInstalledPack) {
        try {
            previousReceipt = JSON.parse(fs.readFileSync(previousReceiptPath, "utf-8")) as Record<string, unknown>;
        } catch {
            previousReceipt = {};
        }
    }
    let swapped = false;
    fs.mkdirSync(stagingPython, { recursive: true });
    fs.mkdirSync(stagingModels, { recursive: true });
    try {
        if (!manifest.smokeCheck) throw new Error("Feature pack asset manifest has no smokeCheck contract.");
        const environment = await detectFeaturePackInstallEnvironment(pythonExe, output);
        await installPipDependencies(pythonExe, stagingPython, requirementsFile, output, sources);
        const verifiedAssets = [];
        for (const asset of manifest.assets) {
            verifiedAssets.push(await downloadFeaturePackAsset(asset, stagingModels, output));
        }
        const primaryModel = verifiedAssets[0]?.path;
        if (!primaryModel) throw new Error("Feature pack manifest has no model asset");
        const smokeResult = await runAssetSmokeCheck(
            pythonExe,
            stagingPython,
            primaryModel,
            manifest.smokeCheck,
            environment,
            output,
        );
        const receipt = {
            version: 1,
            packId: definition.id,
            packVersion: manifest.version,
            installedAt: nowIso(),
            license: manifest.license || null,
            environment,
            smokeCheck: smokeResult,
            assets: verifiedAssets.map((asset) => ({
                id: asset.id,
                target: asset.target,
                size: asset.size,
                sha256: asset.verifiedSha256,
                url: asset.url,
            })),
        };
        fs.writeFileSync(path.join(stagingRoot, "receipt.json"), JSON.stringify(receipt, null, 2), "utf-8");
        if (fs.existsSync(packRoot)) fs.renameSync(packRoot, backupRoot);
        try {
            fs.renameSync(stagingRoot, packRoot);
            swapped = true;
        } catch (error) {
            if (fs.existsSync(backupRoot) && !fs.existsSync(packRoot)) fs.renameSync(backupRoot, packRoot);
            throw error;
        }
        updateFeaturePackConfig(definition.id, {
            status: "installed",
            targetDir: path.join(packRoot, "python"),
            assetRoot: path.join(packRoot, "models"),
            receiptRef: path.join(packRoot, "receipt.json"),
            version: manifest.version,
            logRef,
            lastError: null,
            restartRequired: true,
        });
        fs.rmSync(backupRoot, { recursive: true, force: true });
        output.end();
    } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        output.write(`\n[Transactional install error] ${message}\n`);
        if (swapped && fs.existsSync(packRoot)) {
            fs.rmSync(packRoot, { recursive: true, force: true });
        }
        if (fs.existsSync(backupRoot) && !fs.existsSync(packRoot)) {
            fs.renameSync(backupRoot, packRoot);
        }
        fs.rmSync(stagingRoot, { recursive: true, force: true });
        try {
            updateFeaturePackConfig(definition.id, hadInstalledPack ? {
                status: "installed",
                targetDir,
                assetRoot: path.join(packRoot, "models"),
                receiptRef: previousReceiptPath,
                version: previousReceipt.packVersion || null,
                logRef,
                lastError: message,
                restartRequired: true,
            } : {
                status: "failed",
                targetDir,
                logRef,
                lastError: message,
                restartRequired: true,
            });
        } catch (configError) {
            output.write(`[Config recovery error] ${configError instanceof Error ? configError.message : String(configError)}\n`);
        }
        output.end();
    }
}

async function runFeaturePackInstallSequence(input: {
    definition: FeaturePackDefinition;
    pythonExe: string;
    targetDir: string;
    requirementsFile: string;
    logRef: string;
    output: fs.WriteStream;
    sources: PipSource[];
}) {
    const { definition, pythonExe, targetDir, requirementsFile, logRef, output, sources } = input;
    const assetManifest = readAssetManifest(definition);
    if (assetManifest) {
        await runTransactionalAssetPackInstall({
            definition,
            manifest: assetManifest,
            pythonExe,
            targetDir,
            requirementsFile,
            logRef,
            output,
            sources,
        });
        return;
    }
    const attempts: PipAttemptSummary[] = [];
    try {
        await detectFeaturePackInstallEnvironment(pythonExe, output);
        for (const [index, source] of sources.entries()) {
            const args = buildPipInstallArgs(targetDir, requirementsFile, source);
            const commandSummary = formatCommandSummary(pythonExe, args);
            output.write(`\n[Source] ${source.label}\n`);
            output.write(`[Command] ${commandSummary}\n\n`);
            const result = await runPipAttempt(pythonExe, args, output);
            const ok = result.exitCode === 0;
            const recoverable = ok ? false : isRecoverablePipFailure(result.outputPreview, source);
            attempts.push({
                sourceId: source.id,
                sourceLabel: source.label,
                exitCode: result.exitCode,
                recoverable,
                error: result.error,
            });
            output.write(`\n[Exit code] ${result.exitCode ?? "unknown"}\n`);
            if (ok) {
                output.end();
                updateFeaturePackConfig(definition.id, {
                    status: "installed",
                    targetDir,
                    logRef,
                    lastError: null,
                    restartRequired: true,
                });
                return;
            }
            if (!recoverable || index === sources.length - 1) {
                break;
            }
            const nextSource = sources[index + 1];
            output.write(`[Fallback] ${source.label} failed with a recoverable download error; retrying via ${nextSource.label}.\n`);
        }
        output.end();
        updateFeaturePackConfig(definition.id, {
            status: "failed",
            targetDir,
            logRef,
            lastError: buildInstallFailureMessage(attempts),
            restartRequired: true,
        });
    } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        output.write(`\n[Install sequence error] ${message}\n`);
        output.end();
        updateFeaturePackConfig(definition.id, {
            status: "failed",
            targetDir,
            logRef,
            lastError: "Feature pack install failed unexpectedly. See logRef for details.",
            restartRequired: true,
        });
    }
}

export async function triggerFeaturePackInstall(packId: string, dryRun = false, locale = "en") {
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
    const assetManifest = readAssetManifest(definition);
    const pythonExe = resolvePythonExecutable(config);
    const sources = pipSourceStrategy(locale);
    const firstAttemptArgs = buildPipInstallArgs(targetDir, requirementsFile, sources[0]);
    const commandSummary = formatCommandSummary(pythonExe, firstAttemptArgs);
    if (dryRun) {
        return {
            status: "dry_run",
            packId: definition.id,
            commandSummary,
            sourceStrategy: sourceStrategyForResponse(sources),
            targetDir,
            requirementsFile,
            assetManifest: assetManifest ? {
                version: assetManifest.version,
                license: assetManifest.license || null,
                assets: assetManifest.assets.map((asset) => ({ id: asset.id, size: asset.size, sha256: asset.sha256 })),
            } : null,
            restartRequired: true,
        };
    }

    const existing = (config.runtimeRegistry?.featurePacks?.[definition.id] || {}) as FeaturePackConfigRecord;
    if (normalizeStatus(existing.status) === "installing") {
        return {
            status: "installing",
            packId: definition.id,
            commandSummary,
            sourceStrategy: sourceStrategyForResponse(sources),
            targetDir: String(existing.targetDir || targetDir),
            requirementsFile,
            logRef: existing.logRef || null,
            restartRequired: true,
            message: "能力包正在安装，本次请求未重复启动下载。",
        };
    }

    if (!definition.assetManifestFile) {
        fs.mkdirSync(targetDir, { recursive: true });
    }
    fs.mkdirSync(featurePackLogRoot(), { recursive: true });
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    const logRef = path.join(featurePackLogRoot(), `${definition.id}-${timestamp}.log`);
    const output = fs.createWriteStream(logRef, { flags: "a", encoding: "utf-8" });
    output.write(`[V8OS Feature Pack] ${definition.productName}\n`);
    output.write(`[Locale] ${locale}\n`);
    output.write(`[Source strategy] ${sources.map((source) => source.label).join(" -> ")}\n`);

    updateFeaturePackConfig(definition.id, {
        status: "installing",
        targetDir,
        logRef,
        lastError: null,
        restartRequired: true,
    });

    void runFeaturePackInstallSequence({
        definition,
        pythonExe,
        targetDir,
        requirementsFile,
        logRef,
        output,
        sources,
    });

    return {
        status: "started",
        packId: definition.id,
        commandSummary,
        sourceStrategy: sourceStrategyForResponse(sources),
        targetDir,
        requirementsFile,
        logRef,
        restartRequired: true,
        message: "能力包安装已开始。安装完成后如状态提示需要重启，请重启 Engine 后继续使用。",
    };
}
