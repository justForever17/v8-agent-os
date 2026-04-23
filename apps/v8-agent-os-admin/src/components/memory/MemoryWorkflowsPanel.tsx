"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw, Save, ShieldAlert, Trash2 } from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/use-toast";

type WorkflowCandidate = {
    id: string;
    task_family?: string;
    taskFamily?: string;
    scope?: string;
    status?: string;
    maturity_score?: number;
    maturityScore?: number;
    success_count?: number;
    successCount?: number;
    correction_count?: number;
    correctionCount?: number;
    negative_feedback_count?: number;
    negativeFeedbackCount?: number;
    confidence?: number;
    canonicalTriggerPatterns?: string[];
    firstActionTriggers?: string[];
    goldenPathSteps?: string[];
    antiPatterns?: string[];
    verificationSteps?: string[];
    sourceEpisodeIds?: string[];
    riskTier?: string;
    risk_tier?: string;
    approvalRequired?: boolean;
    approval_required?: boolean;
    lastHintOutcome?: string;
    last_hint_outcome?: string;
    workflowClass?: string;
    sourceRuntime?: string;
    proofBacked?: boolean;
    verificationBacked?: boolean;
    lastVerificationStatus?: string;
    worksetRisk?: string;
    outsideWriteSetCount?: number;
    manualOverrideCount?: number;
    proofEntryIds?: string[];
    guideState?: Record<string, unknown>;
    updated_at?: string;
    updatedAt?: string;
    metadata?: Record<string, unknown>;
};

type WorkflowEpisode = {
    id: string;
    status?: string;
    task_family?: string;
    task_family_signature?: string;
    taskFamily?: string;
    failureMarkers?: string[];
    userCorrectionPoints?: string[];
    final_success_evidence?: string;
    finalSuccessEvidence?: string;
    workflowClass?: string;
    sourceRuntime?: string;
    verificationBacked?: boolean;
    worksetRisk?: string;
    proofRefs?: string[];
    metadata?: Record<string, unknown>;
    created_at?: string;
};

type WorkflowHintEvent = {
    id: string;
    outcome?: string;
    query?: string;
    created_at?: string;
    metadata?: Record<string, unknown>;
    injectedHint?: Record<string, unknown>;
};

const listToText = (items?: unknown[]) => (Array.isArray(items) ? items.map(String).join("\n") : "");
const textToList = (text: string) => text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);

const HINT_OUTCOME_ORDER = ["helped_success", "accepted", "ignored", "contradicted", "caused_failure", "injected"] as const;
const DELIVERY_MODE_ORDER = ["direct_guide", "planner_checklist_bias"] as const;

function toCountMap(value: unknown): Record<string, number> {
    if (!value || typeof value !== "object" || Array.isArray(value)) return {};
    return Object.entries(value as Record<string, unknown>).reduce<Record<string, number>>((acc, [key, raw]) => {
        const next = Number(raw ?? 0);
        if (Number.isFinite(next) && next > 0) acc[key] = next;
        return acc;
    }, {});
}

function summarizeHintTrend(candidate: WorkflowCandidate | null, hintEvents: WorkflowHintEvent[]) {
    const metadataCounts = toCountMap(candidate?.metadata?.hintOutcomeCounts);
    const eventCounts = hintEvents.reduce<Record<string, number>>((acc, event) => {
        const key = String(event.outcome || "injected").trim().toLowerCase() || "injected";
        acc[key] = (acc[key] || 0) + 1;
        return acc;
    }, {});
    const outcomeCounts = Object.keys(metadataCounts).length ? metadataCounts : eventCounts;
    const deliveryModeBreakdown = hintEvents.reduce<Record<string, number>>((acc, event) => {
        const mode = String(event.metadata?.deliveryMode || event.injectedHint?.deliveryMode || "").trim();
        if (!mode) return acc;
        acc[mode] = (acc[mode] || 0) + 1;
        return acc;
    }, {});
    const plannerAwareCount = hintEvents.reduce((acc, event) => {
        return event.metadata?.plannerAware || event.injectedHint?.plannerAware ? acc + 1 : acc;
    }, 0);
    return {
        outcomeCounts,
        deliveryModeBreakdown,
        plannerAwareCount,
        totalOutcomes: Object.values(outcomeCounts).reduce((sum, value) => sum + Number(value || 0), 0),
    };
}

function outcomeTone(outcome: string) {
    switch (outcome) {
        case "helped_success":
            return "bg-emerald-500";
        case "accepted":
            return "bg-green-400";
        case "ignored":
            return "bg-slate-400";
        case "contradicted":
            return "bg-amber-500";
        case "caused_failure":
            return "bg-rose-500";
        default:
            return "bg-sky-400";
    }
}

export default function MemoryWorkflowsPanel() {
    const t = useT();
    const { toast } = useToast();
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [query, setQuery] = useState("");
    const [workflowClassFilter, setWorkflowClassFilter] = useState("all");
    const [proofBackedFilter, setProofBackedFilter] = useState("all");
    const [verificationStatusFilter, setVerificationStatusFilter] = useState("all");
    const [sourceRuntimeFilter, setSourceRuntimeFilter] = useState("");
    const [candidates, setCandidates] = useState<WorkflowCandidate[]>([]);
    const [selectedId, setSelectedId] = useState<string>("");
    const [selected, setSelected] = useState<WorkflowCandidate | null>(null);
    const [episodes, setEpisodes] = useState<WorkflowEpisode[]>([]);
    const [hintEvents, setHintEvents] = useState<WorkflowHintEvent[]>([]);
    const [edit, setEdit] = useState({
        taskFamily: "",
        status: "candidate",
        canonicalTriggerPatterns: "",
        firstActionTriggers: "",
        goldenPathSteps: "",
        antiPatterns: "",
        verificationSteps: "",
    });

    const activeCandidate = useMemo(
        () => candidates.find((item) => item.id === selectedId) || candidates[0] || null,
        [candidates, selectedId],
    );
    const hintTrend = useMemo(
        () => summarizeHintTrend(selected, hintEvents),
        [selected, hintEvents],
    );

    const loadCandidates = useCallback(async () => {
        setLoading(true);
        try {
            const params = new URLSearchParams({ limit: "100" });
            if (query.trim()) params.set("q", query.trim());
            if (workflowClassFilter !== "all") params.set("class", workflowClassFilter);
            if (proofBackedFilter !== "all") params.set("proofBacked", proofBackedFilter === "true" ? "true" : "false");
            if (verificationStatusFilter !== "all") params.set("verificationStatus", verificationStatusFilter);
            if (sourceRuntimeFilter.trim()) params.set("sourceRuntime", sourceRuntimeFilter.trim());
            const res = await fetch(`/api/memory/workflows?${params.toString()}`, { cache: "no-store" });
            if (!res.ok) throw new Error(`Load failed: ${res.status}`);
            const payload = await res.json();
            const items = Array.isArray(payload.items) ? payload.items : [];
            setCandidates(items);
            if (!selectedId && items[0]?.id) setSelectedId(items[0].id);
        } catch (error) {
            toast({
                title: t("components.memory.MemoryWorkflowsPanel.loadFailed"),
                description: String(error),
                variant: "destructive",
            });
        } finally {
            setLoading(false);
        }
    }, [proofBackedFilter, query, selectedId, sourceRuntimeFilter, t, toast, verificationStatusFilter, workflowClassFilter]);

    const loadSelected = useCallback(async (id: string) => {
        if (!id) {
            setSelected(null);
            setEpisodes([]);
            setHintEvents([]);
            return;
        }
        try {
            const [candidateRes, episodesRes, hintsRes] = await Promise.all([
                fetch(`/api/memory/workflows/${encodeURIComponent(id)}`, { cache: "no-store" }),
                fetch(`/api/memory/workflows/${encodeURIComponent(id)}/episodes`, { cache: "no-store" }),
                fetch(`/api/memory/workflows/${encodeURIComponent(id)}/hint-events`, { cache: "no-store" }),
            ]);
            if (!candidateRes.ok) throw new Error(`Candidate failed: ${candidateRes.status}`);
            const candidate = await candidateRes.json();
            const episodesPayload = episodesRes.ok ? await episodesRes.json() : { items: [] };
            const hintsPayload = hintsRes.ok ? await hintsRes.json() : { items: [] };
            setSelected(candidate);
            setEpisodes(Array.isArray(episodesPayload.items) ? episodesPayload.items : []);
            setHintEvents(Array.isArray(hintsPayload.items) ? hintsPayload.items : []);
            setEdit({
                taskFamily: candidate.task_family || candidate.taskFamily || "",
                status: candidate.status || "candidate",
                canonicalTriggerPatterns: listToText(candidate.canonicalTriggerPatterns),
                firstActionTriggers: listToText(candidate.firstActionTriggers),
                goldenPathSteps: listToText(candidate.goldenPathSteps),
                antiPatterns: listToText(candidate.antiPatterns),
                verificationSteps: listToText(candidate.verificationSteps),
            });
        } catch (error) {
            toast({
                title: t("components.memory.MemoryWorkflowsPanel.loadDetailFailed"),
                description: String(error),
                variant: "destructive",
            });
        }
    }, [t, toast]);

    useEffect(() => {
        void loadCandidates();
    }, [loadCandidates]);

    useEffect(() => {
        const id = activeCandidate?.id || "";
        if (id && id !== selectedId) setSelectedId(id);
        if (id) void loadSelected(id);
    }, [activeCandidate?.id]);

    const saveSelected = useCallback(async (statusOverride?: string) => {
        if (!selected?.id) return;
        setSaving(true);
        try {
            const payload = {
                taskFamily: edit.taskFamily,
                status: statusOverride || edit.status,
                canonicalTriggerPatterns: textToList(edit.canonicalTriggerPatterns),
                firstActionTriggers: textToList(edit.firstActionTriggers),
                goldenPathSteps: textToList(edit.goldenPathSteps),
                antiPatterns: textToList(edit.antiPatterns),
                verificationSteps: textToList(edit.verificationSteps),
            };
            const res = await fetch(`/api/memory/workflows/${encodeURIComponent(selected.id)}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            if (!res.ok) throw new Error(`Save failed: ${res.status}`);
            toast({ title: t("components.memory.MemoryWorkflowsPanel.saved") });
            await loadCandidates();
            await loadSelected(selected.id);
        } catch (error) {
            toast({
                title: t("components.memory.MemoryWorkflowsPanel.saveFailed"),
                description: String(error),
                variant: "destructive",
            });
        } finally {
            setSaving(false);
        }
    }, [edit, loadCandidates, loadSelected, selected?.id, t, toast]);

    const deleteSelected = useCallback(async () => {
        if (!selected?.id || !window.confirm(t("components.memory.MemoryWorkflowsPanel.deleteConfirm"))) return;
        const id = selected.id;
        const res = await fetch(`/api/memory/workflows/${encodeURIComponent(id)}`, { method: "DELETE" });
        if (!res.ok) {
            toast({ title: t("components.memory.MemoryWorkflowsPanel.deleteFailed"), variant: "destructive" });
            return;
        }
        setSelectedId("");
        await loadCandidates();
    }, [loadCandidates, selected?.id, t, toast]);

    if (loading) {
        return (
            <div className="flex h-64 items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
        );
    }

    return (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[360px_1fr]">
            <Card className="border-border/60">
                <CardHeader>
                    <CardTitle>{t("components.memory.MemoryWorkflowsPanel.title")}</CardTitle>
                    <CardDescription>{t("components.memory.MemoryWorkflowsPanel.description")}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                    <div className="flex gap-2">
                        <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("components.memory.MemoryWorkflowsPanel.searchPlaceholder")} />
                        <Button variant="outline" onClick={() => void loadCandidates()}>
                            <RefreshCw className="h-4 w-4" />
                        </Button>
                    </div>
                    <div className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-2">
                        <Select value={workflowClassFilter} onValueChange={setWorkflowClassFilter}>
                            <SelectTrigger><SelectValue /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all">{t("components.memory.MemoryWorkflowsPanel.filter.allClasses")}</SelectItem>
                                <SelectItem value="general">{t("components.memory.MemoryWorkflowsPanel.filter.general")}</SelectItem>
                                <SelectItem value="engineering">{t("components.memory.MemoryWorkflowsPanel.filter.engineering")}</SelectItem>
                            </SelectContent>
                        </Select>
                        <Select value={proofBackedFilter} onValueChange={setProofBackedFilter}>
                            <SelectTrigger><SelectValue /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all">{t("components.memory.MemoryWorkflowsPanel.filter.allProof")}</SelectItem>
                                <SelectItem value="true">{t("components.memory.MemoryWorkflowsPanel.filter.proofBacked")}</SelectItem>
                                <SelectItem value="false">{t("components.memory.MemoryWorkflowsPanel.filter.notProofBacked")}</SelectItem>
                            </SelectContent>
                        </Select>
                        <Select value={verificationStatusFilter} onValueChange={setVerificationStatusFilter}>
                            <SelectTrigger><SelectValue /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all">{t("components.memory.MemoryWorkflowsPanel.filter.allVerification")}</SelectItem>
                                <SelectItem value="verified">verified</SelectItem>
                                <SelectItem value="unverified">unverified</SelectItem>
                                <SelectItem value="failed_verification">failed_verification</SelectItem>
                                <SelectItem value="planned">planned</SelectItem>
                            </SelectContent>
                        </Select>
                        <Input value={sourceRuntimeFilter} onChange={(event) => setSourceRuntimeFilter(event.target.value)} placeholder={t("components.memory.MemoryWorkflowsPanel.filter.sourceRuntime")} />
                    </div>
                    <div className="max-h-[640px] space-y-2 overflow-y-auto pr-1">
                        {candidates.length === 0 ? (
                            <div className="rounded-lg border border-dashed p-5 text-sm text-muted-foreground">
                                {t("components.memory.MemoryWorkflowsPanel.empty")}
                            </div>
                        ) : candidates.map((item) => {
                            const active = item.id === selected?.id;
                            return (
                                <button
                                    key={item.id}
                                    type="button"
                                    className={`w-full rounded-lg border p-3 text-left transition ${active ? "border-primary bg-primary/5" : "hover:bg-muted/40"}`}
                                    onClick={() => setSelectedId(item.id)}
                                >
                                    <div className="flex items-start justify-between gap-2">
                                        <div className="min-w-0">
                                            <div className="truncate text-sm font-semibold">{item.task_family || item.taskFamily || item.id}</div>
                                            <div className="mt-1 font-mono text-[11px] text-muted-foreground">{item.id}</div>
                                        </div>
                                        <Badge variant={item.status === "active_hint" || item.status === "approved" ? "default" : item.status === "quarantine" ? "destructive" : "secondary"}>
                                            {item.status || "candidate"}
                                        </Badge>
                                    </div>
                                    <div className="mt-3 grid grid-cols-3 gap-2 text-xs text-muted-foreground">
                                        <span>{t("components.memory.MemoryWorkflowsPanel.success")}: {item.success_count ?? item.successCount ?? 0}</span>
                                        <span>{t("components.memory.MemoryWorkflowsPanel.corrections")}: {item.correction_count ?? item.correctionCount ?? 0}</span>
                                        <span>{t("components.memory.MemoryWorkflowsPanel.maturity")}: {Number(item.maturity_score ?? item.maturityScore ?? 0).toFixed(2)}</span>
                                    </div>
                                    <div className="mt-2 flex flex-wrap gap-1">
                                        <Badge variant="outline">{t("components.memory.MemoryWorkflowsPanel.risk")}: {item.riskTier || item.risk_tier || "low"}</Badge>
                                        <Badge variant="outline">{item.workflowClass || "general"}</Badge>
                                        {item.proofBacked ? <Badge variant="secondary">proof</Badge> : null}
                                        {item.lastVerificationStatus ? <Badge variant="outline">{item.lastVerificationStatus}</Badge> : null}
                                        {(item.approvalRequired ?? item.approval_required) ? <Badge variant="secondary">{t("components.memory.MemoryWorkflowsPanel.approvalRequired")}</Badge> : null}
                                    </div>
                                </button>
                            );
                        })}
                    </div>
                </CardContent>
            </Card>

            <Card className="border-border/60">
                <CardHeader>
                    <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                            <CardTitle>{selected ? (selected.task_family || selected.taskFamily || selected.id) : t("components.memory.MemoryWorkflowsPanel.noSelection")}</CardTitle>
                            <CardDescription>{selected?.id}</CardDescription>
                        </div>
                        {selected ? (
                            <div className="flex flex-wrap gap-2">
                                <Button variant="outline" size="sm" onClick={() => void saveSelected("active_hint")} disabled={saving}>
                                    <CheckCircle2 className="mr-2 h-4 w-4" />
                                    {t("components.memory.MemoryWorkflowsPanel.approve")}
                                </Button>
                                <Button variant="outline" size="sm" onClick={() => void saveSelected("quarantine")} disabled={saving}>
                                    <ShieldAlert className="mr-2 h-4 w-4" />
                                    {t("components.memory.MemoryWorkflowsPanel.quarantine")}
                                </Button>
                                <Button size="sm" onClick={() => void saveSelected()} disabled={saving}>
                                    {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                                    {t("components.memory.MemoryWorkflowsPanel.save")}
                                </Button>
                                <Button variant="destructive" size="sm" onClick={() => void deleteSelected()}>
                                    <Trash2 className="mr-2 h-4 w-4" />
                                    {t("components.memory.MemoryWorkflowsPanel.delete")}
                                </Button>
                            </div>
                        ) : null}
                    </div>
                </CardHeader>
                <CardContent>
                    {!selected ? (
                        <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
                            {t("components.memory.MemoryWorkflowsPanel.noSelection")}
                        </div>
                    ) : (
                        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                            <div className="space-y-4">
                                <div className="grid grid-cols-2 gap-3">
                                    <div className="space-y-1.5">
                                        <Label>{t("components.memory.MemoryWorkflowsPanel.taskFamily")}</Label>
                                        <Input value={edit.taskFamily} onChange={(event) => setEdit((prev) => ({ ...prev, taskFamily: event.target.value }))} />
                                    </div>
                                    <div className="space-y-1.5">
                                        <Label>{t("components.memory.MemoryWorkflowsPanel.status")}</Label>
                                        <Input value={edit.status} onChange={(event) => setEdit((prev) => ({ ...prev, status: event.target.value }))} />
                                    </div>
                                </div>
                                <FieldTextarea label={t("components.memory.MemoryWorkflowsPanel.triggers")} value={edit.canonicalTriggerPatterns} onChange={(value) => setEdit((prev) => ({ ...prev, canonicalTriggerPatterns: value }))} />
                                <FieldTextarea label={t("components.memory.MemoryWorkflowsPanel.firstActions")} value={edit.firstActionTriggers} onChange={(value) => setEdit((prev) => ({ ...prev, firstActionTriggers: value }))} />
                                <FieldTextarea label={t("components.memory.MemoryWorkflowsPanel.goldenPath")} value={edit.goldenPathSteps} onChange={(value) => setEdit((prev) => ({ ...prev, goldenPathSteps: value }))} rows={7} />
                                <FieldTextarea label={t("components.memory.MemoryWorkflowsPanel.antiPatterns")} value={edit.antiPatterns} onChange={(value) => setEdit((prev) => ({ ...prev, antiPatterns: value }))} />
                                <FieldTextarea label={t("components.memory.MemoryWorkflowsPanel.verification")} value={edit.verificationSteps} onChange={(value) => setEdit((prev) => ({ ...prev, verificationSteps: value }))} />
                            </div>

                            <div className="space-y-4">
                                <div className="rounded-lg border p-4">
                                    <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
                                        <AlertTriangle className="h-4 w-4 text-amber-500" />
                                        {t("components.memory.MemoryWorkflowsPanel.evidence")}
                                    </div>
                                    <div className="grid grid-cols-2 gap-3 text-sm">
                                        <Metric label={t("components.memory.MemoryWorkflowsPanel.success")} value={selected.success_count ?? selected.successCount ?? 0} />
                                        <Metric label={t("components.memory.MemoryWorkflowsPanel.corrections")} value={selected.correction_count ?? selected.correctionCount ?? 0} />
                                        <Metric label={t("components.memory.MemoryWorkflowsPanel.negative")} value={selected.negative_feedback_count ?? selected.negativeFeedbackCount ?? 0} />
                                        <Metric label={t("components.memory.MemoryWorkflowsPanel.confidence")} value={Number(selected.confidence ?? 0).toFixed(2)} />
                                        <Metric label={t("components.memory.MemoryWorkflowsPanel.risk")} value={selected.riskTier || selected.risk_tier || "low"} />
                                        <Metric label={t("components.memory.MemoryWorkflowsPanel.lastOutcome")} value={selected.lastHintOutcome || selected.last_hint_outcome || "n/a"} />
                                        <Metric label={t("components.memory.MemoryWorkflowsPanel.workflowClass")} value={selected.workflowClass || "general"} />
                                        <Metric label={t("components.memory.MemoryWorkflowsPanel.verificationStatus")} value={selected.lastVerificationStatus || "n/a"} />
                                        <Metric label={t("components.memory.MemoryWorkflowsPanel.worksetRisk")} value={selected.worksetRisk || "n/a"} />
                                        <Metric label={t("components.memory.MemoryWorkflowsPanel.proofRefs")} value={selected.proofEntryIds?.length || 0} />
                                    </div>
                                    <div className="mt-4 space-y-3 rounded-md bg-muted/30 p-3">
                                        <div className="flex items-center justify-between gap-2">
                                            <span className="text-xs font-semibold text-muted-foreground">
                                                {t("components.memory.MemoryWorkflowsPanel.hintTrend")}
                                            </span>
                                            <span className="text-[11px] text-muted-foreground">
                                                {hintTrend.totalOutcomes || 0} {t("components.memory.MemoryWorkflowsPanel.hintEvents")}
                                            </span>
                                        </div>
                                        <div className="flex h-2 overflow-hidden rounded-full bg-muted">
                                            {HINT_OUTCOME_ORDER.map((outcome) => {
                                                const count = Number(hintTrend.outcomeCounts[outcome] || 0);
                                                const total = hintTrend.totalOutcomes || 0;
                                                if (!count || !total) return null;
                                                return (
                                                    <div
                                                        key={outcome}
                                                        className={outcomeTone(outcome)}
                                                        style={{ width: `${(count / total) * 100}%` }}
                                                        title={`${t(`components.memory.MemoryWorkflowsPanel.outcome.${outcome}`)}: ${count}`}
                                                    />
                                                );
                                            })}
                                        </div>
                                        <div className="flex flex-wrap gap-2 text-[11px] text-muted-foreground">
                                            {HINT_OUTCOME_ORDER.map((outcome) => {
                                                const count = Number(hintTrend.outcomeCounts[outcome] || 0);
                                                if (!count) return null;
                                                return (
                                                    <span key={outcome} className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5">
                                                        <span className={`inline-block h-2 w-2 rounded-full ${outcomeTone(outcome)}`} />
                                                        {t(`components.memory.MemoryWorkflowsPanel.outcome.${outcome}`)} {count}
                                                    </span>
                                                );
                                            })}
                                        </div>
                                        <div className="grid grid-cols-1 gap-2 text-xs text-muted-foreground sm:grid-cols-2">
                                            <div className="rounded-md border bg-background/60 p-2">
                                                <div className="font-medium">{t("components.memory.MemoryWorkflowsPanel.plannerAwareHits")}</div>
                                                <div className="mt-1 font-mono text-base text-foreground">{hintTrend.plannerAwareCount}</div>
                                            </div>
                                            <div className="rounded-md border bg-background/60 p-2">
                                                <div className="font-medium">{t("components.memory.MemoryWorkflowsPanel.deliveryModes")}</div>
                                                <div className="mt-1 flex flex-wrap gap-1">
                                                    {DELIVERY_MODE_ORDER.map((mode) => {
                                                        const count = Number(hintTrend.deliveryModeBreakdown[mode] || 0);
                                                        if (!count) return null;
                                                        return (
                                                            <Badge key={mode} variant="outline">
                                                                {t(`components.memory.MemoryWorkflowsPanel.deliveryMode.${mode}`)} {count}
                                                            </Badge>
                                                        );
                                                    })}
                                                    {!Object.keys(hintTrend.deliveryModeBreakdown).length ? (
                                                        <span className="text-muted-foreground/80">{t("components.memory.MemoryWorkflowsPanel.noHintEvents")}</span>
                                                    ) : null}
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </div>

                                <div className="rounded-lg border p-4">
                                    <h3 className="mb-3 text-sm font-semibold">{t("components.memory.MemoryWorkflowsPanel.episodes")}</h3>
                                    <div className="max-h-64 space-y-2 overflow-y-auto">
                                        {episodes.length ? episodes.map((episode) => (
                                            <div key={episode.id} className="rounded-md bg-muted/40 p-3 text-xs">
                                                <div className="flex items-center justify-between gap-2">
                                                    <span className="font-mono">{episode.id}</span>
                                                    <Badge variant={episode.status === "negative_feedback" ? "destructive" : "secondary"}>{episode.status || "episode"}</Badge>
                                                </div>
                                                <p className="mt-2 text-muted-foreground">{episode.finalSuccessEvidence || episode.final_success_evidence || episode.taskFamily || episode.task_family}</p>
                                                {Array.isArray(episode.metadata?.runtimeEvidence) ? (
                                                    <p className="mt-1 text-muted-foreground">
                                                        {t("components.memory.MemoryWorkflowsPanel.rawEvidence")}: {episode.metadata.runtimeEvidence.length}
                                                    </p>
                                                ) : null}
                                                {episode.workflowClass === "engineering" || episode.sourceRuntime ? (
                                                    <p className="mt-1 text-muted-foreground">
                                                        {episode.workflowClass || "general"} · {episode.sourceRuntime || "memory"}
                                                    </p>
                                                ) : null}
                                                {Array.isArray(episode.failureMarkers) && episode.failureMarkers.length ? (
                                                    <p className="mt-1 text-amber-600 dark:text-amber-300">
                                                        {t("components.memory.MemoryWorkflowsPanel.antiPatterns")}: {episode.failureMarkers.slice(0, 2).join("; ")}
                                                    </p>
                                                ) : null}
                                            </div>
                                        )) : <p className="text-xs text-muted-foreground">{t("components.memory.MemoryWorkflowsPanel.noEpisodes")}</p>}
                                    </div>
                                </div>

                                <div className="rounded-lg border p-4">
                                    <h3 className="mb-3 text-sm font-semibold">{t("components.memory.MemoryWorkflowsPanel.hintEvents")}</h3>
                                    <div className="max-h-64 space-y-2 overflow-y-auto">
                                        {hintEvents.length ? hintEvents.map((event) => (
                                            <div key={event.id} className="rounded-md bg-muted/40 p-3 text-xs">
                                                <div className="flex items-center justify-between gap-2">
                                                    <span className="font-mono">{event.id}</span>
                                                    <Badge variant="secondary">{event.outcome || "injected"}</Badge>
                                                </div>
                                                <p className="mt-2 text-muted-foreground">{event.query}</p>
                                            </div>
                                        )) : <p className="text-xs text-muted-foreground">{t("components.memory.MemoryWorkflowsPanel.noHintEvents")}</p>}
                                    </div>
                                </div>
                            </div>
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}

function FieldTextarea({ label, value, onChange, rows = 4 }: { label: string; value: string; rows?: number; onChange: (value: string) => void }) {
    return (
        <div className="space-y-1.5">
            <Label>{label}</Label>
            <Textarea value={value} rows={rows} onChange={(event) => onChange(event.target.value)} className="font-mono text-xs" />
        </div>
    );
}

function Metric({ label, value }: { label: string; value: string | number }) {
    return (
        <div className="rounded-md bg-muted/40 p-3">
            <div className="text-xs text-muted-foreground">{label}</div>
            <div className="mt-1 font-mono text-lg">{value}</div>
        </div>
    );
}
