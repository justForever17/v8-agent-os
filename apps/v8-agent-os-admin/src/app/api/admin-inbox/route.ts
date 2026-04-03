import { NextRequest, NextResponse } from "next/server";

import { LOCALE_COOKIE_NAME, lt, pickLocalizedText, resolveInitialLocale } from "@/lib/locale";
import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

type InboxItem = {
    id: string;
    title: string;
    summary: string;
    severity: "error" | "warning" | "info";
    href: string;
    source: string;
};

type OperationsSummary = {
    pendingApprovals: number;
    health?: {
        mcp_tools?: number;
        mcp?: {
            configured?: number;
            connected?: number;
            degraded?: number;
            executionImpacted?: boolean;
            degradedServers?: Array<{ name?: string; lastError?: string | null }>;
            streamableHttpIssues?: Array<{ name?: string; lastError?: string | null; executionImpacted?: boolean }>;
        };
        memory?: {
            mode?: string;
            interpreterPath?: string;
            expectedInterpreterPath?: string;
            interpreterDrift?: boolean;
            warnings?: string[];
        };
    };
};

type PluginHostSnapshot = {
    startupState?: string | null;
    lastRefreshError?: string | null;
    hostSurface?: {
        bridgeReady?: boolean;
        handoffReady?: boolean;
        handoffDrift?: boolean;
        bridgePluginId?: string | null;
        recentInboundProof?: {
            stage?: string | null;
            reason?: string | null;
        } | null;
        gatewayHealth?: {
            runtime?: { status?: string | null; detail?: string | null };
            rpc?: { ok?: boolean; error?: string | null };
        } | null;
    };
};

const AUTH_PATTERN = /(auth|authorization|unauthorized|forbidden|permission|token|api key|credential|鉴权|授权|令牌|密钥|权限)/i;

function pushItem(items: InboxItem[], item: InboxItem | null) {
    if (!item) return;
    if (items.some((existing) => existing.id === item.id)) {
        return;
    }
    items.push(item);
}

function containsAuthIssue(values: Array<string | null | undefined>) {
    return values.some((value) => AUTH_PATTERN.test(String(value || "")));
}

export async function GET(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) {
        return unauthorized;
    }

    try {
        const locale = resolveInitialLocale(req.cookies.get(LOCALE_COOKIE_NAME)?.value, req.headers.get("accept-language"));
        const text = (zh: string, en: string) => pickLocalizedText(locale, lt(zh, en));
        const [{ data: approvals }, { data: runs }, { data: health }, { data: pluginHost }] = await Promise.all([
            proxyEngineJson("/approvals?status=pending"),
            proxyEngineJson("/runs?limit=12"),
            proxyEngineJson("/health"),
            proxyEngineJson("/plugin-host"),
        ]);

        const approvalItems = Array.isArray((approvals as { approvals?: unknown[] })?.approvals)
            ? ((approvals as { approvals?: unknown[] }).approvals || [])
            : [];
        const runItems = Array.isArray((runs as { runs?: Array<{ status?: string }> })?.runs)
            ? ((runs as { runs?: Array<{ status?: string }> }).runs || [])
            : [];

        const summary: OperationsSummary = {
            pendingApprovals: approvalItems.length,
            health: (health || {}) as OperationsSummary["health"],
        };
        const pluginSnapshot = (pluginHost || {}) as PluginHostSnapshot;
        const items: InboxItem[] = [];

        if (summary.pendingApprovals > 0) {
            pushItem(items, {
                id: "pending-approvals",
                title: text("待处理确认", "Pending approvals"),
                summary: text(`${summary.pendingApprovals} 项运行等待人工确认`, `${summary.pendingApprovals} runs are waiting for review`),
                severity: "warning",
                href: "/admin/operations-center?tab=approvals",
                source: "operations-center",
            });
        }

        if (summary.health?.memory?.interpreterDrift) {
            pushItem(items, {
                id: "memory-interpreter-drift",
                title: text("记忆环境漂移", "Memory drift"),
                summary: text(
                    `当前解释器 ${summary.health.memory.interpreterPath || "unknown"} 与期望环境 ${summary.health.memory.expectedInterpreterPath || "Engine .venv"} 不一致`,
                    `Interpreter ${summary.health.memory.interpreterPath || "unknown"} does not match ${summary.health.memory.expectedInterpreterPath || "Engine .venv"}`,
                ),
                severity: "error",
                href: "/admin/operations-center",
                source: "operations-center",
            });
        } else if (summary.health?.memory?.mode === "fts5_only_degraded") {
            pushItem(items, {
                id: "memory-degraded",
                title: text("记忆后端降级", "Memory degraded"),
                summary: text("当前记忆链路已退化为 FTS5-only，请检查向量后端和解释器环境", "Memory has fallen back to FTS5-only. Check vector backend and interpreter state."),
                severity: "warning",
                href: "/admin/operations-center",
                source: "operations-center",
            });
        }

        const degradedServers = summary.health?.mcp?.degradedServers || [];
        const streamableIssues = summary.health?.mcp?.streamableHttpIssues || [];
        if (summary.health?.mcp?.executionImpacted) {
            pushItem(items, {
                id: "mcp-exec-impacted",
                title: text("Extensions / MCP 异常", "Extensions / MCP issue"),
                summary: text("至少有一个扩展连接问题已影响执行链路", "At least one extension connection issue is affecting execution."),
                severity: "error",
                href: "/admin/operations-center",
                source: "operations-center",
            });
        } else if (degradedServers.length > 0 || streamableIssues.length > 0) {
            pushItem(items, {
                id: "mcp-connectivity-warning",
                title: text("Extensions / MCP 波动", "Extensions / MCP drift"),
                summary: text(`检测到 ${degradedServers.length + streamableIssues.length} 条异常连接或漂移记录`, `${degradedServers.length + streamableIssues.length} degraded or drifting connections detected`),
                severity: "warning",
                href: "/admin/operations-center",
                source: "operations-center",
            });
        }

        if (
            pluginSnapshot.hostSurface?.bridgeReady === false
            || pluginSnapshot.hostSurface?.handoffReady === false
            || pluginSnapshot.hostSurface?.handoffDrift
        ) {
            pushItem(items, {
                id: "plugin-host-bridge-warning",
                title: text("Plugin Host 桥接异常", "Plugin Host bridge issue"),
                summary: pluginSnapshot.hostSurface?.handoffDrift
                    ? text("最近观测到 handoff 漂移，请检查桥接与渠道 claim 状态", "Recent handoff drift detected. Check bridge and channel claim state.")
                    : pluginSnapshot.hostSurface?.bridgeReady === false
                        ? text(`Bridge 当前未就绪 (${pluginSnapshot.hostSurface?.bridgePluginId || "unknown"})`, `Bridge is not ready (${pluginSnapshot.hostSurface?.bridgePluginId || "unknown"})`)
                        : text("Handoff 当前未就绪，请检查 OpenClaw 插件桥状态", "Handoff is not ready. Check the OpenClaw bridge."),
                severity: pluginSnapshot.hostSurface?.bridgeReady === false ? "error" : "warning",
                href: "/admin/plugin-host",
                source: "plugin-host",
            });
        }

        const authSignals = [
            ...degradedServers.map((item) => item.lastError),
            ...streamableIssues.map((item) => item.lastError),
            ...(summary.health?.memory?.warnings || []),
            pluginSnapshot.lastRefreshError,
            pluginSnapshot.hostSurface?.gatewayHealth?.runtime?.detail,
            pluginSnapshot.hostSurface?.gatewayHealth?.rpc?.error,
            pluginSnapshot.hostSurface?.recentInboundProof?.reason,
        ];
        if (containsAuthIssue(authSignals)) {
            pushItem(items, {
                id: "authorization-needed",
                title: text("需要授权", "Authorization needed"),
                summary: text("当前检测到鉴权或密钥相关异常，请检查渠道、模型或插件凭据", "Auth or credential issues detected. Check channel, model, or plugin credentials."),
                severity: "warning",
                href: "/admin/plugin-host",
                source: "plugin-host",
            });
        }

        if (!items.length && runItems.some((run) => ["paused", "failed", "waiting_input"].includes(run.status || ""))) {
            pushItem(items, {
                id: "recoverable-runs",
                title: text("有待恢复运行", "Recoverable runs"),
                summary: text("系统中存在可恢复或待补充输入的运行", "Some runs can resume or still need additional input."),
                severity: "info",
                href: "/admin/operations-center?tab=runs",
                source: "operations-center",
            });
        }

        const severityOrder = { error: 0, warning: 1, info: 2 };
        items.sort((left, right) => severityOrder[left.severity] - severityOrder[right.severity]);

        return NextResponse.json({ items });
    } catch (error) {
        console.error("[Admin Inbox] Failed to build inbox:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
