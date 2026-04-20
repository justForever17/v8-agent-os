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
            degradedServers?: Array<{
                name?: string;
                transport?: string;
                status?: string;
                impact?: string;
                lastError?: string | null;
            }>;
            streamableHttpIssues?: Array<{
                name?: string;
                status?: string;
                impact?: string;
                lastError?: string | null;
                executionImpacted?: boolean;
            }>;
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
    const focusRunId = searchParams.get("focusRun");
    const focusSessionId = searchParams.get("focusSession");
    const loadSummary = async () => {
        setLoading(true);
        try {
            const response = await fetch("/api/operations-center/summary", { cache: "no-store" });
            const payload = await response.json().catch(() => null);
            if (response.ok) {
                setSummary(payload);
            }
        }
        finally {
            setLoading(false);
        }
    };
    useEffect(() => {
        void loadSummary();
    }, []);
    const streamableHttpIssues = summary?.health?.mcp?.streamableHttpIssues || [];
    const degradedServers = summary?.health?.mcp?.degradedServers || [];
    const pendingApprovalMismatch = typeof summary?.pendingApprovals === "number" && summary.pendingApprovals !== runtime.approvals.length;
    return (<AdminPageShell>
            <AdminPageHeader title={"app.admin.dashboard.operations.center.page.k756910c0"} description={"app.admin.dashboard.operations.center.page.k5c84b2ac"} actions={<Button variant="outline" onClick={() => void loadSummary()} disabled={loading}>
                        {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> : <RefreshCw className="mr-2 h-4 w-4"/>}
                        {t("app.admin.dashboard.operations.center.page.kd4db8d84")}
                    </Button>}/>

            <DomainSummaryStrip items={[
            {
                label: "app.admin.dashboard.operations.center.page.k61329e7f",
                value: runtime.approvals.length,
                description: "app.admin.dashboard.operations.center.page.k0ecf1629",
            },
            {
                label: "app.admin.dashboard.operations.center.page.k69570f96",
                value: runtime.runs.filter((run) => run.status === "running").length,
                description: "app.admin.dashboard.operations.center.page.kc166b15e",
            },
            {
                label: "app.admin.dashboard.operations.center.page.k22d985d6",
                value: runtime.runs.filter((run) => ["paused", "failed", "waiting_input"].includes(run.status || "")).length,
                description: "app.admin.dashboard.operations.center.page.k6b24ed69",
            },
            {
                label: "app.admin.dashboard.operations.center.page.k0280ab58",
                value: summary?.health?.status === "ok" ? t("app.admin.dashboard.operations.center.page.k3f88d199") : t("app.admin.dashboard.operations.center.page.kab954a08"),
                description: t("app.admin.dashboard.operations.center.page.summary.visibleTools", {
                    visible_tools: summary?.health?.mcp_tools ?? "-",
                }),
            },
            {
                label: "app.admin.dashboard.operations.center.page.kc67937eb",
                value: summary?.health?.memory?.mode === "fts5_only_degraded"
                    ? t("app.admin.dashboard.operations.center.page.k8da7daa5")
                    : summary?.health?.memory?.mode === "sqlite_fts5_plus_chromadb"
                        ? t("app.admin.dashboard.operations.center.page.kdbf6b1a0")
                        : t("app.admin.dashboard.operations.center.page.k76ebff7c"),
                description: summary?.health?.memory?.interpreterDrift
                    ? "app.admin.dashboard.operations.center.page.k5e58cf57"
                    : summary?.health?.memory?.chromadb?.available === false
                        ? "app.admin.dashboard.operations.center.page.k22d69497"
                        : summary?.health?.memory?.mode === "sqlite_fts5_plus_chromadb"
                            ? "app.admin.dashboard.operations.center.page.k33fb7ffa"
                            : "app.admin.dashboard.operations.center.page.k9d4e92f0",
            },
        ]}/>

            {summary?.health?.memory?.mode === "fts5_only_degraded" || summary?.health?.memory?.interpreterDrift ? (<StatusNotice title={"app.admin.dashboard.operations.center.page.k400d7e2f"} description={summary?.health?.memory?.interpreterDrift
                ? t("app.admin.dashboard.operations.center.page.warning.interpreterDrift", {
                    interpreter_path: summary?.health?.memory?.interpreterPath || t("app.admin.dashboard.operations.center.page.unknown"),
                    expected_path: summary?.health?.memory?.expectedInterpreterPath || "Engine .venv",
                })
                : "app.admin.dashboard.operations.center.page.ke5b335d8"} tone="warning"/>) : null}

            {(summary?.health?.mcp?.executionImpacted || (summary?.health?.mcp?.streamableHttpIssues?.length || 0) > 0) ? (<StatusNotice title={"app.admin.dashboard.operations.center.page.kb5668925"} description={summary?.health?.mcp?.executionImpacted
                ? "app.admin.dashboard.operations.center.page.kc1e3ce76"
                : summary?.health?.mcp?.backgroundReconnectOnly
                    ? "app.admin.dashboard.operations.center.page.k2d3e075a"
                    : "app.admin.dashboard.operations.center.page.kdda9746a"} tone="warning"/>) : null}

            <div className="grid gap-4 xl:grid-cols-3">
                <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                    <div className="text-sm font-medium text-slate-900">{t("app.admin.dashboard.operations.center.page.k7190d60a")}</div>
                    <div className="mt-3 space-y-2 text-sm text-slate-600">
                        <div><span className="font-medium text-slate-900">{t("app.admin.dashboard.operations.center.page.kfbd6399e")}</span>{summary?.health?.memory?.mode || t("app.admin.dashboard.operations.center.page.k76ebff7c")}</div>
                        <div><span className="font-medium text-slate-900">{t("app.admin.dashboard.operations.center.page.kc6b5096c")}</span><span className="break-all font-mono text-xs">{summary?.health?.memory?.interpreterPath || t("app.admin.dashboard.operations.center.page.k76ebff7c")}</span></div>
                        <div><span className="font-medium text-slate-900">ChromaDB：</span>{summary?.health?.memory?.chromadb?.available ? t("app.admin.dashboard.operations.center.page.k8b78e9e2", {
        summary_health_memory_chromadb_version: summary?.health?.memory?.chromadb?.version || t("app.admin.dashboard.operations.center.page.unknownVersion")
    }) : t("app.admin.dashboard.operations.center.page.kd2037c91")}</div>
                        <div><span className="font-medium text-slate-900">{t("app.admin.dashboard.operations.center.page.k224983a9")}</span>{summary?.health?.memory?.vectorBackend?.ready ? "ready" : "not ready"}</div>
                    </div>
                </div>
                <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                    <div className="text-sm font-medium text-slate-900">{t("app.admin.dashboard.operations.center.page.k127a42f4")}</div>
                    <div className="mt-3 space-y-2 text-sm text-slate-600">
                        {summary?.health?.memory?.warnings?.length ? (summary.health.memory.warnings.map((warning) => (<div key={warning} className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">
                                    {warning}
                                </div>))) : (<div className="rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs leading-5 text-emerald-900">
                                {t("app.admin.dashboard.operations.center.page.k8a57c639")}
                            </div>)}
                    </div>
                </div>
                <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                    <div className="text-sm font-medium text-slate-900">{t("app.admin.dashboard.operations.center.page.k778a6bf7")}</div>
                    <div className="mt-3 space-y-2 text-sm text-slate-600">
                        <div><span className="font-medium text-slate-900">{t("app.admin.dashboard.operations.center.page.ke583c052")}</span>{summary?.health?.mcp?.configured ?? 0}</div>
                        <div><span className="font-medium text-slate-900">{t("app.admin.dashboard.operations.center.page.k8f5a01b3")}</span>{summary?.health?.mcp?.connected ?? 0}</div>
                        <div><span className="font-medium text-slate-900">{t("app.admin.dashboard.operations.center.page.k7c7ba6d0")}</span>{summary?.health?.mcp?.degraded ?? 0}</div>
                        <div><span className="font-medium text-slate-900">{t("app.admin.dashboard.operations.center.page.k37dfd1b7")}</span>{summary?.health?.mcp?.executionImpacted ? t("app.admin.dashboard.operations.center.page.k2ae24b34") : t("app.admin.dashboard.operations.center.page.k8d9f05ae")}</div>
                        {summary?.health?.mcp?.backgroundReconnectOnly ? (<div className="rounded-xl border border-sky-200 bg-sky-50 px-3 py-2 text-xs leading-5 text-sky-900">
                                {t("app.admin.dashboard.operations.center.page.k0b0a6aba")}
                            </div>) : null}
                        {streamableHttpIssues.length > 0 ? (<div className="space-y-2">
                                <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">
                                    {t("app.admin.dashboard.operations.center.page.k5325bd42", {
            streamableHttpIssues_length: streamableHttpIssues.length
        })}
                                </div>
                                {streamableHttpIssues.slice(0, 3).map((issue) => (<div key={`${issue.name}-${issue.status}`} className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-700">
                                        <div><span className="font-medium text-slate-900">{issue.name || "unknown server"}</span> · {issue.status || "unknown"}</div>
                                        <div>{issue.executionImpacted ? t("app.admin.dashboard.operations.center.page.k81feb590") : issue.impact === "background_reconnect" ? t("app.admin.dashboard.operations.center.page.k92c7108e") : t("app.admin.dashboard.operations.center.page.k91c91e24")}</div>
                                        {issue.lastError ? <div className="text-slate-500">{issue.lastError}</div> : null}
                                    </div>))}
                            </div>) : degradedServers.length > 0 ? (<div className="space-y-2">
                                <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">
                                    {t("app.admin.dashboard.operations.center.page.k8ffbe607", {
            degradedServers_length: degradedServers.length
        })}
                                </div>
                                {degradedServers.slice(0, 2).map((server) => (<div key={`${server.name}-${server.status}`} className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-700">
                                        <div><span className="font-medium text-slate-900">{server.name || "unknown server"}</span> · {server.transport || "unknown"} · {server.status || "unknown"}</div>
                                        <div>{server.impact === "background_reconnect" ? t("app.admin.dashboard.operations.center.page.k92c7108e") : t("app.admin.dashboard.operations.center.page.k0e3d684b")} </div>
                                        {server.lastError ? <div className="text-slate-500">{server.lastError}</div> : null}
                                    </div>))}
                            </div>) : (<div className="rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs leading-5 text-emerald-900">
                                {t("app.admin.dashboard.operations.center.page.k30e0aa83")}
                            </div>)}
                    </div>
                </div>
            </div>

            <Tabs value={activeTab} onValueChange={(value) => router.replace(`/admin/operations-center?tab=${encodeURIComponent(value)}`, { scroll: false })} className="space-y-4">
                <TabsList className="grid w-full grid-cols-4 rounded-2xl bg-white shadow-sm">
                    <TabsTrigger value="overview">{t("app.admin.dashboard.operations.center.page.kbd84d331")}</TabsTrigger>
                    <TabsTrigger value="approvals">{t("app.admin.dashboard.operations.center.page.k61dba659")}</TabsTrigger>
                    <TabsTrigger value="runs">{t("app.admin.dashboard.operations.center.page.k1a586b06")}</TabsTrigger>
                    <TabsTrigger value="advanced">{t("app.admin.dashboard.operations.center.page.kdce17454")}</TabsTrigger>
                </TabsList>

                <TabsContent value="overview" className="space-y-4">
                    {pendingApprovalMismatch ? (<StatusNotice title={"app.admin.dashboard.operations.center.page.kb951aaf4"} description={t("app.admin.dashboard.operations.center.page.warning.pendingApprovalMismatch", {
                        summary_count: summary?.pendingApprovals ?? "-",
                        approval_count: runtime.approvals.length,
                    })} tone="warning"/>) : null}
                    {runtime.approvals.length > 0 ? (<StatusNotice title={"app.admin.dashboard.operations.center.page.kca01fb70"} description={"app.admin.dashboard.operations.center.page.ke9a1b1b4"} tone="warning"/>) : null}
                    <div className="grid gap-4 xl:grid-cols-2">
                        <PendingApprovalsPanel hook={runtime} focusRunId={focusRunId} focusSessionId={focusSessionId}/>
                        <RecentRunsPanel hook={runtime} focusRunId={focusRunId} focusSessionId={focusSessionId}/>
                    </div>
                </TabsContent>

                <TabsContent value="approvals">
                    <PendingApprovalsPanel hook={runtime} focusRunId={focusRunId} focusSessionId={focusSessionId}/>
                </TabsContent>

                <TabsContent value="runs" className="space-y-4">
                    {runtime.runs.some((run) => ["paused", "failed", "waiting_input"].includes(run.status || "")) ? (<StatusNotice title={"app.admin.dashboard.operations.center.page.k9eb5cbb2"} description={"app.admin.dashboard.operations.center.page.ke692c9ed"} tone="success"/>) : null}
                    <RecentRunsPanel hook={runtime} focusRunId={focusRunId} focusSessionId={focusSessionId}/>
                </TabsContent>

                <TabsContent value="advanced">
                    <AdvancedSection title={"app.admin.dashboard.operations.center.page.k428237fe"} defaultOpen>
                        <AuditLogsPanel />
                    </AdvancedSection>
                </TabsContent>
            </Tabs>
        </AdminPageShell>);
}
