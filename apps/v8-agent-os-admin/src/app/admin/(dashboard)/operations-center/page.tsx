"use client";
import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2, RefreshCw } from "lucide-react";
import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { AdvancedSection } from "@/components/admin-shell/AdvancedSection";
import { AdminHoverInfo } from "@/components/admin-shell/AdminHoverInfo";
import { DomainSummaryStrip } from "@/components/admin-shell/DomainSummaryStrip";
import { StatusNotice } from "@/components/admin-shell/StatusNotice";
import AuditLogsPanel from "@/components/memory/AuditLogsPanel";
import { PendingApprovalsPanel } from "@/components/runtime/PendingApprovalsPanel";
import { RecentRunsPanel } from "@/components/runtime/RecentRunsPanel";
import { useRuntimeOpsData } from "@/components/runtime/use-runtime-ops";
import { useT } from "@/components/providers/LocaleProvider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
type StorageRetentionPayload = {
    maxBytes?: number;
    totalGovernedBytes?: number;
    overCapBytes?: number;
    components?: Record<string, number>;
    recentRetentionEvents?: Array<{
        id?: string;
        status?: string;
        before_bytes?: number;
        after_bytes?: number;
        beforeBytes?: number;
        afterBytes?: number;
        created_at?: string;
        createdAt?: string;
        actions?: unknown[];
    }>;
};
const VALID_TABS = new Set(["overview", "approvals", "runs", "evidence", "advanced"]);
const OPERATION_LOG_SOURCES = ["all", "runtime", "audit", "cron", "hook", "safety", "storage"] as const;
type OperationLogSource = (typeof OPERATION_LOG_SOURCES)[number];

type OperationLogItem = {
    id: string;
    timestamp: string;
    source: string;
    status: string;
    action: string;
    runId?: string;
    sessionId?: string;
    summary: string;
    details?: string;
};

type RunLedgerPayload = {
    runId?: string;
    status?: string;
    runtimeKind?: string;
    nextAction?: string;
    refs?: Record<string, string[]>;
    timeline?: Array<{
        id?: string;
        type?: string;
        source?: string;
        runtimeKind?: string;
        ts?: string;
        summary?: string;
        refs?: Record<string, unknown>;
    }>;
};

function formatBytes(value?: number) {
    const bytes = Number(value || 0);
    if (bytes <= 0) return "0 MB";
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function StorageRetentionPanel() {
    const [stats, setStats] = useState<StorageRetentionPayload | null>(null);
    const [lastResult, setLastResult] = useState<Record<string, any> | null>(null);
    const [loading, setLoading] = useState(false);
    const load = async () => {
        setLoading(true);
        try {
            const response = await fetch("/api/storage-retention/stats", { cache: "no-store" });
            const payload = await response.json().catch(() => null);
            if (response.ok) setStats(payload);
        }
        finally {
            setLoading(false);
        }
    };
    const run = async (kind: "dry-run" | "prune") => {
        setLoading(true);
        try {
            const response = await fetch(`/api/storage-retention/${kind}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ reason: `admin_${kind}` }),
            });
            const payload = await response.json().catch(() => null);
            setLastResult(payload);
            await load();
        }
        finally {
            setLoading(false);
        }
    };
    useEffect(() => {
        void load();
    }, []);
    const components = stats?.components || {};
    const actionCount = Array.isArray(lastResult?.actions) ? lastResult.actions.length : 0;
    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <div className="text-sm font-semibold text-slate-900">Storage Retention</div>
                    <div className="text-xs leading-5 text-slate-500">
                        200MB hard cap for observability logs, snapshots, checkpoints and runtime log files. User-visible messages and artifacts are protected.
                    </div>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
                        {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                        Refresh
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => void run("dry-run")} disabled={loading}>Dry run</Button>
                    <Button size="sm" onClick={() => void run("prune")} disabled={loading}>Prune</Button>
                </div>
            </div>
            <div className="grid gap-3 md:grid-cols-4">
                <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm">
                    <div className="text-xs text-slate-500">Governed</div>
                    <div className="font-semibold text-slate-900">{formatBytes(stats?.totalGovernedBytes)}</div>
                </div>
                <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm">
                    <div className="text-xs text-slate-500">Cap</div>
                    <div className="font-semibold text-slate-900">{formatBytes(stats?.maxBytes)}</div>
                </div>
                <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm">
                    <div className="text-xs text-slate-500">Over cap</div>
                    <div className={Number(stats?.overCapBytes || 0) > 0 ? "font-semibold text-amber-700" : "font-semibold text-emerald-700"}>{formatBytes(stats?.overCapBytes)}</div>
                </div>
                <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm">
                    <div className="text-xs text-slate-500">Last action</div>
                    <div className="font-semibold text-slate-900">{lastResult?.status || stats?.recentRetentionEvents?.[0]?.status || "none"}</div>
                </div>
            </div>
            <div className="grid gap-2 md:grid-cols-2">
                {Object.entries(components).map(([key, value]) => (
                    <div key={key} className="flex items-center justify-between rounded-xl border border-slate-200 px-3 py-2 text-xs">
                        <span className="text-slate-500">{key}</span>
                        <span className="font-mono text-slate-900">{formatBytes(Number(value || 0))}</span>
                    </div>
                ))}
            </div>
            {lastResult ? (
                <div className="rounded-xl border border-slate-200 bg-white p-3 text-xs leading-5 text-slate-600">
                    Result: {lastResult.status || "unknown"} · actions {actionCount} · before {formatBytes(lastResult.beforeBytes)} · after {formatBytes(lastResult.afterBytes)}
                </div>
            ) : null}
        </div>
    );
}

function stringifyLogDetails(value: unknown) {
    if (value == null) return "";
    if (typeof value === "string") return value;
    try {
        return JSON.stringify(value);
    } catch {
        return String(value);
    }
}

function logTimestamp(value: Record<string, any>) {
    return String(value.createdAt || value.created_at || value.updatedAt || value.updated_at || value.timestamp || value.started_at || value.finished_at || "");
}

function OperationLogsPanel() {
    const t = useT();
    const [source, setSource] = useState<OperationLogSource>("all");
    const [items, setItems] = useState<OperationLogItem[]>([]);
    const [loading, setLoading] = useState(false);

    const load = async () => {
        setLoading(true);
        try {
            const requests: Array<Promise<{ kind: string; payload: any }>> = [];
            if (source === "all" || source === "runtime") {
                requests.push(fetch("/api/runs?limit=40", { cache: "no-store" }).then(async (response) => ({ kind: "runtime", payload: response.ok ? await response.json().catch(() => ({})) : {} })));
            }
            if (source === "all" || source === "audit" || source === "hook" || source === "safety" || source === "storage") {
                const params = new URLSearchParams({ limit: "80" });
                if (source === "hook") params.set("source_type", "HOOK");
                if (source === "safety") params.set("source_type", "SAFETY");
                if (source === "storage") params.set("source_type", "STORAGE_RETENTION");
                requests.push(fetch(`/api/audit/logs?${params.toString()}`, { cache: "no-store" }).then(async (response) => ({ kind: "audit", payload: response.ok ? await response.json().catch(() => ({})) : {} })));
            }
            if (source === "all" || source === "cron") {
                requests.push(fetch("/api/cron/logs?limit=80", { cache: "no-store" }).then(async (response) => ({ kind: "cron", payload: response.ok ? await response.json().catch(() => ({})) : {} })));
            }
            const responses = await Promise.all(requests);
            const nextItems: OperationLogItem[] = [];
            for (const item of responses) {
                if (item.kind === "runtime") {
                    for (const run of Array.isArray(item.payload?.runs) ? item.payload.runs : []) {
                        const record = (run || {}) as Record<string, any>;
                        nextItems.push({
                            id: String(record.id || record.runId || `run-${nextItems.length}`),
                            timestamp: logTimestamp(record),
                            source: "Runtime Runs",
                            status: String(record.status || "unknown"),
                            action: String(record.runtimeKind || record.runtime_kind || record.kind || "run"),
                            runId: String(record.id || record.runId || ""),
                            sessionId: String(record.sessionId || record.session_id || ""),
                            summary: String(record.summary || record.title || record.name || record.status || "runtime run"),
                            details: stringifyLogDetails(record.lastEvent || record.error || record.metadata || ""),
                        });
                    }
                }
                if (item.kind === "audit") {
                    for (const log of Array.isArray(item.payload?.logs) ? item.payload.logs : []) {
                        const record = (log || {}) as Record<string, any>;
                        nextItems.push({
                            id: String(record.id || `audit-${nextItems.length}`),
                            timestamp: logTimestamp(record),
                            source: String(record.source_type || record.sourceType || "Audit"),
                            status: String(record.status || "unknown"),
                            action: String(record.action || "audit"),
                            runId: String(record.run_id || record.runId || ""),
                            sessionId: String(record.session_id || record.sessionId || ""),
                            summary: String(record.summary || record.message || record.action || "audit log"),
                            details: stringifyLogDetails(record.details),
                        });
                    }
                }
                if (item.kind === "cron") {
                    for (const log of Array.isArray(item.payload?.logs) ? item.payload.logs : []) {
                        const record = (log || {}) as Record<string, any>;
                        nextItems.push({
                            id: String(record.id || record.execution_id || `cron-${nextItems.length}`),
                            timestamp: logTimestamp(record),
                            source: "Cron",
                            status: String(record.status || record.result || "unknown"),
                            action: String(record.job_id || record.jobId || record.name || "cron job"),
                            runId: String(record.run_id || record.runId || ""),
                            sessionId: String(record.session_id || record.sessionId || ""),
                            summary: String(record.summary || record.message || record.error || record.status || "cron execution"),
                            details: stringifyLogDetails(record.details || record.payload || record.error),
                        });
                    }
                }
            }
            nextItems.sort((a, b) => String(b.timestamp).localeCompare(String(a.timestamp)));
            setItems(nextItems.slice(0, 120));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        void load();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [source]);

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <AdminHoverInfo content={t("app.admin.dashboard.operations.center.logs.description")}>
                    <h2 className="text-sm font-semibold text-slate-900">{t("app.admin.dashboard.operations.center.logs.title")}</h2>
                </AdminHoverInfo>
                <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
                    {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                    {t("app.admin.dashboard.operations.center.page.kd4db8d84")}
                </Button>
            </div>
            <div className="flex flex-wrap gap-2">
                {OPERATION_LOG_SOURCES.map((item) => (
                    <button
                        key={item}
                        type="button"
                        onClick={() => setSource(item)}
                        className={`rounded-full px-3 py-1 text-xs font-medium transition ${source === item ? "bg-slate-950 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}
                    >
                        {t(`app.admin.dashboard.operations.center.logs.source.${item}`)}
                    </button>
                ))}
            </div>
            <div className="max-h-[520px] space-y-2 overflow-auto rounded-2xl border border-slate-200 bg-white p-3">
                {items.length === 0 ? (
                    <div className="rounded-xl border border-dashed border-slate-200 p-5 text-sm text-slate-500">
                        {t("app.admin.dashboard.operations.center.logs.empty")}
                    </div>
                ) : items.map((item) => (
                    <div key={`${item.source}-${item.id}`} className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                            <div className="font-semibold text-slate-900">{item.action}</div>
                            <div className="flex flex-wrap items-center gap-2 text-slate-500">
                                <span>{item.source}</span>
                                <span>{item.status}</span>
                                <span>{item.timestamp || "-"}</span>
                            </div>
                        </div>
                        <div className="mt-1 text-slate-700">{item.summary}</div>
                        <div className="mt-1 flex flex-wrap gap-2 font-mono text-[11px] text-slate-500">
                            {item.runId ? <span>run {compactId(item.runId)}</span> : null}
                            {item.sessionId ? <span>session {compactId(item.sessionId)}</span> : null}
                        </div>
                        {item.details ? <div className="mt-2 line-clamp-3 break-all text-slate-500">{item.details}</div> : null}
                    </div>
                ))}
            </div>
        </div>
    );
}

type ToolObservation = {
    id?: string;
    rawRef?: string;
    toolName?: string;
    toolCallId?: string;
    runtimeKind?: string;
    surface?: string;
    rawChars?: number;
    visibleChars?: number;
    rawSha256?: string;
    created_at?: string;
    createdAt?: string;
    preview?: string;
    previewChars?: number;
    omittedChars?: number;
    redacted?: boolean;
    budget?: Record<string, any>;
    metadata?: Record<string, any>;
};

type CompactionRecord = {
    id?: string;
    run_id?: string;
    session_id?: string;
    target_role?: string;
    trigger_reason?: string;
    summary_method?: string;
    estimated_saved_tokens?: number;
    covered_message_count?: number;
    created_at?: string;
};

function compactId(value?: string) {
    const text = String(value || "");
    if (text.length <= 18) return text || "-";
    return `${text.slice(0, 9)}...${text.slice(-6)}`;
}

function EvidencePanel() {
    const t = useT();
    const [items, setItems] = useState<ToolObservation[]>([]);
    const [compactions, setCompactions] = useState<CompactionRecord[]>([]);
    const [selected, setSelected] = useState<ToolObservation | null>(null);
    const [nextCursor, setNextCursor] = useState<string | null>(null);
    const [filters, setFilters] = useState({ runId: "", sessionId: "", toolName: "", runtimeKind: "" });
    const [loading, setLoading] = useState(false);
    const [revealing, setRevealing] = useState(false);

    const buildQuery = (cursor?: string | null) => {
        const params = new URLSearchParams();
        Object.entries(filters).forEach(([key, value]) => {
            if (value.trim()) params.set(key, value.trim());
        });
        params.set("limit", "50");
        if (cursor) params.set("cursor", cursor);
        return params.toString();
    };

    const load = async (mode: "replace" | "append" = "replace") => {
        setLoading(true);
        try {
            const query = buildQuery(mode === "append" ? nextCursor : null);
            const [observationsResponse, compactionsResponse] = await Promise.all([
                fetch(`/api/observability/tool-observations?${query}`, { cache: "no-store" }),
                fetch("/api/observability/compactions?limit=12", { cache: "no-store" }),
            ]);
            const observations = await observationsResponse.json().catch(() => ({}));
            const compactionPayload = await compactionsResponse.json().catch(() => ({}));
            if (observationsResponse.ok) {
                const nextItems = Array.isArray(observations.items) ? observations.items : [];
                setItems((current) => mode === "append" ? [...current, ...nextItems] : nextItems);
                setNextCursor(observations.nextCursor || null);
                if (mode === "replace") setSelected(nextItems[0] || null);
            }
            if (compactionsResponse.ok) {
                setCompactions(Array.isArray(compactionPayload.items) ? compactionPayload.items : []);
            }
        }
        finally {
            setLoading(false);
        }
    };

    const reveal = async () => {
        if (!selected?.id) return;
        setRevealing(true);
        try {
            const response = await fetch(`/api/observability/tool-observations/${encodeURIComponent(selected.id)}/reveal`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ maxChars: 12000 }),
            });
            const payload = await response.json().catch(() => null);
            if (response.ok && payload) setSelected(payload);
        }
        finally {
            setRevealing(false);
        }
    };

    useEffect(() => {
        void load();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const selectedRunId = String(selected?.metadata?.runId || selected?.metadata?.run_id || "");
    const selectedSessionId = String(selected?.metadata?.sessionId || selected?.metadata?.session_id || "");
    const relatedCompactions = compactions.filter((item) => (
        (selectedRunId && item.run_id === selectedRunId) || (selectedSessionId && item.session_id === selectedSessionId)
    ));

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <AdminHoverInfo
                    content={t("app.admin.dashboard.operations.center.evidence.description")}
                    panelClassName="text-sm leading-6"
                >
                    <h2 className="text-sm font-semibold text-slate-900">{t("app.admin.dashboard.operations.center.evidence.title")}</h2>
                </AdminHoverInfo>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
                        {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                        {t("app.admin.dashboard.operations.center.page.kd4db8d84")}
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => selected?.rawRef && navigator.clipboard?.writeText(selected.rawRef)} disabled={!selected?.rawRef}>
                        {t("app.admin.dashboard.operations.center.evidence.copyRawRef")}
                    </Button>
                </div>
            </div>

            <div className="grid gap-2 md:grid-cols-4">
                <Input value={filters.runId} onChange={(event) => setFilters({ ...filters, runId: event.target.value })} placeholder={t("app.admin.dashboard.operations.center.evidence.runId")} />
                <Input value={filters.sessionId} onChange={(event) => setFilters({ ...filters, sessionId: event.target.value })} placeholder={t("app.admin.dashboard.operations.center.evidence.sessionId")} />
                <Input value={filters.toolName} onChange={(event) => setFilters({ ...filters, toolName: event.target.value })} placeholder={t("app.admin.dashboard.operations.center.evidence.toolName")} />
                <Input value={filters.runtimeKind} onChange={(event) => setFilters({ ...filters, runtimeKind: event.target.value })} placeholder={t("app.admin.dashboard.operations.center.evidence.runtimeKind")} />
            </div>

            <div className="grid gap-4 xl:grid-cols-[360px_1fr]">
                <div className="space-y-2 rounded-2xl border border-slate-200 bg-white p-3 shadow-sm">
                    {items.length === 0 ? (
                        <div className="rounded-xl border border-dashed border-slate-200 p-4 text-sm text-slate-500">{t("app.admin.dashboard.operations.center.evidence.empty")}</div>
                    ) : items.map((item) => (
                        <button
                            key={item.id || item.rawRef}
                            type="button"
                            onClick={() => setSelected(item)}
                            className={`w-full rounded-xl border px-3 py-2 text-left text-xs transition ${selected?.id === item.id ? "border-slate-900 bg-slate-950 text-white" : "border-slate-200 bg-slate-50 text-slate-700 hover:border-slate-300"}`}
                        >
                            <div className="flex items-center justify-between gap-2">
                                <span className="truncate font-semibold">{item.toolName || "unknown"}</span>
                                <span className="shrink-0 opacity-70">{compactId(item.runtimeKind)}</span>
                            </div>
                            <div className="mt-1 truncate opacity-70">{item.createdAt || item.created_at || "-"}</div>
                            <div className="mt-1 truncate font-mono opacity-70">{compactId(item.rawRef)}</div>
                        </button>
                    ))}
                    {nextCursor ? (
                        <Button className="w-full" variant="outline" size="sm" onClick={() => void load("append")} disabled={loading}>
                            {t("app.admin.dashboard.operations.center.evidence.loadMore")}
                        </Button>
                    ) : null}
                </div>

                <div className="space-y-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                    {selected ? (
                        <>
                            <div className="grid gap-2 text-xs md:grid-cols-3">
                                <div className="rounded-xl border border-slate-200 p-3">
                                    <div className="text-slate-500">{t("app.admin.dashboard.operations.center.evidence.rawRef")}</div>
                                    <div className="mt-1 break-all font-mono text-slate-900">{selected.rawRef || "-"}</div>
                                </div>
                                <div className="rounded-xl border border-slate-200 p-3">
                                    <div className="text-slate-500">{t("app.admin.dashboard.operations.center.evidence.size")}</div>
                                    <div className="mt-1 font-semibold text-slate-900">{selected.rawChars || 0} / {selected.visibleChars || 0}</div>
                                </div>
                                <div className="rounded-xl border border-slate-200 p-3">
                                    <div className="text-slate-500">{t("app.admin.dashboard.operations.center.evidence.hash")}</div>
                                    <div className="mt-1 truncate font-mono text-slate-900">{selected.rawSha256 || "-"}</div>
                                </div>
                            </div>
                            <div className="grid gap-2 text-xs md:grid-cols-2">
                                <pre className="max-h-44 overflow-auto rounded-xl bg-slate-950 p-3 text-white">{JSON.stringify(selected.budget || {}, null, 2)}</pre>
                                <pre className="max-h-44 overflow-auto rounded-xl bg-slate-950 p-3 text-white">{JSON.stringify(selected.metadata || {}, null, 2)}</pre>
                            </div>
                            <div className="rounded-xl border border-slate-200 p-3 text-xs">
                                <div className="font-semibold text-slate-900">{t("app.admin.dashboard.operations.center.evidence.relatedCompactions")}</div>
                                <div className="mt-2 space-y-1 text-slate-600">
                                    {relatedCompactions.length ? relatedCompactions.slice(0, 3).map((item) => (
                                        <div key={item.id} className="flex flex-wrap items-center justify-between gap-2">
                                            <span className="font-mono">{compactId(item.id)}</span>
                                            <span>{item.summary_method || "-"}</span>
                                            <span>{t("app.admin.dashboard.operations.center.evidence.savedTokens", { tokens: item.estimated_saved_tokens || 0 })}</span>
                                        </div>
                                    )) : t("app.admin.dashboard.operations.center.evidence.noRelatedCompactions")}
                                </div>
                            </div>
                            <div>
                                <div className="mb-2 flex items-center justify-between gap-2">
                                    <div className="text-sm font-semibold text-slate-900">{t("app.admin.dashboard.operations.center.evidence.preview")}</div>
                                    <div className="flex gap-2">
                                        <Button variant="outline" size="sm" onClick={() => navigator.clipboard?.writeText(selected.preview || "")}>
                                            {t("app.admin.dashboard.operations.center.evidence.copyPreview")}
                                        </Button>
                                        <Button size="sm" onClick={() => void reveal()} disabled={revealing}>
                                            {revealing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                                            {t("app.admin.dashboard.operations.center.evidence.reveal")}
                                        </Button>
                                    </div>
                                </div>
                                <pre className="max-h-[440px] overflow-auto whitespace-pre-wrap rounded-2xl border border-slate-200 bg-slate-50 p-4 text-xs leading-5 text-slate-800">{selected.preview || ""}</pre>
                                <div className="mt-2 text-xs text-slate-500">
                                    {t("app.admin.dashboard.operations.center.evidence.omitted", { omitted_chars: selected.omittedChars || 0, redacted: selected.redacted ? "yes" : "no" })}
                                </div>
                            </div>
                        </>
                    ) : (
                        <div className="rounded-xl border border-dashed border-slate-200 p-8 text-center text-sm text-slate-500">{t("app.admin.dashboard.operations.center.evidence.select")}</div>
                    )}
                </div>
            </div>

            <AdvancedSection title={t("app.admin.dashboard.operations.center.evidence.compactions")} defaultOpen={false}>
                <div className="space-y-2">
                    {compactions.length === 0 ? (
                        <div className="rounded-xl border border-dashed border-slate-200 p-4 text-sm text-slate-500">{t("app.admin.dashboard.operations.center.evidence.noCompactions")}</div>
                    ) : compactions.map((item) => (
                        <div key={item.id} className="grid gap-2 rounded-xl border border-slate-200 p-3 text-xs md:grid-cols-5">
                            <div className="font-mono text-slate-600">{compactId(item.id)}</div>
                            <div>{item.target_role || "-"}</div>
                            <div>{item.summary_method || "-"}</div>
                            <div>{t("app.admin.dashboard.operations.center.evidence.savedTokens", { tokens: item.estimated_saved_tokens || 0 })}</div>
                            <div className="truncate text-slate-500">{item.trigger_reason || "-"}</div>
                        </div>
                    ))}
                </div>
            </AdvancedSection>
        </div>
    );
}

function RunLedgerPanel({ runId }: { runId: string | null }) {
    const [ledger, setLedger] = useState<RunLedgerPayload | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const load = async () => {
        if (!runId) return;
        setLoading(true);
        setError(null);
        try {
            const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/ledger`, { cache: "no-store" });
            const payload = await response.json().catch(() => null);
            if (!response.ok) {
                setError(String(payload?.detail || payload?.error || "Run ledger unavailable"));
                setLedger(null);
                return;
            }
            setLedger(payload || null);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Run ledger unavailable");
            setLedger(null);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        void load();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [runId]);

    if (!runId) {
        return null;
    }

    const timeline = Array.isArray(ledger?.timeline) ? ledger.timeline : [];
    const refs = ledger?.refs || {};
    return (
        <div className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <AdminHoverInfo content="Run Ledger 聚合 runtime events、raw evidence、approval、artifact、compaction 与 compat lifecycle，便于从 runId 追踪发生了什么。">
                    <div>
                        <h2 className="text-lg font-semibold text-slate-950">Run Ledger</h2>
                        <p className="mt-1 font-mono text-xs text-slate-500">{runId}</p>
                    </div>
                </AdminHoverInfo>
                <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
                    {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                    刷新
                </Button>
            </div>
            {error ? <div className="mt-4 rounded-2xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">{error}</div> : null}
            {ledger ? (
                <div className="mt-4 grid gap-4 xl:grid-cols-[260px_minmax(0,1fr)]">
                    <div className="space-y-2 rounded-2xl border border-slate-100 bg-slate-50 p-3 text-sm text-slate-600">
                        <div>状态：<span className="font-medium text-slate-950">{ledger.status || "-"}</span></div>
                        <div>Runtime：<span className="font-medium text-slate-950">{ledger.runtimeKind || "-"}</span></div>
                        <div>下一步：<span className="font-medium text-slate-950">{ledger.nextAction || "-"}</span></div>
                        {Object.entries(refs).map(([key, value]) => (
                            <div key={key}>{key}: <span className="font-mono text-xs text-slate-700">{Array.isArray(value) ? value.length : 0}</span></div>
                        ))}
                    </div>
                    <div className="max-h-[420px] space-y-2 overflow-auto pr-1">
                        {timeline.length === 0 ? (
                            <div className="rounded-2xl border border-dashed border-slate-200 p-5 text-sm text-slate-500">暂无 ledger 事件。</div>
                        ) : timeline.map((item) => (
                            <div key={item.id || `${item.type}-${item.ts}`} className="rounded-2xl border border-slate-100 bg-white p-3 shadow-sm">
                                <div className="flex flex-wrap items-center gap-2 text-xs">
                                    <span className="rounded-full bg-slate-950 px-2 py-0.5 font-medium text-white">{item.type || "event"}</span>
                                    <span className="text-slate-500">{item.source || "-"}</span>
                                    <span className="font-mono text-slate-400">{item.ts || "-"}</span>
                                </div>
                                {item.summary ? <p className="mt-2 text-sm text-slate-700">{item.summary}</p> : null}
                                {item.refs && Object.keys(item.refs).length > 0 ? (
                                    <pre className="mt-2 max-h-28 overflow-auto rounded-xl bg-slate-50 p-2 text-xs text-slate-500">
                                        {JSON.stringify(item.refs, null, 2)}
                                    </pre>
                                ) : null}
                            </div>
                        ))}
                    </div>
                </div>
            ) : !loading && !error ? (
                <div className="mt-4 rounded-2xl border border-dashed border-slate-200 p-5 text-sm text-slate-500">暂无 ledger 数据。</div>
            ) : null}
        </div>
    );
}
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
                <TabsList className="grid w-full grid-cols-5 rounded-2xl bg-white shadow-sm">
                    <TabsTrigger value="overview">{t("app.admin.dashboard.operations.center.page.kbd84d331")}</TabsTrigger>
                    <TabsTrigger value="approvals">{t("app.admin.dashboard.operations.center.page.k61dba659")}</TabsTrigger>
                    <TabsTrigger value="runs">{t("app.admin.dashboard.operations.center.page.k1a586b06")}</TabsTrigger>
                    <TabsTrigger value="evidence">{t("app.admin.dashboard.operations.center.evidence.tab")}</TabsTrigger>
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
                    <RunLedgerPanel runId={focusRunId} />
                    <RecentRunsPanel hook={runtime} focusRunId={focusRunId} focusSessionId={focusSessionId}/>
                </TabsContent>

                <TabsContent value="evidence">
                    <EvidencePanel />
                </TabsContent>

                <TabsContent value="advanced">
                    <AdvancedSection title="Storage Retention" defaultOpen={false}>
                        <StorageRetentionPanel />
                    </AdvancedSection>
                    <AdvancedSection title={t("app.admin.dashboard.operations.center.logs.title")} defaultOpen>
                        <OperationLogsPanel />
                    </AdvancedSection>
                    <AdvancedSection title={"app.admin.dashboard.operations.center.page.k428237fe"} defaultOpen>
                        <AuditLogsPanel />
                    </AdvancedSection>
                </TabsContent>
            </Tabs>
        </AdminPageShell>);
}
