"use client";
import { AlertCircle, FolderTree } from "lucide-react";
import { useLocale, useT } from "@/components/providers/LocaleProvider";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TechnicalReferenceDetails } from "@/components/common/TechnicalReferenceDetails";
interface ExtractionRun {
    runId?: string;
    sessionId?: string;
    status?: string;
    startedAt?: string;
    finishedAt?: string;
    extractorModel?: string;
    extractionFailureStage?: string | null;
    extractionFailureReason?: string | null;
    skipReason?: string | null;
    extractionMode?: string | null;
    transcriptSource?: string | null;
    rawOutputPreview?: string | null;
    parserErrorPreview?: string | null;
    summary?: string | null;
    resolvedScope?: string | null;
    effectiveMemoryScope?: string | null;
    memoryPolicy?: string | null;
    noPersistedMemoryReason?: string | null;
    extractedPreferenceCount?: number;
    extractedKnowledgeCount?: number;
    persistedPreferenceCount?: number;
    persistedKnowledgeCount?: number;
    persistedRelationCount?: number;
    invocationError?: string | null;
}
interface MaintenanceRun {
    runId?: string;
    status?: string;
    startedAt?: string;
    finishedAt?: string;
    summaryMissingCountBefore?: number;
    summaryMissingCountAfter?: number;
    summaryBackfilledCount?: number;
    summaryStaleCountBefore?: number;
    summaryStaleCountAfter?: number;
    knowledgeCandidateCount?: number;
    knowledgeSupersededCount?: number;
    knowledgeMergeSuggestionCount?: number;
    graphPrunedIsolatedEntityCount?: number;
    touchedRefs?: string[];
}
type MemoryRuntimeDiagnosticsData = {
    extractions?: {
        summary?: Record<string, number>;
        recent?: ExtractionRun[];
    };
    memoryMap?: {
        counts?: Record<string, number>;
        missingRefs?: string[];
        staleRefs?: string[];
    };
    maintenance?: {
        summary?: Record<string, number>;
        recent?: MaintenanceRun[];
    };
};
function formatRelativeTimestamp(value: string | null | undefined, locale: "zh-CN" | "en-US") {
    if (!value)
        return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime()))
        return value;
    return date.toLocaleString(locale, {
        hour12: false,
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
    });
}
function formatExtractionOutcome(run: ExtractionRun, t: ReturnType<typeof useT>) {
    if ((run.status || "").toLowerCase() === "skipped" || run.skipReason) {
        const labels: Record<string, string> = {
            duplicate_transcript: t("components.memory.MemoryRuntimeDiagnosticsPanel.k345cfd85"),
            duplicate_increment: t("components.memory.MemoryRuntimeDiagnosticsPanel.kbfb62149"),
            no_semantic_content: t("components.memory.MemoryRuntimeDiagnosticsPanel.k185a5d81"),
            no_messages: t("components.memory.MemoryRuntimeDiagnosticsPanel.k294be171"),
            no_user_message: t("components.memory.MemoryRuntimeDiagnosticsPanel.ke1b42880"),
        };
        const skipKey = run.skipReason || run.extractionMode || "";
        return {
            title: labels[skipKey] || t("components.memory.MemoryRuntimeDiagnosticsPanel.kdb68ad90"),
            tone: "border-border bg-muted/40 text-muted-foreground",
            detail: t("components.memory.MemoryRuntimeDiagnosticsPanel.kafd1f2f4"),
        };
    }
    if (run.extractionFailureStage) {
        const labels: Record<string, string> = {
            extractor_config_missing: t("components.memory.MemoryRuntimeDiagnosticsPanel.k0ca1b416"),
            llm_response_empty: t("components.memory.MemoryRuntimeDiagnosticsPanel.k1dae71f7"),
            parser_failed: t("components.memory.MemoryRuntimeDiagnosticsPanel.kffb955f7"),
            repair_parser_failed: t("components.memory.MemoryRuntimeDiagnosticsPanel.k33d9b538"),
            llm_invoke_failed: t("components.memory.MemoryRuntimeDiagnosticsPanel.kf5fe5253"),
        };
        return {
            title: labels[run.extractionFailureStage] || run.extractionFailureStage,
            tone: "bg-red-500/10 text-red-600 border-red-500/20",
            detail: run.extractionFailureReason || run.invocationError || t("components.memory.MemoryRuntimeDiagnosticsPanel.k5177baaa"),
        };
    }
    if (run.noPersistedMemoryReason === "policy_filtered") {
        return {
            title: t("components.memory.MemoryRuntimeDiagnosticsPanel.k9715cf4c"),
            tone: "bg-amber-500/10 text-amber-700 border-amber-500/20",
            detail: t("components.memory.MemoryRuntimeDiagnosticsPanel.kccb7958d"),
        };
    }
    if ((run.persistedKnowledgeCount || 0) > 0 || (run.persistedPreferenceCount || 0) > 0) {
        return {
            title: t("components.memory.MemoryRuntimeDiagnosticsPanel.ka14d1335"),
            tone: "bg-emerald-500/10 text-emerald-700 border-emerald-500/20",
            detail: t("components.memory.MemoryRuntimeDiagnosticsPanel.kd26966c2"),
        };
    }
    if ((run.extractedKnowledgeCount || 0) > 0 || (run.extractedPreferenceCount || 0) > 0) {
        return {
            title: t("components.memory.MemoryRuntimeDiagnosticsPanel.k69e27a10"),
            tone: "bg-sky-500/10 text-sky-700 border-sky-500/20",
            detail: t("components.memory.MemoryRuntimeDiagnosticsPanel.kd2ed21b7"),
        };
    }
    return {
        title: t("components.memory.MemoryRuntimeDiagnosticsPanel.k3ce2ee86"),
        tone: "bg-muted text-muted-foreground border-border/60",
        detail: t("components.memory.MemoryRuntimeDiagnosticsPanel.k2b009000"),
    };
}
export default function MemoryRuntimeDiagnosticsPanel({ data }: {
    data: MemoryRuntimeDiagnosticsData;
}) {
    const { locale } = useLocale();
    const t = useT();
    const uiLocale = locale.startsWith("en") ? "en-US" : "zh-CN";
    const extractionSummary = data?.extractions?.summary || {};
    const recentExtractions = (data?.extractions?.recent || []) as ExtractionRun[];
    const memoryMapHealth = data?.memoryMap || {};
    const maintenanceSummary = data?.maintenance?.summary || {};
    const recentMaintenanceRuns = (data?.maintenance?.recent || []) as MaintenanceRun[];
    return (<div className="space-y-6">
            <Card className="border-border/60">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-lg">
                        <AlertCircle className="h-5 w-5 text-primary"/>
                        {t("components.memory.MemoryRuntimeDiagnosticsPanel.kf0f767f4")}
                    </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
                        {[
            [t("components.memory.MemoryRuntimeDiagnosticsPanel.kc69483d7"), extractionSummary.completed || 0],
            [t("components.memory.MemoryRuntimeDiagnosticsPanel.kdb68ad90"), extractionSummary.skipped || 0],
            [t("components.memory.MemoryRuntimeDiagnosticsPanel.ka14d1335"), extractionSummary.persisted || 0],
            [t("components.memory.MemoryRuntimeDiagnosticsPanel.k7284320c"), extractionSummary.policyFiltered || 0],
            [t("components.memory.MemoryRuntimeDiagnosticsPanel.k4e8fedf6"), extractionSummary.llmResponseEmpty || 0],
            [t("components.memory.MemoryRuntimeDiagnosticsPanel.ka2fd8dee"), (extractionSummary.parserFailed || 0) + (extractionSummary.repairParserFailed || 0)],
            [t("components.memory.MemoryRuntimeDiagnosticsPanel.k899b16d3"), extractionSummary.llmInvokeFailed || 0],
            [t("components.memory.MemoryRuntimeDiagnosticsPanel.k2f3c7f5d"), extractionSummary.duplicateTranscript || 0],
            [t("components.memory.MemoryRuntimeDiagnosticsPanel.k78b2d5ad"), extractionSummary.extractorConfigMissing || 0],
            [t("components.memory.MemoryRuntimeDiagnosticsPanel.k1c78a3af"), extractionSummary.noSemanticContent || 0],
        ].map(([label, value]) => (<div key={String(label)} className="rounded-xl border bg-muted/20 p-3">
                                <p className="text-xs text-muted-foreground">{label}</p>
                                <p className="mt-2 text-2xl font-semibold">{value}</p>
                            </div>))}
                    </div>

                    {recentExtractions.length > 0 ? (<div className="max-h-[560px] space-y-3 overflow-y-auto pr-1">
                            {recentExtractions.map((run) => {
                const outcome = formatExtractionOutcome(run, t);
                return (<div key={run.runId || `${run.sessionId}-${run.startedAt}`} className="rounded-xl border bg-background p-4">
                                        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                                            <div className="min-w-0 flex-1 space-y-2">
                                                <div className="flex flex-wrap items-center gap-2">
                                                    <span className={`rounded-full border px-2.5 py-1 text-xs font-medium ${outcome.tone}`}>
                                                        {outcome.title}
                                                    </span>
                                                </div>
                                                <p className="text-sm text-foreground">{outcome.detail}</p>
                                                {run.summary ? (<p className="rounded-lg bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
                                                        {run.summary}
                                                    </p>) : null}
                                                <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                                                    <span>{t("components.memory.MemoryRuntimeDiagnosticsPanel.k93e56ead")}：{formatRelativeTimestamp(run.startedAt, uiLocale)}</span>
                                                    <span>{t("components.memory.MemoryRuntimeDiagnosticsPanel.kb854ae58")}：{formatRelativeTimestamp(run.finishedAt, uiLocale)}</span>
                                                    <span>{t("components.memory.MemoryRuntimeDiagnosticsPanel.kca695f8f")}：{run.extractorModel || "—"}</span>
                                                    <span>{t("components.memory.MemoryRuntimeDiagnosticsPanel.scope")}：{run.effectiveMemoryScope || run.resolvedScope || "—"}</span>
                                                    <span>{t("components.memory.MemoryRuntimeDiagnosticsPanel.policy")}：{run.memoryPolicy || "—"}</span>
                                                    <span>{t("components.memory.MemoryRuntimeDiagnosticsPanel.kc6cf9db6")}：{run.extractionMode || "—"}</span>
                                                    <span>{t("components.memory.MemoryRuntimeDiagnosticsPanel.transcript")}：{run.transcriptSource || "—"}</span>
                                                </div>
                                                <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                                                    <span>{t("components.memory.MemoryRuntimeDiagnosticsPanel.k9cbe8e03")}：{run.extractedPreferenceCount || 0}</span>
                                                    <span>{t("components.memory.MemoryRuntimeDiagnosticsPanel.ka575f4d3")}：{run.extractedKnowledgeCount || 0}</span>
                                                    <span>{t("components.memory.MemoryRuntimeDiagnosticsPanel.k16b0462c")}：{run.persistedPreferenceCount || 0}</span>
                                                    <span>{t("components.memory.MemoryRuntimeDiagnosticsPanel.kc4e2d04d")}：{run.persistedKnowledgeCount || 0}</span>
                                                    <span>{t("components.memory.MemoryRuntimeDiagnosticsPanel.k1d64a1e6")}：{run.persistedRelationCount || 0}</span>
                                                </div>
                                                <TechnicalReferenceDetails items={[
                                                    { label: t("components.common.sessionReference"), value: run.sessionId },
                                                    { label: t("components.common.runReference"), value: run.runId },
                                                ]} />
                                                {(run.rawOutputPreview || run.parserErrorPreview || run.invocationError) ? (<div className="grid gap-2 xl:grid-cols-3">
                                                        {run.rawOutputPreview ? (<div className="rounded-lg border bg-muted/20 p-3">
                                                                <p className="mb-2 text-xs font-medium text-muted-foreground">{t("components.memory.MemoryRuntimeDiagnosticsPanel.k221baf08")}</p>
                                                                <pre className="overflow-x-auto whitespace-pre-wrap break-all font-mono text-xs leading-relaxed">{run.rawOutputPreview}</pre>
                                                            </div>) : null}
                                                        {run.parserErrorPreview ? (<div className="rounded-lg border bg-muted/20 p-3">
                                                                <p className="mb-2 text-xs font-medium text-muted-foreground">{t("components.memory.MemoryRuntimeDiagnosticsPanel.k44439060")}</p>
                                                                <pre className="overflow-x-auto whitespace-pre-wrap break-all font-mono text-xs leading-relaxed">{run.parserErrorPreview}</pre>
                                                            </div>) : null}
                                                        {run.invocationError ? (<div className="rounded-lg border bg-muted/20 p-3">
                                                                <p className="mb-2 text-xs font-medium text-muted-foreground">{t("components.memory.MemoryRuntimeDiagnosticsPanel.kcbd61b9a")}</p>
                                                                <pre className="overflow-x-auto whitespace-pre-wrap break-all font-mono text-xs leading-relaxed">{run.invocationError}</pre>
                                                            </div>) : null}
                                                    </div>) : null}
                                            </div>
                                        </div>
                                    </div>);
            })}
                        </div>) : (<div className="rounded-xl border border-dashed bg-muted/20 p-6 text-sm text-muted-foreground">
                            {t("components.memory.MemoryRuntimeDiagnosticsPanel.kff457764")}
                        </div>)}
                </CardContent>
            </Card>

            <Card className="border-border/60">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-lg">
                        <FolderTree className="h-5 w-5 text-primary"/>
                        {t("components.memory.MemoryRuntimeDiagnosticsPanel.kfecf45db")}
                    </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
                        {[
            [t("components.memory.MemoryRuntimeDiagnosticsPanel.k6edad83c"), memoryMapHealth?.counts?.year || 0],
            [t("components.memory.MemoryRuntimeDiagnosticsPanel.k14c25f84"), memoryMapHealth?.counts?.month || 0],
            [t("components.memory.MemoryRuntimeDiagnosticsPanel.k052857e0"), memoryMapHealth?.counts?.week || 0],
            [t("components.memory.MemoryRuntimeDiagnosticsPanel.k5db91adb"), memoryMapHealth?.counts?.day || 0],
            [t("components.memory.MemoryRuntimeDiagnosticsPanel.k74873285"), memoryMapHealth?.counts?.missing || 0],
            [t("components.memory.MemoryRuntimeDiagnosticsPanel.ked33029b"), memoryMapHealth?.counts?.stale || 0],
            [t("components.memory.MemoryRuntimeDiagnosticsPanel.k0ddb4572"), maintenanceSummary.summaryBackfilled || 0],
            [t("components.memory.MemoryRuntimeDiagnosticsPanel.knowledgeSuperseded"), maintenanceSummary.knowledgeSuperseded || 0],
            [t("components.memory.MemoryRuntimeDiagnosticsPanel.graphPrunedEntities"), maintenanceSummary.graphPrunedEntities || 0],
        ].map(([label, value]) => (<div key={String(label)} className="rounded-xl border bg-muted/20 p-3">
                                <p className="text-xs text-muted-foreground">{label}</p>
                                <p className="mt-2 text-2xl font-semibold">{value}</p>
                            </div>))}
                    </div>

                    <div className="grid gap-4 xl:grid-cols-2">
                        <div className="rounded-xl border bg-background p-4">
                            <div className="mb-3 text-sm font-medium">{t("components.memory.MemoryRuntimeDiagnosticsPanel.kf3e517a0")}</div>
                            {(memoryMapHealth?.missingRefs || []).length > 0 ? (<div className="max-h-[260px] space-y-2 overflow-y-auto pr-1">
                                    {(memoryMapHealth.missingRefs || []).map((ref: string) => (<div key={ref} className="rounded-lg bg-muted/30 px-3 py-2 font-mono text-xs text-muted-foreground">
                                            {ref}
                                        </div>))}
                                </div>) : (<div className="text-sm text-muted-foreground">{t("components.memory.MemoryRuntimeDiagnosticsPanel.kbb69ce20")}</div>)}
                        </div>

                        <div className="rounded-xl border bg-background p-4">
                            <div className="mb-3 text-sm font-medium">{t("components.memory.MemoryRuntimeDiagnosticsPanel.k952bd203")}</div>
                            {(memoryMapHealth?.staleRefs || []).length > 0 ? (<div className="max-h-[260px] space-y-2 overflow-y-auto pr-1">
                                    {(memoryMapHealth.staleRefs || []).map((ref: string) => (<div key={ref} className="rounded-lg bg-muted/30 px-3 py-2 font-mono text-xs text-muted-foreground">
                                            {ref}
                                        </div>))}
                                </div>) : (<div className="text-sm text-muted-foreground">{t("components.memory.MemoryRuntimeDiagnosticsPanel.k9904af6d")}</div>)}
                        </div>
                    </div>

                    {recentMaintenanceRuns.length > 0 ? (<div className="max-h-[420px] space-y-3 overflow-y-auto pr-1">
                            {recentMaintenanceRuns.map((run) => (<div key={run.runId || `${run.startedAt}`} className="rounded-xl border bg-background p-4">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <span className="rounded-full border border-border/60 px-2.5 py-1 text-xs font-medium">
                                            {run.status || t("components.memory.MemoryRuntimeDiagnosticsPanel.k76ebff7c")}
                                        </span>
                                        <span className="text-xs text-muted-foreground">{t("components.memory.MemoryRuntimeDiagnosticsPanel.k93e56ead")}：{formatRelativeTimestamp(run.startedAt, uiLocale)}</span>
                                        <span className="text-xs text-muted-foreground">{t("components.memory.MemoryRuntimeDiagnosticsPanel.kb854ae58")}：{formatRelativeTimestamp(run.finishedAt, uiLocale)}</span>
                                    </div>
                                    <TechnicalReferenceDetails className="mt-3" items={[
                                        { label: t("components.common.runReference"), value: run.runId },
                                    ]} />
                                    <div className="mt-3 flex flex-wrap gap-3 text-xs text-muted-foreground">
                                        <span>{t("components.memory.MemoryRuntimeDiagnosticsPanel.k12c76d3f")}：{run.summaryMissingCountBefore || 0}</span>
                                        <span>{t("components.memory.MemoryRuntimeDiagnosticsPanel.k7b552ca4")}：{run.summaryMissingCountAfter || 0}</span>
                                        <span>{t("components.memory.MemoryRuntimeDiagnosticsPanel.k530f0b61")}：{run.summaryStaleCountBefore || 0}</span>
                                        <span>{t("components.memory.MemoryRuntimeDiagnosticsPanel.ke02b302b")}：{run.summaryStaleCountAfter || 0}</span>
                                        <span>{t("components.memory.MemoryRuntimeDiagnosticsPanel.k484644f7")}：{run.summaryBackfilledCount || 0}</span>
                                        <span>{t("components.memory.MemoryRuntimeDiagnosticsPanel.knowledgeCandidates")}：{run.knowledgeCandidateCount || 0}</span>
                                        <span>{t("components.memory.MemoryRuntimeDiagnosticsPanel.knowledgeSuperseded")}：{run.knowledgeSupersededCount || 0}</span>
                                        <span>{t("components.memory.MemoryRuntimeDiagnosticsPanel.knowledgeMergeSuggestions")}：{run.knowledgeMergeSuggestionCount || 0}</span>
                                        <span>{t("components.memory.MemoryRuntimeDiagnosticsPanel.graphPrunedEntities")}：{run.graphPrunedIsolatedEntityCount || 0}</span>
                                    </div>
                                    {(run.touchedRefs || []).length > 0 ? (<div className="mt-3 space-y-2">
                                            {(run.touchedRefs || []).map((ref) => (<div key={ref} className="rounded-lg bg-muted/30 px-3 py-2 font-mono text-xs text-muted-foreground">
                                                    {ref}
                                                </div>))}
                                        </div>) : null}
                                </div>))}
                        </div>) : (<div className="text-sm text-muted-foreground">{t("components.memory.MemoryRuntimeDiagnosticsPanel.kd7044415")}</div>)}
                </CardContent>
            </Card>
        </div>);
}
