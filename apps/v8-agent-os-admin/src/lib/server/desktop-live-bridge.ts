import fs from "fs";
import path from "path";
import { execFile, spawn } from "child_process";

import {
    resolveDesktopLiveConfig,
    resolveDesktopLiveBridgeBaseUrl,
    resolveEnginePythonPath,
    resolveInternalSecret,
} from "@/lib/server/runtime-config";

let ensurePromise: Promise<string> | null = null;

export type BridgeStatusPayload = {
    available?: boolean;
    reason?: string | null;
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
        captureDisplay?: string;
    };
    mode?: string;
    fallbackMode?: string;
    captureSurface?: string;
    bridgeLayer?: string;
    bridgeExecutable?: string;
    bridgeReachable?: boolean;
};

type BridgeProcessInfo = {
    pid: number;
    parentPid: number | null;
};

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
        reason: reason || (startable ? "桌面直播 bridge 正在启动，请稍后重试。" : null),
        bridgeReady: false,
        bridgeStartable: startable,
        bridgeWarming: false,
        activeSessionId: null,
        viewerCount: 0,
        singleViewer: config.singleViewerOnly !== false,
        bridgeActive: false,
        bridgeReachable: false,
        mode: "webrtc_bridge",
        fallbackMode: "multipart_jpeg_stream",
        captureSurface: "primary_display",
        bridgeLayer: "python_local_webrtc_bridge",
        bridgeExecutable: bridgeExecutable || undefined,
        config: {
            enabled,
            maxWidth: Number(config.maxWidth || 640),
            maxHeight: Number(config.maxHeight || 360),
            targetFps: Number(config.targetFps || 5),
            idleReleaseSeconds: Number(config.idleReleaseSeconds || 15),
            captureDisplay: String(config.captureDisplay || "primary"),
        },
    };
}

export async function getDesktopLiveBridgeStatus() {
    const payload = await pingBridge();
    if (payload) {
        const currentPid = await findBridgePidByPort();
        await cleanupDuplicateBridgeProcesses(currentPid);
        return {
            ...payload,
            available: payload.available === true,
            bridgeReady: true,
            bridgeStartable: true,
            bridgeWarming: false,
            bridgeReachable: true,
        };
    }
    return buildDormantBridgeStatus();
}

export async function warmDesktopLiveBridge() {
    const dormant = buildDormantBridgeStatus();
    if (dormant.bridgeStartable !== true) {
        return dormant;
    }

    const reachable = await pingBridge();
    if (reachable) {
        return {
            ...reachable,
            available: reachable.available === true,
            bridgeReady: true,
            bridgeStartable: true,
            bridgeWarming: false,
            bridgeReachable: true,
        } satisfies BridgeStatusPayload;
    }

    void ensureDesktopLiveBridge().catch(() => undefined);
    return {
        ...dormant,
        bridgeWarming: true,
    } satisfies BridgeStatusPayload;
}

export async function stopDesktopLiveBridge() {
    await stopExistingBridgeProcesses();
    return !(await findBridgePidByPort());
}

function spawnBridgeProcess(pythonExecutable: string) {
    const scriptPath = getBridgeScriptPath();
    if (!fs.existsSync(scriptPath)) {
        throw new Error(`桌面直播 bridge 脚本不存在：${scriptPath}`);
    }

    const child = spawn(
        pythonExecutable,
        [scriptPath],
        {
            cwd: path.dirname(scriptPath),
            detached: true,
            stdio: "ignore",
            windowsHide: true,
            env: {
                ...process.env,
                V8_AGENT_OS_INTERNAL_SECRET: resolveInternalSecret(),
                V8_AGENT_OS_DESKTOP_LIVE_BRIDGE_URL: resolveDesktopLiveBridgeBaseUrl(),
            },
        },
    );
    child.unref();
}

export async function ensureDesktopLiveBridge() {
    const expectedPython = resolveEnginePythonPath() || "python";
    const existingBridge = await pingBridge();
    if (existingBridge) {
        const currentPid = await findBridgePidByPort();
        await cleanupDuplicateBridgeProcesses(currentPid);
        return resolveDesktopLiveBridgeBaseUrl();
    }

    if (!ensurePromise) {
        ensurePromise = (async () => {
            if (existingBridge) {
                await stopExistingBridgeProcesses();
                await new Promise((resolve) => setTimeout(resolve, 300));
            }
            spawnBridgeProcess(expectedPython);
            for (let attempt = 0; attempt < 12; attempt += 1) {
                const payload = await pingBridge();
                if (payload) {
                    return resolveDesktopLiveBridgeBaseUrl();
                }
                await new Promise((resolve) => setTimeout(resolve, 500));
            }
            throw new Error("桌面直播 bridge 启动失败，请检查 Python bridge 日志。");
        })().finally(() => {
            ensurePromise = null;
        });
    }

    return ensurePromise;
}
