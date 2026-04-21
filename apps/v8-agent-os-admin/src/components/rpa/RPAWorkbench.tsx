"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle, CheckCircle2, FileCode2, Play, RefreshCw, ShieldAlert, Wand2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import { useT } from "@/components/providers/LocaleProvider";
import { useToast } from "@/components/ui/use-toast";
import { RUN_LABELS, formatWhen } from "@/components/runtime/use-runtime-ops";

type AvailabilityPayload = {
    robotFramework?: boolean;
    rpaFramework?: boolean;
    libraries?: Record<string, boolean>;
    robotFrameworkDetail?: AvailabilityProbe;
    rpaFrameworkDetail?: AvailabilityProbe;
    libraryDetails?: Record<string, AvailabilityProbe>;
};

type AvailabilityProbe = {
    detected?: boolean;
    importable?: boolean;
    origin?: string | null;
    error?: string | null;
};

type AssessmentSignals = {
    acceptedRatio?: number;
    excludedRatio?: number;
    nativeSemanticRatio?: number;
    recoveryHeavyRatio?: number;
    profileAugmentedSteps?: number;
    profileAugmentedRatio?: number;
    calibratedSteps?: number;
    historicalNativeSuccessRate?: number;
    historicalScriptRuns?: number;
    historicalScriptCompletedRate?: number;
    historicalScriptFallbackHeavyRate?: number;
    historicalScriptNativeSuccessRate?: number;
    historicalScriptProfileAugmentedRatio?: number;
    historicalScriptReviewRequiredRate?: number;
    historicalScriptCompileBlockedRate?: number;
    historicalScriptCalibrationSource?: string;
};

type AssessmentTrustModel = {
    effectiveStepReviewThreshold?: number;
    effectiveStepExcludeThreshold?: number;
    effectiveScriptTrustedThreshold?: number;
    effectiveScriptReviewThreshold?: number;
    effectiveScriptFallbackHeavyThreshold?: number;
    effectiveBlockedAcceptedRatioMin?: number;
    effectiveBlockedExcludedRatioMax?: number;
    trustModelVersion?: string;
};

type DraftPayload = {
    id: string;
    name?: string;
    appId?: string;
    goal?: string;
    source?: {
        type?: string;
        traceRunId?: string;
        traceRunIds?: string[];
    };
    steps?: Array<{ stepId?: string; use?: string; approval?: { mode?: string }; assessment?: { score?: number; status?: string } }>;
    variables?: Array<{ name?: string; required?: boolean; type?: string }>;
    assessment?: {
        score?: number;
        status?: string;
        band?: string;
        reasons?: string[];
        acceptedSteps?: number;
        reviewRequiredSteps?: number;
        excludedSteps?: number;
        signals?: AssessmentSignals;
        trustModel?: AssessmentTrustModel;
    };
    metadata?: {
        compileIssues?: string[];
        templateGovernance?: {
            stage?: string;
            recommendedDecision?: string;
            confidence?: number;
            rolloutMode?: string;
            reasons?: string[];
        };
        templateGovernanceStage?: string;
        templateRecommendedDecision?: string;
        templateTrustConfidence?: number;
        templateRolloutMode?: string;
        templateStatus?: string;
    };
    robot?: {
        tags?: string[];
        libraries?: Array<{ name?: string; required?: boolean }>;
    };
};

type ScriptPayload = {
    name: string;
    path: string;
    updatedAt?: string;
    size?: number;
};

type TemplateReviewSummary = {
    total?: number;
    approveCount?: number;
    freezeCount?: number;
    rollbackCount?: number;
    reviewRequiredCount?: number;
    rejectedCount?: number;
    lastDecision?: string | null;
    lastDecisionLabel?: string | null;
    lastReviewer?: string | null;
    lastReviewedAt?: string | null;
    approvalRequired?: boolean;
};

type TemplateViewPayload = {
    statusLabel?: string;
    stageLabel?: string;
    recommendedDecisionLabel?: string;
    rolloutModeLabel?: string;
    executionPath?: string;
    executionPathLabel?: string;
    confidenceLabel?: string;
    riskFlags?: string[];
    riskFlagLabels?: string[];
    reviewSummary?: TemplateReviewSummary;
    signalSummary?: {
        sourceTraceCount?: number;
        localRepairCount?: number;
        compileIssueCount?: number;
        historicalRuns?: number;
        historicalCompletedRate?: number | null;
        historicalFallbackHeavyRate?: number | null;
        historicalReviewRequiredRate?: number | null;
    };
};

type TemplatePayload = {
    id: string;
    name?: string;
    appId?: string;
    status?: string;
    updatedAt?: string;
    source?: {
        draftId?: string;
        templateStage?: string;
        templateStatus?: string;
    };
    metadata?: {
        revision?: number;
        reviewHistory?: Array<{ decision?: string; reviewer?: string; at?: string; notes?: string | null }>;
    };
    governance?: {
        stage?: string;
        confidence?: number;
        recommendedDecision?: string;
        rolloutMode?: string;
        reasons?: string[];
    };
    view?: TemplateViewPayload;
};

type TemplateHistoryRecord = {
    path?: string;
    revision?: number;
    actor?: string;
    reason?: string | null;
    at?: string;
    view?: TemplateViewPayload;
    historyView?: {
        revision?: number;
        statusLabel?: string;
        reason?: string | null;
        actor?: string | null;
        at?: string | null;
    };
    template?: TemplatePayload;
};

type TemplateSummaryPayload = {
    total?: number;
    byStatus?: Record<string, number>;
    byStage?: Record<string, number>;
    templatePreferredCount?: number;
    computerUseFirstCount?: number;
    atRiskCount?: number;
    reviewRequiredCount?: number;
    maxConfidence?: number;
};

type ApprovalRecord = {
    id: string;
    run_id?: string;
    session_id?: string;
    approval_kind?: string;
    created_at?: string;
    request?: {
        question?: string;
        prompt?: string;
        rpa?: {
            subject?: string;
            scriptId?: string;
            robotFile?: string;
            requiredApprovals?: Array<{ stepId?: string; use?: string; mode?: string; reason?: string; confidence?: number }>;
        };
    };
};

type RunRecord = {
    id: string;
    session_id?: string;
    status?: string;
    run_type?: string;
    trigger_source?: string;
    created_at?: string;
    metadata?: Record<string, unknown>;
};

function formatConfidence(score?: number | null) {
    if (typeof score !== "number" || Number.isNaN(score)) {
        return "n/a";
    }
    return `${Math.round(score * 100)}%`;
}

function formatRatio(value?: number | null) {
    if (typeof value !== "number" || Number.isNaN(value)) {
        return "n/a";
    }
    return `${Math.round(value * 100)}%`;
}

function formatCalibrationSource(source?: string | null) {
    if (!source) {
        return "n/a";
    }
    if (source === "fingerprint") {
        return "fingerprint 复用";
    }
    if (source === "script") {
        return "script 直连";
    }
    return source;
}

function readRunAssessment(metadata?: Record<string, unknown>) {
    const assessment = metadata?.assessment;
    if (assessment && typeof assessment === "object" && !Array.isArray(assessment)) {
        return assessment as {
            score?: number;
            status?: string;
            band?: string;
            acceptedSteps?: number;
            reviewRequiredSteps?: number;
            excludedSteps?: number;
            signals?: AssessmentSignals;
            trustModel?: AssessmentTrustModel;
        };
    }
    return null;
}

function readRunExecutionState(metadata?: Record<string, unknown>) {
    const state = metadata?.executionState;
    return typeof state === "string" && state.trim() ? state : null;
}

function readRunFallback(metadata?: Record<string, unknown>) {
    const fallback = metadata?.fallback;
    if (fallback && typeof fallback === "object" && !Array.isArray(fallback)) {
        return fallback as { type?: string; sourceScriptId?: string; sourceTraceRunId?: string };
    }
    return null;
}

function readRunTemplatePolicy(metadata?: Record<string, unknown>) {
    const policy = metadata?.templateExecutionPolicy;
    if (policy && typeof policy === "object" && !Array.isArray(policy)) {
        return policy as {
            stage?: string;
            status?: string;
            rolloutMode?: string;
            executionPath?: string;
            recommendedDecision?: string;
            confidence?: number;
        };
    }
    return null;
}

function prettyJson(value: unknown) {
    return JSON.stringify(value, null, 2);
}

function probeState(detail?: AvailabilityProbe) {
    if (!detail?.detected) {
        return { label:"components.rpa.RPAWorkbench.kc623317d", variant: "secondary" as const };
    }
    if (detail.importable) {
        return { label:"components.rpa.RPAWorkbench.k6965af3b", variant: "default" as const };
    }
    return { label:"components.rpa.RPAWorkbench.k71ca3eb3", variant: "destructive" as const };
}

function parseJsonObject(value: string) {
    const trimmed = value.trim();
    if (!trimmed) return {};
    const parsed = JSON.parse(trimmed);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("变量必须是 JSON 对象。");
    }
    return parsed;
}

function parseRunIdsInput(value: string) {
    return Array.from(
        new Set(
            value
                .split(/[\s,，]+/)
                .map((item) => item.trim())
                .filter(Boolean)
        )
    );
}

function readRunScriptName(metadata?: Record<string, unknown>) {
    const script = metadata?.script;
    if (script && typeof script === "object" && !Array.isArray(script)) {
        const name = (script as { name?: unknown }).name;
        if (typeof name === "string" && name.trim()) {
            return name;
        }
    }
    return null;
}

function readRunRobotFile(metadata?: Record<string, unknown>) {
    const robotFile = metadata?.robotFile;
    return typeof robotFile === "string" && robotFile.trim() ? robotFile : null;
}

function readRunApprovalCount(metadata?: Record<string, unknown>) {
    const script = metadata?.script;
    if (script && typeof script === "object" && !Array.isArray(script)) {
        const steps = (script as { steps?: Array<{ approval?: unknown }> }).steps;
        if (Array.isArray(steps)) {
            return steps.filter((item) => !!item?.approval).length;
        }
    }
    return 0;
}

function readRunUnavailableLibraries(metadata?: Record<string, unknown>) {
    const availability = metadata?.availability;
    if (!availability || typeof availability !== "object" || Array.isArray(availability)) {
        return [];
    }
    const libraryDetails = (availability as { libraryDetails?: Record<string, AvailabilityProbe> }).libraryDetails;
    if (!libraryDetails || typeof libraryDetails !== "object") {
        return [];
    }
    return Object.entries(libraryDetails)
        .filter(([, detail]) => detail && detail.detected && !detail.importable)
        .map(([name]) => name);
}

export function RPAWorkbench() {
    const { toast } = useToast();
    const t = useT();
    const [loading, setLoading] = useState(true);
    const [busyAction, setBusyAction] = useState<string | null>(null);
    const [availability, setAvailability] = useState<AvailabilityPayload>({});
    const [drafts, setDrafts] = useState<DraftPayload[]>([]);
    const [scripts, setScripts] = useState<ScriptPayload[]>([]);
    const [templates, setTemplates] = useState<TemplatePayload[]>([]);
    const [templateSummary, setTemplateSummary] = useState<TemplateSummaryPayload>({});
    const [templateHistory, setTemplateHistory] = useState<TemplateHistoryRecord[]>([]);
    const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);
    const [runs, setRuns] = useState<RunRecord[]>([]);
    const [approvalDrafts, setApprovalDrafts] = useState<Record<string, string>>({});
    const [selectedDraftId, setSelectedDraftId] = useState<string>("");
    const [selectedTemplateId, setSelectedTemplateId] = useState<string>("");
    const [templateNote, setTemplateNote] = useState("");
    const [compileRunId, setCompileRunId] = useState("");
    const [variablesText, setVariablesText] = useState("{}");
    const [existingRobotFile, setExistingRobotFile] = useState("");
    const [existingVariablesText, setExistingVariablesText] = useState("{}");
    const [cwd, setCwd] = useState("");
    const [outputDir, setOutputDir] = useState("");
    const [timeoutMs, setTimeoutMs] = useState("600000");
    const [latestResult, setLatestResult] = useState<unknown>(null);

    const selectedDraft = useMemo(
        () => drafts.find((item) => item.id === selectedDraftId) || null,
        [drafts, selectedDraftId]
    );
    const selectedTemplate = useMemo(
        () => templates.find((item) => item.id === selectedTemplateId) || null,
        [templates, selectedTemplateId]
    );

    const isRpaRun = (run: RunRecord) =>
        run.run_type === "rpa" ||
        run.metadata?.runtime === "rpa" ||
        run.metadata?.mode === "draft" ||
        run.metadata?.mode === "existing_robot" ||
        String(run.session_id || "").startsWith("rpa:");

    const isRpaApproval = (approval: ApprovalRecord) =>
        String(approval.approval_kind || "").startsWith("rpa") ||
        !!approval.request?.rpa ||
        String(approval.session_id || "").startsWith("rpa:");

    const rpaRuns = useMemo(() => runs.filter(isRpaRun).slice(0, 8), [runs]);
    const rpaApprovals = useMemo(() => approvals.filter(isRpaApproval), [approvals]);

    const loadAll = useCallback(async () => {
        setLoading(true);
        try {
            const [availabilityRes, draftsRes, scriptsRes, templatesRes, approvalsRes, runsRes] = await Promise.all([
                fetch("/api/rpa/availability", { cache: "no-store" }),
                fetch("/api/rpa/drafts", { cache: "no-store" }),
                fetch("/api/rpa/scripts", { cache: "no-store" }),
                fetch("/api/rpa/templates", { cache: "no-store" }),
                fetch("/api/approvals?status=pending", { cache: "no-store" }),
                fetch("/api/runs?limit=20", { cache: "no-store" }),
            ]);
            const nextAvailability = availabilityRes.ok ? await availabilityRes.json() : {};
            const nextDraftsPayload = draftsRes.ok ? await draftsRes.json() : {};
            const nextScriptsPayload = scriptsRes.ok ? await scriptsRes.json() : {};
            const nextTemplatesPayload = templatesRes.ok ? await templatesRes.json() : {};
            const approvalsData = approvalsRes.ok ? await approvalsRes.json().catch(() => ({})) : {};
            const runsData = runsRes.ok ? await runsRes.json().catch(() => ({})) : {};
            setAvailability(nextAvailability || {});
            const nextDrafts = Array.isArray(nextDraftsPayload?.drafts) ? nextDraftsPayload.drafts : [];
            const nextScripts = Array.isArray(nextScriptsPayload?.scripts) ? nextScriptsPayload.scripts : [];
            const nextTemplates = Array.isArray(nextTemplatesPayload?.templates) ? nextTemplatesPayload.templates : [];
            setDrafts(nextDrafts);
            setScripts(nextScripts);
            setTemplates(nextTemplates);
            setTemplateSummary(nextTemplatesPayload?.summary || {});
            setApprovals(Array.isArray(approvalsData?.approvals) ? approvalsData.approvals : []);
            setRuns(Array.isArray(runsData?.runs) ? runsData.runs : []);
            if (!selectedDraftId && nextDrafts[0]?.id) {
                setSelectedDraftId(nextDrafts[0].id);
            }
            if (!selectedTemplateId && nextTemplates[0]?.id) {
                setSelectedTemplateId(nextTemplates[0].id);
            }
        } catch (error) {
            console.error("[RPAWorkbench] load failed:", error);
            toast({
                variant: "destructive",
                title: t("components.rpa.RPAWorkbench.k83db87f2"),
                description: error instanceof Error ? error.message : "未知错误",
            });
        } finally {
            setLoading(false);
        }
    }, [selectedDraftId, selectedTemplateId, toast]);

    useEffect(() => {
        void loadAll();
    }, [loadAll]);

    useEffect(() => {
        if (!selectedTemplateId) {
            setTemplateHistory([]);
            return;
        }
        let cancelled = false;
        const loadHistory = async () => {
            try {
                const res = await fetch(`/api/rpa/templates/${encodeURIComponent(selectedTemplateId)}/history`, { cache: "no-store" });
                const data = res.ok ? await res.json() : {};
                if (!cancelled) {
                    setTemplateHistory(Array.isArray(data?.history) ? data.history : []);
                }
            } catch {
                if (!cancelled) {
                    setTemplateHistory([]);
                }
            }
        };
        void loadHistory();
        return () => {
            cancelled = true;
        };
    }, [selectedTemplateId]);

    const runAction = async (actionKey: string, runner: () => Promise<Response>, successTitle: string) => {
        setBusyAction(actionKey);
        try {
            const res = await runner();
            const data = await res.json().catch(() => ({}));
            setLatestResult(data);
            if (!res.ok) {
                throw new Error(data?.detail || data?.error || "请求失败");
            }
            toast({
                title: successTitle,
                description: typeof data?.status === "string" ? `状态：${data.status}` : "执行完成",
            });
            await loadAll();
            return data;
        } catch (error) {
            console.error(`[RPAWorkbench] ${actionKey} failed:`, error);
            toast({
                variant: "destructive",
                title: t("components.rpa.RPAWorkbench.k2e9cdd7b"),
                description: error instanceof Error ? error.message : "未知错误",
            });
            throw error;
        } finally {
            setBusyAction(null);
        }
    };

    const commonPayload = () => ({
        cwd: cwd.trim() || undefined,
        outputDir: outputDir.trim() || undefined,
    });

    const handleCompile = async () => {
        const runIds = parseRunIdsInput(compileRunId);
        if (runIds.length === 0) {
            toast({ variant: "destructive", title: t("components.rpa.RPAWorkbench.kbf8ee2bb"), description: t("components.rpa.RPAWorkbench.k67c52b94") });
            return;
        }
        const compileRequest =
            runIds.length === 1
                ? () =>
                      fetch(`/api/rpa/compile/${encodeURIComponent(runIds[0])}`, {
                          method: "POST",
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({ save: true }),
                      })
                : () =>
                      fetch("/api/rpa/compile", {
                          method: "POST",
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({ runIds, save: true }),
                      });
        const data = await runAction(
            "compile",
            compileRequest,
            runIds.length > 1 ? "多条 Trace 已合并编译为 RPA draft" : "Trace 已编译为 RPA draft"
        );
        if (data?.id) {
            setSelectedDraftId(data.id);
        }
    };

    const handleViewSourceTrace = async () => {
        if (!selectedDraftId) {
            toast({ variant: "destructive", title: t("components.rpa.RPAWorkbench.ke05762b8"), description: t("components.rpa.RPAWorkbench.k95f5425c") });
            return;
        }
        await runAction(
            `draft:source:${selectedDraftId}`,
            () => fetch(`/api/rpa/drafts/${encodeURIComponent(selectedDraftId)}/source-traces?include_steps=true&max_steps=8`, { cache: "no-store" }),
            "已载入 draft 来源 trace"
        );
    };

    const handleDraftAction = async (mode: "export" | "prepare" | "run") => {
        if (!selectedDraftId) {
            toast({ variant: "destructive", title: t("components.rpa.RPAWorkbench.ke05762b8"), description: t("components.rpa.RPAWorkbench.k95f5425c") });
            return;
        }
        let variables: Record<string, unknown>;
        try {
            variables = parseJsonObject(variablesText);
        } catch (error) {
            toast({
                variant: "destructive",
                title: t("components.rpa.RPAWorkbench.k35f41066"),
                description: error instanceof Error ? error.message : "变量必须是 JSON 对象。",
            });
            return;
        }
        const payload = {
            variables,
            timeoutMs: Number(timeoutMs || 600000),
            ...commonPayload(),
        };
        await runAction(
            `draft:${mode}`,
            () =>
                fetch(`/api/rpa/drafts/${encodeURIComponent(selectedDraftId)}/${mode}`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(mode === "export" ? commonPayload() : payload),
                }),
            mode === "run" ? "Draft 已提交运行" : mode === "prepare" ? "Draft 运行命令已准备" : "Draft 已导出 .robot"
        );
    };

    const handleExistingAction = async (mode: "prepare-existing" | "run-existing") => {
        const robotFile = existingRobotFile.trim();
        if (!robotFile) {
            toast({ variant: "destructive", title: t("components.rpa.RPAWorkbench.kfdb80aec"), description: t("components.rpa.RPAWorkbench.kbf440ba2") });
            return;
        }
        let variables: Record<string, unknown>;
        try {
            variables = parseJsonObject(existingVariablesText);
        } catch (error) {
            toast({
                variant: "destructive",
                title: t("components.rpa.RPAWorkbench.k35f41066"),
                description: error instanceof Error ? error.message : "变量必须是 JSON 对象。",
            });
            return;
        }
        const payload = {
            robotFile,
            variables,
            timeoutMs: Number(timeoutMs || 600000),
            ...commonPayload(),
        };
        await runAction(
            mode,
            () =>
                fetch(`/api/rpa/${mode}`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload),
                }),
            mode === "run-existing" ? "已有流程已提交运行" : "已有流程运行命令已准备"
        );
    };

    const handleApproval = async (approvalId: string, approve: boolean) => {
        const answer = approvalDrafts[approvalId]?.trim() || "";
        const endpoint = approve ? `/api/approvals/${approvalId}/approve` : `/api/approvals/${approvalId}/reject`;
        await runAction(
            `approval:${approve ? "approve" : "reject"}:${approvalId}`,
            () =>
                fetch(endpoint, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        response: {
                            answer,
                            approved: approve,
                        },
                    }),
                }),
            approve ? "审批已批准" : "审批已拒绝"
        );
        setApprovalDrafts((current) => {
            const next = { ...current };
            delete next[approvalId];
            return next;
        });
    };

    const handleRunCommand = async (runId: string, command: "interrupt" | "retry") => {
        await runAction(
            `run:${command}:${runId}`,
            () =>
                fetch(`/api/runs/${runId}/commands/${command}`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        reason: command === "interrupt" ? "admin_rpa_interrupt" : "admin_rpa_retry",
                    }),
                }),
            command === "interrupt" ? "运行已请求中断" : "运行已请求重试"
        );
    };

    const handleTemplateAction = async (action: "approve" | "freeze" | "review_required") => {
        if (!selectedTemplateId) {
            toast({ variant: "destructive", title: t("components.rpa.RPAWorkbench.ka3b5b6f9"), description: t("components.rpa.RPAWorkbench.k899e7048") });
            return;
        }
        const endpoint =
            action === "approve"
                ? `/api/rpa/templates/${encodeURIComponent(selectedTemplateId)}/approve`
                : action === "freeze"
                  ? `/api/rpa/templates/${encodeURIComponent(selectedTemplateId)}/freeze`
                  : `/api/rpa/templates/${encodeURIComponent(selectedTemplateId)}/review`;
        const body =
            action === "review_required"
                ? { decision: "review_required", reviewer: "admin_ui", notes: templateNote.trim() || undefined }
                : { reviewer: "admin_ui", notes: templateNote.trim() || undefined };
        await runAction(
            `template:${action}:${selectedTemplateId}`,
            () =>
                fetch(endpoint, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(body),
                }),
            action === "approve" ? "模板已批准" : action === "freeze" ? "模板已冻结" : "模板已标记待复核"
        );
    };

    const handleTemplateRollback = async (revision?: number, historyPath?: string) => {
        if (!selectedTemplateId) {
            return;
        }
        await runAction(
            `template:rollback:${selectedTemplateId}:${revision || historyPath || "latest"}`,
            () =>
                fetch(`/api/rpa/templates/${encodeURIComponent(selectedTemplateId)}/rollback`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        revision,
                        historyPath: historyPath || undefined,
                        reviewer: "admin_ui",
                        notes: templateNote.trim() || undefined,
                    }),
                }),
            "模板已回滚"
        );
    };

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between gap-4">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">RPA 流程</h1>
                    <p className="mt-1 text-muted-foreground">手动编译 ComputerUse trace、导出 `.robot`、运行 draft 或已有 Robot Framework 流程。</p>
                </div>
                <Button variant="outline" onClick={() => void loadAll()} disabled={loading || !!busyAction}>
                    <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
                    刷新
                </Button>
            </div>

            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                <Card className="border-border/60">
                    <CardHeader className="pb-3">
                        <CardTitle className="text-base">Robot Framework</CardTitle>
                        <CardDescription>执行 `.robot` 的基础运行时</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-2">
                        <Badge variant={probeState(availability.robotFrameworkDetail).variant} className="max-w-full overflow-hidden">
                            <span className="block max-w-[10rem] overflow-hidden whitespace-nowrap [mask-image:linear-gradient(90deg,#000_82%,transparent)]">
                                {t(probeState(availability.robotFrameworkDetail).label)}
                            </span>
                        </Badge>
                        {availability.robotFrameworkDetail?.origin ? (
                            <div className="break-all text-xs text-muted-foreground">{availability.robotFrameworkDetail.origin}</div>
                        ) : null}
                        {availability.robotFrameworkDetail?.error ? (
                            <div className="text-xs text-destructive">{availability.robotFrameworkDetail.error}</div>
                        ) : null}
                    </CardContent>
                </Card>
                <Card className="border-border/60">
                    <CardHeader className="pb-3">
                        <CardTitle className="text-base">RPA Framework</CardTitle>
                        <CardDescription>扩展桌面/浏览器/文件关键字库</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-2">
                        <Badge variant={probeState(availability.rpaFrameworkDetail).variant} className="max-w-full overflow-hidden">
                            <span className="block max-w-[10rem] overflow-hidden whitespace-nowrap [mask-image:linear-gradient(90deg,#000_82%,transparent)]">
                                {t(probeState(availability.rpaFrameworkDetail).label)}
                            </span>
                        </Badge>
                        {availability.rpaFrameworkDetail?.origin ? (
                            <div className="break-all text-xs text-muted-foreground">{availability.rpaFrameworkDetail.origin}</div>
                        ) : null}
                        {availability.rpaFrameworkDetail?.error ? (
                            <div className="text-xs text-destructive">{availability.rpaFrameworkDetail.error}</div>
                        ) : null}
                    </CardContent>
                </Card>
                <Card className="border-border/60 md:col-span-2">
                    <CardHeader className="pb-3">
                        <CardTitle className="text-base">可选库状态</CardTitle>
                        <CardDescription>导出 `.robot` 时会按可用性注入库声明或缺失提示</CardDescription>
                    </CardHeader>
                    <CardContent className="grid gap-3 md:grid-cols-2">
                        {Object.entries(availability.libraryDetails || {}).map(([name, detail]) => (
                            <div key={name} className="rounded-xl border border-border/60 p-3">
                                <div className="flex items-start justify-between gap-3">
                                    <div className="min-w-0 flex-1 break-words text-sm font-medium">{name}</div>
                                    <Badge variant={probeState(detail).variant} className="max-w-[11rem] shrink-0 overflow-hidden">
                                        <span className="block overflow-hidden whitespace-nowrap [mask-image:linear-gradient(90deg,#000_82%,transparent)]">
                                            {t(probeState(detail).label)}
                                        </span>
                                    </Badge>
                                </div>
                                {detail?.origin ? (
                                    <div className="mt-2 break-all text-xs text-muted-foreground">{detail.origin}</div>
                                ) : null}
                                {detail?.error ? (
                                    <div className="mt-2 text-xs text-destructive">{detail.error}</div>
                                ) : null}
                            </div>
                        ))}
                    </CardContent>
                </Card>
            </div>

            <div className="grid gap-6 xl:grid-cols-[1.2fr_1fr]">
                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-lg">
                            <Wand2 className="h-5 w-5 text-primary" />
                            从 ComputerUse trace 编译 draft
                        </CardTitle>
                        <CardDescription>输入已成功执行的 `run_id`，生成可复用的 RPA draft。</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="grid gap-2">
                            <Label htmlFor="compile-run-id">ComputerUse run_id</Label>
                            <Input id="compile-run-id" value={compileRunId} onChange={(event) => setCompileRunId(event.target.value)} placeholder={t("components.rpa.RPAWorkbench.k5b954856")} />
                        </div>
                        <Button onClick={() => void handleCompile()} disabled={busyAction === "compile"}>
                            <FileCode2 className="mr-2 h-4 w-4" />
                            {busyAction === "compile" ? "编译中..." : "编译为 draft"}
                        </Button>
                    </CardContent>
                </Card>

                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle className="text-lg">运行参数</CardTitle>
                        <CardDescription>手动运行 draft 或已有 `.robot` 时复用</CardDescription>
                    </CardHeader>
                    <CardContent className="grid gap-4">
                        <div className="grid gap-2">
                            <Label htmlFor="cwd">工作目录（可选）</Label>
                            <Input id="cwd" value={cwd} onChange={(event) => setCwd(event.target.value)} placeholder={t("components.rpa.RPAWorkbench.kcb17052e")} />
                        </div>
                        <div className="grid gap-2">
                            <Label htmlFor="output-dir">输出目录（可选）</Label>
                            <Input id="output-dir" value={outputDir} onChange={(event) => setOutputDir(event.target.value)} placeholder={t("components.rpa.RPAWorkbench.kbc389513")} />
                        </div>
                        <div className="grid gap-2">
                            <Label htmlFor="timeout-ms">超时（毫秒）</Label>
                            <Input id="timeout-ms" type="number" value={timeoutMs} onChange={(event) => setTimeoutMs(event.target.value)} />
                        </div>
                    </CardContent>
                </Card>
            </div>

            <div className="grid gap-6 xl:grid-cols-[1.15fr_1fr]">
                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle className="text-lg">Draft 列表</CardTitle>
                        <CardDescription>选择 draft 后可以导出、准备命令或直接运行</CardDescription>
                    </CardHeader>
                    <CardContent>
                        <ScrollArea className="h-[420px] pr-4">
                            <div className="space-y-3">
                                {drafts.length === 0 ? (
                                    <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">暂无 draft。先从 ComputerUse trace 编译一条。</div>
                                ) : (
                                    drafts.map((draft) => {
                                        const selected = draft.id === selectedDraftId;
                                        const highRisk = (draft.steps || []).some((item) => item.approval?.mode);
                                        const assessment = draft.assessment;
                                        const reviewRequired = String(assessment?.status || "").includes("review");
                                        return (
                                            <button
                                                key={draft.id}
                                                type="button"
                                                onClick={() => setSelectedDraftId(draft.id)}
                                                className={`w-full rounded-2xl border p-4 text-left transition-colors ${selected ? "border-primary bg-primary/5" : "border-border/60 hover:border-primary/40 hover:bg-muted/30"}`}
                                            >
                                                <div className="flex flex-wrap items-start justify-between gap-3">
                                                    <div>
                                                        <div className="text-sm font-medium">{draft.name || draft.id}</div>
                                                        <div className="mt-1 text-xs text-muted-foreground">{draft.id}</div>
                                                    </div>
                                                    <div className="flex flex-wrap gap-2">
                                                        <Badge variant="outline">{draft.appId || "desktop"}</Badge>
                                                        <Badge variant={highRisk ? "destructive" : "secondary"}>
                                                            {highRisk ? "含高风险步骤" : "常规流程"}
                                                        </Badge>
                                                        {assessment?.status ? (
                                                            <Badge variant={reviewRequired ? "destructive" : "secondary"}>
                                                                {assessment.status}{assessment.band ? ` · ${assessment.band}` : ""}
                                                            </Badge>
                                                        ) : null}
                                                    </div>
                                                </div>
                                                <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-foreground">
                                                    <span>{(draft.steps || []).length} steps</span>
                                                    <span>·</span>
                                                    <span>{(draft.variables || []).length} vars</span>
                                                    {assessment?.score != null ? (
                                                        <>
                                                            <span>·</span>
                                                            <span>置信度 {formatConfidence(assessment.score)}</span>
                                                        </>
                                                    ) : null}
                                                </div>
                                                {assessment?.reasons?.length ? (
                                                    <div className="mt-2 text-xs text-muted-foreground">
                                                        {assessment.reasons[0]}
                                                    </div>
                                                ) : null}
                                                {assessment ? (
                                                    <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-muted-foreground">
                                                        <span>accepted {assessment.acceptedSteps ?? 0}</span>
                                                        <span>review {assessment.reviewRequiredSteps ?? 0}</span>
                                                        <span>excluded {assessment.excludedSteps ?? 0}</span>
                                                    </div>
                                                ) : null}
                                                {assessment?.signals?.historicalScriptRuns ? (
                                                    <div className="mt-1 text-[11px] text-muted-foreground">
                                                        历史 {assessment.signals.historicalScriptRuns} 次 · 成功 {formatRatio(assessment.signals.historicalScriptCompletedRate)} · 回退重 {formatRatio(assessment.signals.historicalScriptFallbackHeavyRate)}
                                                    </div>
                                                ) : null}
                                                <div className="mt-3 flex flex-wrap gap-1.5">
                                                    {(draft.robot?.tags || []).slice(0, 5).map((tag) => (
                                                        <Badge key={`${draft.id}:${tag}`} variant="secondary">{tag}</Badge>
                                                    ))}
                                                </div>
                                            </button>
                                        );
                                    })
                                )}
                            </div>
                        </ScrollArea>
                    </CardContent>
                </Card>

                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle className="text-lg">Draft 操作</CardTitle>
                        <CardDescription>{selectedDraft ? `${selectedDraft.name || selectedDraft.id}` : "请先选择 draft"}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="grid gap-2">
                            <Label htmlFor="draft-vars">变量（JSON 对象）</Label>
                            <Textarea id="draft-vars" className="min-h-[180px] font-mono text-xs" value={variablesText} onChange={(event) => setVariablesText(event.target.value)} />
                        </div>
                        <div className="flex flex-wrap gap-2">
                            <Button variant="ghost" onClick={() => void handleViewSourceTrace()} disabled={!selectedDraftId || busyAction === `draft:source:${selectedDraftId}`}>
                                查看来源 trace
                            </Button>
                            <Button variant="outline" onClick={() => void handleDraftAction("export")} disabled={!selectedDraftId || busyAction === "draft:export"}>
                                导出 .robot
                            </Button>
                            <Button variant="outline" onClick={() => void handleDraftAction("prepare")} disabled={!selectedDraftId || busyAction === "draft:prepare"}>
                                准备命令
                            </Button>
                            <Button onClick={() => void handleDraftAction("run")} disabled={!selectedDraftId || busyAction === "draft:run"}>
                                <Play className="mr-2 h-4 w-4" />
                                直接运行 draft
                            </Button>
                        </div>
                        {selectedDraft ? (
                            <div className="rounded-xl border border-border/60 bg-muted/20 p-3 text-xs text-muted-foreground">
                                {selectedDraft.assessment ? (
                                    <div className="space-y-1 pb-2 text-foreground">
                                        <div>准入：{selectedDraft.assessment.status || "unknown"}{selectedDraft.assessment.band ? ` · ${selectedDraft.assessment.band}` : ""} · 置信度 {formatConfidence(selectedDraft.assessment.score)}</div>
                                        <div className="text-xs text-muted-foreground">
                                            accepted {selectedDraft.assessment.acceptedSteps ?? 0} · review {selectedDraft.assessment.reviewRequiredSteps ?? 0} · excluded {selectedDraft.assessment.excludedSteps ?? 0}
                                        </div>
                                        {selectedDraft.assessment.signals ? (
                                            <div className="text-xs text-muted-foreground">
                                                acceptedRatio {formatRatio(selectedDraft.assessment.signals.acceptedRatio)} · nativeRatio {formatRatio(selectedDraft.assessment.signals.nativeSemanticRatio)} · recoveryHeavy {formatRatio(selectedDraft.assessment.signals.recoveryHeavyRatio)} · profileAugmented {formatRatio(selectedDraft.assessment.signals.profileAugmentedRatio)}
                                            </div>
                                        ) : null}
                                        {selectedDraft.assessment.signals ? (
                                            <div className="text-xs text-muted-foreground">
                                                历史 {selectedDraft.assessment.signals.historicalScriptRuns ?? 0} 次 · 成功 {formatRatio(selectedDraft.assessment.signals.historicalScriptCompletedRate)} · review {formatRatio(selectedDraft.assessment.signals.historicalScriptReviewRequiredRate)} · blocked {formatRatio(selectedDraft.assessment.signals.historicalScriptCompileBlockedRate)}
                                            </div>
                                        ) : null}
                                        {selectedDraft.assessment.signals ? (
                                        <div className="text-xs text-muted-foreground">
                                            校准来源 {formatCalibrationSource(selectedDraft.assessment.signals.historicalScriptCalibrationSource)} · calibratedSteps {selectedDraft.assessment.signals.calibratedSteps ?? 0} · profileSteps {selectedDraft.assessment.signals.profileAugmentedSteps ?? 0} · 历史 profile {formatRatio(selectedDraft.assessment.signals.historicalScriptProfileAugmentedRatio)} · 历史原生成功 {formatRatio(selectedDraft.assessment.signals.historicalScriptNativeSuccessRate ?? selectedDraft.assessment.signals.historicalNativeSuccessRate)}
                                        </div>
                                    ) : null}
                                        {selectedDraft.assessment.trustModel ? (
                                            <div className="text-xs text-muted-foreground">
                                                阈值 trusted {formatRatio(selectedDraft.assessment.trustModel.effectiveScriptTrustedThreshold)} · review {formatRatio(selectedDraft.assessment.trustModel.effectiveScriptReviewThreshold)} · fallbackHeavy {formatRatio(selectedDraft.assessment.trustModel.effectiveScriptFallbackHeavyThreshold)}
                                            </div>
                                        ) : null}
                                        {selectedDraft.metadata?.templateGovernance ? (
                                            <div className="pt-2 text-xs text-muted-foreground">
                                                模板状态：{selectedDraft.metadata.templateGovernance.stage || selectedDraft.metadata.templateGovernanceStage || "unknown"} ·
                                                决策 {selectedDraft.metadata.templateGovernance.recommendedDecision || selectedDraft.metadata.templateRecommendedDecision || "n/a"} ·
                                                选路 {selectedDraft.metadata.templateGovernance.rolloutMode || selectedDraft.metadata.templateRolloutMode || "n/a"} ·
                                                信任 {formatConfidence(selectedDraft.metadata.templateGovernance.confidence ?? selectedDraft.metadata.templateTrustConfidence)}
                                            </div>
                                        ) : null}
                                    </div>
                                ) : null}
                                {(selectedDraft.steps || []).slice(0, 4).map((step) => (
                                    <div key={`${selectedDraft.id}:${step.stepId || step.use}`} className="py-1">
                                        {step.stepId || step.use} · {step.use}
                                        {step.assessment?.status ? ` · ${step.assessment.status}` : ""}
                                        {step.assessment?.score != null ? ` · ${formatConfidence(step.assessment.score)}` : ""}
                                    </div>
                                ))}
                                {selectedDraft.source?.traceRunIds?.length ? (
                                    <div className="pt-2 text-muted-foreground">
                                        来源 trace：{selectedDraft.source.traceRunIds.length} 条
                                    </div>
                                ) : selectedDraft.source?.traceRunId ? (
                                    <div className="pt-2 text-muted-foreground">
                                        来源 trace：{selectedDraft.source.traceRunId}
                                    </div>
                                ) : null}
                                {(selectedDraft.metadata?.compileIssues || []).slice(0, 2).map((issue, index) => (
                                    <div key={`${selectedDraft.id}:issue:${index}`} className="pt-1 text-destructive">
                                        {issue}
                                    </div>
                                ))}
                            </div>
                        ) : null}
                    </CardContent>
                </Card>
            </div>

            <div className="grid gap-6 xl:grid-cols-[1.05fr_1fr]">
                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle className="text-lg">运行已有 `.robot` 流程</CardTitle>
                        <CardDescription>支持本地已有流程或下载来的 Robot Framework 工作流。</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="grid gap-2">
                            <Label htmlFor="existing-robot">`.robot` 文件路径</Label>
                            <Input id="existing-robot" value={existingRobotFile} onChange={(event) => setExistingRobotFile(event.target.value)} placeholder={t("components.rpa.RPAWorkbench.k0f5296b5")} />
                        </div>
                        <div className="grid gap-2">
                            <Label htmlFor="existing-vars">变量（JSON 对象）</Label>
                            <Textarea id="existing-vars" className="min-h-[160px] font-mono text-xs" value={existingVariablesText} onChange={(event) => setExistingVariablesText(event.target.value)} />
                        </div>
                        <div className="flex flex-wrap gap-2">
                            <Button variant="outline" onClick={() => void handleExistingAction("prepare-existing")} disabled={busyAction === "prepare-existing"}>
                                准备已有流程
                            </Button>
                            <Button onClick={() => void handleExistingAction("run-existing")} disabled={busyAction === "run-existing"}>
                                <Play className="mr-2 h-4 w-4" />
                                直接运行已有流程
                            </Button>
                        </div>
                    </CardContent>
                </Card>

                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle className="text-lg">已有 `.robot` 资产</CardTitle>
                        <CardDescription>来自导出目录的现有 Robot Framework 脚本</CardDescription>
                    </CardHeader>
                    <CardContent>
                        <ScrollArea className="h-[340px] pr-4">
                            <div className="space-y-3">
                                {scripts.length === 0 ? (
                                    <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">暂无已导出脚本。</div>
                                ) : (
                                    scripts.map((script) => (
                                        <div key={script.path} className="rounded-2xl border border-border/60 p-4">
                                            <div className="text-sm font-medium">{script.name}</div>
                                            <div className="mt-1 break-all text-xs text-muted-foreground">{script.path}</div>
                                            <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-foreground">
                                                {script.updatedAt ? <span>{script.updatedAt}</span> : null}
                                                {typeof script.size === "number" ? <span>· {script.size} bytes</span> : null}
                                            </div>
                                        </div>
                                    ))
                                )}
                            </div>
                        </ScrollArea>
                    </CardContent>
                </Card>
            </div>

            <div className="grid gap-6 xl:grid-cols-[1.05fr_1fr]">
                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle className="text-lg">模板详情</CardTitle>
                        <CardDescription>按 trust policy 展示模板阶段、建议决策、执行选路和风险标签。</CardDescription>
                    </CardHeader>
                    <CardContent>
                        <div className="mb-4 flex flex-wrap gap-2 text-xs text-muted-foreground">
                            <Badge variant="outline">模板 {templateSummary.total ?? 0}</Badge>
                            <Badge variant="secondary">模板优先 {templateSummary.templatePreferredCount ?? 0}</Badge>
                            <Badge variant="secondary">CU 优先 {templateSummary.computerUseFirstCount ?? 0}</Badge>
                            <Badge variant={templateSummary.atRiskCount ? "destructive" : "outline"}>风险模板 {templateSummary.atRiskCount ?? 0}</Badge>
                            <Badge variant="outline">待复核 {templateSummary.reviewRequiredCount ?? 0}</Badge>
                        </div>
                        <ScrollArea className="h-[420px] pr-4">
                            <div className="space-y-3">
                                {templates.length === 0 ? (
                                    <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">暂无模板。请先从 draft 同步 template candidate。</div>
                                ) : (
                                    templates.map((template) => {
                                        const selected = template.id === selectedTemplateId;
                                        return (
                                            <button
                                                key={template.id}
                                                type="button"
                                                onClick={() => setSelectedTemplateId(template.id)}
                                                className={`w-full rounded-2xl border p-4 text-left transition-colors ${selected ? "border-primary bg-primary/5" : "border-border/60 hover:border-primary/40 hover:bg-muted/30"}`}
                                            >
                                                <div className="flex flex-wrap items-start justify-between gap-3">
                                                    <div>
                                                        <div className="text-sm font-medium">{template.name || template.id}</div>
                                                        <div className="mt-1 text-xs text-muted-foreground">{template.id}</div>
                                                    </div>
                                                    <div className="flex flex-wrap gap-2">
                                                        <Badge variant="outline">{template.appId || "desktop"}</Badge>
                                                        <Badge variant="secondary">{template.view?.statusLabel || template.status || "unknown"}</Badge>
                                                        <Badge variant={template.view?.executionPath === "computer_use_first" ? "destructive" : "outline"}>
                                                            {template.view?.executionPathLabel || template.view?.rolloutModeLabel || "未指定"}
                                                        </Badge>
                                                    </div>
                                                </div>
                                                <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-foreground">
                                                    <span>{template.view?.stageLabel || template.governance?.stage || "未分级"}</span>
                                                    <span>·</span>
                                                    <span>{template.view?.recommendedDecisionLabel || template.governance?.recommendedDecision || "未建议"}</span>
                                                    <span>·</span>
                                                    <span>信任 {template.view?.confidenceLabel || formatConfidence(template.governance?.confidence)}</span>
                                                    <span>·</span>
                                                    <span>rev {template.metadata?.revision ?? 0}</span>
                                                </div>
                                                {template.governance?.reasons?.length ? (
                                                    <div className="mt-2 text-xs text-muted-foreground">{template.governance.reasons[0]}</div>
                                                ) : null}
                                                <div className="mt-2 flex flex-wrap gap-1.5">
                                                    {(template.view?.riskFlagLabels || []).slice(0, 4).map((flag) => (
                                                        <Badge key={`${template.id}:${flag}`} variant="outline">{flag}</Badge>
                                                    ))}
                                                </div>
                                            </button>
                                        );
                                    })
                                )}
                            </div>
                        </ScrollArea>
                    </CardContent>
                </Card>

                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle className="text-lg">模板详情与历史</CardTitle>
                        <CardDescription>{selectedTemplate ? `${selectedTemplate.name || selectedTemplate.id}` : "请先选择模板"}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        {selectedTemplate ? (
                            <>
                                <div className="rounded-xl border border-border/60 bg-muted/20 p-3 text-xs text-muted-foreground">
                                    <div className="flex flex-wrap gap-2 pb-2">
                                        <Badge variant="secondary">{selectedTemplate.view?.statusLabel || selectedTemplate.status || "unknown"}</Badge>
                                        <Badge variant="outline">{selectedTemplate.view?.stageLabel || selectedTemplate.governance?.stage || "unknown"}</Badge>
                                        <Badge variant={selectedTemplate.view?.executionPath === "computer_use_first" ? "destructive" : "outline"}>
                                            {selectedTemplate.view?.executionPathLabel || selectedTemplate.view?.rolloutModeLabel || "未指定"}
                                        </Badge>
                                    </div>
                                    <div>推荐决策：<span className="text-foreground">{selectedTemplate.view?.recommendedDecisionLabel || selectedTemplate.governance?.recommendedDecision || "未建议"}</span></div>
                                    <div>当前信任：<span className="text-foreground">{selectedTemplate.view?.confidenceLabel || formatConfidence(selectedTemplate.governance?.confidence)}</span></div>
                                    <div>关联 draft：<span className="text-foreground">{selectedTemplate.source?.draftId || "n/a"}</span></div>
                                    <div className="pt-2">审阅摘要：<span className="text-foreground">
                                        {selectedTemplate.view?.reviewSummary?.total ?? 0} 次 · 批准 {selectedTemplate.view?.reviewSummary?.approveCount ?? 0} · 冻结 {selectedTemplate.view?.reviewSummary?.freezeCount ?? 0} · 回滚 {selectedTemplate.view?.reviewSummary?.rollbackCount ?? 0}
                                    </span></div>
                                    {selectedTemplate.view?.reviewSummary?.lastReviewedAt ? (
                                        <div>最后审阅：<span className="text-foreground">{formatWhen(selectedTemplate.view.reviewSummary.lastReviewedAt)}</span>{selectedTemplate.view.reviewSummary.lastReviewer ? ` · ${selectedTemplate.view.reviewSummary.lastReviewer}` : ""}</div>
                                    ) : null}
                                    {selectedTemplate.governance?.reasons?.length ? (
                                        <div className="pt-2 space-y-1">
                                            {(selectedTemplate.governance.reasons || []).slice(0, 4).map((reason, index) => (
                                                <div key={`${selectedTemplate.id}:reason:${index}`}>- {reason}</div>
                                            ))}
                                        </div>
                                    ) : null}
                                    {(selectedTemplate.view?.riskFlagLabels || []).length ? (
                                        <div className="pt-2 flex flex-wrap gap-1.5">
                                            {(selectedTemplate.view?.riskFlagLabels || []).map((flag) => (
                                                <Badge key={`${selectedTemplate.id}:risk:${flag}`} variant="outline">{flag}</Badge>
                                            ))}
                                        </div>
                                    ) : null}
                                </div>
                                <div className="grid gap-2">
                                    <Label htmlFor="template-note">备注</Label>
                                    <Textarea
                                        id="template-note"
                                        className="min-h-[96px]"
                                        value={templateNote}
                                        onChange={(event) => setTemplateNote(event.target.value)}
                                        placeholder={t("components.rpa.RPAWorkbench.k3d5ec5ad")}
                                    />
                                </div>
                                <div className="flex flex-wrap gap-2">
                                    <Button variant="outline" onClick={() => void handleTemplateAction("review_required")} disabled={busyAction === `template:review_required:${selectedTemplate.id}`}>
                                        标记待复核
                                    </Button>
                                    <Button variant="outline" onClick={() => void handleTemplateAction("freeze")} disabled={busyAction === `template:freeze:${selectedTemplate.id}`}>
                                        冻结模板
                                    </Button>
                                    <Button onClick={() => void handleTemplateAction("approve")} disabled={busyAction === `template:approve:${selectedTemplate.id}`}>
                                        批准模板
                                    </Button>
                                </div>
                                <div className="space-y-3">
                                    <div className="text-sm font-medium">历史快照</div>
                                    <ScrollArea className="h-[220px] pr-4">
                                        <div className="space-y-2">
                                            {templateHistory.length === 0 ? (
                                                <div className="rounded-xl border border-dashed p-4 text-xs text-muted-foreground">暂无历史快照。</div>
                                            ) : (
                                                templateHistory.map((item) => (
                                                    <div key={item.path || `${selectedTemplate.id}:${item.revision}`} className="rounded-xl border border-border/60 p-3 text-xs text-muted-foreground">
                                                        <div className="flex flex-wrap items-center justify-between gap-2">
                                                            <div className="flex flex-wrap gap-2">
                                                                <Badge variant="outline">rev {item.historyView?.revision ?? item.revision ?? 0}</Badge>
                                                                <Badge variant="secondary">{item.historyView?.statusLabel || item.template?.view?.statusLabel || "unknown"}</Badge>
                                                            </div>
                                                            <Button
                                                                variant="ghost"
                                                                size="sm"
                                                                onClick={() => void handleTemplateRollback(item.revision, item.path)}
                                                                disabled={busyAction === `template:rollback:${selectedTemplate.id}:${item.revision || item.path || "latest"}`}
                                                            >
                                                                回滚到此版本
                                                            </Button>
                                                        </div>
                                                        <div className="mt-2">原因：<span className="text-foreground">{item.historyView?.reason || item.reason || "snapshot"}</span></div>
                                                        <div>操作者：<span className="text-foreground">{item.historyView?.actor || item.actor || "system"}</span></div>
                                                        <div>时间：<span className="text-foreground">{formatWhen(item.historyView?.at || item.at)}</span></div>
                                                        {item.template?.view?.executionPathLabel ? (
                                                            <div>选路：<span className="text-foreground">{item.template.view.executionPathLabel}</span></div>
                                                        ) : null}
                                                    </div>
                                                ))
                                            )}
                                        </div>
                                    </ScrollArea>
                                </div>
                            </>
                        ) : (
                            <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">请选择模板后查看详情与历史。</div>
                        )}
                    </CardContent>
                </Card>
            </div>

            <Card className="border-border/60">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-lg">
                        {latestResult && typeof (latestResult as { status?: string }).status === "string" && ["completed", "ready", "completed_with_fallback"].includes((latestResult as { status?: string }).status || "") ? (
                            <CheckCircle2 className="h-5 w-5 text-emerald-500" />
                        ) : latestResult ? (
                            <ShieldAlert className="h-5 w-5 text-amber-500" />
                        ) : (
                            <AlertCircle className="h-5 w-5 text-muted-foreground" />
                        )}
                        最近一次结果
                    </CardTitle>
                </CardHeader>
                <CardContent>
                    <pre className="max-h-[420px] overflow-auto rounded-xl bg-muted/30 p-4 text-xs leading-6">
                        {latestResult ? prettyJson(latestResult) : "暂无执行结果。"}
                    </pre>
                </CardContent>
            </Card>

            <div className="grid gap-6 xl:grid-cols-2">
                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle className="text-lg">RPA 待审批</CardTitle>
                        <CardDescription>这里只显示 `rpa_review` 或归属 RPA 会话的审批项。</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        {rpaApprovals.length === 0 ? (
                            <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">当前没有待处理的 RPA 审批。</div>
                        ) : (
                            rpaApprovals.map((approval) => {
                                const busyApprove = busyAction === `approval:approve:${approval.id}`;
                                const busyReject = busyAction === `approval:reject:${approval.id}`;
                                const question = approval.request?.question || approval.request?.prompt || "该审批未附带额外说明。";
                                return (
                                    <div key={approval.id} className="rounded-2xl border border-border/60 p-4">
                                        <div className="flex flex-wrap gap-2">
                                            <Badge variant="outline">{approval.approval_kind || "approval"}</Badge>
                                            {approval.run_id ? <Badge variant="secondary">Run {approval.run_id}</Badge> : null}
                                            {approval.session_id ? <Badge variant="secondary">Session {approval.session_id}</Badge> : null}
                                        </div>
                                        <p className="mt-3 whitespace-pre-wrap text-sm leading-6">{question}</p>
                                        {approval.request?.rpa?.requiredApprovals?.length ? (
                                            <div className="mt-3 flex flex-wrap gap-2">
                                                {approval.request.rpa.requiredApprovals.map((item, index) => (
                                                    <Badge key={`${approval.id}:${item.stepId || index}`} variant="outline">
                                                        {item.stepId || item.use || "step"} · {item.mode || "review"}
                                                        {item.confidence != null ? ` · ${formatConfidence(item.confidence)}` : ""}
                                                    </Badge>
                                                ))}
                                            </div>
                                        ) : null}
                                        <div className="mt-2 text-xs text-muted-foreground">
                                            创建于 {formatWhen(approval.created_at)}
                                        </div>
                                        <Textarea
                                            className="mt-3 min-h-[96px]"
                                            placeholder={t("components.rpa.RPAWorkbench.k5d7ae816")}
                                            value={approvalDrafts[approval.id] || ""}
                                            onChange={(event) => setApprovalDrafts((current) => ({ ...current, [approval.id]: event.target.value }))}
                                        />
                                        <div className="mt-3 flex justify-end gap-2">
                                            <Button variant="outline" onClick={() => void handleApproval(approval.id, false)} disabled={busyApprove || busyReject}>
                                                {busyReject ? "拒绝中..." : "拒绝"}
                                            </Button>
                                            <Button onClick={() => void handleApproval(approval.id, true)} disabled={busyApprove || busyReject}>
                                                {busyApprove ? "批准中..." : "批准继续"}
                                            </Button>
                                        </div>
                                    </div>
                                );
                            })
                        )}
                    </CardContent>
                </Card>

                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle className="text-lg">最近 RPA 运行</CardTitle>
                        <CardDescription>只显示归属 `RPARuntime` 的运行记录。</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        {rpaRuns.length === 0 ? (
                            <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">暂无 RPA 运行记录。</div>
                        ) : (
                            rpaRuns.map((run) => {
                                const interruptBusy = busyAction === `run:interrupt:${run.id}`;
                                const retryBusy = busyAction === `run:retry:${run.id}`;
                                const status = run.status || "queued";
                                const scriptName = readRunScriptName(run.metadata);
                                const robotFile = readRunRobotFile(run.metadata);
                                const approvalCount = readRunApprovalCount(run.metadata);
                                const missingLibraries = readRunUnavailableLibraries(run.metadata);
                                const assessment = readRunAssessment(run.metadata);
                                const executionState = readRunExecutionState(run.metadata);
                                const fallback = readRunFallback(run.metadata);
                                const templatePolicy = readRunTemplatePolicy(run.metadata);
                                return (
                                    <div key={run.id} className="rounded-2xl border border-border/60 p-4">
                                        <div className="flex flex-wrap gap-2">
                                            <Badge>{t(RUN_LABELS[status] || status)}</Badge>
                                            {run.metadata?.mode ? <Badge variant="outline">{String(run.metadata.mode)}</Badge> : null}
                                            {run.trigger_source ? <Badge variant="secondary">{run.trigger_source}</Badge> : null}
                                            {approvalCount > 0 ? <Badge variant="destructive">{approvalCount} 个审批点</Badge> : null}
                                            {executionState ? <Badge variant="outline">{executionState}</Badge> : null}
                                            {templatePolicy?.executionPath ? <Badge variant="outline">route:{templatePolicy.executionPath}</Badge> : null}
                                            {fallback?.type ? <Badge variant="secondary">fallback:{fallback.type}</Badge> : null}
                                        </div>
                                        <div className="mt-3 space-y-1 text-sm text-muted-foreground">
                                            <div>Run ID: <span className="text-foreground">{run.id}</span></div>
                                            {run.session_id ? <div>Session: <span className="text-foreground">{run.session_id}</span></div> : null}
                                            {scriptName ? <div>流程: <span className="text-foreground">{scriptName}</span></div> : null}
                                            {robotFile ? <div>Robot: <span className="break-all text-foreground">{robotFile}</span></div> : null}
                                            {assessment ? <div>准入: <span className="text-foreground">{assessment.status || "unknown"}{assessment.band ? ` · ${assessment.band}` : ""} · {formatConfidence(assessment.score)}</span></div> : null}
                                            {assessment?.signals ? <div>acceptedRatio: <span className="text-foreground">{formatRatio(assessment.signals.acceptedRatio)}</span> · nativeRatio: <span className="text-foreground">{formatRatio(assessment.signals.nativeSemanticRatio)}</span> · recoveryHeavy: <span className="text-foreground">{formatRatio(assessment.signals.recoveryHeavyRatio)}</span> · profileAugmented: <span className="text-foreground">{formatRatio(assessment.signals.profileAugmentedRatio)}</span></div> : null}
                                            {assessment ? <div>accepted/review/excluded: <span className="text-foreground">{assessment.acceptedSteps ?? 0}/{assessment.reviewRequiredSteps ?? 0}/{assessment.excludedSteps ?? 0}</span></div> : null}
                                            {assessment?.signals ? <div>历史: <span className="text-foreground">{assessment.signals.historicalScriptRuns ?? 0}</span> 次 · 成功 <span className="text-foreground">{formatRatio(assessment.signals.historicalScriptCompletedRate)}</span> · 校准 <span className="text-foreground">{formatCalibrationSource(assessment.signals.historicalScriptCalibrationSource)}</span></div> : null}
                                            {templatePolicy ? <div>模板选路: <span className="text-foreground">{templatePolicy.executionPath || "robot"}</span> · 阶段 <span className="text-foreground">{templatePolicy.stage || "unknown"}</span> · 决策 <span className="text-foreground">{templatePolicy.recommendedDecision || "n/a"}</span></div> : null}
                                            {fallback?.sourceTraceRunId ? <div>Fallback Trace: <span className="text-foreground">{fallback.sourceTraceRunId}</span></div> : null}
                                            {fallback?.sourceScriptId ? <div>Fallback Script: <span className="text-foreground">{fallback.sourceScriptId}</span></div> : null}
                                            <div>时间: <span className="text-foreground">{formatWhen(run.created_at)}</span></div>
                                        </div>
                                        {missingLibraries.length ? (
                                            <div className="mt-3 flex flex-wrap gap-2">
                                                {missingLibraries.map((item) => (
                                                    <Badge key={`${run.id}:${item}`} variant="outline">
                                                        缺库 {item}
                                                    </Badge>
                                                ))}
                                            </div>
                                        ) : null}
                                        <div className="mt-3 flex justify-end gap-2">
                                            {status === "running" ? (
                                                <Button variant="outline" onClick={() => void handleRunCommand(run.id, "interrupt")} disabled={interruptBusy || retryBusy}>
                                                    {interruptBusy ? "中断中..." : "中断"}
                                                </Button>
                                            ) : null}
                                            {["paused", "failed", "cancelled", "waiting_input"].includes(status) ? (
                                                <Button variant="outline" onClick={() => void handleRunCommand(run.id, "retry")} disabled={interruptBusy || retryBusy}>
                                                    {retryBusy ? "重试中..." : "重试"}
                                                </Button>
                                            ) : null}
                                        </div>
                                    </div>
                                );
                            })
                        )}
                    </CardContent>
                </Card>
            </div>
        </div>
    );
}
