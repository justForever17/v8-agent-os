"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2, RefreshCw } from "lucide-react";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { AdvancedSection } from "@/components/admin-shell/AdvancedSection";
import { DomainSummaryStrip } from "@/components/admin-shell/DomainSummaryStrip";
import { StatusNotice } from "@/components/admin-shell/StatusNotice";
import AuditLogsPanel from "@/components/memory/AuditLogsPanel";
import { PendingApprovalsPanel } from "@/components/runtime/PendingApprovalsPanel";
import { RecentRunsPanel } from "@/components/runtime/RecentRunsPanel";
import { useRuntimeOpsData } from "@/components/runtime/use-runtime-ops";
import { useT } from "@/components/providers/LocaleProvider";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { lt } from "@/lib/locale";

type SummaryPayload = {
    pendingApprovals: number;
    recentRuns: number;
    runningCount: number;
    recoverableCount: number;
    health?: {
        status?: string;
        mcp_tools?: number;
        mcp?: {
            configured?: number;
            connected?: number;
            degraded?: number;
            executionImpacted?: boolean;
            backgroundReconnectOnly?: boolean;
            degradedServers?: Array<{ name?: string; transport?: string; status?: string; impact?: string; lastError?: string | null }>;
            streamableHttpIssues?: Array<{ name?: string; status?: string; impact?: string; lastError?: string | null; executionImpacted?: boolean }>;
        };
        memory?: {
            mode?: string;
            interpreterPath?: string;
            expectedInterpreterPath?: string;
            interpreterDrift?: boolean;
            fts5OnlyDegraded?: boolean;
            chromadb?: {
                available?: boolean;
                version?: string;
                error?: string | null;
            };
            vectorBackend?: {
                ready?: boolean;
            };
            warnings?: string[];
        };
    };
};

const VALID_TABS = new Set(["overview", "approvals", "runs", "advanced"]);

export default function OperationsCenterPage() {
    const t = useT();
    const router = useRouter();
    const searchParams = useSearchParams();
    const runtime = useRuntimeOpsData();
    const [summary, setSummary] = useState<SummaryPayload | null>(null);
    const [loading, setLoading] = useState(true);
    const requestedTab = searchParams.get("tab") || "overview";
    const activeTab = VALID_TABS.has(requestedTab) ? requestedTab : "overview";

    const loadSummary = async () => {
        setLoading(true);
        try {
            const response = await fetch("/api/operations-center/summary", { cache: "no-store" });
            const payload = await response.json().catch(() => null);
            if (response.ok) {
                setSummary(payload);
            }
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        void loadSummary();
    }, []);

    const streamableHttpIssues = summary?.health?.mcp?.streamableHttpIssues || [];
    const degradedServers = summary?.health?.mcp?.degradedServers || [];

    return (
        <AdminPageShell>
            <AdminPageHeader
                title={lt("运行与问题", "Operations")}
                description={lt("这里集中查看最近运行、待处理确认、异常和恢复入口。", "Inspect recent runs, pending approvals, failures, and recovery entry points.")}
                actions={
                    <Button variant="outline" onClick={() => void loadSummary()} disabled={loading}>
                        {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                        {t(lt("重新检测", "Refresh"))}
                    </Button>
                }
            />

            <DomainSummaryStrip
                items={[
                    {
                        label: lt("待处理确认", "Pending approvals"),
                        value: summary?.pendingApprovals ?? runtime.approvals.length,
                        description: lt("需要人工确认的运行点。", "Runs waiting for human approval."),
                    },
                    {
                        label: lt("运行中", "Running"),
                        value: summary?.runningCount ?? runtime.runs.filter((run) => run.status === "running").length,
                        description: lt("当前仍在执行的任务。", "Tasks still in progress."),
                    },
                    {
                        label: lt("可恢复", "Recoverable"),
                        value: summary?.recoverableCount ?? runtime.runs.filter((run) => ["paused", "failed", "waiting_input"].includes(run.status || "")).length,
                        description: lt("可以继续或重试的任务。", "Runs that can continue or retry."),
                    },
                    {
                        label: lt("系统状态", "System"),
                        value: summary?.health?.status === "ok" ? t(lt("正常", "Healthy")) : t(lt("检查中", "Checking")),
                        description: lt(`当前可见工具数：${summary?.health?.mcp_tools ?? "-"}`, `Visible tools: ${summary?.health?.mcp_tools ?? "-"}`),
                    },
                    {
                        label: lt("记忆后端", "Memory backend"),
                        value:
                            summary?.health?.memory?.mode === "fts5_only_degraded"
                                ? t(lt("已降级", "Degraded"))
                                : summary?.health?.memory?.mode === "sqlite_fts5_plus_chromadb"
                                  ? t(lt("向量链正常", "Vector ready"))
                                  : t(lt("未知", "Unknown")),
                        description: summary?.health?.memory?.interpreterDrift
                            ? lt("检测到解释器漂移，当前运行环境与 Engine .venv 不一致。", "Interpreter drift detected. The live runtime does not match the Engine .venv.")
                            : summary?.health?.memory?.chromadb?.available === false
                              ? lt("chromadb 当前不可导入，系统退化为 FTS5-only。", "chromadb is unavailable. The system is running in FTS5-only mode.")
                              : summary?.health?.memory?.mode === "sqlite_fts5_plus_chromadb"
                                ? lt("SQLite FTS5 + ChromaDB 设计链路可见。", "The SQLite FTS5 + ChromaDB path is visible.")
                                : lt("暂未拿到记忆后端健康快照。", "No memory backend snapshot yet."),
                    },
                ]}
            />

            {summary?.health?.memory?.mode === "fts5_only_degraded" || summary?.health?.memory?.interpreterDrift ? (
                <StatusNotice
                    title={lt("记忆后端当前处于降级或环境漂移状态", "Memory backend is degraded or drifted")}
                    description={
                        summary?.health?.memory?.interpreterDrift
                            ? lt(`当前解释器是 ${summary?.health?.memory?.interpreterPath || "未知"}，期望使用 ${summary?.health?.memory?.expectedInterpreterPath || "Engine .venv"}。`, `Current interpreter: ${summary?.health?.memory?.interpreterPath || "unknown"}. Expected: ${summary?.health?.memory?.expectedInterpreterPath || "Engine .venv"}.`)
                            : lt("当前没有进入正式的 SQLite FTS5 + ChromaDB 组合链路，系统可能已退化为 FTS5-only。", "The formal SQLite FTS5 + ChromaDB path is not active. The system may be in FTS5-only mode.")
                    }
                    tone="warning"
                />
            ) : null}

            {(summary?.health?.mcp?.executionImpacted || (summary?.health?.mcp?.streamableHttpIssues?.length || 0) > 0) ? (
                <StatusNotice
                    title={lt("Extensions / MCP 当前存在连接波动", "Extensions / MCP connectivity is unstable")}
                    description={
                        summary?.health?.mcp?.executionImpacted
                            ? lt("当前至少有一个 MCP 连接异常可能影响实际执行，请优先检查扩展生态页和对应 server。", "At least one MCP connection issue may affect execution. Check the extensions page and affected server first.")
                            : summary?.health?.mcp?.backgroundReconnectOnly
                              ? lt("当前只观察到 background reconnect 漂移，暂未判定会影响当前执行。", "Only background reconnect drift is visible right now. It is not yet classified as execution-impacting.")
                              : lt("当前存在 streamable_http 连接漂移，但尚未判断为会影响本次执行。", "streamable_http drift is visible, but it is not yet classified as execution-impacting.")
                    }
                    tone="warning"
                />
            ) : null}

            <div className="grid gap-4 xl:grid-cols-3">
                <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                    <div className="text-sm font-medium text-slate-900">{t(lt("记忆后端真相", "Memory backend"))}</div>
                    <div className="mt-3 space-y-2 text-sm text-slate-600">
                        <div><span className="font-medium text-slate-900">{t(lt("模式：", "Mode:"))}</span>{summary?.health?.memory?.mode || t(lt("未知", "Unknown"))}</div>
                        <div><span className="font-medium text-slate-900">{t(lt("解释器：", "Interpreter:"))}</span><span className="break-all font-mono text-xs">{summary?.health?.memory?.interpreterPath || t(lt("未知", "Unknown"))}</span></div>
                        <div><span className="font-medium text-slate-900">ChromaDB：</span>{summary?.health?.memory?.chromadb?.available ? t(lt(`可导入 (${summary?.health?.memory?.chromadb?.version || "未知版本"})`, `Available (${summary?.health?.memory?.chromadb?.version || "unknown version"})`)) : t(lt("不可导入", "Unavailable"))}</div>
                        <div><span className="font-medium text-slate-900">{t(lt("向量后端：", "Vector backend:"))}</span>{summary?.health?.memory?.vectorBackend?.ready ? "ready" : "not ready"}</div>
                    </div>
                </div>
                <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                    <div className="text-sm font-medium text-slate-900">{t(lt("当前警告", "Warnings"))}</div>
                    <div className="mt-3 space-y-2 text-sm text-slate-600">
                        {summary?.health?.memory?.warnings?.length ? (
                            summary.health.memory.warnings.map((warning) => (
                                <div key={warning} className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">
                                    {warning}
                                </div>
                            ))
                        ) : (
                            <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs leading-5 text-emerald-900">
                                {t(lt("当前没有检测到解释器漂移或记忆后端降级。", "No interpreter drift or memory backend degradation is visible."))}
                            </div>
                        )}
                    </div>
                </div>
                <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                    <div className="text-sm font-medium text-slate-900">{t(lt("Extensions / MCP 健康摘要", "Extensions / MCP"))}</div>
                    <div className="mt-3 space-y-2 text-sm text-slate-600">
                        <div><span className="font-medium text-slate-900">{t(lt("已配置：", "Configured:"))}</span>{summary?.health?.mcp?.configured ?? 0}</div>
                        <div><span className="font-medium text-slate-900">{t(lt("已连接：", "Connected:"))}</span>{summary?.health?.mcp?.connected ?? 0}</div>
                        <div><span className="font-medium text-slate-900">{t(lt("异常：", "Degraded:"))}</span>{summary?.health?.mcp?.degraded ?? 0}</div>
                        <div><span className="font-medium text-slate-900">{t(lt("执行影响：", "Exec impact:"))}</span>{summary?.health?.mcp?.executionImpacted ? t(lt("是", "Yes")) : t(lt("否", "No"))}</div>
                        {summary?.health?.mcp?.backgroundReconnectOnly ? (
                            <div className="rounded-xl border border-sky-200 bg-sky-50 px-3 py-2 text-xs leading-5 text-sky-900">
                                {t(lt("当前异常都被归类为 background reconnect，不会直接把 PluginHostRuntime 判成假失效。", "Current issues are classified as background reconnect and will not falsely mark PluginHostRuntime as unhealthy."))}
                            </div>
                        ) : null}
                        {streamableHttpIssues.length > 0 ? (
                            <div className="space-y-2">
                                <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">
                                    {t(lt(`当前有 ${streamableHttpIssues.length} 条 streamable_http 漂移连接。`, `${streamableHttpIssues.length} streamable_http drift connections are active.`))}
                                </div>
                                {streamableHttpIssues.slice(0, 3).map((issue) => (
                                    <div key={`${issue.name}-${issue.status}`} className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-700">
                                        <div><span className="font-medium text-slate-900">{issue.name || "unknown server"}</span> · {issue.status || "unknown"}</div>
                                        <div>{issue.executionImpacted ? t(lt("当前已判定会影响执行。", "Execution impact confirmed.")) : issue.impact === "background_reconnect" ? t(lt("当前判定为 background reconnect。", "Classified as background reconnect.")) : t(lt("当前已记录为异常连接。", "Recorded as a degraded connection."))}</div>
                                        {issue.lastError ? <div className="text-slate-500">{issue.lastError}</div> : null}
                                    </div>
                                ))}
                            </div>
                        ) : degradedServers.length > 0 ? (
                            <div className="space-y-2">
                                <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">
                                    {t(lt(`当前有 ${degradedServers.length} 个 MCP server 状态异常。`, `${degradedServers.length} MCP servers are degraded.`))}
                                </div>
                                {degradedServers.slice(0, 2).map((server) => (
                                    <div key={`${server.name}-${server.status}`} className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-700">
                                        <div><span className="font-medium text-slate-900">{server.name || "unknown server"}</span> · {server.transport || "unknown"} · {server.status || "unknown"}</div>
                                        <div>{server.impact === "background_reconnect" ? t(lt("当前判定为 background reconnect。", "Classified as background reconnect.")) : t(lt("当前可能影响执行。", "This may affect execution."))} </div>
                                        {server.lastError ? <div className="text-slate-500">{server.lastError}</div> : null}
                                    </div>
                                ))}
                            </div>
                        ) : (
                            <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs leading-5 text-emerald-900">
                                {t(lt("当前没有检测到会影响运行的 MCP 漂移连接。", "No execution-impacting MCP drift is visible."))}
                            </div>
                        )}
                    </div>
                </div>
            </div>

            <Tabs
                value={activeTab}
                onValueChange={(value) => router.replace(`/admin/operations-center?tab=${encodeURIComponent(value)}`, { scroll: false })}
                className="space-y-4"
            >
                <TabsList className="grid w-full grid-cols-4 rounded-2xl bg-white shadow-sm">
                    <TabsTrigger value="overview">{t(lt("概览", "Overview"))}</TabsTrigger>
                    <TabsTrigger value="approvals">{t(lt("待处理确认", "Approvals"))}</TabsTrigger>
                    <TabsTrigger value="runs">{t(lt("最近运行", "Recent runs"))}</TabsTrigger>
                    <TabsTrigger value="advanced">{t(lt("高级日志", "Logs"))}</TabsTrigger>
                </TabsList>

                <TabsContent value="overview" className="space-y-4">
                    {runtime.approvals.length > 0 ? (
                        <StatusNotice
                            title={lt("发现待处理确认", "Pending approvals found")}
                            description={lt("建议先处理确认项，再决定是否继续恢复其他运行。", "Handle approvals first, then decide whether other runs should resume.")}
                            tone="warning"
                        />
                    ) : null}
                    <div className="grid gap-4 xl:grid-cols-2">
                        <PendingApprovalsPanel hook={runtime} />
                        <RecentRunsPanel hook={runtime} />
                    </div>
                </TabsContent>

                <TabsContent value="approvals">
                    <PendingApprovalsPanel hook={runtime} />
                </TabsContent>

                <TabsContent value="runs" className="space-y-4">
                    {runtime.runs.some((run) => ["paused", "failed", "waiting_input"].includes(run.status || "")) ? (
                        <StatusNotice
                            title={lt("发现可恢复任务", "Recoverable runs found")}
                            description={lt("如果你知道上次停在哪一步，可以直接在这里重试或先回到聊天页继续。", "If you know where it stopped, retry here or continue from chat.")}
                            tone="success"
                        />
                    ) : null}
                    <RecentRunsPanel hook={runtime} />
                </TabsContent>

                <TabsContent value="advanced">
                    <AdvancedSection
                        title={lt("原始记录", "Raw logs")}
                        defaultOpen
                    >
                        <AuditLogsPanel />
                    </AdvancedSection>
                </TabsContent>
            </Tabs>
        </AdminPageShell>
    );
}
