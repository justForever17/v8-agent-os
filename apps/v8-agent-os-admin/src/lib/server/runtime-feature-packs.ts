import fs from "fs";
import crypto from "crypto";
import os from "os";
import path from "path";
import { spawn, type ChildProcess } from "child_process";
import { Readable, Transform } from "stream";
import { pipeline } from "stream/promises";

import {
    canonicalConfigPath,
    readCanonicalAdminRuntimeConfig,
    type CanonicalConfig,
} from "@/lib/server/bridge-config";
import { readEngineFeaturePackSnapshot } from "@/lib/server/engine-feature-pack-snapshot";
import {
    createFeaturePackInstallJournal,
    isFeaturePackOperationId,
    listFeaturePackInstallJournals,
    planFeaturePackInstallRecovery,
    readFeaturePackInstallJournal,
    transitionFeaturePackInstallJournal,
    type FeaturePackInstallJournal,
} from "@/lib/server/feature-pack-transaction-journal";
import {
    engineFeaturePackSnapshotIsAuthoritative,
    mergeFeaturePackTruth,
} from "@/lib/server/runtime-feature-pack-truth";
import { resolveEngineOrigin, resolveInternalSecret } from "@/lib/server/runtime-config";

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
    refreshing: boolean;
    retryAfterMs: number | null;
    updatedAt: number | null;
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
    lockFilePrefix?: string;
    assetManifestFile?: string;
    smokeModules?: string[];
    smokeModulesByPlatform?: Partial<Record<NodeJS.Platform, string[]>>;
    enabled?: boolean;
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
    operationId?: string | null;
    startedAt?: string | null;
};

const FEATURE_PACK_DEFINITIONS: FeaturePackDefinition[] = [
    {
        id: "document_ingestion",
        productName: "文档读取能力包",
        shortName: "文档读取",
        description: "启用 DOCX、XLS/XLSX、PPTX 和 PDF 的本地解析。",
        hover: "安装后 read_native_file 与记忆入库可读取现代 Office 文档和 PDF；中文环境自动优先可信镜像。",
        recommendedOrder: 1,
        runtimeFamilies: [],
        requirementsFile: "document-ingestion.txt",
        smokeModules: ["pandas", "openpyxl", "xlrd", "docx", "pptx", "fitz", "tabulate"],
    },
    {
        id: "computer_use_desktop",
        productName: "桌面操作能力包",
        shortName: "桌面操作",
        description: "启用桌面截图、窗口识别、点击输入和桌面直播采集。",
        hover: "安装后接入桌面操作与 Desktop Live；适合需要 V8OS 观察并操作本机应用的场景。",
        recommendedOrder: 2,
        runtimeFamilies: ["computer_use", "desktop_live"],
        requirementsFile: "computer-use-desktop.txt",
        smokeModules: ["mss", "av", "aiortc"],
        smokeModulesByPlatform: { win32: ["pywinauto", "pycaw"] },
    },
    {
        id: "rpa_automation",
        productName: "自动流程能力包",
        shortName: "自动流程",
        description: "启用 Robot Framework 的浏览器与表格流程能力；Windows 另含桌面自动化库。",
        hover: "安装受管的 Browser、Excel 能力，Windows 另含 RPA.Windows；已有流程会先做依赖校验。",
        recommendedOrder: 3,
        runtimeFamilies: ["rpa"],
        requirementsFile: "rpa-automation.txt",
        lockFilePrefix: "rpa-automation-cp311",
        smokeModules: ["robot", "RPA", "RPA.Browser.Selenium", "RPA.Excel.Files"],
        smokeModulesByPlatform: { win32: ["RPA.Windows"] },
    },
    {
        id: "local_asr_ocr",
        productName: "可选本地识别包",
        shortName: "本地识别",
        description: "为高性能本机提供本地语音转写、OCR 和媒体理解增强。",
        hover: "适合电脑性能较高且不想依赖云供应商的用户；安装后按需接入本地语音转写、OCR、媒体/附件理解增强。",
        recommendedOrder: 4,
        runtimeFamilies: [],
        requirementsFile: "local-asr-ocr.txt",
        smokeModules: ["faster_whisper", "paddleocr"],
        enabled: false,
    },
    {
        id: "creative_media_image_analysis",
        productName: "图像分析增强包",
        shortName: "图像分析",
        description: "为多媒体创作提供本地主体分割、透明度核验和跨图构图比较。",
        hover: "安装后可离线复用已验签的 IS-Net 模型；仅在复杂不透明背景需要主体分割时使用。",
        recommendedOrder: 5,
        runtimeFamilies: [],
        requirementsFile: "creative-media-image-analysis.txt",
        lockFilePrefix: "creative-media-image-analysis-cp311",
        assetManifestFile: "creative-media-image-analysis.manifest.json",
    },
    {
        id: "creative_media_motion_capture",
        productName: "动作采集能力包",
        shortName: "动作采集",
        description: "为多媒体创作提供单人视频动作提取、骨架预览和动作质量核验。",
        hover: "安装后可离线使用已验签的 MediaPipe Holistic 模型；首版仅支持单人视频或摄像头录制文件。",
        recommendedOrder: 6,
        runtimeFamilies: [],
        requirementsFile: "creative-media-motion-capture.txt",
        assetManifestFile: "creative-media-motion-capture.manifest.json",
    },
];

const FEATURE_PACK_BY_ID = new Map(FEATURE_PACK_DEFINITIONS.map((definition) => [definition.id, definition]));
const ACTIVE_FEATURE_PACK_INSTALLS = new Map<string, string>();
const PENDING_FEATURE_PACK_INSTALLS = new Map<string, string>();
const ACTIVE_FEATURE_PACK_RECOVERIES = new Set<string>();
let FEATURE_PACK_INSTALL_RESERVATION: string | null = null;
const FEATURE_PACK_CONFIG_TIMEOUT_MS = 8_000;
const FEATURE_PACK_PIP_TIMEOUT_MS = 45 * 60_000;
const FEATURE_PACK_ASSET_TIMEOUT_MS = 30 * 60_000;
const FEATURE_PACK_ASSET_MAX_REDIRECTS = 3;
const FEATURE_PACK_STALE_INSTALL_MS = 90 * 60_000;
const FEATURE_PACK_LOG_ERRORS = new WeakMap<fs.WriteStream, Error>();
const FEATURE_PACK_COMMIT_BLOCKING_STATUSES = new Set([401, 409, 422]);

const FEATURE_PACK_CHILD_ENV_KEYS = new Set([
    "APPDATA",
    "COMSPEC",
    "CUDA_VISIBLE_DEVICES",
    "HOME",
    "HOMEDRIVE",
    "HOMEPATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOCALAPPDATA",
    "NO_PROXY",
    "NVIDIA_VISIBLE_DEVICES",
    "NUMBER_OF_PROCESSORS",
    "PATH",
    "PATHEXT",
    "PIP_CERT",
    "PROCESSOR_ARCHITECTURE",
    "PROGRAMDATA",
    "REQUESTS_CA_BUNDLE",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "TZ",
    "USERPROFILE",
    "WINDIR",
]);

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

function lockRuntimePlatform(value: NodeJS.Platform) {
    if (value === "win32") return "windows";
    if (value === "darwin") return "macos";
    if (value === "linux") return "linux";
    return null;
}

function lockRuntimeArchitecture(value: unknown) {
    const normalized = normalizedArchitecture(value);
    if (normalized === "amd64") return "x64";
    if (normalized === "arm64") return "arm64";
    return null;
}

function lockPathFor(
    definition: FeaturePackDefinition,
    environment?: Pick<FeaturePackInstallEnvironment, "platform" | "architecture" | "pythonVersion">,
) {
    if (!definition.lockFilePrefix) return null;
    const runtimePlatform = lockRuntimePlatform(environment?.platform || process.platform);
    const runtimeArchitecture = lockRuntimeArchitecture(environment?.architecture || process.arch);
    const runtimePythonMinor = environment ? pythonMinor(environment.pythonVersion) : "3.11";
    if (!runtimePlatform || !runtimeArchitecture || runtimePythonMinor !== "3.11") return null;
    return path.join(
        resolveEngineRoot(),
        "requirements",
        "feature-packs",
        "locks",
        `${definition.lockFilePrefix}-${runtimePlatform}-${runtimeArchitecture}.txt`,
    );
}

function validateHashedLockFile(lockFile: string) {
    if (!fs.existsSync(lockFile)) return false;
    const entries = requirementsEntries(lockFile);
    if (!entries.length) return false;
    const names = new Set<string>();
    for (const entry of entries) {
        const match = entry.match(/^([A-Za-z0-9][A-Za-z0-9_.-]*)==([^\s]+) --hash=sha256:([0-9a-f]{64})$/);
        if (!match) return false;
        const normalizedName = match[1].toLowerCase().replace(/[-_.]+/g, "-");
        if (names.has(normalizedName)) return false;
        names.add(normalizedName);
    }
    return true;
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

function requirementsEntries(requirementsFile: string) {
    if (!fs.existsSync(requirementsFile)) return [];
    return fs.readFileSync(requirementsFile, "utf-8")
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter((line) => line && !line.startsWith("#"));
}

function definitionInstallable(definition: FeaturePackDefinition) {
    if (definition.enabled === false) return false;
    const requirementsFile = requirementsPathFor(definition);
    const manifestPath = assetManifestPathFor(definition);
    const lockFile = lockPathFor(definition);
    const lockReady = !definition.lockFilePrefix || Boolean(lockFile && validateHashedLockFile(lockFile));
    return requirementsEntries(requirementsFile).length > 0
        && lockReady
        && (!manifestPath || fs.existsSync(manifestPath));
}

function smokeModulesFor(definition: FeaturePackDefinition) {
    return [
        ...(definition.smokeModules || []),
        ...(definition.smokeModulesByPlatform?.[process.platform] || []),
    ];
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

function createGovernedFeaturePackLog(logRef: string) {
    const descriptor = fs.openSync(logRef, "a");
    let output: fs.WriteStream;
    try {
        output = fs.createWriteStream(logRef, {
            fd: descriptor,
            autoClose: true,
            encoding: "utf-8",
        });
    } catch (error) {
        fs.closeSync(descriptor);
        throw error;
    }
    output.on("error", (error) => {
        FEATURE_PACK_LOG_ERRORS.set(output, error);
    });
    return output;
}

function assertFeaturePackLogHealthy(output: fs.WriteStream) {
    if (FEATURE_PACK_LOG_ERRORS.has(output) || output.destroyed) {
        throw new Error("feature_pack_log_write_failed");
    }
}

function safeFeaturePackLogWrite(output: fs.WriteStream, message: string) {
    if (output.destroyed || output.writableEnded) return;
    try {
        output.write(message);
    } catch (error) {
        FEATURE_PACK_LOG_ERRORS.set(output, error instanceof Error ? error : new Error(String(error)));
    }
}

function closeFeaturePackLog(output: fs.WriteStream) {
    if (output.destroyed || output.writableEnded) return;
    try {
        output.end();
    } catch (error) {
        FEATURE_PACK_LOG_ERRORS.set(output, error instanceof Error ? error : new Error(String(error)));
    }
}

function featurePackChildEnv(extra: Record<string, string | undefined> = {}) {
    const env: NodeJS.ProcessEnv = { NODE_ENV: process.env.NODE_ENV };
    for (const [key, value] of Object.entries(process.env)) {
        if (value !== undefined && FEATURE_PACK_CHILD_ENV_KEYS.has(key.toUpperCase())) {
            env[key] = value;
        }
    }
    return {
        ...env,
        PYTHONUTF8: "1",
        PYTHONIOENCODING: "utf-8",
        PYTHONNOUSERSITE: "1",
        PIP_DISABLE_PIP_VERSION_CHECK: "1",
        PIP_NO_INPUT: "1",
        ...extra,
    };
}

function normalizedArchitecture(value: unknown) {
    const normalized = String(value || "").trim().toLowerCase().replace(/[^a-z0-9]/g, "");
    if (normalized === "x64" || normalized === "x8664" || normalized === "amd64") return "amd64";
    if (normalized === "aarch64" || normalized === "arm64") return "arm64";
    return normalized;
}

function pythonMinor(value: unknown) {
    return String(value || "").trim().match(/^(\d+)\.(\d+)/)?.slice(1, 3).join(".") || "";
}

function previousReceiptIsCompatible(input: {
    definition: FeaturePackDefinition;
    receipt: Record<string, unknown>;
    environment: FeaturePackInstallEnvironment;
    requirementsFile: string;
    manifest?: FeaturePackAssetManifest | null;
}) {
    const { definition, receipt, environment, requirementsFile, manifest } = input;
    if (String(receipt.packId || "") !== definition.id) return false;
    const previousEnvironment = receipt.environment && typeof receipt.environment === "object"
        ? receipt.environment as Record<string, unknown>
        : {};
    if (String(previousEnvironment.platform || "") !== environment.platform) return false;
    if (pythonMinor(previousEnvironment.pythonVersion) !== pythonMinor(environment.pythonVersion)) return false;
    if (String(previousEnvironment.pythonImplementation || "").trim().toLowerCase()
        !== environment.pythonImplementation.trim().toLowerCase()) return false;
    if (normalizedArchitecture(previousEnvironment.architecture)
        !== normalizedArchitecture(environment.architecture)) return false;
    const requirements = receipt.requirements && typeof receipt.requirements === "object"
        ? receipt.requirements as Record<string, unknown>
        : {};
    if (String(requirements.sha256 || "").toLowerCase() !== sha256File(requirementsFile).toLowerCase()) return false;
    const lockFile = lockPathFor(definition, environment);
    if (definition.lockFilePrefix) {
        if (!lockFile || !validateHashedLockFile(lockFile)) return false;
        if (String(requirements.lockFile || "") !== path.basename(lockFile)) return false;
        if (String(requirements.lockSha256 || "").toLowerCase() !== sha256File(lockFile).toLowerCase()) return false;
    }
    return !manifest || String(receipt.packVersion || "") === manifest.version;
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

function readEngineFeaturePacks(payload: Record<string, unknown> | null) {
    const rawPacks = Array.isArray(payload?.featurePacks) ? payload.featurePacks : null;
    if (!rawPacks) return null;
    const rawById = new Map<string, Record<string, unknown>>();
    for (const pack of rawPacks) {
        if (!pack || typeof pack !== "object") continue;
        const raw = pack as Record<string, unknown>;
        rawById.set(String(raw.id || ""), raw);
    }
    return FEATURE_PACK_DEFINITIONS.map((definition) =>
        normalizeFeaturePackFromEngine(definition, rawById.get(definition.id) || ({} as Record<string, unknown>)),
    ).sort((a, b) => a.recommendedOrder - b.recommendedOrder);
}

export async function getRuntimeFeaturePackState(options: { forceHealthRefresh?: boolean } = {}): Promise<RuntimeFeaturePackState> {
    let config = readCanonicalAdminRuntimeConfig();
    if (await reconcileInterruptedFeaturePackInstalls(config)) {
        config = readCanonicalAdminRuntimeConfig();
    }
    const engineHealth = await readEngineFeaturePackSnapshot({
        origin: resolveEngineOrigin(),
        internalSecret: resolveInternalSecret(),
        force: options.forceHealthRefresh,
    });
    const enginePacks = engineFeaturePackSnapshotIsAuthoritative(engineHealth)
        ? readEngineFeaturePacks(engineHealth.data)
        : null;
    const configPacks = FEATURE_PACK_DEFINITIONS.map((definition) => normalizeFeaturePackFromConfig(definition, config));
    const packs = mergeFeaturePackTruth(configPacks, enginePacks, engineHealth.updatedAt);
    return {
        engineAvailable: engineHealth.available === true,
        refreshing: engineHealth.refreshing,
        retryAfterMs: engineHealth.refreshing ? 1_500 : null,
        updatedAt: engineHealth.updatedAt || null,
        packs,
        summary: summarize(packs),
        installRoot: featurePackRoot(),
        logRoot: featurePackLogRoot(),
        configPath: canonicalConfigPath(),
    };
}

function cleanupInterruptedStaging(packId: string, operationId: string) {
    if (!/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(operationId)) return;
    const stagingRoot = path.join(featurePackRoot(), ".staging");
    if (!fs.existsSync(stagingRoot)) return;
    const operationRoot = path.join(stagingRoot, `${packId}-${operationId}`);
    if (fs.existsSync(operationRoot)) fs.rmSync(operationRoot, { recursive: true, force: true });
}

class FeaturePackStateCommitError extends Error {
    readonly status: number | null;
    readonly responseReceived: boolean;

    constructor(message: string, status: number | null, responseReceived: boolean) {
        super(message);
        this.name = "FeaturePackStateCommitError";
        this.status = status;
        this.responseReceived = responseReceived;
    }
}

function featurePackErrorMessage(error: unknown) {
    return error instanceof Error ? error.message : String(error);
}

function isBlockingFeaturePackCommitError(error: unknown) {
    return error instanceof FeaturePackStateCommitError
        && error.status !== null
        && FEATURE_PACK_COMMIT_BLOCKING_STATUSES.has(error.status);
}

function journalHasBlockingCommitReceipt(journal: FeaturePackInstallJournal) {
    return /^feature_pack_state_commit_(?:401|409|422)$/.test(String(journal.lastError || ""));
}

async function updateFeaturePackConfig(
    packId: string,
    patch: Record<string, unknown>,
    expectedOperationId: string | null,
) {
    const internalSecret = resolveInternalSecret();
    if (!internalSecret) {
        throw new FeaturePackStateCommitError("feature_pack_state_authority_unavailable", null, false);
    }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), FEATURE_PACK_CONFIG_TIMEOUT_MS);
    let responseReceived = false;
    try {
        const response = await fetch(
            `${resolveEngineOrigin()}/v1/runtime-feature-packs/${encodeURIComponent(packId)}/state`,
            {
                method: "PATCH",
                headers: {
                    "Content-Type": "application/json",
                    "x-v8-agent-os-secret": internalSecret,
                },
                body: JSON.stringify({
                    patch,
                    expectedOperationId,
                }),
                cache: "no-store",
                signal: controller.signal,
            },
        );
        responseReceived = true;
        if (!response.ok) {
            throw new FeaturePackStateCommitError(
                `feature_pack_state_commit_${response.status}`,
                response.status,
                true,
            );
        }
    } catch (error) {
        if (responseReceived) throw error;
        const committed = (readCanonicalAdminRuntimeConfig().runtimeRegistry?.featurePacks?.[packId] || {}) as FeaturePackConfigRecord;
        const patchMatches = Object.entries(patch).every(([field, expected]) => {
            const actual = committed[field as keyof FeaturePackConfigRecord];
            return (actual ?? null) === (expected ?? null);
        });
        if (!patchMatches) throw error;
    } finally {
        clearTimeout(timeout);
    }
}

function currentFeaturePackConfig(packId: string) {
    return (readCanonicalAdminRuntimeConfig().runtimeRegistry?.featurePacks?.[packId] || {}) as FeaturePackConfigRecord;
}

async function commitPublishedFeaturePackJournal(journal: FeaturePackInstallJournal) {
    if (!journal.finalPatch) {
        transitionFeaturePackInstallJournal(featurePackRoot(), journal, "recovery_pending", {
            lastError: "feature_pack_journal_final_patch_missing",
        });
        return false;
    }
    let pending = transitionFeaturePackInstallJournal(featurePackRoot(), journal, "commit_pending", {
        commitAttempts: journal.commitAttempts + 1,
        lastError: null,
    });
    try {
        await updateFeaturePackConfig(journal.packId, journal.finalPatch, journal.operationId);
    } catch (error) {
        const message = featurePackErrorMessage(error);
        pending = transitionFeaturePackInstallJournal(
            featurePackRoot(),
            pending,
            isBlockingFeaturePackCommitError(error) ? "commit_blocked" : "commit_pending",
            { lastError: message },
        );
        return false;
    }
    transitionFeaturePackInstallJournal(featurePackRoot(), pending, "committed", { lastError: null });
    return true;
}

async function publishStagedFeaturePackJournal(journal: FeaturePackInstallJournal) {
    if (!fs.existsSync(journal.paths.stagingRoot) || !fs.existsSync(path.join(journal.paths.stagingRoot, "receipt.json"))) {
        transitionFeaturePackInstallJournal(featurePackRoot(), journal, "recovery_pending", {
            lastError: "feature_pack_staging_receipt_missing",
        });
        return false;
    }
    if (fs.existsSync(journal.paths.versionRoot)) {
        transitionFeaturePackInstallJournal(featurePackRoot(), journal, "recovery_pending", {
            lastError: "feature_pack_version_target_already_exists",
        });
        return false;
    }
    fs.mkdirSync(path.dirname(journal.paths.versionRoot), { recursive: true });
    fs.renameSync(journal.paths.stagingRoot, journal.paths.versionRoot);
    const published = transitionFeaturePackInstallJournal(featurePackRoot(), journal, "published", {
        lastError: null,
    });
    return commitPublishedFeaturePackJournal(published);
}

function journalCanRestorePrevious(journal: FeaturePackInstallJournal) {
    const previous = journal.backup.state;
    return journal.backup.compatible !== false
        && String(previous.status || "") === "installed"
        && Boolean(previous.targetDir)
        && Boolean(previous.receiptRef);
}

async function settlePrePublishFeaturePackJournal(
    journal: FeaturePackInstallJournal,
    message: string,
    options: { preserveStaging?: boolean } = {},
) {
    let restorePrevious = journalCanRestorePrevious(journal);
    const recovering = transitionFeaturePackInstallJournal(featurePackRoot(), journal, "recovery_pending", {
        lastError: message,
    });
    const commitRecoveryState = (status: "installed" | "failed") => updateFeaturePackConfig(journal.packId, {
        status,
        logRef: journal.logRef,
        lastError: message,
        restartRequired: true,
        operationId: null,
        startedAt: null,
    }, journal.operationId);
    try {
        await commitRecoveryState(restorePrevious ? "installed" : "failed");
    } catch (error) {
        const currentOperationId = String(currentFeaturePackConfig(journal.packId).operationId || "").trim();
        if (
            restorePrevious
            && error instanceof FeaturePackStateCommitError
            && error.status === 409
            && currentOperationId === journal.operationId
        ) {
            try {
                await commitRecoveryState("failed");
                restorePrevious = false;
            } catch (fallbackError) {
                transitionFeaturePackInstallJournal(featurePackRoot(), recovering, "recovery_pending", {
                    lastError: featurePackErrorMessage(fallbackError),
                });
                return false;
            }
        } else {
            transitionFeaturePackInstallJournal(featurePackRoot(), recovering, "recovery_pending", {
                lastError: featurePackErrorMessage(error),
            });
            return false;
        }
    }
    if (!options.preserveStaging && fs.existsSync(journal.paths.stagingRoot)) {
        try {
            fs.rmSync(journal.paths.stagingRoot, { recursive: true, force: true });
        } catch (error) {
            transitionFeaturePackInstallJournal(featurePackRoot(), recovering, "recovery_pending", {
                lastError: `feature_pack_staging_cleanup_failed:${featurePackErrorMessage(error)}`,
            });
            return true;
        }
    }
    transitionFeaturePackInstallJournal(
        featurePackRoot(),
        recovering,
        restorePrevious ? "recovered" : "failed",
        { lastError: message },
    );
    return true;
}

async function reconcileFeaturePackInstallJournal(journal: FeaturePackInstallJournal) {
    const raw = currentFeaturePackConfig(journal.packId);
    const currentOperationId = String(raw.operationId || "").trim() || null;
    const locallyOwnedOperation = ACTIVE_FEATURE_PACK_INSTALLS.get(journal.packId)
        || PENDING_FEATURE_PACK_INSTALLS.get(journal.packId)
        || null;
    if (locallyOwnedOperation === journal.operationId) return false;
    if (
        ["commit_blocked", "recovery_pending"].includes(journal.phase)
        && journalHasBlockingCommitReceipt(journal)
        && currentOperationId === journal.operationId
    ) {
        return settlePrePublishFeaturePackJournal(
            journal,
            journal.lastError || "feature_pack_state_commit_blocked",
            { preserveStaging: true },
        );
    }
    const action = planFeaturePackInstallRecovery(journal, {
        activeOperationId: locallyOwnedOperation,
        currentOperationId,
        currentStatus: String(raw.status || "").trim() || null,
        currentTargetDir: String(raw.targetDir || "").trim() || null,
        currentReceiptRef: String(raw.receiptRef || "").trim() || null,
        stagingExists: fs.existsSync(journal.paths.stagingRoot),
        stagingReceiptExists: fs.existsSync(path.join(journal.paths.stagingRoot, "receipt.json")),
        versionExists: fs.existsSync(journal.paths.versionRoot),
        versionReceiptExists: fs.existsSync(journal.paths.receiptRef),
    });
    if (action === "none") return false;
    if (action === "mark_superseded") {
        transitionFeaturePackInstallJournal(featurePackRoot(), journal, "superseded", {
            lastError: "feature_pack_install_superseded",
        });
        return false;
    }
    if (action === "finalize_committed") {
        if (journalHasBlockingCommitReceipt(journal)) {
            transitionFeaturePackInstallJournal(featurePackRoot(), journal, "superseded", {
                lastError: journal.lastError,
            });
            return false;
        }
        transitionFeaturePackInstallJournal(featurePackRoot(), journal, "committed", { lastError: null });
        return true;
    }
    if (action === "publish_staging") return publishStagedFeaturePackJournal(journal);
    if (action === "commit_version") return commitPublishedFeaturePackJournal(journal);
    return settlePrePublishFeaturePackJournal(
        journal,
        action === "restore_previous"
            ? "feature_pack_install_interrupted"
            : "feature_pack_install_failed",
    );
}

function featurePackInstallIsStale(raw: FeaturePackConfigRecord) {
    const startedAt = Date.parse(String(raw.startedAt || ""));
    return !Number.isFinite(startedAt) || Date.now() - startedAt >= FEATURE_PACK_STALE_INSTALL_MS;
}

async function reconcileInterruptedFeaturePackInstalls(config: CanonicalConfig) {
    let changed = false;
    const journalOperations = new Set<string>();
    for (const journal of listFeaturePackInstallJournals(featurePackRoot())) {
        journalOperations.add(`${journal.packId}:${journal.operationId}`);
        const recoveryKey = `${journal.packId}:${journal.operationId}`;
        if (ACTIVE_FEATURE_PACK_RECOVERIES.has(recoveryKey)) continue;
        ACTIVE_FEATURE_PACK_RECOVERIES.add(recoveryKey);
        try {
            changed = await reconcileFeaturePackInstallJournal(journal) || changed;
        } finally {
            ACTIVE_FEATURE_PACK_RECOVERIES.delete(recoveryKey);
        }
    }
    config = readCanonicalAdminRuntimeConfig();
    for (const definition of FEATURE_PACK_DEFINITIONS) {
        const raw = (config.runtimeRegistry?.featurePacks?.[definition.id] || {}) as FeaturePackConfigRecord;
        if (normalizeStatus(raw.status) !== "installing") continue;
        const operationId = String(raw.operationId || "").trim();
        if (operationId && ACTIVE_FEATURE_PACK_INSTALLS.get(definition.id) === operationId) continue;
        if (operationId && PENDING_FEATURE_PACK_INSTALLS.get(definition.id) === operationId) continue;
        if (journalOperations.has(`${definition.id}:${operationId}`)) continue;
        if (!featurePackInstallIsStale(raw)) continue;
        try {
            await updateFeaturePackConfig(definition.id, {
                status: "failed",
                lastError: "feature_pack_install_interrupted",
                operationId: null,
                startedAt: null,
                restartRequired: true,
            }, operationId || null);
        } catch (error) {
            if (isBlockingFeaturePackCommitError(error)) continue;
            throw error;
        }
        cleanupInterruptedStaging(definition.id, operationId);
        changed = true;
    }
    return changed;
}

function resolvePythonExecutable(config: CanonicalConfig) {
    const explicitRuntime = String(process.env.V8_ENGINE_PYTHON || "").trim();
    if (explicitRuntime && fs.existsSync(explicitRuntime)) return explicitRuntime;
    const configured = String(config.systemBase?.channels?.enginePython || "").trim();
    const engineRoot = resolveEngineRoot();
    const managedCandidates = process.platform === "win32"
        ? [path.join(engineRoot, ".python", "python.exe")]
        : [path.join(engineRoot, ".python", "bin", "python3"), path.join(engineRoot, ".python", "bin", "python")];
    const managedRuntime = managedCandidates.find((candidate) => fs.existsSync(candidate));
    if (managedRuntime) return managedRuntime;
    if (configured && fs.existsSync(configured)) return configured;
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

function buildPipInstallArgs(
    targetDir: string,
    requirementsFile: string,
    reportFile: string,
    source: PipSource,
    hashedLock = false,
) {
    const args = [
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--prefer-binary",
        "--ignore-installed",
        "--retries",
        "2",
        "--timeout",
        "30",
        "--target",
        targetDir,
        "--report",
        reportFile,
        "-r",
        requirementsFile,
    ];
    if (hashedLock) {
        args.splice(5, 0, "--only-binary=:all:", "--require-hashes", "--no-deps");
    }
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

function waitForFeaturePackChildExit(child: ChildProcess, timeoutMs: number) {
    if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve(true);
    return new Promise<boolean>((resolve) => {
        let settled = false;
        const finish = (exited: boolean) => {
            if (settled) return;
            settled = true;
            clearTimeout(timeout);
            child.off("exit", onExit);
            resolve(exited);
        };
        const onExit = () => finish(true);
        const timeout = setTimeout(() => finish(false), timeoutMs);
        child.once("exit", onExit);
    });
}

async function terminateFeaturePackChild(child: ChildProcess) {
    if (!child.pid) return true;
    if (process.platform === "win32") {
        const killer = spawn("taskkill.exe", ["/pid", String(child.pid), "/t", "/f"], {
            windowsHide: true,
            stdio: "ignore",
            env: featurePackChildEnv(),
        });
        await new Promise<void>((resolve) => {
            const timeout = setTimeout(resolve, 10_000);
            const finish = () => {
                clearTimeout(timeout);
                resolve();
            };
            killer.once("error", finish);
            killer.once("exit", finish);
        });
        return waitForFeaturePackChildExit(child, 5_000);
    }
    try {
        process.kill(-child.pid, "SIGTERM");
    } catch {
        child.kill("SIGTERM");
    }
    if (await waitForFeaturePackChildExit(child, 5_000)) return true;
    try {
        process.kill(-child.pid, "SIGKILL");
    } catch {
        child.kill("SIGKILL");
    }
    return waitForFeaturePackChildExit(child, 5_000);
}

function runPipAttempt(
    pythonExe: string,
    args: string[],
    output: fs.WriteStream,
    timeoutMs: number,
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
            detached: process.platform !== "win32",
            stdio: ["ignore", "pipe", "pipe"],
            env: featurePackChildEnv(),
        });
        const timeout = setTimeout(() => {
            if (settled) return;
            settled = true;
            void terminateFeaturePackChild(child).catch(() => false).then((terminated) => {
                child.stdout?.off("data", appendOutput);
                child.stderr?.off("data", appendOutput);
                child.stdout?.destroy();
                child.stderr?.destroy();
                output.write("\n[Install timeout] pip exceeded the governed feature-pack deadline.\n");
                resolve({
                    exitCode: null,
                    outputPreview,
                    error: terminated ? "pip_timeout" : "feature_pack_worker_termination_unconfirmed",
                });
            });
        }, timeoutMs);
        child.stdout?.on("data", appendOutput);
        child.stderr?.on("data", appendOutput);
        child.on("error", (error) => {
            if (settled) return;
            settled = true;
            clearTimeout(timeout);
            output.write(`\n[Install error] ${error.message}\n`);
            resolve({ exitCode: null, outputPreview, error: error.message });
        });
        child.on("exit", (code) => {
            if (settled) return;
            settled = true;
            clearTimeout(timeout);
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
    reportFile: string,
    hashedLock = false,
) {
    const attempts: PipAttemptSummary[] = [];
    const pipDeadline = Date.now() + FEATURE_PACK_PIP_TIMEOUT_MS;
    for (const [index, source] of sources.entries()) {
        const remainingMs = pipDeadline - Date.now();
        if (remainingMs <= 0) throw new Error("pip_timeout");
        fs.rmSync(reportFile, { force: true });
        const args = buildPipInstallArgs(targetDir, requirementsFile, reportFile, source, hashedLock);
        output.write(`\n[Source] ${source.label}\n[Command] ${formatCommandSummary(pythonExe, args)}\n\n`);
        const result = await runPipAttempt(pythonExe, args, output, remainingMs);
        if (result.error === "feature_pack_worker_termination_unconfirmed") {
            throw new Error(result.error);
        }
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
        if (ok) return readPipResolutionReport(reportFile);
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


function readPipResolutionReport(reportFile: string) {
    const payload = JSON.parse(fs.readFileSync(reportFile, "utf-8")) as {
        install?: Array<{ metadata?: { name?: string; version?: string } }>;
    };
    const packages = Array.from(new Set((payload.install || []).map((item) => {
        const name = String(item.metadata?.name || "").trim().toLowerCase().replace(/[-_.]+/g, "-");
        const version = String(item.metadata?.version || "").trim();
        if (!name || !version) throw new Error("feature_pack_pip_report_invalid");
        return `${name}==${version}`;
    }))).sort();
    if (!packages.length) throw new Error("feature_pack_pip_report_empty");
    return {
        packages,
        sha256: crypto.createHash("sha256").update(packages.join("\n"), "utf-8").digest("hex"),
    };
}

function assertTrustedFeaturePackAssetUrl(rawUrl: string) {
    const parsed = new URL(rawUrl);
    if (parsed.protocol !== "https:" || !FEATURE_PACK_ASSET_HOSTS.has(parsed.hostname.toLowerCase())) {
        throw new Error(`Feature pack asset host is not allowed: ${parsed.hostname || "unknown"}`);
    }
}

async function fetchTrustedFeaturePackAsset(
    rawUrl: string,
    resumeOffset: number,
    signal: AbortSignal,
) {
    let currentUrl = rawUrl;
    for (let redirectCount = 0; redirectCount <= FEATURE_PACK_ASSET_MAX_REDIRECTS; redirectCount += 1) {
        assertTrustedFeaturePackAssetUrl(currentUrl);
        const response = await fetch(currentUrl, {
            headers: resumeOffset > 0 ? { Range: `bytes=${resumeOffset}-` } : undefined,
            redirect: "manual",
            cache: "no-store",
            signal,
        });
        if (![301, 302, 303, 307, 308].includes(response.status)) {
            assertTrustedFeaturePackAssetUrl(response.url || currentUrl);
            return response;
        }
        if (redirectCount >= FEATURE_PACK_ASSET_MAX_REDIRECTS) {
            await response.body?.cancel().catch(() => undefined);
            throw new Error("feature_pack_asset_redirect_limit");
        }
        const location = response.headers.get("location");
        await response.body?.cancel().catch(() => undefined);
        if (!location) throw new Error("feature_pack_asset_redirect_missing_location");
        currentUrl = new URL(location, currentUrl).toString();
        assertTrustedFeaturePackAssetUrl(currentUrl);
    }
    throw new Error("feature_pack_asset_redirect_limit");
}

async function downloadFeaturePackAsset(asset: FeaturePackAsset, modelRoot: string, output: fs.WriteStream) {
    assertTrustedFeaturePackAssetUrl(asset.url);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), FEATURE_PACK_ASSET_TIMEOUT_MS);
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
    try {
        const request = async (resumeOffset: number) => fetchTrustedFeaturePackAsset(
            asset.url,
            resumeOffset,
            controller.signal,
        );
        let response = await request(offset);
        if (offset > 0 && response.status !== 206) {
            await response.body?.cancel().catch(() => undefined);
            fs.rmSync(partial, { force: true });
            offset = 0;
            response = await request(0);
        }
        if (!response.ok || !response.body) {
            throw new Error(`Asset download failed (${response.status}): ${asset.id}`);
        }
        const contentLength = Number(response.headers.get("content-length"));
        if (Number.isFinite(contentLength) && contentLength > asset.size - offset) {
            await response.body.cancel().catch(() => undefined);
            fs.rmSync(partial, { force: true });
            throw new Error(`feature_pack_asset_size_exceeded:${asset.id}`);
        }
        output.write(`[Asset] ${asset.id} ${offset ? `resuming at ${offset}` : "starting"}\n`);
        let received = 0;
        let sizeExceeded = false;
        const sizeLimiter = new Transform({
            transform(chunk, _encoding, callback) {
                received += Buffer.byteLength(chunk);
                if (offset + received > asset.size) {
                    sizeExceeded = true;
                    controller.abort();
                    callback(new Error("feature_pack_asset_size_exceeded"));
                    return;
                }
                callback(null, chunk);
            },
        });
        try {
            await pipeline(
                Readable.fromWeb(response.body as never),
                sizeLimiter,
                fs.createWriteStream(partial, { flags: offset > 0 ? "a" : "w" }),
                { signal: controller.signal },
            );
        } catch (error) {
            if (sizeExceeded) {
                fs.rmSync(partial, { force: true });
                throw new Error(`feature_pack_asset_size_exceeded:${asset.id}`);
            }
            throw error;
        }
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
    } finally {
        clearTimeout(timeout);
    }
}

function controlledPreviousPackPath(packId: string, value: unknown, leaf: "python" | "models" | "receipt.json") {
    const candidateValue = String(value || "").trim();
    if (!candidateValue) return null;
    const candidate = path.resolve(candidateValue);
    const packRoot = path.resolve(featurePackRoot(), packId);
    if (candidate === path.join(packRoot, leaf)) return candidate;
    const relative = path.relative(path.join(packRoot, "versions"), candidate);
    const segments = relative.split(path.sep);
    if (segments.length === 2 && isFeaturePackOperationId(segments[0]) && segments[1] === leaf) {
        return candidate;
    }
    return null;
}

function previousReceiptRef(journal: FeaturePackInstallJournal) {
    const configured = controlledPreviousPackPath(journal.packId, journal.backup.state.receiptRef, "receipt.json");
    if (configured) return configured;
    const targetDir = controlledPreviousPackPath(journal.packId, journal.backup.state.targetDir, "python");
    if (!targetDir) return null;
    return controlledPreviousPackPath(journal.packId, path.join(path.dirname(targetDir), "receipt.json"), "receipt.json");
}

function previousModelRoots(journal: FeaturePackInstallJournal) {
    const candidates = [
        controlledPreviousPackPath(journal.packId, journal.backup.state.assetRoot, "models"),
    ];
    const receiptRef = previousReceiptRef(journal);
    if (receiptRef) {
        candidates.push(controlledPreviousPackPath(journal.packId, path.join(path.dirname(receiptRef), "models"), "models"));
    }
    candidates.push(controlledPreviousPackPath(
        journal.packId,
        path.join(featurePackRoot(), journal.packId, "models"),
        "models",
    ));
    return [...new Set(candidates.filter((candidate): candidate is string => Boolean(candidate)))];
}

function readPreviousFeaturePackReceipt(journal: FeaturePackInstallJournal) {
    const receiptRef = previousReceiptRef(journal);
    if (!receiptRef || !fs.existsSync(receiptRef)) return {};
    try {
        return JSON.parse(fs.readFileSync(receiptRef, "utf-8")) as Record<string, unknown>;
    } catch {
        return {};
    }
}

function reuseVerifiedFeaturePackAsset(
    asset: FeaturePackAsset,
    existingModelRoots: string[],
    stagingModelRoot: string,
    output: fs.WriteStream,
) {
    for (const existingModelRoot of existingModelRoots) {
        const existingRoot = path.resolve(existingModelRoot);
        const existingPath = path.resolve(existingRoot, asset.target);
        if (existingPath !== existingRoot && !existingPath.startsWith(`${existingRoot}${path.sep}`)) continue;
        if (!fs.existsSync(existingPath) || !fs.statSync(existingPath).isFile()) continue;
        if (fs.statSync(existingPath).size !== asset.size) continue;
        const actualHash = sha256File(existingPath);
        if (actualHash.toLowerCase() !== asset.sha256.toLowerCase()) continue;

        const stagingRoot = path.resolve(stagingModelRoot);
        const stagingPath = path.resolve(stagingRoot, asset.target);
        if (stagingPath !== stagingRoot && !stagingPath.startsWith(`${stagingRoot}${path.sep}`)) {
            throw new Error(`Asset target escapes feature pack root: ${asset.target}`);
        }
        fs.mkdirSync(path.dirname(stagingPath), { recursive: true });
        try {
            fs.linkSync(existingPath, stagingPath);
        } catch {
            fs.copyFileSync(existingPath, stagingPath);
        }
        output.write(`[Asset reused] ${asset.id} sha256=${actualHash}\n`);
        return { ...asset, path: stagingPath, verifiedSha256: actualHash };
    }
    return null;
}

function runProbeProcess(
    command: string,
    args: string[],
    timeoutMs = 20_000,
    extraEnv: Record<string, string | undefined> = {},
) {
    return new Promise<{ code: number | null; output: string; error: string | null }>((resolve) => {
        let settled = false;
        let output = "";
        const child = spawn(command, args, {
            windowsHide: true,
            detached: process.platform !== "win32",
            stdio: ["ignore", "pipe", "pipe"],
            env: featurePackChildEnv(extraEnv),
        });
        const append = (chunk: Buffer | string) => {
            if (output.length < 64_000) output += String(chunk).slice(0, 64_000 - output.length);
        };
        child.stdout?.on("data", append);
        child.stderr?.on("data", append);
        const timeout = setTimeout(() => {
            if (settled) return;
            settled = true;
            void terminateFeaturePackChild(child).catch(() => false).then((terminated) => {
                child.stdout?.off("data", append);
                child.stderr?.off("data", append);
                child.stdout?.destroy();
                child.stderr?.destroy();
                resolve({
                    code: null,
                    output: `${output}\nprobe timed out`,
                    error: terminated ? "probe_timeout" : "feature_pack_worker_termination_unconfirmed",
                });
            });
        }, timeoutMs);
        child.on("error", (error) => {
            if (settled) return;
            settled = true;
            clearTimeout(timeout);
            resolve({ code: null, output: `${output}\n${error.message}`, error: error.message });
        });
        child.on("exit", (code) => {
            if (settled) return;
            settled = true;
            clearTimeout(timeout);
            resolve({ code, output, error: null });
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
    if (python.error === "feature_pack_worker_termination_unconfirmed") throw new Error(python.error);
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

async function detectPythonLockEnvironment(pythonExe: string): Promise<Pick<FeaturePackInstallEnvironment, "platform" | "architecture" | "pythonVersion">> {
    const script = "import json,platform; print(json.dumps({'pythonVersion':platform.python_version(),'architecture':platform.machine() or platform.architecture()[0]}))";
    const result = await runProbeProcess(pythonExe, ["-I", "-S", "-c", script], 10_000);
    if (result.error || result.code !== 0) throw new Error("feature_pack_python_runtime_unavailable");
    try {
        const payload = JSON.parse(result.output.trim()) as Record<string, unknown>;
        return {
            platform: process.platform,
            architecture: String(payload.architecture || process.arch),
            pythonVersion: String(payload.pythonVersion || "unknown"),
        };
    } catch {
        throw new Error("feature_pack_python_runtime_unavailable");
    }
}

function assetSmokeScript(kind: "onnx" | "mediapipe_task") {
    if (kind === "onnx") {
        return [
            "import json,os,sys",
            "root=os.path.realpath(sys.argv[1])",
            "sys.path.insert(0,root)",
            "import onnxruntime as ort",
            "origin=os.path.realpath(getattr(ort,'__file__',''))",
            "assert origin and os.path.commonpath([root,origin]) == root, 'module_not_loaded_from_staging:onnxruntime'",
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
        "import json,os,sys",
        "root=os.path.realpath(sys.argv[1])",
        "sys.path.insert(0,root)",
        "import mediapipe as mp",
        "origin=os.path.realpath(getattr(mp,'__file__',''))",
        "assert origin and os.path.commonpath([root,origin]) == root, 'module_not_loaded_from_staging:mediapipe'",
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
            ["-I", "-S", "-c", assetSmokeScript(smokeCheck.kind), pythonRoot, modelPath, provider],
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
        if (gpu.result.error === "feature_pack_worker_termination_unconfirmed") throw new Error(gpu.result.error);
        if (gpu.payload) return gpu.payload;
        gpuFailure = gpu.result.output.trim().slice(-500) || `exit=${gpu.result.code ?? "timeout"}`;
        output.write("\n[GPU fallback] The detected adapter has no working provider for this feature pack; validating CPU in a fresh process.\n");
    }
    const cpu = await execute("CPU");
    if (cpu.result.error === "feature_pack_worker_termination_unconfirmed") throw new Error(cpu.result.error);
    if (!cpu.payload) {
        throw new Error(`Feature pack ${smokeCheck.kind} validation failed (${cpu.result.code ?? "timeout"}). See logRef for details.`);
    }
    return gpuFailure ? { ...cpu.payload, gpuProbeError: gpuFailure } : cpu.payload;
}

async function runPythonImportSmokeCheck(
    pythonExe: string,
    pythonRoot: string,
    moduleNames: string[],
    output: fs.WriteStream,
) {
    if (moduleNames.length === 0) {
        throw new Error("Feature pack has no Python import smoke contract.");
    }
    const script = [
        "import importlib,json,os,sys",
        "root=os.path.realpath(sys.argv[1])",
        "sys.path.append(root)",
        "modules=json.loads(sys.argv[2])",
        "loaded={}",
        "for name in modules:",
        " module=importlib.import_module(name)",
        " origins=[]",
        " module_file=getattr(module,'__file__',None)",
        " if module_file: origins.append(os.path.realpath(module_file))",
        " module_path=getattr(module,'__path__',None)",
        " if module_path: origins.extend(os.path.realpath(item) for item in module_path)",
        " if not origins or any(os.path.commonpath([root,item]) != root for item in origins): raise RuntimeError('module_not_loaded_from_staging:'+name)",
        " loaded[name]=origins",
        "print('__V8_SMOKE__'+json.dumps({'kind':'python_import','modules':list(loaded)}))",
    ].join("\n");
    const result = await runProbeProcess(
        pythonExe,
        ["-I", "-S", "-c", script, pythonRoot, JSON.stringify(moduleNames)],
        60_000,
    );
    output.write(`\n[Python import smoke]\n${result.output}`);
    if (result.error === "feature_pack_worker_termination_unconfirmed") throw new Error(result.error);
    const marker = result.output.split(/\r?\n/).find((line) => line.startsWith("__V8_SMOKE__"));
    if (result.code !== 0 || !marker) {
        throw new Error(`Feature pack Python import validation failed (${result.code ?? "timeout"}). See logRef for details.`);
    }
    return JSON.parse(marker.slice("__V8_SMOKE__".length)) as Record<string, unknown>;
}

async function runRpaDryRunSmokeCheck(
    pythonExe: string,
    pythonRoot: string,
    stagingRoot: string,
    output: fs.WriteStream,
) {
    const libraries = ["RPA.Browser.Selenium", "RPA.Excel.Files"];
    if (process.platform === "win32") libraries.push("RPA.Windows");
    const smokeRoot = path.join(stagingRoot, ".smoke");
    const suiteRef = path.join(smokeRoot, "rpa-dryrun.robot");
    const suite = [
        "*** Settings ***",
        ...libraries.map((library) => `Library    ${library}`),
        "",
        "*** Test Cases ***",
        "Staging No-op",
        "    Open Available Browser    about:blank",
        "    Create Workbook    v8os-smoke.xlsx",
        ...(process.platform === "win32" ? ["    Windows Run    cmd /c exit 0"] : []),
        "",
    ].join("\n");
    fs.mkdirSync(smokeRoot, { recursive: true });
    fs.writeFileSync(suiteRef, suite, "utf-8");
    try {
        const robotArgs = [
            "--dryrun",
            "--output",
            "NONE",
            "--log",
            "NONE",
            "--report",
            "NONE",
            suiteRef,
        ];
        const isolatedRunner = [
            "import json,runpy,sys",
            "sys.path.insert(0,sys.argv[1])",
            "sys.argv=['robot',*json.loads(sys.argv[2])]",
            "runpy.run_module('robot',run_name='__main__')",
        ].join(";");
        const result = await runProbeProcess(
            pythonExe,
            [
                "-I",
                "-S",
                "-c",
                isolatedRunner,
                pythonRoot,
                JSON.stringify(robotArgs),
            ],
            60_000,
        );
        output.write(`\n[RPA Robot Framework dry-run]\n${result.output}`);
        if (result.error === "feature_pack_worker_termination_unconfirmed") throw new Error(result.error);
        if (result.code !== 0) {
            throw new Error(`Feature pack RPA dry-run failed (${result.code ?? "timeout"}). See logRef for details.`);
        }
        return {
            kind: "robot_dryrun",
            platform: process.platform,
            libraries,
        };
    } finally {
        fs.rmSync(smokeRoot, { recursive: true, force: true });
    }
}

async function runTransactionalPythonPackInstall(input: {
    definition: FeaturePackDefinition;
    journal: FeaturePackInstallJournal;
    pythonExe: string;
    requirementsFile: string;
    output: fs.WriteStream;
    sources: PipSource[];
}) {
    const { definition, pythonExe, requirementsFile, output, sources } = input;
    let journal = input.journal;
    const { stagingRoot, versionRoot, targetDir, receiptRef } = journal.paths;
    const stagingPython = path.join(stagingRoot, "python");
    const previousTargetDir = controlledPreviousPackPath(definition.id, journal.backup.state.targetDir, "python");
    const previousReceiptPath = previousReceiptRef(journal);
    const hadInstalledPack = Boolean(
        previousTargetDir
        && previousReceiptPath
        && fs.existsSync(previousTargetDir)
        && fs.existsSync(previousReceiptPath),
    );
    const previousReceipt = readPreviousFeaturePackReceipt(journal);
    try {
        fs.mkdirSync(stagingPython, { recursive: true });
        const environment = await detectFeaturePackInstallEnvironment(pythonExe, output);
        const lockFile = lockPathFor(definition, environment);
        if (definition.lockFilePrefix && (!lockFile || !validateHashedLockFile(lockFile))) {
            throw new Error("feature_pack_lock_unavailable");
        }
        const previousPackCompatible = hadInstalledPack && previousReceiptIsCompatible({
            definition,
            receipt: previousReceipt,
            environment,
            requirementsFile,
        });
        journal = transitionFeaturePackInstallJournal(featurePackRoot(), journal, "installing", {
            backup: { ...journal.backup, compatible: previousPackCompatible },
        });
        const resolvedPackages = await installPipDependencies(
            pythonExe,
            stagingPython,
            lockFile || requirementsFile,
            output,
            sources,
            path.join(stagingRoot, "pip-report.json"),
            Boolean(lockFile),
        );
        const importSmoke = await runPythonImportSmokeCheck(
            pythonExe,
            stagingPython,
            smokeModulesFor(definition),
            output,
        );
        const smokeResult = definition.id === "rpa_automation"
            ? {
                kind: "rpa_validation",
                importCheck: importSmoke,
                robotDryRun: await runRpaDryRunSmokeCheck(pythonExe, stagingPython, stagingRoot, output),
            }
            : importSmoke;
        const requirementsSha256 = sha256File(requirementsFile);
        const lockSha256 = lockFile ? sha256File(lockFile) : null;
        const recipeSha256 = crypto.createHash("sha256")
            .update([requirementsSha256, lockSha256].filter(Boolean).join("\n"), "utf-8")
            .digest("hex");
        const receipt = {
            version: 1,
            packId: definition.id,
            packVersion: recipeSha256.slice(0, 12),
            installedAt: nowIso(),
            environment,
            smokeCheck: smokeResult,
            requirements: {
                file: path.basename(requirementsFile),
                sha256: requirementsSha256,
                lockFile: lockFile ? path.basename(lockFile) : null,
                lockSha256,
            },
            resolvedPackages,
        };
        assertFeaturePackLogHealthy(output);
        fs.writeFileSync(path.join(stagingRoot, "receipt.json"), JSON.stringify(receipt, null, 2), "utf-8");
        journal = transitionFeaturePackInstallJournal(featurePackRoot(), journal, "staged", {
            finalPatch: {
                status: "installed",
                targetDir,
                receiptRef,
                version: receipt.packVersion,
                logRef: journal.logRef,
                lastError: null,
                restartRequired: true,
                operationId: null,
                startedAt: null,
            },
            lastError: null,
        });
        await publishStagedFeaturePackJournal(journal);
    } catch (error) {
        const message = featurePackErrorMessage(error);
        const workerTerminationUnconfirmed = message === "feature_pack_worker_termination_unconfirmed";
        safeFeaturePackLogWrite(output, `\n[Transactional install error] ${message}\n`);
        const publishable = Boolean(
            journal.finalPatch
            && (
                fs.existsSync(path.join(stagingRoot, "receipt.json"))
                || fs.existsSync(path.join(versionRoot, "receipt.json"))
            ),
        );
        if (publishable) {
            transitionFeaturePackInstallJournal(featurePackRoot(), journal, "recovery_pending", {
                lastError: message,
            });
        } else {
            await settlePrePublishFeaturePackJournal(journal, message, {
                preserveStaging: workerTerminationUnconfirmed,
            });
        }
    } finally {
        closeFeaturePackLog(output);
    }
}

async function runTransactionalAssetPackInstall(input: {
    definition: FeaturePackDefinition;
    journal: FeaturePackInstallJournal;
    manifest: FeaturePackAssetManifest;
    pythonExe: string;
    requirementsFile: string;
    output: fs.WriteStream;
    sources: PipSource[];
}) {
    const { definition, manifest, pythonExe, requirementsFile, output, sources } = input;
    let journal = input.journal;
    const { stagingRoot, versionRoot, targetDir, assetRoot, receiptRef } = journal.paths;
    const stagingPython = path.join(stagingRoot, "python");
    const stagingModels = path.join(stagingRoot, "models");
    const existingModels = previousModelRoots(journal);
    const previousTargetDir = controlledPreviousPackPath(definition.id, journal.backup.state.targetDir, "python");
    const previousReceiptPath = previousReceiptRef(journal);
    const hadInstalledPack = Boolean(
        previousTargetDir
        && previousReceiptPath
        && fs.existsSync(previousTargetDir)
        && fs.existsSync(previousReceiptPath),
    );
    const previousReceipt = readPreviousFeaturePackReceipt(journal);
    try {
        fs.mkdirSync(stagingPython, { recursive: true });
        fs.mkdirSync(stagingModels, { recursive: true });
        if (!manifest.smokeCheck) throw new Error("Feature pack asset manifest has no smokeCheck contract.");
        const environment = await detectFeaturePackInstallEnvironment(pythonExe, output);
        const lockFile = lockPathFor(definition, environment);
        if (definition.lockFilePrefix && (!lockFile || !validateHashedLockFile(lockFile))) {
            throw new Error("feature_pack_lock_unavailable");
        }
        const previousPackCompatible = hadInstalledPack && previousReceiptIsCompatible({
            definition,
            receipt: previousReceipt,
            environment,
            requirementsFile,
            manifest,
        });
        journal = transitionFeaturePackInstallJournal(featurePackRoot(), journal, "installing", {
            backup: { ...journal.backup, compatible: previousPackCompatible },
        });
        const resolvedPackages = await installPipDependencies(
            pythonExe,
            stagingPython,
            lockFile || requirementsFile,
            output,
            sources,
            path.join(stagingRoot, "pip-report.json"),
            Boolean(lockFile),
        );
        const verifiedAssets = [];
        for (const asset of manifest.assets) {
            verifiedAssets.push(
                reuseVerifiedFeaturePackAsset(asset, existingModels, stagingModels, output)
                || await downloadFeaturePackAsset(asset, stagingModels, output),
            );
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
            requirements: {
                file: path.basename(requirementsFile),
                sha256: sha256File(requirementsFile),
                lockFile: lockFile ? path.basename(lockFile) : null,
                lockSha256: lockFile ? sha256File(lockFile) : null,
            },
            resolvedPackages,
            assets: verifiedAssets.map((asset) => ({
                id: asset.id,
                target: asset.target,
                size: asset.size,
                sha256: asset.verifiedSha256,
                url: asset.url,
            })),
        };
        assertFeaturePackLogHealthy(output);
        fs.writeFileSync(path.join(stagingRoot, "receipt.json"), JSON.stringify(receipt, null, 2), "utf-8");
        journal = transitionFeaturePackInstallJournal(featurePackRoot(), journal, "staged", {
            finalPatch: {
                status: "installed",
                targetDir,
                assetRoot,
                receiptRef,
                version: manifest.version,
                logRef: journal.logRef,
                lastError: null,
                restartRequired: true,
                operationId: null,
                startedAt: null,
            },
            lastError: null,
        });
        await publishStagedFeaturePackJournal(journal);
    } catch (error) {
        const message = featurePackErrorMessage(error);
        const workerTerminationUnconfirmed = message === "feature_pack_worker_termination_unconfirmed";
        safeFeaturePackLogWrite(output, `\n[Transactional install error] ${message}\n`);
        const publishable = Boolean(
            journal.finalPatch
            && (
                fs.existsSync(path.join(stagingRoot, "receipt.json"))
                || fs.existsSync(path.join(versionRoot, "receipt.json"))
            ),
        );
        if (publishable) {
            transitionFeaturePackInstallJournal(featurePackRoot(), journal, "recovery_pending", {
                lastError: message,
            });
        } else {
            await settlePrePublishFeaturePackJournal(journal, message, {
                preserveStaging: workerTerminationUnconfirmed,
            });
        }
    } finally {
        closeFeaturePackLog(output);
    }
}

async function runFeaturePackInstallSequence(input: {
    definition: FeaturePackDefinition;
    journal: FeaturePackInstallJournal;
    pythonExe: string;
    requirementsFile: string;
    output: fs.WriteStream;
    sources: PipSource[];
}) {
    const { definition, journal, pythonExe, requirementsFile, output, sources } = input;
    const assetManifest = readAssetManifest(definition);
    if (assetManifest) {
        await runTransactionalAssetPackInstall({
            definition,
            journal,
            manifest: assetManifest,
            pythonExe,
            requirementsFile,
            output,
            sources,
        });
        return;
    }
    await runTransactionalPythonPackInstall({
        definition,
        journal,
        pythonExe,
        requirementsFile,
        output,
        sources,
    });
}

export async function triggerFeaturePackInstall(packId: string, dryRun = false, locale = "en") {
    const definition = FEATURE_PACK_BY_ID.get(String(packId || ""));
    if (!definition) {
        throw new Error(`Unknown feature pack: ${packId}`);
    }
    let config = readCanonicalAdminRuntimeConfig();
    const requirementsFile = requirementsPathFor(definition);
    if (!definitionInstallable(definition)) {
        throw new Error("feature_pack_not_available");
    }
    const dryRunTargetDir = targetDirFor(definition.id);
    const assetManifest = readAssetManifest(definition);
    const pythonExe = resolvePythonExecutable(config);
    let lockFile = lockPathFor(definition);
    if (definition.lockFilePrefix) {
        const lockEnvironment = await detectPythonLockEnvironment(pythonExe);
        lockFile = lockPathFor(definition, lockEnvironment);
        if (!lockFile || !validateHashedLockFile(lockFile)) {
            throw new Error("feature_pack_lock_unavailable");
        }
    }
    const sources = pipSourceStrategy(locale);
    const installRequirementsFile = lockFile || requirementsFile;
    const firstAttemptArgs = buildPipInstallArgs(
        dryRunTargetDir,
        installRequirementsFile,
        path.join(dryRunTargetDir, ".pip-report.json"),
        sources[0],
        Boolean(lockFile),
    );
    const commandSummary = formatCommandSummary(pythonExe, firstAttemptArgs);
    if (dryRun) {
        return {
            status: "dry_run",
            packId: definition.id,
            commandSummary,
            sourceStrategy: sourceStrategyForResponse(sources),
            targetDir: dryRunTargetDir,
            requirementsFile,
            assetManifest: assetManifest ? {
                version: assetManifest.version,
                license: assetManifest.license || null,
                assets: assetManifest.assets.map((asset) => ({ id: asset.id, size: asset.size, sha256: asset.sha256 })),
            } : null,
            restartRequired: true,
        };
    }

    const busyPackId = FEATURE_PACK_INSTALL_RESERVATION
        || ACTIVE_FEATURE_PACK_INSTALLS.keys().next().value
        || PENDING_FEATURE_PACK_INSTALLS.keys().next().value
        || null;
    if (busyPackId) throw new Error("feature_pack_install_busy");
    FEATURE_PACK_INSTALL_RESERVATION = definition.id;

    let existing: FeaturePackConfigRecord;
    let output: fs.WriteStream;
    let operationId: string;
    let journal: FeaturePackInstallJournal;
    let logRef: string;
    try {
        if (await reconcileInterruptedFeaturePackInstalls(config)) {
            config = readCanonicalAdminRuntimeConfig();
        }

        existing = (config.runtimeRegistry?.featurePacks?.[definition.id] || {}) as FeaturePackConfigRecord;
        const locallyOwnedOperation = ACTIVE_FEATURE_PACK_INSTALLS.get(definition.id)
            || PENDING_FEATURE_PACK_INSTALLS.get(definition.id);
        if (normalizeStatus(existing.status) === "installing" || locallyOwnedOperation) {
            return {
                status: "installing",
                packId: definition.id,
                commandSummary,
                sourceStrategy: sourceStrategyForResponse(sources),
                targetDir: String(existing.targetDir || dryRunTargetDir),
                requirementsFile,
                logRef: existing.logRef || null,
                restartRequired: true,
                message: "能力包正在安装，本次请求未重复启动下载。",
            };
        }

        fs.mkdirSync(featurePackLogRoot(), { recursive: true });
        const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
        logRef = path.join(featurePackLogRoot(), `${definition.id}-${timestamp}.log`);
        output = createGovernedFeaturePackLog(logRef);
        safeFeaturePackLogWrite(output, `[V8OS Feature Pack] ${definition.productName}\n`);
        safeFeaturePackLogWrite(output, `[Locale] ${locale}\n`);
        safeFeaturePackLogWrite(output, `[Source strategy] ${sources.map((source) => source.label).join(" -> ")}\n`);
        assertFeaturePackLogHealthy(output);

        operationId = crypto.randomUUID();
        const previousState: Record<string, unknown> = { ...existing };
        if (normalizeStatus(existing.status) === "installed") {
            const previousTargetDir = String(existing.targetDir || dryRunTargetDir);
            const derivedReceiptRef = path.join(path.dirname(previousTargetDir), "receipt.json");
            previousState.targetDir = previousTargetDir;
            if (!existing.receiptRef && fs.existsSync(derivedReceiptRef)) previousState.receiptRef = derivedReceiptRef;
            if (assetManifest && !existing.assetRoot) {
                const derivedAssetRoot = path.join(path.dirname(previousTargetDir), "models");
                if (fs.existsSync(derivedAssetRoot)) previousState.assetRoot = derivedAssetRoot;
            }
        }
        try {
            journal = createFeaturePackInstallJournal({
                installRoot: featurePackRoot(),
                packId: definition.id,
                operationId,
                logRef,
                previousState,
            });
        } catch (error) {
            closeFeaturePackLog(output);
            throw error;
        }
        PENDING_FEATURE_PACK_INSTALLS.set(definition.id, operationId);
        try {
            await updateFeaturePackConfig(definition.id, {
                status: "installing",
                logRef,
                lastError: null,
                restartRequired: true,
                operationId,
                startedAt: nowIso(),
            }, String(existing.operationId || "").trim() || null);
            journal = transitionFeaturePackInstallJournal(featurePackRoot(), journal, "installing");
        } catch (error) {
            const currentOperationId = String(currentFeaturePackConfig(definition.id).operationId || "").trim();
            try {
                transitionFeaturePackInstallJournal(
                    featurePackRoot(),
                    journal,
                    currentOperationId === operationId
                        ? "recovery_pending"
                        : currentOperationId
                            ? "superseded"
                            : "failed",
                    { lastError: featurePackErrorMessage(error) },
                );
            } finally {
                if (PENDING_FEATURE_PACK_INSTALLS.get(definition.id) === operationId) {
                    PENDING_FEATURE_PACK_INSTALLS.delete(definition.id);
                }
            }
            closeFeaturePackLog(output);
            throw error;
        }
        ACTIVE_FEATURE_PACK_INSTALLS.set(definition.id, operationId);
        if (PENDING_FEATURE_PACK_INSTALLS.get(definition.id) === operationId) {
            PENDING_FEATURE_PACK_INSTALLS.delete(definition.id);
        }
    } finally {
        if (FEATURE_PACK_INSTALL_RESERVATION === definition.id) FEATURE_PACK_INSTALL_RESERVATION = null;
    }

    void runFeaturePackInstallSequence({
        definition,
        journal,
        pythonExe,
        requirementsFile,
        output,
        sources,
    }).catch(async (error) => {
        const message = featurePackErrorMessage(error);
        safeFeaturePackLogWrite(output, `[Unhandled install failure] ${message}\n`);
        const currentJournal = readFeaturePackInstallJournal(featurePackRoot(), journal.paths.journalRef);
        if (currentJournal && !["committed", "recovered", "failed", "superseded"].includes(currentJournal.phase)) {
            const publishable = Boolean(
                currentJournal.finalPatch
                && (
                    fs.existsSync(path.join(currentJournal.paths.stagingRoot, "receipt.json"))
                    || fs.existsSync(currentJournal.paths.receiptRef)
                ),
            );
            if (publishable) {
                transitionFeaturePackInstallJournal(featurePackRoot(), currentJournal, "recovery_pending", {
                    lastError: message,
                });
            } else {
                await settlePrePublishFeaturePackJournal(currentJournal, message, {
                    preserveStaging: message === "feature_pack_worker_termination_unconfirmed",
                });
            }
        }
        closeFeaturePackLog(output);
    }).finally(() => {
        if (ACTIVE_FEATURE_PACK_INSTALLS.get(definition.id) === operationId) {
            ACTIVE_FEATURE_PACK_INSTALLS.delete(definition.id);
        }
    });

    return {
        status: "started",
        packId: definition.id,
        commandSummary,
        sourceStrategy: sourceStrategyForResponse(sources),
        targetDir: journal.paths.targetDir,
        requirementsFile,
        logRef,
        restartRequired: true,
        message: "能力包安装已开始。安装完成后如状态提示需要重启，请重启 Engine 后继续使用。",
    };
}
