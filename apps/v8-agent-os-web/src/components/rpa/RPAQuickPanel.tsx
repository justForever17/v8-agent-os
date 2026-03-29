"use client";

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { ArrowLeft, FileCode2, Play, RefreshCw, Workflow } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useT } from "@/components/providers/LocaleProvider";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import { lt } from "@/lib/locale";

type DraftPayload = {
    id: string;
    name?: string;
    appId?: string;
    source?: {
        type?: string;
        traceRunId?: string;
        traceRunIds?: string[];
    };
    steps?: Array<{ stepId?: string; use?: string; assessment?: { score?: number; status?: string } }>;
    variables?: Array<{ name?: string; type?: string }>;
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
    };
};

type AvailabilityProbe = {
    detected?: boolean;
    importable?: boolean;
    origin?: string | null;
    error?: string | null;
};

type AvailabilityPayload = {
    robotFramework?: boolean;
    rpaFramework?: boolean;
    robotFrameworkDetail?: AvailabilityProbe;
    rpaFrameworkDetail?: AvailabilityProbe;
    libraries?: Record<string, boolean>;
    libraryDetails?: Record<string, AvailabilityProbe>;
};

type AssessmentSignals = {
    acceptedRatio?: number;
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
    effectiveScriptTrustedThreshold?: number;
    effectiveScriptReviewThreshold?: number;
    effectiveScriptFallbackHeavyThreshold?: number;
    trustModelVersion?: string;
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
            scriptId?: string;
            subject?: string;
            requiredApprovals?: Array<{ stepId?: string; use?: string; mode?: string; confidence?: number }>;
        };
    };
};

type RunRecord = {
    id: string;
    session_id?: string;
    status?: string;
    created_at?: string;
    trigger_source?: string;
    metadata?: Record<string, unknown>;
};

function parseJsonObject(value: string) {
    const trimmed = value.trim();
    if (!trimmed) return {};
    const parsed = JSON.parse(trimmed);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("Variables must be a JSON object.");
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

function Pill({ children }: { children: ReactNode }) {
    return <span className="rounded-full border border-border/60 bg-muted/40 px-2 py-1 text-xs text-muted-foreground">{children}</span>;
}

function probeState(detail?: AvailabilityProbe) {
    if (!detail?.detected) {
        return lt("未安装", "Not installed");
    }
    if (detail.importable) {
        return lt("可运行", "Ready");
    }
    return lt("缺依赖", "Missing deps");
}

function formatConfidence(score?: number | null) {
    if (typeof score !== "number" || Number.isNaN(score)) {
        return "n/a";
    }
    return `${Math.round(score * 100)}%`;
}

function formatRatio(score?: number | null) {
    if (typeof score !== "number" || Number.isNaN(score)) {
        return "n/a";
    }
    return `${Math.round(score * 100)}%`;
}

function formatCalibrationSource(source?: string | null) {
    if (!source) {
        return "n/a";
    }
    if (source === "fingerprint") {
        return lt("fingerprint 复用", "Fingerprint reuse");
    }
    if (source === "script") {
        return lt("script 直连", "Direct script");
    }
    return source;
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

function readRunExecutionState(metadata?: Record<string, unknown>) {
    const state = metadata?.executionState;
    return typeof state === "string" && state.trim() ? state : null;
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

function readRunFallback(metadata?: Record<string, unknown>) {
    const fallback = metadata?.fallback;
    if (fallback && typeof fallback === "object" && !Array.isArray(fallback)) {
        return fallback as { type?: string; sourceTraceRunId?: string };
    }
    return null;
}

export function RPAQuickPanel() {
    const t = useT();
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState<string | null>(null);
    const [availability, setAvailability] = useState<AvailabilityPayload>({});
    const [drafts, setDrafts] = useState<DraftPayload[]>([]);
    const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);
    const [runs, setRuns] = useState<RunRecord[]>([]);
    const [approvalDrafts, setApprovalDrafts] = useState<Record<string, string>>({});
    const [selectedDraftId, setSelectedDraftId] = useState("");
    const [compileRunId, setCompileRunId] = useState("");
    const [variablesText, setVariablesText] = useState("{}");
    const [existingRobotFile, setExistingRobotFile] = useState("");
    const [latestResult, setLatestResult] = useState<string>(t(lt("暂无结果。", "No result yet.")));

    const selectedDraft = useMemo(
        () => drafts.find((item) => item.id === selectedDraftId) || null,
        [drafts, selectedDraftId]
    );

    const rpaApprovals = useMemo(
        () =>
            approvals.filter((item) =>
                String(item.approval_kind || "").startsWith("rpa") ||
                !!item.request?.rpa ||
                String(item.session_id || "").startsWith("rpa:")
            ),
        [approvals]
    );
    const rpaRuns = useMemo(
        () =>
            runs.filter((item) =>
                item.metadata?.runtime === "rpa" ||
                item.metadata?.mode === "draft" ||
                item.metadata?.mode === "existing_robot" ||
                String(item.session_id || "").startsWith("rpa:")
            ).slice(0, 6),
        [runs]
    );

    const loadDrafts = useCallback(async () => {
        setLoading(true);
        try {
            const [availabilityRes, draftsRes, approvalsRes, runsRes] = await Promise.all([
                fetch("/api/rpa/availability", { cache: "no-store" }),
                fetch("/api/rpa/drafts", { cache: "no-store" }),
                fetch("/api/approvals?status=pending", { cache: "no-store" }),
                fetch("/api/runs?limit=16", { cache: "no-store" }),
            ]);
            const availabilityData = availabilityRes.ok ? await availabilityRes.json() : {};
            const data = draftsRes.ok ? await draftsRes.json() : {};
            const approvalsData = approvalsRes.ok ? await approvalsRes.json().catch(() => ({})) : {};
            const runsData = runsRes.ok ? await runsRes.json().catch(() => ({})) : {};
            const nextDrafts = Array.isArray(data?.drafts) ? data.drafts : [];
            setAvailability(availabilityData || {});
            setDrafts(nextDrafts);
            setApprovals(Array.isArray(approvalsData?.approvals) ? approvalsData.approvals : []);
            setRuns(Array.isArray(runsData?.runs) ? runsData.runs : []);
            setSelectedDraftId((current) => current || nextDrafts[0]?.id || current);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        void loadDrafts();
    }, [loadDrafts]);

    const callApi = async (key: string, url: string, payload: unknown) => {
        setBusy(key);
        try {
            const res = await fetch(url, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            const data = await res.json().catch(() => ({}));
            setLatestResult(JSON.stringify(data, null, 2));
            await loadDrafts();
            return { ok: res.ok, data };
        } finally {
            setBusy(null);
        }
    };

    const withParsedVariables = (value: string) => {
        try {
            return parseJsonObject(value);
        } catch (error) {
            setLatestResult(
                JSON.stringify(
                    {
                        error: error instanceof Error ? error.message : t(lt("变量必须是 JSON 对象。", "Variables must be a JSON object.")),
                    },
                    null,
                    2
                )
            );
            return null;
        }
    };

    const handleApproval = async (approvalId: string, approve: boolean) => {
        await callApi(
            `approval:${approve ? "approve" : "reject"}`,
            `/api/approvals/${encodeURIComponent(approvalId)}/${approve ? "approve" : "reject"}`,
            {
                response: {
                    answer: approvalDrafts[approvalId]?.trim() || "",
                    approved: approve,
                },
            }
        );
        setApprovalDrafts((current) => {
            const next = { ...current };
            delete next[approvalId];
            return next;
        });
    };

    return (
        <div className="mx-auto w-full max-w-6xl px-4 py-4 sm:px-6 sm:py-6">
            <div className="mb-5 rounded-[2rem] border border-border/70 bg-background/88 p-4 shadow-[0_28px_80px_-42px_rgba(15,23,42,0.45)] backdrop-blur-sm sm:p-6">
                <div className="flex flex-col items-start gap-4 md:flex-row md:items-center md:justify-between">
                    <div className="min-w-0">
                        <Button variant="ghost" size="sm" asChild className="mb-2 -ml-3 text-muted-foreground hover:text-foreground">
                        <Link href="/chat">
                            <ArrowLeft className="mr-2 h-4 w-4" />
                            {t(lt("返回", "Back"))}
                        </Link>
                    </Button>
                        <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-muted-foreground">
                            RPA
                        </div>
                        <h1 className="mt-1 flex items-center gap-2 text-2xl font-semibold tracking-tight sm:text-3xl">
                            <Workflow className="h-6 w-6 text-primary sm:h-7 sm:w-7" />
                            {t(lt("自动流程", "Workflows"))}
                        </h1>
                        <p className="mt-2 text-sm leading-6 text-muted-foreground">{t(lt("生成、运行、审批。", "Build, run, review."))}</p>
                    </div>
                    <Button variant="outline" onClick={() => void loadDrafts()} disabled={loading || !!busy} className="w-full sm:w-auto">
                    <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
                    {t(lt("刷新", "Refresh"))}
                </Button>
            </div>
            </div>

            <div className="mb-5 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <Card className="border-border/60">
                    <CardHeader className="pb-3">
                        <CardTitle className="text-base">Robot Framework</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-2">
                        <Pill>{t(probeState(availability.robotFrameworkDetail))}</Pill>
                        {availability.robotFrameworkDetail?.error ? (
                            <div className="text-xs text-destructive">{availability.robotFrameworkDetail.error}</div>
                        ) : null}
                    </CardContent>
                </Card>
                <Card className="border-border/60">
                    <CardHeader className="pb-3">
                        <CardTitle className="text-base">RPA Framework</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-2">
                        <Pill>{t(probeState(availability.rpaFrameworkDetail))}</Pill>
                        {availability.rpaFrameworkDetail?.error ? (
                            <div className="text-xs text-destructive">{availability.rpaFrameworkDetail.error}</div>
                        ) : null}
                    </CardContent>
                </Card>
                <Card className="border-border/60 md:col-span-2">
                    <CardHeader className="pb-3">
                        <CardTitle className="text-base">{t(lt("关键库", "Libraries"))}</CardTitle>
                    </CardHeader>
                    <CardContent className="flex flex-wrap gap-2">
                        {Object.entries(availability.libraryDetails || {}).map(([name, detail]) => (
                            <Pill key={name}>
                                {name} · {t(probeState(detail))}
                            </Pill>
                        ))}
                    </CardContent>
                </Card>
            </div>

            <div className="grid gap-6 xl:grid-cols-[1.1fr_1fr]">
                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle>{t(lt("从记录生成流程", "Build from traces"))}</CardTitle>
                        <CardDescription>{t(lt("输入 run_id。", "Paste run IDs."))}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="grid gap-2">
                            <Label htmlFor="web-rpa-run-id">run_id</Label>
                            <Input id="web-rpa-run-id" value={compileRunId} onChange={(event) => setCompileRunId(event.target.value)} placeholder={t(lt("例如：run_xxx, run_yyy", "e.g. run_xxx, run_yyy"))} />
                        </div>
                        <Button
                            onClick={() => {
                                const runIds = parseRunIdsInput(compileRunId);
                                if (runIds.length === 0) {
                                    setLatestResult(JSON.stringify({ error: t(lt("请先输入至少一个 ComputerUse run_id。", "Enter at least one ComputerUse run_id.")) }, null, 2));
                                    return;
                                }
                                const endpoint = runIds.length === 1 ? `/api/rpa/compile/${encodeURIComponent(runIds[0])}` : "/api/rpa/compile";
                                const payload = runIds.length === 1 ? { save: true } : { runIds, save: true };
                                void callApi("compile", endpoint, payload).then((result) => {
                                    if (result?.ok && typeof result.data?.id === "string") {
                                        setSelectedDraftId(result.data.id);
                                    }
                                });
                            }}
                            disabled={!compileRunId.trim() || busy === "compile"}
                            className="w-full sm:w-auto"
                        >
                            <FileCode2 className="mr-2 h-4 w-4" />
                            {t(lt("生成草稿", "Generate draft"))}
                        </Button>
                    </CardContent>
                </Card>

                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle>{t(lt("运行现有流程", "Run existing"))}</CardTitle>
                        <CardDescription>{t(lt("运行一个 `.robot` 文件。", "Run a `.robot` file."))}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="grid gap-2">
                            <Label htmlFor="web-rpa-existing">{t(lt("`.robot` 路径", "`.robot` path"))}</Label>
                            <Input id="web-rpa-existing" value={existingRobotFile} onChange={(event) => setExistingRobotFile(event.target.value)} placeholder={t(lt("例如：E:\\RPA\\flow.robot", "e.g. E:\\RPA\\flow.robot"))} />
                        </div>
                        <Button
                            onClick={() => {
                                const variables = withParsedVariables(variablesText);
                                if (!variables) return;
                                void callApi("run-existing", "/api/rpa/run-existing", { robotFile: existingRobotFile.trim(), variables });
                            }}
                            disabled={!existingRobotFile.trim() || busy === "run-existing"}
                            className="w-full sm:w-auto"
                        >
                            <Play className="mr-2 h-4 w-4" />
                            {t(lt("运行流程", "Run flow"))}
                        </Button>
                    </CardContent>
                </Card>
            </div>

            <div className="mt-5 grid gap-4 xl:grid-cols-[1.05fr_1fr]">
                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle>{t(lt("流程草稿", "Drafts"))}</CardTitle>
                        <CardDescription>{t(lt("选择一个 draft。", "Pick a draft."))}</CardDescription>
                    </CardHeader>
                    <CardContent>
                        <ScrollArea className="h-[280px] pr-4 sm:h-[360px]">
                            <div className="space-y-3">
                                {drafts.map((draft) => (
                                    <button
                                        key={draft.id}
                                        type="button"
                                        onClick={() => setSelectedDraftId(draft.id)}
                                        className={`w-full rounded-2xl border p-4 text-left transition-colors ${draft.id === selectedDraftId ? "border-primary bg-primary/5" : "border-border/60 hover:border-primary/40 hover:bg-muted/20"}`}
                                    >
                                        <div className="text-sm font-medium">{draft.name || draft.id}</div>
                                        <div className="mt-1 text-xs text-muted-foreground">{draft.id}</div>
                                        <div className="mt-3 flex flex-wrap gap-2">
                                            <Pill>{draft.appId || "desktop"}</Pill>
                                            <Pill>{t(lt(`${(draft.steps || []).length} 步骤`, `${(draft.steps || []).length} steps`))}</Pill>
                                            <Pill>{t(lt(`${(draft.variables || []).length} 变量`, `${(draft.variables || []).length} vars`))}</Pill>
                                            {draft.assessment?.status ? <Pill>{draft.assessment.status}{draft.assessment.band ? ` · ${draft.assessment.band}` : ""}</Pill> : null}
                                            {draft.assessment?.score != null ? <Pill>{formatConfidence(draft.assessment.score)}</Pill> : null}
                                        </div>
                                        {draft.assessment?.reasons?.length ? (
                                            <div className="mt-2 text-xs text-muted-foreground">{draft.assessment.reasons[0]}</div>
                                        ) : null}
                                        {draft.assessment ? (
                                            <div className="mt-2 text-[11px] text-muted-foreground">
                                                {t(lt(`可直接用 ${draft.assessment.acceptedSteps ?? 0} 步 · 需确认 ${draft.assessment.reviewRequiredSteps ?? 0} 步 · 已排除 ${draft.assessment.excludedSteps ?? 0} 步`, `Ready ${draft.assessment.acceptedSteps ?? 0} · Review ${draft.assessment.reviewRequiredSteps ?? 0} · Excluded ${draft.assessment.excludedSteps ?? 0}`))}
                                            </div>
                                        ) : null}
                                        {draft.assessment?.signals?.historicalScriptRuns ? (
                                            <div className="mt-1 text-[11px] text-muted-foreground">
                                                {t(lt(`历史执行 ${draft.assessment.signals.historicalScriptRuns} 次 · 成功率 ${formatRatio(draft.assessment.signals.historicalScriptCompletedRate)}`, `History ${draft.assessment.signals.historicalScriptRuns} runs · Success ${formatRatio(draft.assessment.signals.historicalScriptCompletedRate)}`))}
                                            </div>
                                        ) : null}
                                    </button>
                                ))}
                                {drafts.length === 0 ? (
                                    <div className="rounded-xl border border-dashed p-5 text-sm text-muted-foreground">{t(lt("暂无草稿。", "No drafts yet."))}</div>
                                ) : null}
                            </div>
                        </ScrollArea>
                    </CardContent>
                </Card>

                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle>{t(lt("运行草稿", "Run draft"))}</CardTitle>
                        <CardDescription>{selectedDraft ? `${selectedDraft.name || selectedDraft.id}` : t(lt("请先选择一个草稿", "Select a draft first"))}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        {selectedDraft?.assessment ? (
                            <div className="rounded-xl border border-border/60 bg-muted/20 p-3 text-xs text-muted-foreground">
                                <div className="text-foreground">
                                    {t(lt(`当前状态：${selectedDraft.assessment.status || "unknown"}${selectedDraft.assessment.band ? ` · ${selectedDraft.assessment.band}` : ""} · 置信度 ${formatConfidence(selectedDraft.assessment.score)}`, `Status: ${selectedDraft.assessment.status || "unknown"}${selectedDraft.assessment.band ? ` · ${selectedDraft.assessment.band}` : ""} · Confidence ${formatConfidence(selectedDraft.assessment.score)}`))}
                                </div>
                                <div className="pt-1">
                                    {t(lt(`可直接用 ${selectedDraft.assessment.acceptedSteps ?? 0} 步 · 需确认 ${selectedDraft.assessment.reviewRequiredSteps ?? 0} 步 · 已排除 ${selectedDraft.assessment.excludedSteps ?? 0} 步`, `Ready ${selectedDraft.assessment.acceptedSteps ?? 0} · Review ${selectedDraft.assessment.reviewRequiredSteps ?? 0} · Excluded ${selectedDraft.assessment.excludedSteps ?? 0}`))}
                                </div>
                                {selectedDraft.assessment?.signals?.historicalScriptRuns ? (
                                    <div className="pt-1">
                                        {t(lt(`历史执行 ${selectedDraft.assessment.signals.historicalScriptRuns ?? 0} 次 · 成功率 ${formatRatio(selectedDraft.assessment.signals.historicalScriptCompletedRate)}`, `History ${selectedDraft.assessment.signals.historicalScriptRuns ?? 0} runs · Success ${formatRatio(selectedDraft.assessment.signals.historicalScriptCompletedRate)}`))}
                                    </div>
                                ) : null}
                                {(selectedDraft.assessment.signals || selectedDraft.assessment.trustModel) ? (
                                    <details className="pt-2">
                                        <summary className="cursor-pointer text-xs text-muted-foreground">{t(lt("更多", "More"))}</summary>
                                        <div className="space-y-1 pt-2 text-[11px] text-muted-foreground">
                                            {selectedDraft.assessment.signals ? (
                                                <div>
                                                    {t(lt(`接受比例 ${formatRatio(selectedDraft.assessment.signals.acceptedRatio)} · 原生比例 ${formatRatio(selectedDraft.assessment.signals.nativeSemanticRatio)} · 回退偏重 ${formatRatio(selectedDraft.assessment.signals.recoveryHeavyRatio)} · 画像补充 ${formatRatio(selectedDraft.assessment.signals.profileAugmentedRatio)}`, `Accepted ${formatRatio(selectedDraft.assessment.signals.acceptedRatio)} · Native ${formatRatio(selectedDraft.assessment.signals.nativeSemanticRatio)} · Fallback-heavy ${formatRatio(selectedDraft.assessment.signals.recoveryHeavyRatio)} · Profile ${formatRatio(selectedDraft.assessment.signals.profileAugmentedRatio)}`))}
                                                </div>
                                            ) : null}
                                            {selectedDraft.assessment.signals ? (
                                                <div>
                                                    {t(lt(`历史需确认 ${formatRatio(selectedDraft.assessment.signals.historicalScriptReviewRequiredRate)} · 历史被拦截 ${formatRatio(selectedDraft.assessment.signals.historicalScriptCompileBlockedRate)} · 校准来源 ${t(formatCalibrationSource(selectedDraft.assessment.signals.historicalScriptCalibrationSource))}`, `Review rate ${formatRatio(selectedDraft.assessment.signals.historicalScriptReviewRequiredRate)} · Blocked ${formatRatio(selectedDraft.assessment.signals.historicalScriptCompileBlockedRate)} · Calibration ${t(formatCalibrationSource(selectedDraft.assessment.signals.historicalScriptCalibrationSource))}`))}
                                                </div>
                                            ) : null}
                                            {selectedDraft.assessment.signals ? (
                                                <div>
                                                    {t(lt(`已校准步骤 ${selectedDraft.assessment.signals.calibratedSteps ?? 0} · 画像补充步骤 ${selectedDraft.assessment.signals.profileAugmentedSteps ?? 0} · 历史原生成功 ${formatRatio(selectedDraft.assessment.signals.historicalScriptNativeSuccessRate ?? selectedDraft.assessment.signals.historicalNativeSuccessRate)}`, `Calibrated ${selectedDraft.assessment.signals.calibratedSteps ?? 0} · Profile steps ${selectedDraft.assessment.signals.profileAugmentedSteps ?? 0} · Native success ${formatRatio(selectedDraft.assessment.signals.historicalScriptNativeSuccessRate ?? selectedDraft.assessment.signals.historicalNativeSuccessRate)}`))}
                                                </div>
                                            ) : null}
                                            {selectedDraft.assessment.trustModel ? (
                                                <div>
                                                    {t(lt(`阈值：直接通过 ${formatRatio(selectedDraft.assessment.trustModel.effectiveScriptTrustedThreshold)} · 需确认 ${formatRatio(selectedDraft.assessment.trustModel.effectiveScriptReviewThreshold)} · 回退偏重 ${formatRatio(selectedDraft.assessment.trustModel.effectiveScriptFallbackHeavyThreshold)}`, `Thresholds: trust ${formatRatio(selectedDraft.assessment.trustModel.effectiveScriptTrustedThreshold)} · review ${formatRatio(selectedDraft.assessment.trustModel.effectiveScriptReviewThreshold)} · fallback-heavy ${formatRatio(selectedDraft.assessment.trustModel.effectiveScriptFallbackHeavyThreshold)}`))}
                                                </div>
                                            ) : null}
                                        </div>
                                    </details>
                                ) : null}
                                {selectedDraft.metadata?.compileIssues?.slice(0, 2).map((item, index) => (
                                    <div key={`${selectedDraft.id}:issue:${index}`} className="pt-1 text-destructive">
                                        {item}
                                    </div>
                                ))}
                                {selectedDraft.source?.traceRunIds?.length ? (
                                    <div className="pt-1">
                                        {t(lt(`来源 trace：${selectedDraft.source.traceRunIds.length} 条`, `Source traces: ${selectedDraft.source.traceRunIds.length}`))}
                                    </div>
                                ) : selectedDraft.source?.traceRunId ? (
                                    <div className="pt-1">
                                        {t(lt(`来源 trace：${selectedDraft.source.traceRunId}`, `Source trace: ${selectedDraft.source.traceRunId}`))}
                                    </div>
                                ) : null}
                            </div>
                        ) : null}
                        <div className="grid gap-2">
                            <Label htmlFor="web-rpa-vars">{t(lt("变量（JSON）", "Variables (JSON)"))}</Label>
                            <Textarea id="web-rpa-vars" className="min-h-[160px] font-mono text-xs sm:min-h-[180px]" value={variablesText} onChange={(event) => setVariablesText(event.target.value)} />
                        </div>
                        <Button
                            variant="outline"
                            onClick={() => {
                                if (!selectedDraftId) return;
                                setBusy("source-trace");
                                fetch(`/api/rpa/drafts/${encodeURIComponent(selectedDraftId)}/source-traces?include_steps=true&max_steps=8`, { cache: "no-store" })
                                    .then(async (res) => {
                                        const data = await res.json().catch(() => ({}));
                                        setLatestResult(JSON.stringify(data, null, 2));
                                    })
                                    .finally(() => setBusy(null));
                            }}
                            disabled={!selectedDraftId || busy === "source-trace"}
                            className="w-full sm:w-auto"
                        >
                            {t(lt("查看来源", "View source"))}
                        </Button>
                        <Button
                            onClick={() => {
                                const variables = withParsedVariables(variablesText);
                                if (!variables) return;
                                void callApi("run-draft", `/api/rpa/drafts/${encodeURIComponent(selectedDraftId)}/run`, { variables });
                            }}
                            disabled={!selectedDraftId || busy === "run-draft"}
                            className="w-full sm:w-auto"
                        >
                            <Play className="mr-2 h-4 w-4" />
                            {t(lt("运行草稿", "Run draft"))}
                        </Button>
                    </CardContent>
                </Card>
            </div>

            <Card className="mt-5 border-border/60">
                <CardHeader>
                    <CardTitle>{t(lt("结果", "Result"))}</CardTitle>
                </CardHeader>
                <CardContent>
                    <pre className="max-h-[220px] overflow-auto rounded-xl bg-muted/30 p-4 text-xs leading-6 sm:max-h-[320px]">{latestResult}</pre>
                </CardContent>
            </Card>

            <div className="mt-5 grid gap-4 xl:grid-cols-2">
                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle>{t(lt("待审批", "Approvals"))}</CardTitle>
                        <CardDescription>{t(lt("处理待审批项。", "Review pending items."))}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        {rpaApprovals.length === 0 ? (
                            <div className="rounded-xl border border-dashed p-5 text-sm text-muted-foreground">{t(lt("当前没有待审批项。", "No pending approvals."))}</div>
                        ) : (
                            rpaApprovals.map((approval) => (
                                <div key={approval.id} className="rounded-2xl border border-border/60 p-4">
                                    <div className="text-sm font-medium">{approval.request?.question || approval.request?.prompt || t(lt("RPA 审批", "RPA approval"))}</div>
                                    <div className="mt-2 flex flex-wrap gap-2">
                                        <Pill>{approval.approval_kind || "approval"}</Pill>
                                        {approval.run_id ? <Pill>Run {approval.run_id}</Pill> : null}
                                    </div>
                                    {approval.request?.rpa?.requiredApprovals?.length ? (
                                        <div className="mt-2 flex flex-wrap gap-2">
                                            {approval.request.rpa.requiredApprovals.map((item, index) => (
                                                <Pill key={`${approval.id}:${item.stepId || index}`}>
                                                    {item.stepId || item.use || "step"} · {item.mode || "review"}
                                                    {item.confidence != null ? ` · ${formatConfidence(item.confidence)}` : ""}
                                                </Pill>
                                            ))}
                                        </div>
                                    ) : null}
                                    <Textarea
                                        className="mt-3 min-h-[96px] font-mono text-xs"
                                        placeholder={t(lt("输入说明、变量或拒绝原因。", "Add notes, variables, or a rejection reason."))}
                                        value={approvalDrafts[approval.id] || ""}
                                        onChange={(event) => setApprovalDrafts((current) => ({ ...current, [approval.id]: event.target.value }))}
                                    />
                                    <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                                        <Button variant="outline" onClick={() => void handleApproval(approval.id, false)} disabled={busy?.startsWith("approval:")} className="w-full sm:w-auto">
                                            {t(lt("拒绝", "Reject"))}
                                        </Button>
                                        <Button onClick={() => void handleApproval(approval.id, true)} disabled={busy?.startsWith("approval:")} className="w-full sm:w-auto">
                                            {t(lt("批准继续", "Approve"))}
                                        </Button>
                                    </div>
                                </div>
                            ))
                        )}
                    </CardContent>
                </Card>

                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle>{t(lt("最近运行", "Recent runs"))}</CardTitle>
                        <CardDescription>{t(lt("最近的流程运行。", "Latest workflow runs."))}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        {rpaRuns.length === 0 ? (
                            <div className="rounded-xl border border-dashed p-5 text-sm text-muted-foreground">{t(lt("暂无运行记录。", "No runs yet."))}</div>
                        ) : (
                            rpaRuns.map((run) => {
                                const scriptName = readRunScriptName(run.metadata);
                                const executionState = readRunExecutionState(run.metadata);
                                const assessment = readRunAssessment(run.metadata);
                                const fallback = readRunFallback(run.metadata);
                                const robotFile =
                                    typeof run.metadata?.robotFile === "string" && run.metadata.robotFile.trim()
                                        ? run.metadata.robotFile
                                        : null;
                                return (
                                    <div key={run.id} className="rounded-2xl border border-border/60 p-4">
                                        <div className="flex flex-wrap gap-2">
                                            <Pill>{run.status || "queued"}</Pill>
                                            {run.metadata?.mode ? <Pill>{String(run.metadata.mode)}</Pill> : null}
                                            {executionState ? <Pill>{executionState}</Pill> : null}
                                            {fallback?.type ? <Pill>fallback:{fallback.type}</Pill> : null}
                                        </div>
                                        <div className="mt-3 space-y-1 text-sm text-muted-foreground">
                                            <div>{t(lt("运行编号：", "Run:"))}<span className="text-foreground">{run.id}</span></div>
                                            {run.session_id ? <div>{t(lt("会话：", "Session:"))}<span className="text-foreground">{run.session_id}</span></div> : null}
                                            {scriptName ? <div>{t(lt("流程：", "Flow:"))}<span className="text-foreground">{scriptName}</span></div> : null}
                                            {robotFile ? <div>Robot: <span className="break-all text-foreground">{robotFile}</span></div> : null}
                                            {assessment ? <div>{t(lt("状态：", "Status:"))}<span className="text-foreground">{assessment.status || "unknown"}{assessment.band ? ` · ${assessment.band}` : ""} · {formatConfidence(assessment.score)}</span></div> : null}
                                            {assessment ? <div>{t(lt("步骤概况：", "Step summary:"))}<span className="text-foreground">{t(lt(`可直接用 ${assessment.acceptedSteps ?? 0} · 需确认 ${assessment.reviewRequiredSteps ?? 0} · 已排除 ${assessment.excludedSteps ?? 0}`, `Ready ${assessment.acceptedSteps ?? 0} · Review ${assessment.reviewRequiredSteps ?? 0} · Excluded ${assessment.excludedSteps ?? 0}`))}</span></div> : null}
                                            {assessment?.signals?.historicalScriptRuns ? <div>{t(lt("历史执行：", "History:"))}<span className="text-foreground">{assessment.signals.historicalScriptRuns ?? 0}</span> {t(lt("次", "runs"))} · {t(lt("成功率", "success"))} <span className="text-foreground">{formatRatio(assessment.signals.historicalScriptCompletedRate)}</span></div> : null}
                                            {fallback?.sourceTraceRunId ? <div>{t(lt("回退来源：", "Fallback source:"))}<span className="text-foreground">{fallback.sourceTraceRunId}</span></div> : null}
                                            {(assessment?.signals || fallback?.type) ? (
                                                <details className="pt-1 text-xs text-muted-foreground">
                                                    <summary className="cursor-pointer">{t(lt("更多", "More"))}</summary>
                                                    <div className="space-y-1 pt-2">
                                                        {assessment?.signals ? <div>{t(lt(`接受比例 ${formatRatio(assessment.signals.acceptedRatio)} · 原生比例 ${formatRatio(assessment.signals.nativeSemanticRatio)} · 回退偏重 ${formatRatio(assessment.signals.recoveryHeavyRatio)} · 画像补充 ${formatRatio(assessment.signals.profileAugmentedRatio)}`, `Accepted ${formatRatio(assessment.signals.acceptedRatio)} · Native ${formatRatio(assessment.signals.nativeSemanticRatio)} · Fallback-heavy ${formatRatio(assessment.signals.recoveryHeavyRatio)} · Profile ${formatRatio(assessment.signals.profileAugmentedRatio)}`))}</div> : null}
                                                        {assessment?.signals ? <div>{t(lt(`校准来源 ${t(formatCalibrationSource(assessment.signals.historicalScriptCalibrationSource))}`, `Calibration ${t(formatCalibrationSource(assessment.signals.historicalScriptCalibrationSource))}`))}</div> : null}
                                                        {fallback?.type ? <div>{t(lt(`回退方式 ${fallback.type}`, `Fallback ${fallback.type}`))}</div> : null}
                                                    </div>
                                                </details>
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
