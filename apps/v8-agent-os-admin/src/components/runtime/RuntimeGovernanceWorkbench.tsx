"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Eye, RefreshCw, Route, Save, Settings2, SlidersHorizontal } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useT } from "@/components/providers/LocaleProvider";
import { useToast } from "@/components/ui/use-toast";
import { ApprovalRecord, RunRecord, RUN_LABELS, formatWhen } from "@/components/runtime/use-runtime-ops";
import { CORE_RUNTIME_KINDS, getRuntimeControlHref, isLockedRuntimeKind } from "@/lib/runtime-admin";

type RuntimePolicy = {
    enabled?: boolean;
    auto_route?: boolean;
    expose_direct_tools?: boolean;
    priority?: number;
    notes?: string;
};

type RuntimeCapability = {
    key: string;
    label: string;
    risk_level?: string;
};

type RuntimeDescriptor = {
    kind: string;
    displayName: string;
    summary?: string;
    responsibilities?: string[];
    promptHints?: string[];
    visibility?: string;
    capabilities?: RuntimeCapability[];
    metadata?: Record<string, unknown>;
    policy: RuntimePolicy;
};

type CapabilityRecommendation = {
    kind: string;
    displayName: string;
    score: number;
    matchedKeywords?: string[];
    matchedSignals?: string[];
};

type CapabilitySnapshot = {
    count: number;
    recommendations?: CapabilityRecommendation[];
    runtimes?: RuntimeDescriptor[];
};

type RuntimePresetId = "balanced" | "conservative" | "debug";

type SessionWorkflowView = {
    status?: string;
    recoverable?: boolean;
    ownerRuntime?: string;
    currentStepTitle?: string;
    currentStepStatus?: string;
    updatedAt?: string;
};

type SessionSummary = {
    id: string;
    title?: string;
    source?: string;
    workflow?: SessionWorkflowView | null;
    approvals?: ApprovalRecord[];
    recoverableView?: {
        recoverable?: boolean;
        canResume?: boolean;
        canRetry?: boolean;
        workflowStatus?: string;
    } | null;
    summary?: {
        previewExcerpt?: string;
        workflowStatus?: string;
    } | null;
};

type SessionDetail = {
    id: string;
    source?: string;
    workflow?: SessionWorkflowView | null;
    workflowProjection?: Record<string, unknown> | null;
    runtimeTimeline?: Array<Record<string, unknown>>;
    approvals?: ApprovalRecord[];
    controls?: {
        canResume?: boolean;
        canRetry?: boolean;
        canInterrupt?: boolean;
    } | null;
    recoverable?: {
        recoverable?: boolean;
        workflowStatus?: string;
    } | null;
    summary?: {
        previewExcerpt?: string;
        workflowStatus?: string;
    } | null;
    messages?: Array<{
        id?: string;
        role?: string;
        content?: string;
        createdAt?: string;
    }>;
};

type RuntimeLiveStats = {
    kind: string;
    totalRuns: number;
    activeRuns: number;
    failedRuns: number;
    pendingApprovals: number;
    recoverableSessions: number;
};

type MemoryAuditLog = {
    id?: string;
    source_type?: string;
    action?: string;
    status?: string;
    details?: string;
    timestamp?: string;
};

type MemoryDashboard = {
    extractions?: {
        summary?: Record<string, number>;
    };
    maintenance?: {
        summary?: Record<string, number>;
    };
    workflows?: Record<string, unknown>;
};

const PRESET_LABELS: Record<RuntimePresetId, { title: string; description: string }> = {
    balanced: { title:"components.runtime.RuntimeGovernanceWorkbench.k0590e788", description:"components.runtime.RuntimeGovernanceWorkbench.k4a43ff73" },
    conservative: { title:"components.runtime.RuntimeGovernanceWorkbench.k1de80f3c", description:"components.runtime.RuntimeGovernanceWorkbench.k7b94c86c" },
    debug: { title:"components.runtime.RuntimeGovernanceWorkbench.ka3d1efbb", description:"components.runtime.RuntimeGovernanceWorkbench.kcd231fad" },
};

const NONCORE_RUNTIME_KINDS = ["plugin_host", "computer_use", "rpa"] as const;

function normalizePolicy(policy?: RuntimePolicy): Required<RuntimePolicy> {
    return {
        enabled: policy?.enabled ?? true,
        auto_route: policy?.auto_route ?? true,
        expose_direct_tools: policy?.expose_direct_tools ?? true,
        priority: typeof policy?.priority === "number" ? policy.priority : 100,
        notes: policy?.notes ?? "",
    };
}

function managedToolSummary(runtime: RuntimeDescriptor) {
    const metadata = runtime.metadata || {};
    const exact = Array.isArray(metadata.managedToolNames) ? metadata.managedToolNames.map(String) : [];
    const prefixes = Array.isArray(metadata.managedToolPrefixes) ? metadata.managedToolPrefixes.map(String) : [];
    return { exact, prefixes, hasManaged: exact.length > 0 || prefixes.length > 0 };
}

function buildPresetPolicy(runtime: RuntimeDescriptor, preset: RuntimePresetId): Required<RuntimePolicy> {
    const current = normalizePolicy(runtime.policy);
    const managed = managedToolSummary(runtime);
    const isPrimary = runtime.visibility === "primary";
    if (preset === "debug") {
        return { enabled: true, auto_route: true, expose_direct_tools: managed.hasManaged ? true : current.expose_direct_tools, priority: Math.min(current.priority, 80), notes: "第四阶段调试模板：临时放开 direct tools 便于排障。" };
    }
    if (preset === "conservative") {
        return { enabled: true, auto_route: isPrimary || current.auto_route, expose_direct_tools: false, priority: isPrimary ? 40 : Math.max(current.priority, 120), notes: "第四阶段保守模板：优先 runtime 编排，不建议直连低层工具。" };
    }
    return { enabled: true, auto_route: true, expose_direct_tools: managed.hasManaged ? false : current.expose_direct_tools, priority: isPrimary ? 30 : current.priority, notes: "第四阶段平衡模板：保持自动路由，同时收住大部分低层 direct tools。" };
}

function riskLabel(value?: string) {
    if (value === "high") return "高风险";
    if (value === "medium") return "中风险";
    if (value === "low") return "低风险";
    return "未标注";
}

function inferRunRuntime(run: RunRecord): string {
    const metadata = run.metadata || {};
    const runtime = typeof metadata.runtime === "string" && metadata.runtime.trim() ? metadata.runtime : null;
    if (runtime) return runtime;
    if (typeof run.run_type === "string" && run.run_type.trim()) return run.run_type;
    return "chat";
}

function asRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : {};
}

function asRecordArray(value: unknown): Record<string, unknown>[] {
    return Array.isArray(value)
        ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item))
        : [];
}

function asString(value: unknown): string {
    return typeof value === "string" ? value.trim() : "";
}

function asStringArray(value: unknown): string[] {
    return Array.isArray(value)
        ? value.map((item) => asString(item)).filter(Boolean)
        : [];
}

function parseMemoryAuditDetails(value?: string) {
    if (!value) return {};
    try {
        const parsed = JSON.parse(value);
        return parsed && typeof parsed === "object" && !Array.isArray(parsed)
            ? parsed as Record<string, unknown>
            : {};
    } catch {
        return {};
    }
}

function compactNumber(value: unknown) {
    const num = Number(value || 0);
    if (!Number.isFinite(num)) return 0;
    return num;
}

function extractPlannerInspector(detail: SessionDetail | null) {
    if (!detail) return null;
    const workflowProjection = asRecord(detail.workflowProjection);
    const steps = asRecordArray(workflowProjection.steps);
    const plannerSteps = steps.filter((step) => asString(step.ownerRuntime) === "planner_lane");
    const latestPlannerStep = plannerSteps[plannerSteps.length - 1] || null;
    const plannerInput = asRecord(latestPlannerStep?.input);
    const plannerPlan = asRecord(plannerInput.plannerPlan);
    const runtimeTimeline = asRecordArray(detail.runtimeTimeline);
    const plannerEntries = runtimeTimeline.filter((entry) => asString(entry.runtimeId) === "planner_lane");
    const latestPlannerEntry = plannerEntries[plannerEntries.length - 1] || null;
    const latestPlannerMetadata = asRecord(latestPlannerEntry?.metadata);
    const selectedDelegations = asRecordArray(latestPlannerMetadata.selectedDelegations);
    const taskBriefs = asRecordArray(latestPlannerMetadata.taskBriefs).length > 0
        ? asRecordArray(latestPlannerMetadata.taskBriefs)
        : asRecordArray(plannerPlan.taskBriefs);
    const dependencies = asRecordArray(latestPlannerMetadata.dependencies);
    const riskFlags = asStringArray(latestPlannerMetadata.riskFlags).length > 0
        ? asStringArray(latestPlannerMetadata.riskFlags)
        : asStringArray(plannerPlan.riskFlags);
    const executionStrategy = asString(latestPlannerMetadata.executionStrategy) || asString(plannerPlan.executionStrategy);
    const planSummary = asString(latestPlannerMetadata.planSummary) || asString(plannerPlan.planSummary);
    const planId = asString(latestPlannerMetadata.planId) || asString(plannerPlan.planId);
    const globalAcceptanceContract = asString(latestPlannerMetadata.globalAcceptanceContract) || asString(plannerPlan.globalAcceptanceContract);
    const traceRef = asRecord(latestPlannerMetadata.traceRef);
    if (!planId && !planSummary && taskBriefs.length === 0 && selectedDelegations.length === 0) {
        return null;
    }
    return {
        planId,
        planSummary,
        executionStrategy,
        globalAcceptanceContract,
        taskBriefs,
        selectedDelegations,
        riskFlags,
        dependencies,
        traceRef,
        stepTitle: asString(latestPlannerStep?.title),
    };
}

function isRecoverableSession(session: SessionSummary) {
    return Boolean(session.workflow?.recoverable || session.recoverableView?.recoverable);
}

type RuntimeGovernanceWorkbenchProps = {
    embedded?: boolean;
};

export function RuntimeGovernanceWorkbench({ embedded = false }: RuntimeGovernanceWorkbenchProps) {
    const { toast } = useToast();
    const t = useT();
    const [loading, setLoading] = useState(true);
    const [busyKey, setBusyKey] = useState<string | null>(null);
    const [query, setQuery] = useState("帮我做桌面自动化、RPA 和网页阅读");
    const [snapshot, setSnapshot] = useState<CapabilitySnapshot>({ count: 0, recommendations: [], runtimes: [] });
    const [draftPolicies, setDraftPolicies] = useState<Record<string, Required<RuntimePolicy>>>({});
    const [runs, setRuns] = useState<RunRecord[]>([]);
    const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);
    const [sessions, setSessions] = useState<SessionSummary[]>([]);
    const [activeRuntimeKind, setActiveRuntimeKind] = useState<string | null>(null);
    const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
    const [selectedSessionDetail, setSelectedSessionDetail] = useState<SessionDetail | null>(null);
    const [detailLoading, setDetailLoading] = useState(false);
    const [memoryDashboard, setMemoryDashboard] = useState<MemoryDashboard | null>(null);
    const [memoryAuditLogs, setMemoryAuditLogs] = useState<MemoryAuditLog[]>([]);

    const runtimes = useMemo(() => snapshot.runtimes || [], [snapshot.runtimes]);
    const recommendations = useMemo(() => snapshot.recommendations || [], [snapshot.recommendations]);
    const runtimeNameMap = useMemo(
        () => new Map(runtimes.map((item) => [item.kind, item.displayName] as const)),
        [runtimes]
    );

    const summary = useMemo(() => ({
        enabled: runtimes.filter((item) => normalizePolicy(item.policy).enabled).length,
        autoRoute: runtimes.filter((item) => normalizePolicy(item.policy).auto_route).length,
        directTools: runtimes.filter((item) => normalizePolicy(item.policy).expose_direct_tools).length,
    }), [runtimes]);

    const groupedRuntimes = useMemo(() => {
        const sections: Array<{
            key: string;
            title: string;
            description: string;
            runtimeKinds: string[];
        }> = [
            {
                key: "core",
                title:"components.runtime.RuntimeGovernanceWorkbench.k1bb93e07",
                description:"components.runtime.RuntimeGovernanceWorkbench.kecf19787",
                runtimeKinds: [...CORE_RUNTIME_KINDS],
            },
            {
                key: "noncore",
                title:"components.runtime.RuntimeGovernanceWorkbench.k29368dac",
                description:"components.runtime.RuntimeGovernanceWorkbench.k875af2ed",
                runtimeKinds: [...NONCORE_RUNTIME_KINDS],
            },
        ];
        const knownKinds = new Set(sections.flatMap((section) => section.runtimeKinds));
        const overflow = runtimes.filter((item) => !knownKinds.has(item.kind));
        if (overflow.length) {
            sections.push({
                key: "other",
                title:"components.runtime.RuntimeGovernanceWorkbench.k2f02da37",
                description:"components.runtime.RuntimeGovernanceWorkbench.k69ebb082",
                runtimeKinds: overflow.map((item) => item.kind),
            });
        }
        return sections.map((section) => ({
            ...section,
            runtimes: section.runtimeKinds
                .map((kind) => runtimes.find((item) => item.kind === kind))
                .filter((item): item is RuntimeDescriptor => Boolean(item)),
        }));
    }, [runtimes]);

    const runMap = useMemo(() => new Map(runs.map((run) => [run.id, run] as const)), [runs]);
    const sessionMap = useMemo(() => new Map(sessions.map((session) => [session.id, session] as const)), [sessions]);

    const observability = useMemo(() => {
        const stats = new Map<string, RuntimeLiveStats>();
        for (const runtime of runtimes) {
            stats.set(runtime.kind, { kind: runtime.kind, totalRuns: 0, activeRuns: 0, failedRuns: 0, pendingApprovals: 0, recoverableSessions: 0 });
        }

        for (const run of runs) {
            const kind = inferRunRuntime(run);
            const bucket = stats.get(kind) || { kind, totalRuns: 0, activeRuns: 0, failedRuns: 0, pendingApprovals: 0, recoverableSessions: 0 };
            bucket.totalRuns += 1;
            if (["running", "waiting_approval", "waiting_input", "paused"].includes(run.status || "")) bucket.activeRuns += 1;
            if (["failed", "cancelled"].includes(run.status || "")) bucket.failedRuns += 1;
            stats.set(kind, bucket);
        }

        for (const approval of approvals) {
            let kind = "chat";
            if (approval.run_id && runMap.has(approval.run_id)) {
                kind = inferRunRuntime(runMap.get(approval.run_id)!);
            } else if (approval.session_id && sessionMap.has(approval.session_id)) {
                kind = sessionMap.get(approval.session_id)?.workflow?.ownerRuntime || "chat";
            } else if ((approval.approval_kind || "").startsWith("rpa")) {
                kind = "rpa";
            }
            const bucket = stats.get(kind) || { kind, totalRuns: 0, activeRuns: 0, failedRuns: 0, pendingApprovals: 0, recoverableSessions: 0 };
            bucket.pendingApprovals += 1;
            stats.set(kind, bucket);
        }

        for (const session of sessions) {
            const kind = session.workflow?.ownerRuntime || "chat";
            if (!isRecoverableSession(session)) continue;
            const bucket = stats.get(kind) || { kind, totalRuns: 0, activeRuns: 0, failedRuns: 0, pendingApprovals: 0, recoverableSessions: 0 };
            bucket.recoverableSessions += 1;
            stats.set(kind, bucket);
        }

        return Array.from(stats.values()).sort((a, b) => a.kind.localeCompare(b.kind));
    }, [approvals, runMap, runtimes, runs, sessionMap, sessions]);

    const failedRuns = useMemo(() => runs.filter((run) => ["failed", "cancelled"].includes(run.status || "")), [runs]);
    const recoverableSessions = useMemo(() => sessions.filter((session) => isRecoverableSession(session)), [sessions]);

    const filteredFailedRuns = useMemo(
        () => (activeRuntimeKind ? failedRuns.filter((run) => inferRunRuntime(run) === activeRuntimeKind) : failedRuns),
        [activeRuntimeKind, failedRuns]
    );

    const filteredApprovals = useMemo(() => {
        if (!activeRuntimeKind) return approvals;
        return approvals.filter((approval) => {
            if (approval.run_id && runMap.has(approval.run_id)) {
                return inferRunRuntime(runMap.get(approval.run_id)!) === activeRuntimeKind;
            }
            if (approval.session_id && sessionMap.has(approval.session_id)) {
                return (sessionMap.get(approval.session_id)?.workflow?.ownerRuntime || "chat") === activeRuntimeKind;
            }
            return (approval.approval_kind || "").startsWith(activeRuntimeKind);
        });
    }, [activeRuntimeKind, approvals, runMap, sessionMap]);

    const filteredRecoverableSessions = useMemo(
        () => (activeRuntimeKind ? recoverableSessions.filter((session) => (session.workflow?.ownerRuntime || "chat") === activeRuntimeKind) : recoverableSessions),
        [activeRuntimeKind, recoverableSessions]
    );
    const plannerInspector = useMemo(
        () => extractPlannerInspector(selectedSessionDetail),
        [selectedSessionDetail]
    );
    const memoryExtractionSummary = memoryDashboard?.extractions?.summary || {};
    const memoryMaintenanceSummary = memoryDashboard?.maintenance?.summary || {};

    const loadSnapshot = useCallback(async (nextQuery?: string) => {
        const suffix = nextQuery?.trim() ? `?query=${encodeURIComponent(nextQuery.trim())}` : "";
        const res = await fetch(`/api/runtime-capabilities${suffix}`, { cache: "no-store" });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            throw new Error(data?.detail || data?.error || "加载规则状态失败");
        }
        const payload = data as CapabilitySnapshot;
        setSnapshot(payload);
        setDraftPolicies((current) => {
            const next = { ...current };
            for (const runtime of payload.runtimes || []) {
                next[runtime.kind] = current[runtime.kind] || normalizePolicy(runtime.policy);
            }
            return next;
        });
    }, []);

    const loadObservability = useCallback(async () => {
        const [runsRes, approvalsRes, sessionsRes, memoryRes, auditRes] = await Promise.all([
            fetch("/api/runs?limit=40", { cache: "no-store" }),
            fetch("/api/approvals?status=pending", { cache: "no-store" }),
            fetch("/api/conversations", { cache: "no-store" }),
            fetch("/api/memory/dashboard", { cache: "no-store" }),
            fetch("/api/audit/logs?source_type=MEMORY&limit=50", { cache: "no-store" }),
        ]);
        const runsData = runsRes.ok ? await runsRes.json().catch(() => ({})) : {};
        const approvalsData = approvalsRes.ok ? await approvalsRes.json().catch(() => ({})) : {};
        const sessionsData = sessionsRes.ok ? await sessionsRes.json().catch(() => []) : [];
        const memoryData = memoryRes.ok ? await memoryRes.json().catch(() => ({})) : {};
        const auditData = auditRes.ok ? await auditRes.json().catch(() => ({})) : {};
        setRuns(Array.isArray(runsData?.runs) ? runsData.runs : []);
        setApprovals(Array.isArray(approvalsData?.approvals) ? approvalsData.approvals : []);
        setSessions(Array.isArray(sessionsData) ? sessionsData : []);
        setMemoryDashboard(memoryData || null);
        setMemoryAuditLogs(Array.isArray(auditData?.logs) ? auditData.logs : []);
    }, []);

    const loadAll = useCallback(async (nextQuery?: string) => {
        await Promise.all([loadSnapshot(nextQuery), loadObservability()]);
    }, [loadObservability, loadSnapshot]);

    const inspectSession = useCallback(async (sessionId: string) => {
        setSelectedSessionId(sessionId);
        setDetailLoading(true);
        try {
            const res = await fetch(`/api/conversations/${encodeURIComponent(sessionId)}`, { cache: "no-store" });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                throw new Error(data?.detail || data?.error || "加载现场详情失败");
            }
            setSelectedSessionDetail(data as SessionDetail);
        } catch (error) {
            toast({ variant: "destructive", title:"components.runtime.RuntimeGovernanceWorkbench.kb6ea6822", description: error instanceof Error ? error.message : "未知错误" });
        } finally {
            setDetailLoading(false);
        }
    }, [toast]);

    useEffect(() => {
        void (async () => {
            try {
                await loadAll("帮我做桌面自动化、RPA 和网页阅读");
            } catch (error) {
                toast({ variant: "destructive", title:"components.runtime.RuntimeGovernanceWorkbench.k4a84157b", description: error instanceof Error ? error.message : "未知错误" });
            } finally {
                setLoading(false);
            }
        })();
    }, [loadAll, toast]);

    const patchPolicy = (kind: string, patch: Partial<Required<RuntimePolicy>>) => {
        setDraftPolicies((current) => ({
            ...current,
            [kind]: { ...(current[kind] || normalizePolicy(runtimes.find((item) => item.kind === kind)?.policy)), ...patch },
        }));
    };

    const handleSearch = async () => {
        setBusyKey("query");
        try {
            await loadSnapshot(query);
        } catch (error) {
            toast({ variant: "destructive", title:"components.runtime.RuntimeGovernanceWorkbench.ke7a17970", description: error instanceof Error ? error.message : "未知错误" });
        } finally {
            setBusyKey(null);
        }
    };

    const handleRefresh = async () => {
        setLoading(true);
        try {
            await loadAll(query);
            if (selectedSessionId) {
                await inspectSession(selectedSessionId);
            }
        } catch (error) {
            toast({ variant: "destructive", title:"components.runtime.RuntimeGovernanceWorkbench.kaeec4304", description: error instanceof Error ? error.message : "未知错误" });
        } finally {
            setLoading(false);
        }
    };

    const savePolicy = async (kind: string) => {
        const payload = draftPolicies[kind];
        if (!payload) return;
        setBusyKey(`save:${kind}`);
        try {
            const res = await fetch(`/api/runtime-capabilities/${encodeURIComponent(kind)}/policy`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    enabled: payload.enabled,
                    autoRoute: payload.auto_route,
                    exposeDirectTools: payload.expose_direct_tools,
                    priority: Number(payload.priority || 100),
                    notes: payload.notes,
                }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data?.detail || data?.error || "保存失败");
            toast({ title:"components.runtime.RuntimeGovernanceWorkbench.k93e608a9", description: `${kind} 已更新。` });
            await loadAll(query);
        } catch (error) {
            toast({ variant: "destructive", title:"components.runtime.RuntimeGovernanceWorkbench.k12769ce1", description: error instanceof Error ? error.message : "未知错误" });
        } finally {
            setBusyKey(null);
        }
    };

    const resetPolicy = async (kind: string) => {
        setBusyKey(`reset:${kind}`);
        try {
            const res = await fetch(`/api/runtime-capabilities/${encodeURIComponent(kind)}/policy`, { method: "DELETE" });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data?.detail || data?.error || "重置失败");
            toast({ title:"components.runtime.RuntimeGovernanceWorkbench.kd5cd9463", description: `${kind} 已恢复默认值。` });
            await loadAll(query);
        } catch (error) {
            toast({ variant: "destructive", title:"components.runtime.RuntimeGovernanceWorkbench.kbb48c954", description: error instanceof Error ? error.message : "未知错误" });
        } finally {
            setBusyKey(null);
        }
    };

    const applyPreset = async (preset: RuntimePresetId) => {
        setBusyKey(`preset:${preset}`);
        try {
            await Promise.all(runtimes.map(async (runtime) => {
                const policy = buildPresetPolicy(runtime, preset);
                const res = await fetch(`/api/runtime-capabilities/${encodeURIComponent(runtime.kind)}/policy`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        enabled: policy.enabled,
                        autoRoute: policy.auto_route,
                        exposeDirectTools: policy.expose_direct_tools,
                        priority: Number(policy.priority || 100),
                        notes: policy.notes,
                    }),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data?.detail || data?.error || `${runtime.kind} 模板应用失败`);
            }));
            toast({ title:"components.runtime.RuntimeGovernanceWorkbench.kbd7964c6", description: PRESET_LABELS[preset].title });
            await loadAll(query);
        } catch (error) {
            toast({ variant: "destructive", title:"components.runtime.RuntimeGovernanceWorkbench.k5c114cf0", description: error instanceof Error ? error.message : "未知错误" });
        } finally {
            setBusyKey(null);
        }
    };

    return (
        <div className="space-y-6">
            <div className="flex flex-wrap items-center justify-between gap-4">
                {embedded ? (
                    <div className="text-sm text-muted-foreground">
                        这里直接读取 Engine 的 runtime 能力、策略和运行现场，不再依赖静态说明卡片。
                    </div>
                ) : (
                    <div>
                        <h1 className="text-3xl font-bold tracking-tight">运行时治理工作台</h1>
                        <p className="mt-1 text-muted-foreground">这里查看 runtime 策略、待处理确认和真实运行现场。</p>
                    </div>
                )}
                <div className="flex flex-wrap gap-2">
                    {activeRuntimeKind ? (
                        <Button variant="outline" onClick={() => setActiveRuntimeKind(null)}>
                            清除 runtime 筛选
                        </Button>
                    ) : null}
                    <Button variant="outline" onClick={() => void handleRefresh()} disabled={loading}>
                        <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
                        刷新当前状态
                    </Button>
                </div>
            </div>

            <div className="grid gap-4 md:grid-cols-4">
                <Card className="border-border/60"><CardHeader className="pb-2"><CardDescription>注册 Runtime</CardDescription><CardTitle className="text-3xl">{snapshot.count || 0}</CardTitle></CardHeader><CardContent className="text-xs text-muted-foreground">Capability Registry 当前纳管的 runtime 总数。</CardContent></Card>
                <Card className="border-border/60"><CardHeader className="pb-2"><CardDescription>已启用</CardDescription><CardTitle className="text-3xl">{summary.enabled}</CardTitle></CardHeader><CardContent className="text-xs text-muted-foreground">这些 runtime 当前参与执行编排。</CardContent></Card>
                <Card className="border-border/60"><CardHeader className="pb-2"><CardDescription>待审批</CardDescription><CardTitle className="text-3xl">{approvals.length}</CardTitle></CardHeader><CardContent className="text-xs text-muted-foreground">这里会显示待处理确认和当前状态。</CardContent></Card>
                <Card className="border-border/60"><CardHeader className="pb-2"><CardDescription>可恢复 Workflow</CardDescription><CardTitle className="text-3xl">{recoverableSessions.length}</CardTitle></CardHeader><CardContent className="text-xs text-muted-foreground">这里统计的是当前仍可 resume/retry 的会话工作流。</CardContent></Card>
            </div>

            <Card className="border-border/60">
                <CardHeader>
                    <CardTitle className="text-lg">{t("components.runtime.RuntimeGovernanceWorkbench.memoryObservabilityTitle")}</CardTitle>
                    <CardDescription>{t("components.runtime.RuntimeGovernanceWorkbench.memoryObservabilityDescription")}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="grid gap-3 md:grid-cols-4">
                        {[
                            [t("components.runtime.RuntimeGovernanceWorkbench.memoryMetricCompleted"), memoryExtractionSummary.completed],
                            [t("components.runtime.RuntimeGovernanceWorkbench.memoryMetricSkipped"), memoryExtractionSummary.skipped],
                            [t("components.runtime.RuntimeGovernanceWorkbench.memoryMetricPersisted"), memoryExtractionSummary.persisted],
                            [t("components.runtime.RuntimeGovernanceWorkbench.memoryMetricBackfilled"), memoryMaintenanceSummary.summaryBackfilled],
                        ].map(([label, value]) => (
                            <div key={String(label)} className="rounded-xl border bg-muted/20 p-3">
                                <div className="text-xs text-muted-foreground">{label}</div>
                                <div className="mt-2 text-2xl font-semibold">{compactNumber(value)}</div>
                            </div>
                        ))}
                    </div>
                    <div className="max-h-72 space-y-2 overflow-y-auto pr-1">
                        {memoryAuditLogs.length ? memoryAuditLogs.slice(0, 12).map((log) => {
                            const details = parseMemoryAuditDetails(log.details);
                            return (
                                <div key={log.id || `${log.timestamp}-${log.action}`} className="rounded-xl border bg-background p-3">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <Badge variant="outline">{String(details.action || log.action || "memory")}</Badge>
                                        <Badge variant={String(log.status || "").toUpperCase() === "SUCCESS" ? "default" : "secondary"}>{log.status || "INFO"}</Badge>
                                        {details.callsLlm === true ? <Badge>{t("components.runtime.RuntimeGovernanceWorkbench.memoryCallsLlm")}</Badge> : <Badge variant="secondary">{t("components.runtime.RuntimeGovernanceWorkbench.memoryNoLlm")}</Badge>}
                                        <span className="text-xs text-muted-foreground">{formatWhen(log.timestamp)}</span>
                                    </div>
                                    <div className="mt-2 grid gap-x-4 gap-y-1 text-xs text-muted-foreground md:grid-cols-4">
                                        <span>{t("components.runtime.RuntimeGovernanceWorkbench.memoryTrigger")}: {String(details.trigger || "—")}</span>
                                        <span>{t("components.runtime.RuntimeGovernanceWorkbench.memoryInputChars")}: {String(details.inputCharEstimate ?? "—")}</span>
                                        <span>{t("components.runtime.RuntimeGovernanceWorkbench.memoryWritten")}: {String(details.persistedKnowledgeCount ?? details.chunkCount ?? details.summaryBackfilledCount ?? "—")}</span>
                                        <span>{t("components.runtime.RuntimeGovernanceWorkbench.memorySkipReason")}: {String(details.skipReason || details.rejectReason || "—")}</span>
                                    </div>
                                </div>
                            );
                        }) : (
                            <div className="rounded-xl border border-dashed bg-muted/20 p-6 text-sm text-muted-foreground">
                                {t("components.runtime.RuntimeGovernanceWorkbench.memoryNoAuditLogs")}
                            </div>
                        )}
                    </div>
                </CardContent>
            </Card>

            <div className="grid gap-6 xl:grid-cols-[1.15fr_0.85fr]">
                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-lg"><Route className="h-5 w-5 text-primary" />Runtime 路由试投</CardTitle>
                        <CardDescription>用真实问题试试当前 runtime 推荐顺序，确认系统会优先选择哪条执行路径。</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="flex flex-col gap-3 md:flex-row">
                            <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="components.runtime.RuntimeGovernanceWorkbench.kab2a9ac2" />
                            <Button onClick={() => void handleSearch()} disabled={busyKey === "query"}>重新推荐</Button>
                        </div>
                        <div className="space-y-3">
                            {recommendations.length === 0 ? (
                                <div className="rounded-xl border border-dashed p-4 text-sm text-muted-foreground">当前没有推荐结果，可以换一个更具体的问题试试。</div>
                            ) : recommendations.map((item) => (
                                <div key={item.kind} className="rounded-2xl border border-border/60 p-4">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <div className="text-sm font-medium">{item.displayName}</div>
                                        <Badge variant="outline">{item.kind}</Badge>
                                        <Badge>{item.score.toFixed(1)}</Badge>
                                        <Button variant="outline" size="sm" onClick={() => setActiveRuntimeKind(item.kind)}>
                                            只看这个 runtime
                                        </Button>
                                    </div>
                                    <div className="mt-2 text-xs text-muted-foreground">命中关键词：{item.matchedKeywords?.length ? item.matchedKeywords.join("、") : "无"} · 命中信号：{item.matchedSignals?.length ? item.matchedSignals.join("、") : "无"}</div>
                                </div>
                            ))}
                        </div>
                    </CardContent>
                </Card>

                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-lg"><SlidersHorizontal className="h-5 w-5 text-primary" />规则模板</CardTitle>
                        <CardDescription>这里可以套用常见规则组合。</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        {(Object.keys(PRESET_LABELS) as RuntimePresetId[]).map((preset) => (
                            <div key={preset} className="rounded-2xl border border-border/60 p-4">
                                <div className="text-sm font-medium">{PRESET_LABELS[preset].title}</div>
                                <div className="mt-1 text-xs text-muted-foreground">{PRESET_LABELS[preset].description}</div>
                                <Button className="mt-3" variant="outline" onClick={() => void applyPreset(preset)} disabled={busyKey === `preset:${preset}`}>
                                    {busyKey === `preset:${preset}` ? "应用中..." : "应用模板"}
                                </Button>
                            </div>
                        ))}
                    </CardContent>
                </Card>
            </div>

            <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle className="text-lg">运行时能力与策略</CardTitle>
                        <CardDescription>这里按核心 / 非核心 runtime 分组治理，不再把不同执行面混成一张静态说明表。</CardDescription>
                </CardHeader>
                <CardContent>
                    <ScrollArea className="h-[640px] pr-4">
                        <div className="space-y-6">
                            {groupedRuntimes.map((section) => (
                                <div key={section.key} className="space-y-4">
                                    <div className="rounded-2xl border border-border/60 bg-muted/30 px-4 py-3">
                                        <div className="text-sm font-semibold text-slate-900">{section.title}</div>
                                        <div className="mt-1 text-xs leading-5 text-muted-foreground">{section.description}</div>
                                    </div>
                                    <div className="space-y-4">
                                        {section.runtimes.map((runtime) => {
                                            const policy = draftPolicies[runtime.kind] || normalizePolicy(runtime.policy);
                                            const managed = managedToolSummary(runtime);
                                            const live = observability.find((item) => item.kind === runtime.kind);
                                            const highlighted = activeRuntimeKind === runtime.kind;
                                            const isCoreRuntime = CORE_RUNTIME_KINDS.includes(runtime.kind as (typeof CORE_RUNTIME_KINDS)[number]);
                                            const isLockedRuntime = isLockedRuntimeKind(runtime.kind);
                                            const controlHref = getRuntimeControlHref(runtime.kind);
                                            return (
                                                <div key={runtime.kind} className={`rounded-2xl border p-4 ${highlighted ? "border-primary/60 bg-primary/5" : "border-border/60"}`}>
                                                    <div className="flex flex-wrap items-start justify-between gap-4">
                                                        <div>
                                                            <div className="flex flex-wrap items-center gap-2">
                                                                <div className="text-base font-semibold">{runtime.displayName}</div>
                                                                <Badge variant="outline">{runtime.kind}</Badge>
                                                                <Badge variant={isCoreRuntime ? "default" : "secondary"}>{isCoreRuntime ? "核心 runtime" : "非核心 runtime"}</Badge>
                                                                <Badge variant="secondary">{runtime.visibility || "internal"}</Badge>
                                                                {policy.expose_direct_tools ? <Badge>direct tools</Badge> : <Badge variant="secondary">runtime-only</Badge>}
                                                                <Button variant="outline" size="sm" onClick={() => setActiveRuntimeKind(highlighted ? null : runtime.kind)}>
                                                                    {highlighted ? "取消筛选" : "查看联动"}
                                                                </Button>
                                                                {controlHref ? (
                                                                    <Button variant="outline" size="sm" asChild>
                                                                        <Link href={controlHref}>打开控制页</Link>
                                                                    </Button>
                                                                ) : null}
                                                            </div>
                                                            <div className="mt-2 text-sm text-muted-foreground">{runtime.summary || "暂无摘要。"}</div>
                                                            <div className="mt-2 flex flex-wrap gap-2">
                                                                {runtime.capabilities?.slice(0, 6).map((item) => (
                                                                    <Badge key={`${runtime.kind}:${item.key}`} variant="outline">{item.label} · {riskLabel(item.risk_level)}</Badge>
                                                                ))}
                                                            </div>
                                                            <div className="mt-3 flex flex-wrap gap-2 text-xs">
                                                                <Badge variant="secondary">近 40 次 run：{live?.totalRuns ?? 0}</Badge>
                                                                <Badge variant="secondary">活跃：{live?.activeRuns ?? 0}</Badge>
                                                                <Badge variant="secondary">失败：{live?.failedRuns ?? 0}</Badge>
                                                                <Badge variant="secondary">待审批：{live?.pendingApprovals ?? 0}</Badge>
                                                                <Badge variant="secondary">可恢复：{live?.recoverableSessions ?? 0}</Badge>
                                                            </div>
                                                        </div>
                                                        <div className="flex gap-2">
                                                            <Button variant="outline" onClick={() => void resetPolicy(runtime.kind)} disabled={busyKey === `reset:${runtime.kind}` || busyKey === `save:${runtime.kind}`}>{busyKey === `reset:${runtime.kind}` ? "重置中..." : "恢复默认"}</Button>
                                                            <Button onClick={() => void savePolicy(runtime.kind)} disabled={busyKey === `save:${runtime.kind}` || busyKey === `reset:${runtime.kind}`}><Save className="mr-2 h-4 w-4" />{busyKey === `save:${runtime.kind}` ? "保存中..." : "保存策略"}</Button>
                                                        </div>
                                                    </div>
                                                    <div className="mt-4 grid gap-4 md:grid-cols-3">
                                                        <div className="rounded-xl border border-border/50 p-3"><div className="flex items-center justify-between"><Label htmlFor={`${runtime.kind}-enabled`}>启用</Label><Switch id={`${runtime.kind}-enabled`} checked={policy.enabled} onCheckedChange={(checked) => patchPolicy(runtime.kind, { enabled: checked })} disabled={isLockedRuntime} /></div><p className="mt-2 text-xs text-muted-foreground">{isLockedRuntime ? "这个运行时属于最小安装锁定核心，不允许在治理页里真正关闭。" : isCoreRuntime ? "它属于最小安装核心家族，但这里仍允许做策略治理。" : "关闭后，主理人不会再把它当作可用的执行能力。"}</p></div>
                                                        <div className="rounded-xl border border-border/50 p-3"><div className="flex items-center justify-between"><Label htmlFor={`${runtime.kind}-auto`}>自动路由</Label><Switch id={`${runtime.kind}-auto`} checked={policy.auto_route} onCheckedChange={(checked) => patchPolicy(runtime.kind, { auto_route: checked })} /></div><p className="mt-2 text-xs text-muted-foreground">关闭后，它仍可手动调用，但不参与自动推荐。</p></div>
                                                        <div className="rounded-xl border border-border/50 p-3"><div className="flex items-center justify-between"><Label htmlFor={`${runtime.kind}-direct`}>直连工具</Label><Switch id={`${runtime.kind}-direct`} checked={policy.expose_direct_tools} onCheckedChange={(checked) => patchPolicy(runtime.kind, { expose_direct_tools: checked })} /></div><p className="mt-2 text-xs text-muted-foreground">关闭后，这组运行能力管理的底层工具不会再直接暴露给主理人。</p></div>
                                                    </div>
                                                    <div className="mt-4 grid gap-4 xl:grid-cols-[0.38fr_0.62fr]">
                                                        <div className="rounded-xl border border-border/50 p-3">
                                                            <Label htmlFor={`${runtime.kind}-priority`}>Priority</Label>
                                                            <Input id={`${runtime.kind}-priority`} className="mt-2" type="number" value={policy.priority} onChange={(event) => patchPolicy(runtime.kind, { priority: Number(event.target.value || 100) })} />
                                                            <div className="mt-2 text-xs text-muted-foreground">managed exact：{managed.exact.length ? managed.exact.join("、") : "无"}</div>
                                                            <div className="mt-1 text-xs text-muted-foreground">managed prefixes：{managed.prefixes.length ? managed.prefixes.join("、") : "无"}</div>
                                                        </div>
                                                        <div>
                                                            <Label htmlFor={`${runtime.kind}-notes`}>备注</Label>
                                                            <Textarea id={`${runtime.kind}-notes`} className="mt-2 min-h-[110px]" value={policy.notes} onChange={(event) => patchPolicy(runtime.kind, { notes: event.target.value })} placeholder="components.runtime.RuntimeGovernanceWorkbench.kaba1447c" />
                                                            {runtime.promptHints?.length ? <div className="mt-2 rounded-lg bg-muted/40 p-3 text-xs text-muted-foreground"><Settings2 className="mr-2 inline h-3 w-3" />路由提示：{runtime.promptHints.join("；")}</div> : null}
                                                        </div>
                                                    </div>
                                                </div>
                                            );
                                        })}
                                    </div>
                                </div>
                            ))}
                        </div>
                    </ScrollArea>
                </CardContent>
            </Card>

            <div className="grid gap-6 xl:grid-cols-2">
                <Card className="border-border/60 xl:col-span-2">
                    <CardHeader>
                        <CardTitle className="text-lg">运行现场与恢复面板</CardTitle>
                        <CardDescription>
                            {activeRuntimeKind
                                ? `当前按 ${runtimeNameMap.get(activeRuntimeKind) || activeRuntimeKind} 过滤，方便确认规则是否生效。`
                                : "这里会把规则和真实运行状态放在一起查看。"}
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="grid gap-6 xl:grid-cols-3">
                        <div className="space-y-3">
                            <div className="text-sm font-medium">最近失败 Run</div>
                            {filteredFailedRuns.length === 0 ? (
                                <div className="rounded-xl border border-dashed p-4 text-sm text-muted-foreground">当前没有失败 run。</div>
                            ) : (
                                filteredFailedRuns.slice(0, 6).map((run) => (
                                    <div key={run.id} className="rounded-2xl border border-border/60 p-4">
                                        <div className="flex flex-wrap items-center gap-2">
                                            <Badge>{t(RUN_LABELS[run.status || "failed"] || run.status || "failed")}</Badge>
                                            <Badge variant="outline">{runtimeNameMap.get(inferRunRuntime(run)) || inferRunRuntime(run)}</Badge>
                                            {run.trigger_source ? <Badge variant="secondary">{run.trigger_source}</Badge> : null}
                                        </div>
                                        <div className="mt-2 text-xs text-muted-foreground">Run: {run.id}</div>
                                        {run.session_id ? <div className="mt-1 text-xs text-muted-foreground">Session: {run.session_id}</div> : null}
                                        <div className="mt-1 text-xs text-muted-foreground">时间：{formatWhen(run.started_at || run.created_at)}</div>
                                        {run.session_id ? (
                                            <Button className="mt-3" variant="outline" size="sm" onClick={() => void inspectSession(run.session_id!)}>
                                                <Eye className="mr-2 h-4 w-4" />
                                                查看现场
                                            </Button>
                                        ) : null}
                                    </div>
                                ))
                            )}
                        </div>

                        <div className="space-y-3">
                            <div className="text-sm font-medium">待审批项</div>
                            {filteredApprovals.length === 0 ? (
                                <div className="rounded-xl border border-dashed p-4 text-sm text-muted-foreground">当前没有待审批项。</div>
                            ) : (
                                filteredApprovals.slice(0, 6).map((approval) => {
                                    const runtimeKind =
                                        approval.run_id && runMap.has(approval.run_id)
                                            ? inferRunRuntime(runMap.get(approval.run_id)!)
                                            : approval.session_id && sessionMap.has(approval.session_id)
                                              ? sessionMap.get(approval.session_id)?.workflow?.ownerRuntime || "chat"
                                              : (approval.approval_kind || "").startsWith("rpa")
                                                ? "rpa"
                                                : "chat";
                                    return (
                                        <div key={approval.id} className="rounded-2xl border border-border/60 p-4">
                                            <div className="flex flex-wrap items-center gap-2">
                                                <Badge>{approval.approval_kind || "approval"}</Badge>
                                                <Badge variant="outline">{runtimeNameMap.get(runtimeKind) || runtimeKind}</Badge>
                                            </div>
                                            <div className="mt-2 text-xs text-muted-foreground">{approval.request?.question || approval.request?.prompt || "审批未附带说明。"}</div>
                                            {approval.session_id ? (
                                                <Button className="mt-3" variant="outline" size="sm" onClick={() => void inspectSession(approval.session_id!)}>
                                                    <Eye className="mr-2 h-4 w-4" />
                                                    查看现场
                                                </Button>
                                            ) : null}
                                        </div>
                                    );
                                })
                            )}
                        </div>

                        <div className="space-y-3">
                            <div className="text-sm font-medium">可恢复 Workflow</div>
                            {filteredRecoverableSessions.length === 0 ? (
                                <div className="rounded-xl border border-dashed p-4 text-sm text-muted-foreground">当前没有可恢复 workflow。</div>
                            ) : (
                                filteredRecoverableSessions.slice(0, 6).map((session) => (
                                    <div key={session.id} className="rounded-2xl border border-border/60 p-4">
                                        <div className="flex flex-wrap items-center gap-2">
                                            <Badge variant="outline">{runtimeNameMap.get(session.workflow?.ownerRuntime || "chat") || session.workflow?.ownerRuntime || "chat"}</Badge>
                                            <Badge>{session.workflow?.status || session.recoverableView?.workflowStatus || "recoverable"}</Badge>
                                        </div>
                                        <div className="mt-2 text-sm font-medium">{session.title || session.id}</div>
                                        <div className="mt-1 text-xs text-muted-foreground">{session.summary?.previewExcerpt || "暂无摘要，建议查看现场详情。"}</div>
                                        <Button className="mt-3" variant="outline" size="sm" onClick={() => void inspectSession(session.id)}>
                                            <Eye className="mr-2 h-4 w-4" />
                                            查看现场
                                        </Button>
                                    </div>
                                ))
                            )}
                        </div>
                    </CardContent>
                </Card>

                {selectedSessionId ? (
                    <Card className="border-border/60 xl:col-span-2">
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2 text-lg"><Eye className="h-5 w-5 text-primary" />现场详情</CardTitle>
                            <CardDescription>{selectedSessionId}{selectedSessionDetail?.source ? ` · 来源 ${selectedSessionDetail.source}` : ""}</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            {detailLoading ? (
                                <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">现场详情加载中...</div>
                            ) : selectedSessionDetail ? (
                                <>
                                    <div className="grid gap-4 md:grid-cols-4">
                                        <div className="rounded-xl border border-border/50 p-3"><div className="text-xs text-muted-foreground">workflow</div><div className="mt-2 text-sm font-medium">{selectedSessionDetail.workflow?.status || selectedSessionDetail.recoverable?.workflowStatus || "unknown"}</div></div>
                                        <div className="rounded-xl border border-border/50 p-3"><div className="text-xs text-muted-foreground">owner runtime</div><div className="mt-2 text-sm font-medium">{runtimeNameMap.get(selectedSessionDetail.workflow?.ownerRuntime || "chat") || selectedSessionDetail.workflow?.ownerRuntime || "chat"}</div></div>
                                        <div className="rounded-xl border border-border/50 p-3"><div className="text-xs text-muted-foreground">recoverable</div><div className="mt-2 text-sm font-medium">{selectedSessionDetail.recoverable?.recoverable ? "是" : "否"}</div></div>
                                        <div className="rounded-xl border border-border/50 p-3"><div className="text-xs text-muted-foreground">approvals</div><div className="mt-2 text-sm font-medium">{selectedSessionDetail.approvals?.length || 0}</div></div>
                                    </div>
                                    {plannerInspector ? (
                                        <div className="rounded-xl border border-border/50 p-4">
                                            <div className="flex flex-wrap items-center gap-2">
                                                <div className="text-sm font-medium">Planner Inspector</div>
                                                {plannerInspector.executionStrategy ? <Badge variant="secondary">{plannerInspector.executionStrategy}</Badge> : null}
                                                {plannerInspector.planId ? <Badge variant="outline">{plannerInspector.planId}</Badge> : null}
                                            </div>
                                            <div className="mt-2 text-xs text-muted-foreground">
                                                {plannerInspector.stepTitle || "planner_lane"}{plannerInspector.traceRef.runId ? ` · run ${String(plannerInspector.traceRef.runId)}` : ""}{plannerInspector.traceRef.planId ? ` · trace ${String(plannerInspector.traceRef.planId)}` : ""}
                                            </div>
                                            <div className="mt-4 grid gap-3 md:grid-cols-4">
                                                <div className="rounded-lg border border-border/50 p-3">
                                                    <div className="text-xs text-muted-foreground">plan summary</div>
                                                    <div className="mt-2 text-sm font-medium">{plannerInspector.planSummary || "n/a"}</div>
                                                </div>
                                                <div className="rounded-lg border border-border/50 p-3">
                                                    <div className="text-xs text-muted-foreground">task briefs</div>
                                                    <div className="mt-2 text-sm font-medium">{plannerInspector.taskBriefs.length}</div>
                                                </div>
                                                <div className="rounded-lg border border-border/50 p-3">
                                                    <div className="text-xs text-muted-foreground">selected delegations</div>
                                                    <div className="mt-2 text-sm font-medium">{plannerInspector.selectedDelegations.length}</div>
                                                </div>
                                                <div className="rounded-lg border border-border/50 p-3">
                                                    <div className="text-xs text-muted-foreground">risk flags</div>
                                                    <div className="mt-2 text-sm font-medium">{plannerInspector.riskFlags.length || 0}</div>
                                                </div>
                                            </div>
                                            <div className="mt-4 grid gap-4 xl:grid-cols-[0.58fr_0.42fr]">
                                                <div className="rounded-lg border border-border/50 p-3">
                                                    <div className="text-sm font-medium">Broker-ready task briefs</div>
                                                    <div className="mt-3 space-y-3">
                                                        {plannerInspector.taskBriefs.length > 0 ? plannerInspector.taskBriefs.map((taskBrief, index) => (
                                                            <div key={asString(taskBrief.taskBriefId) || `task-brief:${index}`} className="rounded-lg bg-muted/40 p-3">
                                                                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                                                                    <span>{asString(taskBrief.taskBriefId) || `task-${index + 1}`}</span>
                                                                    {asString(taskBrief.executionLaneHint) ? <Badge variant="outline">{asString(taskBrief.executionLaneHint)}</Badge> : null}
                                                                    {asString(taskBrief.parallelGroup) ? <Badge variant="secondary">{asString(taskBrief.parallelGroup)}</Badge> : null}
                                                                </div>
                                                                <div className="mt-2 text-sm font-medium">{asString(taskBrief.goal) || "n/a"}</div>
                                                                <div className="mt-2 space-y-1 text-xs text-muted-foreground">
                                                                    <div>writeSet：{asStringArray(taskBrief.writeSet).join(" / ") || "n/a"}</div>
                                                                    <div>behaviorScope：{asStringArray(taskBrief.behaviorScope).join(" / ") || "n/a"}</div>
                                                                    <div>requiredCapabilities：{asStringArray(taskBrief.requiredCapabilities).join(" / ") || "n/a"}</div>
                                                                    <div>acceptance：{asString(taskBrief.acceptanceContract) || "n/a"}</div>
                                                                </div>
                                                            </div>
                                                        )) : (
                                                            <div className="rounded-lg border border-dashed p-3 text-xs text-muted-foreground">当前 planner 没有产出 task briefs。</div>
                                                        )}
                                                    </div>
                                                </div>
                                                <div className="space-y-4">
                                                    <div className="rounded-lg border border-border/50 p-3">
                                                        <div className="text-sm font-medium">Selected delegations</div>
                                                        <div className="mt-3 space-y-2">
                                                            {plannerInspector.selectedDelegations.length > 0 ? plannerInspector.selectedDelegations.map((item, index) => (
                                                                <div key={asString(item.delegationId) || `delegation:${index}`} className="rounded-lg bg-muted/40 p-3 text-xs text-muted-foreground">
                                                                    <div className="font-medium text-foreground">{asString(item.targetLabel) || asString(item.targetId) || "delegation target"}</div>
                                                                    <div className="mt-1">lane：{asString(item.lane) || "n/a"} · status：{asString(item.status) || "n/a"}</div>
                                                                    <div className="mt-1">taskBriefId：{asString(item.taskBriefId) || "n/a"}</div>
                                                                </div>
                                                            )) : (
                                                                <div className="rounded-lg border border-dashed p-3 text-xs text-muted-foreground">当前 planner 尚未选出 delegation targets。</div>
                                                            )}
                                                        </div>
                                                    </div>
                                                    <div className="rounded-lg border border-border/50 p-3">
                                                        <div className="text-sm font-medium">Acceptance & risks</div>
                                                        <div className="mt-3 space-y-2 text-xs text-muted-foreground">
                                                            <div>globalAcceptance：{plannerInspector.globalAcceptanceContract || "n/a"}</div>
                                                            <div>riskFlags：{plannerInspector.riskFlags.join(" / ") || "n/a"}</div>
                                                            <div>dependencies：{plannerInspector.dependencies.length > 0 ? plannerInspector.dependencies.length : "0"}</div>
                                                        </div>
                                                    </div>
                                                </div>
                                            </div>
                                        </div>
                                    ) : null}
                                    <div className="grid gap-4 xl:grid-cols-[0.42fr_0.58fr]">
                                        <div className="rounded-xl border border-border/50 p-4">
                                            <div className="text-sm font-medium">控制与恢复状态</div>
                                            <div className="mt-3 space-y-2 text-xs text-muted-foreground">
                                                <div>canResume：{selectedSessionDetail.controls?.canResume ? "true" : "false"}</div>
                                                <div>canRetry：{selectedSessionDetail.controls?.canRetry ? "true" : "false"}</div>
                                                <div>canInterrupt：{selectedSessionDetail.controls?.canInterrupt ? "true" : "false"}</div>
                                                <div>currentStep：{selectedSessionDetail.workflow?.currentStepTitle || "n/a"} · {selectedSessionDetail.workflow?.currentStepStatus || "n/a"}</div>
                                            </div>
                                        </div>
                                        <div className="rounded-xl border border-border/50 p-4">
                                            <div className="text-sm font-medium">最近消息摘录</div>
                                            <div className="mt-3 space-y-3">
                                                {(selectedSessionDetail.messages || []).slice(-3).map((message, index) => (
                                                    <div key={message.id || `${message.role || "msg"}:${index}`} className="rounded-lg bg-muted/40 p-3">
                                                        <div className="text-xs text-muted-foreground">{message.role || "message"} · {formatWhen(message.createdAt)}</div>
                                                        <div className="mt-2 whitespace-pre-wrap text-sm">{message.content || "(空消息)"}</div>
                                                    </div>
                                                ))}
                                            </div>
                                        </div>
                                    </div>
                                </>
                            ) : (
                                <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">还没有载入详情。</div>
                            )}
                        </CardContent>
                    </Card>
                ) : null}

            </div>
        </div>
    );
}
