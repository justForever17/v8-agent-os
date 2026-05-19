import fs from "fs";
import path from "path";
import { execFile, spawn } from "child_process";

import {
    resolveDesktopLiveConfig,
    resolveDesktopLiveBridgeBaseUrl,
    resolveEnginePythonPath,
    resolveInternalSecret,
} from "@/lib/server/runtime-config";
import { getBaseDir } from "@/lib/storage";

let ensurePromise: Promise<string> | null = null;
let idleStopTimer: NodeJS.Timeout | null = null;

type BridgePhase = "idle" | "warming" | "ready" | "degraded";
type BridgeErrorStage = "spawn" | "port" | "status" | "capture" | "webrtc" | "session" | "offer" | "candidate" | "track";

type BridgeRuntimeState = {
    phase: BridgePhase;
    warmingStartedAt: string | null;
    lastErrorStage: BridgeErrorStage | null;
    lastErrorMessage: string | null;
    retryAllowed: boolean;
    bridgePid: number | null;
    logPath: string | null;
};

const bridgeRuntimeState: BridgeRuntimeState = {
    phase: "idle",
    warmingStartedAt: null,
    lastErrorStage: null,
    lastErrorMessage: null,
    retryAllowed: false,
    bridgePid: null,
    logPath: null,
};

export type BridgeStatusPayload = {
    available?: boolean;
    reason?: string | null;
    phase?: BridgePhase;
    bridgeReady?: boolean;
    bridgeStartable?: boolean;
    bridgeWarming?: boolean;
    activeSessionId?: string | null;
    viewerCount?: number;
    singleViewer?: boolean;
    bridgeActive?: boolean;
    config?: {
        enabled?: boolean;
        maxWidth?: number;
        maxHeight?: number;
        targetFps?: number;
        idleReleaseSeconds?: number;
        keepWarmStandby?: boolean;
        autoWarmOnStatus?: boolean;
        captureDisplay?: string;
        audioEnabled?: boolean;
        audioSource?: string;
        audioSampleRate?: number;
        audioChannels?: number;
    };
    mode?: string;
    fallbackMode?: string;
    captureSurface?: string;
    bridgeLayer?: string;
    bridgeExecutable?: string;
    bridgeReachable?: boolean;
    logPath?: string;
    captureProvider?: string;
    webrtcReady?: boolean;
    streamFallbackReady?: boolean;
    audioAvailable?: boolean;
    audioEnabled?: boolean;
    audioProvider?: string;
    audioReason?: string | null;
    iceServersConfigured?: boolean;
    turnConfigured?: boolean;
    iceServers?: Array<{
        urls?: string | string[];
        username?: string;
        credential?: string;
    }>;
    warmingStartedAt?: string;
    lastErrorStage?: BridgeErrorStage;
    lastErrorMessage?: string;
    retryAllowed?: boolean;
    bridgePid?: number;
};

type BridgeProcessInfo = {
    pid: number;
    parentPid: number | null;
};

function setBridgeRuntimeState(next: Partial<BridgeRuntimeState>) {
    Object.assign(bridgeRuntimeState, next);
}

function clearDesktopLiveIdleStopTimer() {
    if (idleStopTimer) {
        clearTimeout(idleStopTimer);
        idleStopTimer = null;
    }
}

function shouldKeepWarmStandby() {
    return resolveDesktopLiveConfig().keepWarmStandby !== false;
}

function shouldAutoWarmOnStatus() {
    return resolveDesktopLiveConfig().autoWarmOnStatus !== false;
}

function getBridgeScriptPath() {
    const candidates = [
        path.resolve(process.cwd(), "..", "v8-agent-os-engine", "desktop_live_bridge_server.py"),
        path.resolve(process.cwd(), "..", "engine", "desktop_live_bridge_server.py"),
    ];

    for (const candidate of candidates) {
        if (fs.existsSync(candidate)) {
            return candidate;
        }
    }

    return candidates[0];
}

function getBridgeLogPath() {
    const dir = path.join(getBaseDir(), "logs", "desktop-live-bridge");
    fs.mkdirSync(dir, { recursive: true });
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    return path.join(dir, `desktop-live-bridge-${stamp}.log`);
}

async function pingBridge(timeoutMs = 1200): Promise<BridgeStatusPayload | null> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
        const response = await fetch(`${resolveDesktopLiveBridgeBaseUrl()}/desktop-live/status`, {
            cache: "no-store",
            headers: {
                "x-v8-agent-os-secret": resolveInternalSecret(),
            },
            signal: controller.signal,
        });
        if (!response.ok) {
            return null;
        }
        return await response.json().catch(() => ({}));
    } catch {
        return null;
    } finally {
        clearTimeout(timeout);
    }
}

async function waitForBridgePortState(expected: "listening" | "closed", timeoutMs = 2500) {
    const startedAt = Date.now();
    while (Date.now() - startedAt < timeoutMs) {
        const pid = await findBridgePidByPort();
        if (expected === "listening" && pid) {
            return true;
        }
        if (expected === "closed" && !pid) {
            return true;
        }
        await new Promise((resolve) => setTimeout(resolve, 150));
    }
    return false;
}

function execFileAsync(file: string, args: string[]) {
    return new Promise<string>((resolve, reject) => {
        execFile(file, args, { windowsHide: true }, (error, stdout) => {
            if (error) {
                reject(error);
                return;
            }
            resolve(stdout);
        });
    });
}

async function findBridgePidByPort() {
    const bridgeUrl = new URL(resolveDesktopLiveBridgeBaseUrl());
    const port = String(bridgeUrl.port || 8011);
    try {
        if (process.platform === "win32") {
            const stdout = await execFileAsync("powershell", [
                "-NoProfile",
                "-Command",
                `Get-NetTCPConnection -State Listen -LocalPort ${port} | Select-Object -ExpandProperty OwningProcess`,
            ]);
            const pid = Number(String(stdout).trim().split(/\s+/)[0] || 0);
            return Number.isFinite(pid) && pid > 0 ? pid : null;
        }

        const stdout = await execFileAsync("lsof", ["-ti", `tcp:${port}`]);
        const pid = Number(String(stdout).trim().split(/\s+/)[0] || 0);
        return Number.isFinite(pid) && pid > 0 ? pid : null;
    } catch {
        return null;
    }
}

async function findBridgeProcesses(): Promise<BridgeProcessInfo[]> {
    const scriptPath = getBridgeScriptPath().replace(/\\/g, "\\\\");
    try {
        if (process.platform === "win32") {
            const stdout = await execFileAsync("powershell", [
                "-NoProfile",
                "-Command",
                `Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*desktop_live_bridge_server.py*' } | Select-Object ProcessId,ParentProcessId | ConvertTo-Json -Compress`,
            ]);
            const raw = String(stdout).trim();
            if (!raw) {
                return [];
            }
            const parsed = JSON.parse(raw) as Array<{ ProcessId?: number; ParentProcessId?: number }> | { ProcessId?: number; ParentProcessId?: number };
            const items = Array.isArray(parsed) ? parsed : [parsed];
            return items
                .map((item) => ({
                    pid: Number(item.ProcessId || 0),
                    parentPid: Number(item.ParentProcessId || 0) || null,
                }))
                .filter((item) => Number.isFinite(item.pid) && item.pid > 0);
        }

        const stdout = await execFileAsync("pgrep", ["-f", scriptPath]);
        return String(stdout)
            .trim()
            .split(/\s+/)
            .map((value) => ({
                pid: Number(value),
                parentPid: null,
            }))
            .filter((item) => Number.isFinite(item.pid) && item.pid > 0);
    } catch {
        return [];
    }
}

async function findBridgeScriptPids() {
    const processes = await findBridgeProcesses();
    return processes.map((item) => item.pid);
}

async function stopBridgeProcess(pid: number, options?: { includeTree?: boolean }) {
    const includeTree = options?.includeTree !== false;
    if (process.platform === "win32") {
        try {
            const args = ["/PID", String(pid)];
            if (includeTree) {
                args.push("/T");
            }
            args.push("/F");
            await execFileAsync("taskkill", args);
            return;
        } catch {
            // fallback to process.kill below
        }
    }

    try {
        process.kill(pid);
    } catch {
        // noop
    }
}

async function cleanupDuplicateBridgeProcesses(exceptPid: number | null) {
    if (exceptPid === null) {
        return;
    }
    const processes = await findBridgeProcesses();
    const childrenByParent = new Map<number, number[]>();
    const processByPid = new Map<number, BridgeProcessInfo>();
    for (const process of processes) {
        processByPid.set(process.pid, process);
        if (process.parentPid) {
            const siblings = childrenByParent.get(process.parentPid) || [];
            siblings.push(process.pid);
            childrenByParent.set(process.parentPid, siblings);
        }
    }

    const protectedPids = new Set<number>();
    const queue = [exceptPid];
    while (queue.length > 0) {
        const current = queue.shift();
        if (!current || protectedPids.has(current)) {
            continue;
        }
        protectedPids.add(current);
        const parentPid = processByPid.get(current)?.parentPid;
        if (parentPid && processByPid.has(parentPid) && !protectedPids.has(parentPid)) {
            queue.push(parentPid);
        }
        for (const childPid of childrenByParent.get(current) || []) {
            if (!protectedPids.has(childPid)) {
                queue.push(childPid);
            }
        }
    }

    const stalePids = processes
        .map((process) => process.pid)
        .filter((pid) => !protectedPids.has(pid));
    await Promise.all(stalePids.map((pid) => stopBridgeProcess(pid, { includeTree: false })));
}

async function stopExistingBridgeProcesses() {
    const pids = new Set<number>();
    const listeningPid = await findBridgePidByPort();
    if (listeningPid) {
        pids.add(listeningPid);
    }
    for (const pid of await findBridgeScriptPids()) {
        pids.add(pid);
    }

    await Promise.all(Array.from(pids).map((pid) => stopBridgeProcess(pid, { includeTree: true })));
    await waitForBridgePortState("closed");
}

function buildDormantBridgeStatus(): BridgeStatusPayload {
    const config = resolveDesktopLiveConfig();
    const bridgeExecutable = resolveEnginePythonPath();
    const scriptExists = fs.existsSync(getBridgeScriptPath());
    const enabled = config.enabled !== false;
    const startable = enabled && Boolean(resolveInternalSecret()) && Boolean(bridgeExecutable) && scriptExists;
    const iceServers = Array.isArray(config.iceServers) ? config.iceServers : [];

    let reason: string | null = null;
    if (!enabled) {
        reason = "系统基础配置已关闭桌面直播。";
    } else if (!bridgeExecutable) {
        reason = "未配置桌面直播所需的 Python 解释器。";
    } else if (!scriptExists) {
        reason = "桌面直播 bridge 脚本不存在。";
    } else if (!resolveInternalSecret()) {
        reason = "未配置桌面直播内部密钥。";
    }

    return {
        available: false,
        reason: bridgeRuntimeState.lastErrorMessage || reason || (startable ? "桌面直播 bridge 正在启动，请稍候。" : null),
        phase: bridgeRuntimeState.phase === "ready" ? "idle" : bridgeRuntimeState.phase,
        bridgeReady: false,
        bridgeStartable: startable,
        bridgeWarming: bridgeRuntimeState.phase === "warming",
        activeSessionId: null,
        viewerCount: 0,
        singleViewer: config.singleViewerOnly !== false,
        bridgeActive: false,
        bridgeReachable: false,
        audioEnabled: config.audioEnabled !== false,
        audioAvailable: false,
        audioProvider: "none",
        iceServersConfigured: iceServers.length > 0,
        turnConfigured: iceServers.some((server) => {
            const urls = server?.urls;
            const values = typeof urls === "string" ? [urls] : Array.isArray(urls) ? urls : [];
            return values.some((url) => String(url).toLowerCase().startsWith("turn"));
        }),
        iceServers,
        mode: "webrtc_bridge",
        fallbackMode: "multipart_jpeg_stream",
        captureSurface: "primary_display",
        bridgeLayer: "python_local_webrtc_bridge",
        bridgeExecutable: bridgeExecutable || undefined,
        warmingStartedAt: bridgeRuntimeState.warmingStartedAt || undefined,
        lastErrorStage: bridgeRuntimeState.lastErrorStage || undefined,
        lastErrorMessage: bridgeRuntimeState.lastErrorMessage || undefined,
        retryAllowed: bridgeRuntimeState.phase === "degraded",
        bridgePid: bridgeRuntimeState.bridgePid || undefined,
        logPath: bridgeRuntimeState.logPath || undefined,
        config: {
            enabled,
            maxWidth: Number(config.maxWidth || 640),
            maxHeight: Number(config.maxHeight || 360),
            targetFps: Number(config.targetFps || 5),
            idleReleaseSeconds: Number(config.idleReleaseSeconds || 15),
            keepWarmStandby: shouldKeepWarmStandby(),
            autoWarmOnStatus: shouldAutoWarmOnStatus(),
            captureDisplay: String(config.captureDisplay || "primary"),
            audioEnabled: config.audioEnabled !== false,
            audioSource: String(config.audioSource || "system"),
            audioSampleRate: Number(config.audioSampleRate || 48000),
            audioChannels: Number(config.audioChannels || 2),
        },
    };
}

export async function getDesktopLiveBridgeStatus() {
    const payload = await pingBridge();
    if (payload) {
        const currentPid = await findBridgePidByPort();
        await cleanupDuplicateBridgeProcesses(currentPid);
        setBridgeRuntimeState({
            phase: "ready",
            warmingStartedAt: null,
            lastErrorStage: null,
            lastErrorMessage: null,
            retryAllowed: false,
            bridgePid: currentPid,
            logPath: bridgeRuntimeState.logPath,
        });
        return {
            ...payload,
            available: payload.available === true,
            phase: "ready",
            bridgeReady: true,
            bridgeStartable: true,
            bridgeWarming: false,
            bridgeReachable: true,
            warmingStartedAt: undefined,
            lastErrorStage: undefined,
            retryAllowed: false,
            bridgePid: currentPid || undefined,
            logPath: bridgeRuntimeState.logPath || undefined,
        };
    }
    if (ensurePromise || bridgeRuntimeState.phase === "warming") {
        return {
            ...buildDormantBridgeStatus(),
            phase: "warming",
            bridgeWarming: true,
            retryAllowed: false,
            reason: "桌面直播 bridge 正在启动，请稍候。",
        };
    }
    const dormant = buildDormantBridgeStatus();
    if (dormant.bridgeStartable === true && shouldAutoWarmOnStatus()) {
        setBridgeRuntimeState({
            phase: "warming",
            warmingStartedAt: bridgeRuntimeState.warmingStartedAt || new Date().toISOString(),
            lastErrorStage: null,
            lastErrorMessage: null,
            retryAllowed: false,
        });
        void ensureDesktopLiveBridge().catch(() => undefined);
        return {
            ...dormant,
            phase: "warming",
            bridgeWarming: true,
            retryAllowed: false,
            reason: "桌面直播 bridge 正在后台预热，请稍候。",
        } satisfies BridgeStatusPayload;
    }
    return dormant;
}

export async function warmDesktopLiveBridge() {
    const dormant = buildDormantBridgeStatus();
    if (dormant.bridgeStartable !== true) {
        return dormant;
    }

    const reachable = await pingBridge();
    if (reachable) {
        const currentPid = await findBridgePidByPort();
        setBridgeRuntimeState({
            phase: "ready",
            warmingStartedAt: null,
            lastErrorStage: null,
            lastErrorMessage: null,
            retryAllowed: false,
            bridgePid: currentPid,
            logPath: bridgeRuntimeState.logPath,
        });
        return {
            ...reachable,
            available: reachable.available === true,
            phase: "ready",
            bridgeReady: true,
            bridgeStartable: true,
            bridgeWarming: false,
            bridgeReachable: true,
            retryAllowed: false,
            bridgePid: currentPid || undefined,
            logPath: bridgeRuntimeState.logPath || undefined,
        } satisfies BridgeStatusPayload;
    }

    setBridgeRuntimeState({
        phase: "warming",
        warmingStartedAt: bridgeRuntimeState.warmingStartedAt || new Date().toISOString(),
        lastErrorStage: null,
        lastErrorMessage: null,
        retryAllowed: false,
    });
    void ensureDesktopLiveBridge().catch(() => undefined);
    return {
        ...dormant,
        phase: "warming",
        bridgeWarming: true,
        retryAllowed: false,
        reason: "桌面直播 bridge 正在启动，请稍候。",
    } satisfies BridgeStatusPayload;
}

export async function stopDesktopLiveBridge() {
    clearDesktopLiveIdleStopTimer();
    await stopExistingBridgeProcesses();
    setBridgeRuntimeState({
        phase: "idle",
        warmingStartedAt: null,
        lastErrorStage: null,
        lastErrorMessage: null,
        retryAllowed: false,
        bridgePid: null,
        logPath: null,
    });
    return !(await findBridgePidByPort());
}

function spawnBridgeProcess(pythonExecutable: string) {
    const scriptPath = getBridgeScriptPath();
    if (!fs.existsSync(scriptPath)) {
        throw new Error(`桌面直播 bridge 脚本不存在：${scriptPath}`);
    }
    const bridgePythonExecutable = resolveHiddenBridgePythonExecutable(pythonExecutable);
    const logPath = getBridgeLogPath();
    const logFd = fs.openSync(logPath, "a");
    setBridgeRuntimeState({ logPath });

    try {
        fs.writeSync(logFd, `\n[${new Date().toISOString()}] spawning desktop live bridge\n`);
        fs.writeSync(
            logFd,
            `python=${pythonExecutable}\nlauncher=${bridgePythonExecutable}\nscript=${scriptPath}\nurl=${resolveDesktopLiveBridgeBaseUrl()}\n`,
        );
        const child = spawn(
            bridgePythonExecutable,
            [scriptPath],
            {
                cwd: path.dirname(scriptPath),
                detached: true,
                stdio: ["ignore", logFd, logFd],
                windowsHide: true,
                env: {
                    ...process.env,
                    V8_AGENT_OS_INTERNAL_SECRET: resolveInternalSecret(),
                    V8_AGENT_OS_DESKTOP_LIVE_BRIDGE_URL: resolveDesktopLiveBridgeBaseUrl(),
                },
            },
        );
        child.unref();
        return child.pid ?? null;
    } finally {
        try {
            fs.closeSync(logFd);
        } catch {
            // best-effort
        }
    }
}

function resolveHiddenBridgePythonExecutable(pythonExecutable: string) {
    if (process.platform !== "win32") {
        return pythonExecutable;
    }
    const parsed = path.parse(pythonExecutable);
    if (parsed.name.toLowerCase() !== "python") {
        return pythonExecutable;
    }
    const pythonwExecutable = path.join(parsed.dir, "pythonw.exe");
    return fs.existsSync(pythonwExecutable) ? pythonwExecutable : pythonExecutable;
}

export function scheduleDesktopLiveBridgeIdleStop() {
    clearDesktopLiveIdleStopTimer();
    if (shouldKeepWarmStandby()) {
        return;
    }
    const idleReleaseSeconds = Number(resolveDesktopLiveConfig().idleReleaseSeconds || 15);
    if (!Number.isFinite(idleReleaseSeconds) || idleReleaseSeconds <= 0) {
        return;
    }

    idleStopTimer = setTimeout(async () => {
        idleStopTimer = null;
        try {
            const payload = await pingBridge(1200);
            const hasActiveViewer = Boolean(payload?.activeSessionId) || Number(payload?.viewerCount || 0) > 0;
            if (hasActiveViewer) {
                return;
            }
            await stopDesktopLiveBridge();
        } catch {
            // idle cleanup is best-effort
        }
    }, idleReleaseSeconds * 1000);
}

export async function ensureDesktopLiveBridge() {
    clearDesktopLiveIdleStopTimer();
    const expectedPython = resolveEnginePythonPath() || "python";
    const existingBridge = await pingBridge();
    if (existingBridge) {
        const currentPid = await findBridgePidByPort();
        await cleanupDuplicateBridgeProcesses(currentPid);
        setBridgeRuntimeState({
            phase: "ready",
            warmingStartedAt: null,
            lastErrorStage: null,
            lastErrorMessage: null,
            retryAllowed: false,
            bridgePid: currentPid,
            logPath: bridgeRuntimeState.logPath,
        });
        return resolveDesktopLiveBridgeBaseUrl();
    }

    if (!ensurePromise) {
        ensurePromise = (async () => {
            setBridgeRuntimeState({
                phase: "warming",
                warmingStartedAt: new Date().toISOString(),
                lastErrorStage: "spawn",
                lastErrorMessage: null,
                retryAllowed: false,
                logPath: null,
            });
            const spawnedPid = spawnBridgeProcess(expectedPython);
            setBridgeRuntimeState({
                bridgePid: spawnedPid,
                lastErrorStage: "status",
            });
            for (let attempt = 0; attempt < 12; attempt += 1) {
                const payload = await pingBridge();
                if (payload) {
                    const currentPid = await findBridgePidByPort();
                    setBridgeRuntimeState({
                        phase: "ready",
                        warmingStartedAt: null,
                        lastErrorStage: null,
                        lastErrorMessage: null,
                        retryAllowed: false,
                        bridgePid: currentPid,
                        logPath: bridgeRuntimeState.logPath,
                    });
                    return resolveDesktopLiveBridgeBaseUrl();
                }
                await new Promise((resolve) => setTimeout(resolve, 500));
            }
            setBridgeRuntimeState({
                phase: "degraded",
                lastErrorStage: "status",
                lastErrorMessage: "桌面直播 bridge 启动超时，请检查 bridge 日志。",
                retryAllowed: true,
            });
            throw new Error("桌面直播 bridge 启动超时，请检查 bridge 日志。");
        })().catch((error) => {
            setBridgeRuntimeState({
                phase: "degraded",
                lastErrorStage: bridgeRuntimeState.lastErrorStage || "spawn",
                lastErrorMessage: error instanceof Error ? error.message : "桌面直播 bridge 启动失败。",
                retryAllowed: true,
            });
            throw error;
        }).finally(() => {
            ensurePromise = null;
        });
    }

    return ensurePromise;
}
