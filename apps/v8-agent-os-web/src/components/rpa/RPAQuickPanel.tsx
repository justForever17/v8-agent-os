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
        return "web.generated.bb35d5d1f2";
    }
    if (detail.importable) {
        return "web.generated.833b7329b7";
    }
    return "web.generated.bb4f665cd6";
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
        return "web.generated.f232e86b7b";
    }
    if (source === "script") {
        return "web.generated.6076b54ef6";
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
    const [latestResult, setLatestResult] = useState<string>(t("web.generated.cf39962c4a"));

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
                        error: error instanceof Error ? error.message : t("web.generated.16cbe6ae4f"),
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
                            {t("web.generated.dae8bf43e5")}
                        </Link>
                    </Button>
                        <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-muted-foreground">
                            {t("web.generated.6ee7a4c326")}
                        </div>
                        <h1 className="mt-1 flex items-center gap-2 text-2xl font-semibold tracking-tight sm:text-3xl">
                            <Workflow className="h-6 w-6 text-primary sm:h-7 sm:w-7" />
                            {t("web.generated.6ee7a4c326")}
                        </h1>
                        <p className="mt-2 text-sm leading-6 text-muted-foreground">{t("web.generated.b47e83fc5c")}</p>
                    </div>
                    <Button variant="outline" onClick={() => void loadDrafts()} disabled={loading || !!busy} className="w-full sm:w-auto">
                    <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
                    {t("web.generated.140abb8251")}
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
                        <CardTitle className="text-base">{t("web.generated.72258b359c")}</CardTitle>
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
                        <CardTitle>{t("web.generated.9e71458ff3")}</CardTitle>
                        <CardDescription>{t("web.generated.72a0059f9a")}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="grid gap-2">
                            <Label htmlFor="web-rpa-run-id">run_id</Label>
                            <Input id="web-rpa-run-id" value={compileRunId} onChange={(event) => setCompileRunId(event.target.value)} placeholder={t("web.generated.dddbbf00d1")} />
                        </div>
                        <Button
                            onClick={() => {
                                const runIds = parseRunIdsInput(compileRunId);
                                if (runIds.length === 0) {
                                    setLatestResult(JSON.stringify({ error: t("web.generated.4481ecfde4") }, null, 2));
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
                            {t("web.generated.21bfc9076f")}
                        </Button>
                    </CardContent>
                </Card>

                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle>{t("web.generated.7226bd0492")}</CardTitle>
                        <CardDescription>{t("web.generated.b90b079d6a")}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="grid gap-2">
                            <Label htmlFor="web-rpa-existing">{t("web.generated.4676c7194e")}</Label>
                            <Input id="web-rpa-existing" value={existingRobotFile} onChange={(event) => setExistingRobotFile(event.target.value)} placeholder={t("web.generated.bcedafab96")} />
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
                            {t("web.generated.e7cde6530a")}
                        </Button>
                    </CardContent>
                </Card>
            </div>

            <div className="mt-5 grid gap-4 xl:grid-cols-[1.05fr_1fr]">
                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle>{t("web.generated.b28c51e928")}</CardTitle>
                        <CardDescription>{t("web.generated.0e73dcfad4")}</CardDescription>
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
                                            <Pill>{t("web.generated.c25e46f3e2", { value0: (draft.steps || []).length })}</Pill>
                                            <Pill>{t("web.generated.8f392fe27e", { value0: (draft.variables || []).length })}</Pill>
                                            {draft.assessment?.status ? <Pill>{draft.assessment.status}{draft.assessment.band ? ` · ${draft.assessment.band}` : ""}</Pill> : null}
                                            {draft.assessment?.score != null ? <Pill>{formatConfidence(draft.assessment.score)}</Pill> : null}
                                        </div>
                                        {draft.assessment?.reasons?.length ? (
                                            <div className="mt-2 text-xs text-muted-foreground">{draft.assessment.reasons[0]}</div>
                                        ) : null}
                                        {draft.assessment ? (
                                            <div className="mt-2 text-[11px] text-muted-foreground">
                                                {t("web.generated.44031648bb", { value0: draft.assessment.acceptedSteps ?? 0, value1: draft.assessment.reviewRequiredSteps ?? 0, value2: draft.assessment.excludedSteps ?? 0 })}
                                            </div>
                                        ) : null}
                                        {draft.assessment?.signals?.historicalScriptRuns ? (
                                            <div className="mt-1 text-[11px] text-muted-foreground">
                                                {t("web.generated.b64c32846b", { value0: draft.assessment.signals.historicalScriptRuns, value1: formatRatio(draft.assessment.signals.historicalScriptCompletedRate) })}
                                            </div>
                                        ) : null}
                                    </button>
                                ))}
                                {drafts.length === 0 ? (
                                    <div className="rounded-xl border border-dashed p-5 text-sm text-muted-foreground">{t("web.generated.4ad3f4e58c")}</div>
                                ) : null}
                            </div>
                        </ScrollArea>
                    </CardContent>
                </Card>

                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle>{t("web.generated.4278008367")}</CardTitle>
                        <CardDescription>{selectedDraft ? `${selectedDraft.name || selectedDraft.id}` : t("web.generated.f5fe95dc9c")}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        {selectedDraft?.assessment ? (
                            <div className="rounded-xl border border-border/60 bg-muted/20 p-3 text-xs text-muted-foreground">
                                <div className="text-foreground">
                                    {t("web.generated.bbb70eb4ec", { value0: selectedDraft.assessment.status || "unknown", value1: selectedDraft.assessment.band ? ` · ${selectedDraft.assessment.band}` : "", value2: formatConfidence(selectedDraft.assessment.score) })}
                                </div>
                                <div className="pt-1">
                                    {t("web.generated.44031648bb", { value0: selectedDraft.assessment.acceptedSteps ?? 0, value1: selectedDraft.assessment.reviewRequiredSteps ?? 0, value2: selectedDraft.assessment.excludedSteps ?? 0 })}
                                </div>
                                {selectedDraft.assessment?.signals?.historicalScriptRuns ? (
                                    <div className="pt-1">
                                        {t("web.generated.b64c32846b", { value0: selectedDraft.assessment.signals.historicalScriptRuns ?? 0, value1: formatRatio(selectedDraft.assessment.signals.historicalScriptCompletedRate) })}
                                    </div>
                                ) : null}
                                {(selectedDraft.assessment.signals || selectedDraft.assessment.trustModel) ? (
                                    <details className="pt-2">
                                        <summary className="cursor-pointer text-xs text-muted-foreground">{t("web.generated.fbfbeacd32")}</summary>
                                        <div className="space-y-1 pt-2 text-[11px] text-muted-foreground">
                                            {selectedDraft.assessment.signals ? (
                                                <div>
                                                    {t("web.generated.3ed2a7f377", { value0: formatRatio(selectedDraft.assessment.signals.acceptedRatio), value1: formatRatio(selectedDraft.assessment.signals.nativeSemanticRatio), value2: formatRatio(selectedDraft.assessment.signals.recoveryHeavyRatio), value3: formatRatio(selectedDraft.assessment.signals.profileAugmentedRatio) })}
                                                </div>
                                            ) : null}
                                            {selectedDraft.assessment.signals ? (
                                                <div>
                                                    {t("web.generated.eb040ba7a1", { value0: formatRatio(selectedDraft.assessment.signals.historicalScriptReviewRequiredRate), value1: formatRatio(selectedDraft.assessment.signals.historicalScriptCompileBlockedRate), value2: t(formatCalibrationSource(selectedDraft.assessment.signals.historicalScriptCalibrationSource)) })}
                                                </div>
                                            ) : null}
                                            {selectedDraft.assessment.signals ? (
                                                <div>
                                                    {t("web.generated.b32f595a92", { value0: selectedDraft.assessment.signals.calibratedSteps ?? 0, value1: selectedDraft.assessment.signals.profileAugmentedSteps ?? 0, value2: formatRatio(selectedDraft.assessment.signals.historicalScriptNativeSuccessRate ?? selectedDraft.assessment.signals.historicalNativeSuccessRate) })}
                                                </div>
                                            ) : null}
                                            {selectedDraft.assessment.trustModel ? (
                                                <div>
                                                    {t("web.generated.4b35e1e7b3", { value0: formatRatio(selectedDraft.assessment.trustModel.effectiveScriptTrustedThreshold), value1: formatRatio(selectedDraft.assessment.trustModel.effectiveScriptReviewThreshold), value2: formatRatio(selectedDraft.assessment.trustModel.effectiveScriptFallbackHeavyThreshold) })}
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
                                        {t("web.generated.bdd698902f", { value0: selectedDraft.source.traceRunIds.length })}
                                    </div>
                                ) : selectedDraft.source?.traceRunId ? (
                                    <div className="pt-1">
                                        {t("web.generated.333fafadaa", { value0: selectedDraft.source.traceRunId })}
                                    </div>
                                ) : null}
                            </div>
                        ) : null}
                        <div className="grid gap-2">
                            <Label htmlFor="web-rpa-vars">{t("web.generated.dafd4dc560")}</Label>
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
                            {t("web.generated.737489f9e4")}
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
                            {t("web.generated.4278008367")}
                        </Button>
                    </CardContent>
                </Card>
            </div>

            <Card className="mt-5 border-border/60">
                <CardHeader>
                    <CardTitle>{t("web.generated.df9aef0438")}</CardTitle>
                </CardHeader>
                <CardContent>
                    <pre className="max-h-[220px] overflow-auto rounded-xl bg-muted/30 p-4 text-xs leading-6 sm:max-h-[320px]">{latestResult}</pre>
                </CardContent>
            </Card>

            <div className="mt-5 grid gap-4 xl:grid-cols-2">
                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle>{t("web.generated.6c0dc74213")}</CardTitle>
                        <CardDescription>{t("web.generated.c54c890699")}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        {rpaApprovals.length === 0 ? (
                            <div className="rounded-xl border border-dashed p-5 text-sm text-muted-foreground">{t("web.generated.98b8b825cd")}</div>
                        ) : (
                            rpaApprovals.map((approval) => (
                                <div key={approval.id} className="rounded-2xl border border-border/60 p-4">
                                    <div className="text-sm font-medium">{approval.request?.question || approval.request?.prompt || t("web.generated.a2a4e2853c")}</div>
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
                                        placeholder={t("web.generated.6a278e70f3")}
                                        value={approvalDrafts[approval.id] || ""}
                                        onChange={(event) => setApprovalDrafts((current) => ({ ...current, [approval.id]: event.target.value }))}
                                    />
                                    <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                                        <Button variant="outline" onClick={() => void handleApproval(approval.id, false)} disabled={busy?.startsWith("approval:")} className="w-full sm:w-auto">
                                            {t("web.generated.39589b7736")}
                                        </Button>
                                        <Button onClick={() => void handleApproval(approval.id, true)} disabled={busy?.startsWith("approval:")} className="w-full sm:w-auto">
                                            {t("web.generated.9dbb7128dd")}
                                        </Button>
                                    </div>
                                </div>
                            ))
                        )}
                    </CardContent>
                </Card>

                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle>{t("web.generated.69318463ef")}</CardTitle>
                        <CardDescription>{t("web.generated.140ff374e7")}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        {rpaRuns.length === 0 ? (
                            <div className="rounded-xl border border-dashed p-5 text-sm text-muted-foreground">{t("web.generated.f0999f1b95")}</div>
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
                                            <div>{t("web.generated.38ff3830d5")}<span className="text-foreground">{run.id}</span></div>
                                            {run.session_id ? <div>{t("web.generated.1964ff8136")}<span className="text-foreground">{run.session_id}</span></div> : null}
                                            {scriptName ? <div>{t("web.generated.25c060623e")}<span className="text-foreground">{scriptName}</span></div> : null}
                                            {robotFile ? <div>Robot: <span className="break-all text-foreground">{robotFile}</span></div> : null}
                                            {assessment ? <div>{t("web.generated.b93dbabff8")}<span className="text-foreground">{assessment.status || "unknown"}{assessment.band ? ` · ${assessment.band}` : ""} · {formatConfidence(assessment.score)}</span></div> : null}
                                            {assessment ? <div>{t("web.generated.e22b9f4477")}<span className="text-foreground">{t("web.generated.4379db286f", { value0: assessment.acceptedSteps ?? 0, value1: assessment.reviewRequiredSteps ?? 0, value2: assessment.excludedSteps ?? 0 })}</span></div> : null}
                                            {assessment?.signals?.historicalScriptRuns ? <div>{t("web.generated.373b8a18ef")}<span className="text-foreground">{assessment.signals.historicalScriptRuns ?? 0}</span> {t("web.generated.58392891f7")} · {t("web.generated.7439b120ef")} <span className="text-foreground">{formatRatio(assessment.signals.historicalScriptCompletedRate)}</span></div> : null}
                                            {fallback?.sourceTraceRunId ? <div>{t("web.generated.bf38950783")}<span className="text-foreground">{fallback.sourceTraceRunId}</span></div> : null}
                                            {(assessment?.signals || fallback?.type) ? (
                                                <details className="pt-1 text-xs text-muted-foreground">
                                                    <summary className="cursor-pointer">{t("web.generated.fbfbeacd32")}</summary>
                                                    <div className="space-y-1 pt-2">
                                                        {assessment?.signals ? <div>{t("web.generated.3ed2a7f377", { value0: formatRatio(assessment.signals.acceptedRatio), value1: formatRatio(assessment.signals.nativeSemanticRatio), value2: formatRatio(assessment.signals.recoveryHeavyRatio), value3: formatRatio(assessment.signals.profileAugmentedRatio) })}</div> : null}
                                                        {assessment?.signals ? <div>{t("web.generated.860913e029", { value0: t(formatCalibrationSource(assessment.signals.historicalScriptCalibrationSource)) })}</div> : null}
                                                        {fallback?.type ? <div>{t("web.generated.72c7cb5f30", { value0: fallback.type })}</div> : null}
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
