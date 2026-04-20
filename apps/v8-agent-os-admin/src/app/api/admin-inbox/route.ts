import { NextRequest, NextResponse } from "next/server";

import { createTranslator, LOCALE_COOKIE_NAME, resolveInitialLocale } from "@/lib/locale";
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
        pluginsAllowConfigured?: boolean | null;
        pluginProvenanceWarnings?: Array<{
            kind?: string | null;
            level?: string | null;
            title?: string | null;
            description?: string | null;
            pluginId?: string | null;
            pluginIds?: string[] | null;
        }> | null;
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

async function proxyEngineJsonSafe<T>(path: string) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 4000);
    try {
        const { response, data } = await proxyEngineJson(path, { signal: controller.signal });
        if (!response.ok) {
            console.warn("[Admin Inbox] Engine endpoint returned non-ok status", {
                path,
                status: response.status,
            });
        }
        return {
            ok: response.ok,
            data: (data || {}) as T,
            error: response.ok ? null : `engine_status_${response.status}`,
        };
    } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        console.warn("[Admin Inbox] Engine endpoint request failed", {
            path,
            error: message,
        });
        return {
            ok: false,
            data: {} as T,
            error: message,
        };
    } finally {
        clearTimeout(timeoutId);
    }
}

export async function GET(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) {
        return unauthorized;
    }

    try {
        const locale = resolveInitialLocale(req.cookies.get(LOCALE_COOKIE_NAME)?.value, req.headers.get("accept-language"));
        const t = createTranslator(locale);
        const [approvalsResult, runsResult, healthResult, pluginHostResult] = await Promise.all([
            proxyEngineJsonSafe<{ approvals?: unknown[] }>("/approvals?status=pending"),
            proxyEngineJsonSafe<{ runs?: Array<{ status?: string }> }>("/runs?limit=12"),
            proxyEngineJsonSafe<OperationsSummary["health"]>("/health"),
            proxyEngineJsonSafe<PluginHostSnapshot>("/plugin-host"),
        ]);
        const approvals = approvalsResult.data;
        const runs = runsResult.data;
        const health = healthResult.data;
        const pluginHost = pluginHostResult.data;

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
                title: t("app.api.adminInbox.pendingApprovals.title"),
                summary: t("app.api.adminInbox.pendingApprovals.summary", {
                    count: summary.pendingApprovals,
                }),
                severity: "warning",
                href: "/admin/operations-center?tab=approvals",
                source: "operations-center",
            });
        }

        if (summary.health?.memory?.interpreterDrift) {
            pushItem(items, {
                id: "memory-interpreter-drift",
                title: t("app.api.adminInbox.memoryInterpreterDrift.title"),
                summary: t("app.api.adminInbox.memoryInterpreterDrift.summary", {
                    current: summary.health.memory.interpreterPath || "unknown",
                    expected: summary.health.memory.expectedInterpreterPath || "Engine .venv",
                }),
                severity: "error",
                href: "/admin/operations-center",
                source: "operations-center",
            });
        } else if (summary.health?.memory?.mode === "fts5_only_degraded") {
            pushItem(items, {
                id: "memory-degraded",
                title: t("app.api.adminInbox.memoryDegraded.title"),
                summary: t("app.api.adminInbox.memoryDegraded.summary"),
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
                title: t("app.api.adminInbox.mcpExecImpacted.title"),
                summary: t("app.api.adminInbox.mcpExecImpacted.summary"),
                severity: "error",
                href: "/admin/operations-center",
                source: "operations-center",
            });
        } else if (degradedServers.length > 0 || streamableIssues.length > 0) {
            pushItem(items, {
                id: "mcp-connectivity-warning",
                title: t("app.api.adminInbox.mcpConnectivity.title"),
                summary: t("app.api.adminInbox.mcpConnectivity.summary", {
                    count: degradedServers.length + streamableIssues.length,
                }),
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
                title: t("app.api.adminInbox.pluginHostBridge.title"),
                summary: pluginSnapshot.hostSurface?.handoffDrift
                    ? t("app.api.adminInbox.pluginHostBridge.handoffDrift")
                    : pluginSnapshot.hostSurface?.bridgeReady === false
                        ? t("app.api.adminInbox.pluginHostBridge.bridgeNotReady", {
                            plugin_id: pluginSnapshot.hostSurface?.bridgePluginId || "unknown",
                        })
                        : t("app.api.adminInbox.pluginHostBridge.handoffNotReady"),
                severity: pluginSnapshot.hostSurface?.bridgeReady === false ? "error" : "warning",
                href: "/admin/plugin-host",
                source: "plugin-host",
            });
        }

        const pluginTrustWarnings = Array.isArray(pluginSnapshot.hostSurface?.pluginProvenanceWarnings)
            ? pluginSnapshot.hostSurface?.pluginProvenanceWarnings || []
            : [];
        if (pluginSnapshot.hostSurface?.pluginsAllowConfigured === false || pluginTrustWarnings.length > 0) {
            pushItem(items, {
                id: "plugin-host-trust-warning",
                title: t("app.api.adminInbox.pluginHostTrust.title"),
                summary: pluginSnapshot.hostSurface?.pluginsAllowConfigured === false
                    ? t("app.api.adminInbox.pluginHostTrust.allowEmpty")
                    : t("app.api.adminInbox.pluginHostTrust.provenanceWarning", {
                        count: pluginTrustWarnings.length,
                    }),
                severity: "warning",
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
                title: t("app.api.adminInbox.authorizationNeeded.title"),
                summary: t("app.api.adminInbox.authorizationNeeded.summary"),
                severity: "warning",
                href: "/admin/plugin-host",
                source: "plugin-host",
            });
        }

        if (!items.length && runItems.some((run) => ["paused", "failed", "waiting_input"].includes(run.status || ""))) {
            pushItem(items, {
                id: "recoverable-runs",
                title: t("app.api.adminInbox.recoverableRuns.title"),
                summary: t("app.api.adminInbox.recoverableRuns.summary"),
                severity: "info",
                href: "/admin/operations-center?tab=runs",
                source: "operations-center",
            });
        }

        const endpointErrors = [
            approvalsResult.error,
            runsResult.error,
            healthResult.error,
            pluginHostResult.error,
        ].filter(Boolean);
        if (!items.length && endpointErrors.length > 0) {
            pushItem(items, {
                id: "admin-inbox-degraded",
                title: t("app.api.adminInbox.degraded.title"),
                summary: t("app.api.adminInbox.degraded.summary"),
                severity: "info",
                href: "/admin/operations-center",
                source: "admin",
            });
        }

        const severityOrder = { error: 0, warning: 1, info: 2 };
        items.sort((left, right) => severityOrder[left.severity] - severityOrder[right.severity]);

        return NextResponse.json({
            items,
            degraded: endpointErrors.length > 0,
        });
    } catch (error) {
        console.error("[Admin Inbox] Failed to build inbox:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
