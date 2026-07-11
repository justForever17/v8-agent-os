"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, Archive, Box, ChevronDown, ChevronRight, Clapperboard, FolderOpen, RefreshCw, Save, Sparkles, Trash2, UserRound } from "lucide-react";

import { AdminHoverInfo } from "@/components/admin-shell/AdminHoverInfo";
import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ModelSelect, type AdminModelSelectOption } from "@/components/models/ModelSelect";
import { useT } from "@/components/providers/LocaleProvider";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fetchAdminJson } from "@/lib/admin-client-cache";
import { useDebugMode } from "@/lib/useDebugMode";

type CreativeMediaData = {
    catalog?: Record<string, unknown>;
    resolutions?: Record<string, unknown>;
    workOrders: Array<Record<string, unknown>>;
    recipes: Array<Record<string, unknown>>;
    assets: Array<Record<string, unknown>>;
    characterBibles: Array<Record<string, unknown>>;
    keyframes: Array<Record<string, unknown>>;
    jobs: Array<Record<string, unknown>>;
    editPlans: Array<Record<string, unknown>>;
    renders: Array<Record<string, unknown>>;
    qualityJobs: Array<Record<string, unknown>>;
    costEntries: Array<Record<string, unknown>>;
    safetyEvents: Array<Record<string, unknown>>;
    modelPreferences?: CreativeModelPreferences;
};

type CreativeModelCandidate = {
    candidateId: string;
    modality: string;
    operationKind?: string;
    providerId: string;
    providerName: string;
    modelId: string;
    modelRef?: string;
    providerLogoAsset?: string;
    modelLogoAsset?: string;
    adapter: string;
    source?: string;
    available?: boolean;
    briefOnly?: boolean;
    enabled?: boolean;
    priority?: number;
};

type CreativeOperationRow = {
    operationKind: string;
    modality: string;
    enabled?: boolean;
    selectedModelRefs?: string[];
    priority?: number;
    optionCount?: number;
};

type CreativeModelPreferences = {
    version?: number;
    updatedAt?: string;
    candidates: CreativeModelCandidate[];
    connectedOptions?: CreativeModelCandidate[];
    diagnosticCandidates?: CreativeModelCandidate[];
    operationRows?: CreativeOperationRow[];
    policies?: Record<string, { models?: CreativeModelCandidate[]; fallbackEnabled?: boolean }>;
};

const EMPTY_DATA: CreativeMediaData = {
    workOrders: [],
    recipes: [],
    assets: [],
    characterBibles: [],
    keyframes: [],
    jobs: [],
    editPlans: [],
    renders: [],
    qualityJobs: [],
    costEntries: [],
    safetyEvents: [],
};

const DEFAULT_OPERATION_ROWS: CreativeOperationRow[] = [
    { operationKind: "image.edit", modality: "image", priority: 100 },
    { operationKind: "image.generate", modality: "image", priority: 100 },
    { operationKind: "model3d.generate", modality: "model3d", priority: 100 },
    { operationKind: "music.brief", modality: "music", priority: 5 },
    { operationKind: "music.cover", modality: "music", priority: 100 },
    { operationKind: "music.generate", modality: "music", priority: 100 },
    { operationKind: "video.first_last_frame", modality: "video", priority: 100 },
    { operationKind: "video.image_to_video", modality: "video", priority: 100 },
    { operationKind: "video.reference_to_video", modality: "video", priority: 100 },
    { operationKind: "video.text_to_video", modality: "video", priority: 100 },
    { operationKind: "voice.tts", modality: "voice", priority: 100 },
];

const PLUGIN_ONLY_OPERATION_KINDS = new Set([
    "video.action_transfer",
    "video.avatar",
    "video.lipsync",
    "video.replacement",
    "video.style_repaint",
    "video.video_edit",
]);

function mergeOperationRows(rows?: CreativeOperationRow[]) {
    const byOperation = new Map<string, CreativeOperationRow>();
    for (const row of DEFAULT_OPERATION_ROWS) {
        byOperation.set(row.operationKind, { ...row, selectedModelRefs: [] });
    }
    for (const row of rows || []) {
        const key = String(row.operationKind || "").trim();
        if (!key || PLUGIN_ONLY_OPERATION_KINDS.has(key)) continue;
        byOperation.set(key, {
            ...(byOperation.get(key) || {}),
            ...row,
            modality: row.modality || byOperation.get(key)?.modality || "image",
            selectedModelRefs: Array.isArray(row.selectedModelRefs) ? row.selectedModelRefs : [],
        });
    }
    return Array.from(byOperation.values());
}

function text(value: unknown, fallback = "-") {
    const normalized = String(value || "").trim();
    return normalized || fallback;
}

function countCatalogModalities(catalog?: Record<string, unknown>) {
    const modalities = catalog?.modalities;
    if (!modalities || typeof modalities !== "object") return 0;
    return Object.keys(modalities).length;
}

function modalityLabel(t: ReturnType<typeof useT>, modality: string) {
    const normalized = String(modality || "").toLowerCase();
    const keyMap: Record<string, string> = {
        image: "app.admin.dashboard.creativeMedia.modalityImage",
        video: "app.admin.dashboard.creativeMedia.modalityVideo",
        voice: "app.admin.dashboard.creativeMedia.modalityVoice",
        music: "app.admin.dashboard.creativeMedia.modalityMusic",
        model3d: "app.admin.dashboard.creativeMedia.modalityModel3d",
    };
    return keyMap[normalized] ? t(keyMap[normalized]) : text(modality);
}

function creativeMediaStatusLabel(t: ReturnType<typeof useT>, status: unknown) {
    const normalized = String(status || "").toLowerCase();
    const keyMap: Record<string, string> = {
        archived: "app.admin.dashboard.creativeMedia.status.archived",
        deleted: "app.admin.dashboard.creativeMedia.status.deleted",
        rendering: "app.admin.dashboard.creativeMedia.status.rendering",
        completed: "app.admin.dashboard.creativeMedia.status.completed",
        failed: "app.admin.dashboard.creativeMedia.status.failed",
        pending_quality_review: "app.admin.dashboard.creativeMedia.status.pending_quality_review",
        running: "app.admin.dashboard.creativeMedia.status.running",
        paused: "app.admin.dashboard.creativeMedia.status.paused",
        waiting_input: "app.admin.dashboard.creativeMedia.status.waiting_input",
        waiting_approval: "app.admin.dashboard.creativeMedia.status.waiting_approval",
    };
    return keyMap[normalized] ? t(keyMap[normalized]) : text(status);
}

function listOfStrings(value: unknown): string[] {
    if (Array.isArray(value)) {
        return value.map((item) => text(item, "")).filter(Boolean);
    }
    const normalized = text(value, "");
    return normalized ? [normalized] : [];
}

function nestedText(value: unknown, key: string) {
    if (!value || typeof value !== "object") return "";
    return text((value as Record<string, unknown>)[key], "");
}

function productionTitle(workOrder: Record<string, unknown>) {
    return (
        text(workOrder.title, "") ||
        text(workOrder.brief, "") ||
        text(workOrder.intent, "") ||
        text(workOrder.workOrderKind, "") ||
        text(workOrder.workOrderId)
    );
}

function productionWorkspace(workOrder: Record<string, unknown>) {
    return (
        text(workOrder.workspacePath, "") ||
        text(workOrder.workspace, "") ||
        text(workOrder.workspaceId, "") ||
        text(workOrder.projectId, "")
    );
}

function productionRecipeIds(workOrder: Record<string, unknown>) {
    return listOfStrings([
        text(workOrder.recipeId, ""),
        ...listOfStrings(workOrder.recipeIds),
        ...listOfStrings(workOrder.recipeRefs),
    ].filter(Boolean));
}

function sourceRefs(record: Record<string, unknown>) {
    return new Set([
        ...listOfStrings(record.sourceRefs),
        text(record.recipeId, ""),
        text(record.workOrderId, ""),
        nestedText(record.lineage, "recipeId"),
        nestedText(record.lineage, "workOrderId"),
        nestedText(record.request, "recipeId"),
        nestedText(record.request, "workOrderId"),
    ].filter(Boolean));
}

function relatedCount(records: Array<Record<string, unknown>>, workOrder: Record<string, unknown>, extraKeys: string[] = []) {
    const workOrderId = text(workOrder.workOrderId, "");
    const recipeIds = new Set(productionRecipeIds(workOrder));
    return records.filter((record) => {
        const refs = sourceRefs(record);
        if (workOrderId && refs.has(workOrderId)) return true;
        for (const recipeId of recipeIds) {
            if (refs.has(recipeId)) return true;
        }
        return extraKeys.some((key) => workOrderId && text(record[key], "") === workOrderId);
    }).length;
}

function candidateWarningReason(t: ReturnType<typeof useT>, candidate: CreativeModelCandidate) {
    if (candidate.briefOnly) return t("app.admin.dashboard.creativeMedia.candidateRiskBriefOnly");
    if (candidate.available === false) return t("app.admin.dashboard.creativeMedia.candidateRiskUnavailable");
    return "";
}

function asModelSelectOption(candidate: CreativeModelCandidate, warningReason = ""): AdminModelSelectOption {
    return {
        id: candidate.modelRef || candidate.candidateId,
        modelRef: candidate.modelRef,
        providerId: candidate.providerId,
        modelId: candidate.modelId,
        logoAsset: candidate.modelLogoAsset || candidate.providerLogoAsset || null,
        type: candidate.modality?.toUpperCase(),
        provider: {
            id: candidate.providerId,
            name: candidate.providerName,
        },
        providerName: candidate.providerName,
        warningReason,
    };
}

function CompactJson({ value }: { value: unknown }) {
    const json = useMemo(() => JSON.stringify(value || {}, null, 2), [value]);
    return (
        <pre className="max-h-52 overflow-auto rounded-md bg-slate-950 p-3 text-xs leading-relaxed text-slate-100">
            {json}
        </pre>
    );
}

function EmptyRow({ colSpan, label }: { colSpan: number; label: string }) {
    return (
        <TableRow>
            <TableCell colSpan={colSpan} className="py-8 text-center text-sm text-muted-foreground">
                {label}
            </TableCell>
        </TableRow>
    );
}

function recordValue(value: Record<string, unknown>, key: string): Record<string, unknown> {
    const child = value[key];
    return child && typeof child === "object" && !Array.isArray(child) ? child as Record<string, unknown> : {};
}

function arrayValue(value: Record<string, unknown>, key: string): Array<Record<string, unknown>> {
    const child = value[key];
    return Array.isArray(child) ? child as Array<Record<string, unknown>> : [];
}

function normalizeModelPreferences(value: Record<string, unknown>): CreativeModelPreferences {
    return {
        candidates: arrayValue(value, "candidates") as CreativeModelCandidate[],
        connectedOptions: arrayValue(value, "connectedOptions") as CreativeModelCandidate[],
        diagnosticCandidates: arrayValue(value, "diagnosticCandidates") as CreativeModelCandidate[],
        operationRows: arrayValue(value, "operationRows") as CreativeOperationRow[],
        policies: value.policies && typeof value.policies === "object" ? value.policies as CreativeModelPreferences["policies"] : {},
        updatedAt: String(value.updatedAt || ""),
        version: typeof value.version === "number" ? value.version : undefined,
    };
}

function normalizeCreativeBootstrap(value: Record<string, unknown>): CreativeMediaData {
    const modelPreferences = recordValue(value, "modelPreferences");
    return {
        ...EMPTY_DATA,
        catalog: recordValue(value, "catalog"),
        resolutions: recordValue(value, "resolutions"),
        workOrders: arrayValue(recordValue(value, "workOrders"), "workOrders"),
        recipes: arrayValue(recordValue(value, "recipes"), "recipes"),
        assets: arrayValue(recordValue(value, "assets"), "assets"),
        jobs: arrayValue(recordValue(value, "jobs"), "jobs"),
        modelPreferences: normalizeModelPreferences(modelPreferences),
    };
}

function normalizeCreativeDiagnostics(value: Record<string, unknown>): Partial<CreativeMediaData> {
    return {
        characterBibles: arrayValue(recordValue(value, "characterBibles"), "characterBibles"),
        keyframes: arrayValue(recordValue(value, "keyframes"), "keyframes"),
        editPlans: arrayValue(recordValue(value, "editPlans"), "editPlans"),
        renders: arrayValue(recordValue(value, "renders"), "renders"),
        qualityJobs: arrayValue(recordValue(value, "qualityJobs"), "qualityJobs"),
        costEntries: arrayValue(recordValue(value, "costLedger"), "entries"),
        safetyEvents: arrayValue(recordValue(value, "safetyEvents"), "events"),
    };
}

export default function CreativeMediaPage() {
    const t = useT();
    const [debugMode] = useDebugMode();
    const [data, setData] = useState<CreativeMediaData>(EMPTY_DATA);
    const [loading, setLoading] = useState(true);
    const [savingModels, setSavingModels] = useState(false);
    const [error, setError] = useState("");
    const [modelPreferencesOpen, setModelPreferencesOpen] = useState(false);
    const [workingWorkOrderId, setWorkingWorkOrderId] = useState("");

    const fetchDiagnostics = useCallback(async (force = false) => {
        try {
            const [characterBibles, keyframes, editPlans, renders, qualityJobs, costLedger, safetyEvents] = await Promise.all([
                fetchAdminJson("/api/creative-media/character-bibles", { force, ttlMs: 10_000 }),
                fetchAdminJson("/api/creative-media/keyframes", { force, ttlMs: 10_000 }),
                fetchAdminJson("/api/creative-media/edit-plans", { force, ttlMs: 10_000 }),
                fetchAdminJson("/api/creative-media/renders", { force, ttlMs: 10_000 }),
                fetchAdminJson("/api/creative-media/quality-jobs", { force, ttlMs: 10_000 }),
                fetchAdminJson("/api/creative-media/cost-ledger", { force, ttlMs: 10_000 }),
                fetchAdminJson("/api/creative-media/safety-events", { force, ttlMs: 10_000 }),
            ]);
            const diagnostics = normalizeCreativeDiagnostics({ characterBibles, keyframes, editPlans, renders, qualityJobs, costLedger, safetyEvents });
            setData((current) => ({ ...current, ...diagnostics }));
        } catch {
            // Delayed diagnostics should not block the production-control first screen.
        }
    }, []);

    const fetchData = useCallback(async (force = false) => {
        setLoading(true);
        setError("");
        try {
            const payload = await fetchAdminJson<Record<string, unknown>>("/api/creative-media/bootstrap", { force });
            setData(normalizeCreativeBootstrap(payload));
            void fetchDiagnostics(force);
        } catch (err) {
            setError(String(err));
        } finally {
            setLoading(false);
        }
    }, [fetchDiagnostics]);

    useEffect(() => {
        void fetchData();
    }, [fetchData]);

    const musicRecipes = data.recipes.filter((item) => text(item.modality, "") === "music");
    const connectedModelOptions = data.modelPreferences?.connectedOptions || [];
    const diagnosticCandidates = data.modelPreferences?.diagnosticCandidates || [];
    const operationRows = useMemo(
        () => mergeOperationRows(data.modelPreferences?.operationRows),
        [data.modelPreferences?.operationRows],
    );
    const updateOperationRow = useCallback((operationKind: string, patch: Partial<CreativeOperationRow>) => {
        setData((current) => ({
            ...current,
            modelPreferences: {
                ...(current.modelPreferences || { candidates: [] }),
                operationRows: mergeOperationRows(current.modelPreferences?.operationRows).map((row) => (
                    row.operationKind === operationKind ? { ...row, ...patch } : row
                )),
            },
        }));
    }, []);
    const setOperationModelRef = useCallback((operationKind: string, index: number, modelRef: string) => {
        setData((current) => ({
            ...current,
            modelPreferences: {
                ...(current.modelPreferences || { candidates: [] }),
                operationRows: mergeOperationRows(current.modelPreferences?.operationRows).map((row) => {
                    if (row.operationKind !== operationKind) return row;
                    const selected = [...(row.selectedModelRefs || [])];
                    selected[index] = modelRef;
                    const deduped = selected.filter((value, itemIndex, array) => value && array.indexOf(value) === itemIndex).slice(0, 3);
                    return { ...row, selectedModelRefs: deduped, enabled: deduped.length > 0 ? true : row.enabled };
                }),
            },
        }));
    }, []);
    const saveModelPreferences = useCallback(async () => {
        setSavingModels(true);
        setError("");
        try {
            const response = await fetch("/api/creative-media/model-preferences", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    selections: operationRows.map((row) => ({
                        operationKind: row.operationKind,
                        modelRefs: (row.selectedModelRefs || []).filter(Boolean).slice(0, 3),
                        enabled: row.enabled !== false && (row.selectedModelRefs || []).filter(Boolean).length > 0,
                        priority: Number(row.priority || 100),
                    })),
                }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(String(payload.detail || payload.error || response.statusText));
            }
            setData((current) => ({
                ...current,
                modelPreferences: {
                    candidates: Array.isArray(payload.candidates) ? payload.candidates : [],
                    connectedOptions: Array.isArray(payload.connectedOptions) ? payload.connectedOptions : [],
                    diagnosticCandidates: Array.isArray(payload.diagnosticCandidates) ? payload.diagnosticCandidates : [],
                    operationRows: Array.isArray(payload.operationRows) ? payload.operationRows : [],
                    policies: payload.policies || {},
                    updatedAt: payload.updatedAt || "",
                    version: payload.version,
                },
            }));
        } catch (err) {
            setError(String(err));
        } finally {
            setSavingModels(false);
        }
    }, [operationRows]);

    const mutateWorkOrder = useCallback(async (workOrderId: string, action: "archive" | "delete") => {
        if (!workOrderId) return;
        const confirmKey = action === "archive"
            ? "app.admin.dashboard.creativeMedia.archiveWorkOrderConfirm"
            : "app.admin.dashboard.creativeMedia.deleteWorkOrderConfirm";
        if (!window.confirm(t(confirmKey))) return;
        setWorkingWorkOrderId(workOrderId);
        setError("");
        try {
            const response = await fetch(`/api/creative-media/work-orders/${encodeURIComponent(workOrderId)}/${action}`, {
                method: "POST",
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(String(payload.detail || payload.error || response.statusText));
            }
            await fetchData(true);
        } catch (err) {
            setError(`${t("app.admin.dashboard.creativeMedia.workOrderActionFailed")}: ${String(err)}`);
        } finally {
            setWorkingWorkOrderId("");
        }
    }, [fetchData, t]);

    return (
        <AdminPageShell className="max-w-none gap-5">
            <AdminPageHeader
                title="app.admin.dashboard.creativeMedia.title"
                description="app.admin.dashboard.creativeMedia.description"
                actions={(
                    <Button variant="outline" onClick={() => void fetchData(true)} disabled={loading}>
                        <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
                        {t("app.admin.dashboard.creativeMedia.refresh")}
                    </Button>
                )}
            />

            {error ? (
                <Card className="border-rose-200 bg-rose-50">
                    <CardContent className="p-4 text-sm text-rose-700">{error}</CardContent>
                </Card>
            ) : null}

            <div className="grid gap-3 md:grid-cols-6">
                {[
                    { key: "catalog", label: t("app.admin.dashboard.creativeMedia.statCatalog"), value: countCatalogModalities(data.catalog), icon: Box },
                    { key: "workOrders", label: t("app.admin.dashboard.creativeMedia.statWorkOrders"), value: data.workOrders.length, icon: Sparkles },
                    { key: "assets", label: t("app.admin.dashboard.creativeMedia.statAssets"), value: data.assets.length, icon: Box },
                    { key: "characters", label: t("app.admin.dashboard.creativeMedia.statCharacters"), value: data.characterBibles.length, icon: UserRound },
                    { key: "keyframes", label: t("app.admin.dashboard.creativeMedia.statKeyframesJobs"), value: `${data.keyframes.length} / ${data.jobs.length}`, icon: Clapperboard },
                    { key: "qualityCost", label: t("app.admin.dashboard.creativeMedia.statQualityCost"), value: `${data.qualityJobs.length} / ${data.costEntries.length}`, icon: Clapperboard },
                ].map((item) => (
                    <Card key={item.key} className="border-border bg-card/90 shadow-sm">
                        <CardContent className="flex items-center gap-3 p-4">
                            <item.icon className="h-5 w-5 text-muted-foreground" />
                            <div>
                                <div className="text-xl font-bold">{item.value}</div>
                                <div className="text-xs text-muted-foreground">{item.label}</div>
                            </div>
                        </CardContent>
                    </Card>
                ))}
            </div>

            <Card className="border-border bg-card/95 shadow-sm">
                <CardHeader>
                    <CardTitle>
                        <AdminHoverInfo
                            content={
                                <span>
                                    {t("app.admin.dashboard.creativeMedia.workflowsHover")}
                                </span>
                            }
                            panelClassName="text-sm leading-6"
                        >
                            <span>{t("app.admin.dashboard.creativeMedia.workflowsTitle")}</span>
                        </AdminHoverInfo>
                    </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="grid gap-3 md:grid-cols-3">
                        <div className="rounded-lg border bg-muted/20 p-3">
                            <div className="text-sm font-semibold">{t("app.admin.dashboard.creativeMedia.productionStepPlanTitle")}</div>
                            <p className="mt-1 text-xs leading-5 text-muted-foreground">{t("app.admin.dashboard.creativeMedia.productionStepPlanDescription")}</p>
                        </div>
                        <div className="rounded-lg border bg-muted/20 p-3">
                            <div className="text-sm font-semibold">{t("app.admin.dashboard.creativeMedia.productionStepGenerateTitle")}</div>
                            <p className="mt-1 text-xs leading-5 text-muted-foreground">{t("app.admin.dashboard.creativeMedia.productionStepGenerateDescription")}</p>
                        </div>
                        <div className="rounded-lg border bg-muted/20 p-3">
                            <div className="text-sm font-semibold">{t("app.admin.dashboard.creativeMedia.productionStepReviewTitle")}</div>
                            <p className="mt-1 text-xs leading-5 text-muted-foreground">{t("app.admin.dashboard.creativeMedia.productionStepReviewDescription")}</p>
                        </div>
                    </div>
                    <div>
                        <div className="mb-3 text-sm font-semibold">{t("app.admin.dashboard.creativeMedia.recentWorkOrdersTitle")}</div>
                        {data.workOrders.length ? (
                            <div className="grid gap-3 xl:grid-cols-2">
                                {data.workOrders.slice(0, 8).map((workOrder) => {
                                    const workOrderId = text(workOrder.workOrderId, "");
                                    const workspace = productionWorkspace(workOrder);
                                    const recipeIds = productionRecipeIds(workOrder);
                                    const assetCount = relatedCount(data.assets, workOrder);
                                    const jobCount = relatedCount(data.jobs, workOrder);
                                    const disabled = workingWorkOrderId === workOrderId;
                                    return (
                                        <div key={workOrderId || productionTitle(workOrder)} className="rounded-xl border bg-background p-4">
                                            <div className="flex items-start justify-between gap-3">
                                                <div className="min-w-0">
                                                    <div className="flex flex-wrap items-center gap-2">
                                                        <Badge variant="outline">{creativeMediaStatusLabel(t, workOrder.status)}</Badge>
                                                        <Badge variant="secondary">{text(workOrder.workOrderKind, text(workOrder.intent))}</Badge>
                                                    </div>
                                                    <div className="mt-2 truncate text-sm font-semibold" title={productionTitle(workOrder)}>
                                                        {productionTitle(workOrder)}
                                                    </div>
                                                </div>
                                                <div className="flex shrink-0 items-center gap-1">
                                                    <Button
                                                        aria-label={t("app.admin.dashboard.creativeMedia.archiveWorkOrder")}
                                                        size="icon"
                                                        variant="ghost"
                                                        disabled={disabled}
                                                        onClick={() => void mutateWorkOrder(workOrderId, "archive")}
                                                    >
                                                        <Archive className="h-4 w-4" />
                                                    </Button>
                                                    <Button
                                                        aria-label={t("app.admin.dashboard.creativeMedia.deleteWorkOrder")}
                                                        size="icon"
                                                        variant="ghost"
                                                        disabled={disabled}
                                                        onClick={() => void mutateWorkOrder(workOrderId, "delete")}
                                                    >
                                                        <Trash2 className="h-4 w-4 text-rose-500" />
                                                    </Button>
                                                </div>
                                            </div>
                                            <div className="mt-3 grid gap-2 text-xs text-muted-foreground md:grid-cols-2">
                                                <AdminHoverInfo
                                                    content={<span>{workspace || t("app.admin.dashboard.creativeMedia.productionCardUnknownWorkspace")}</span>}
                                                    panelClassName="max-w-md text-xs leading-5"
                                                >
                                                    <span className="flex min-w-0 items-center gap-1">
                                                        <FolderOpen className="h-3.5 w-3.5 shrink-0" />
                                                        <span className="truncate">
                                                            {workspace || t("app.admin.dashboard.creativeMedia.productionCardUnknownWorkspace")}
                                                        </span>
                                                    </span>
                                                </AdminHoverInfo>
                                                <span>{t("app.admin.dashboard.creativeMedia.productionCardUpdated")}: {text(workOrder.updatedAt || workOrder.createdAt)}</span>
                                            </div>
                                            <div className="mt-3 flex flex-wrap gap-2 text-xs">
                                                <Badge variant="outline">{t("app.admin.dashboard.creativeMedia.productionCardPlans")}: {recipeIds.length}</Badge>
                                                <Badge variant="outline">{t("app.admin.dashboard.creativeMedia.productionCardAssets")}: {assetCount}</Badge>
                                                <Badge variant="outline">{t("app.admin.dashboard.creativeMedia.productionCardJobs")}: {jobCount}</Badge>
                                            </div>
                                        </div>
                                    );
                                })}
                            </div>
                        ) : (
                            <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
                                {t("app.admin.dashboard.creativeMedia.emptyWorkOrders")}
                            </div>
                        )}
                    </div>
                </CardContent>
            </Card>

            <Card className="border-border bg-card/95 shadow-sm">
                <CardHeader>
                    <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                            <CardTitle>{t("app.admin.dashboard.creativeMedia.modelPreferencesTitle")}</CardTitle>
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                            <Button variant="outline" onClick={() => setModelPreferencesOpen((value) => !value)}>
                                {modelPreferencesOpen ? <ChevronDown className="mr-2 h-4 w-4" /> : <ChevronRight className="mr-2 h-4 w-4" />}
                                {modelPreferencesOpen ? t("app.admin.dashboard.creativeMedia.collapseModelPreferences") : t("app.admin.dashboard.creativeMedia.expandModelPreferences")}
                            </Button>
                            <Button onClick={() => void saveModelPreferences()} disabled={savingModels || loading}>
                                <Save className="mr-2 h-4 w-4" />
                                {savingModels ? t("app.admin.dashboard.creativeMedia.saving") : t("app.admin.dashboard.creativeMedia.saveModelPreferences")}
                            </Button>
                        </div>
                    </div>
                </CardHeader>
                {modelPreferencesOpen ? <CardContent>
                    <div className="overflow-x-auto">
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableEnabled")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableMedia")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableOperation")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableModel")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableFallback")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tablePriority")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableAvailable")}</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {operationRows.length ? operationRows.map((row) => {
                                    const rowCandidates = connectedModelOptions
                                        .filter((candidate) => candidate.operationKind === row.operationKind);
                                    const options = rowCandidates
                                        .map((candidate) => asModelSelectOption(candidate, candidateWarningReason(t, candidate)));
                                    const rowWarning = rowCandidates.find((candidate) => candidate.briefOnly || candidate.available === false);
                                    const rowWarningReason = rowWarning ? candidateWarningReason(t, rowWarning) : "";
                                    const selected = row.selectedModelRefs || [];
                                    return (
                                        <TableRow key={row.operationKind}>
                                            <TableCell>
                                                <Switch
                                                    checked={row.enabled !== false && selected.length > 0}
                                                    disabled={options.length === 0}
                                                    onCheckedChange={(checked) => updateOperationRow(row.operationKind, { enabled: checked })}
                                                />
                                            </TableCell>
                                            <TableCell>
                                                <Badge variant="outline">{modalityLabel(t, row.modality)}</Badge>
                                            </TableCell>
                                            <TableCell className="font-mono text-xs">{text(row.operationKind)}</TableCell>
                                            <TableCell className="min-w-72">
                                                {options.length ? (
                                                    <div>
                                                        <ModelSelect
                                                            models={options}
                                                            value={selected[0] || ""}
                                                            emptyLabel={t("app.admin.dashboard.creativeMedia.selectNone")}
                                                            placeholder={t("app.admin.dashboard.creativeMedia.selectPrimaryModel")}
                                                            onValueChange={(value) => setOperationModelRef(row.operationKind, 0, value)}
                                                            showCompatibilityHint={false}
                                                        />
                                                    </div>
                                                ) : (
                                                    <div className="flex min-w-72 flex-wrap items-center gap-2 text-sm">
                                                        <span className="text-muted-foreground">
                                                            {t("app.admin.dashboard.creativeMedia.noConnectedModels")}
                                                        </span>
                                                        <a className="font-medium text-primary hover:underline" href="/admin/model-hub">
                                                            {t("app.admin.dashboard.creativeMedia.openModelHub")}
                                                        </a>
                                                    </div>
                                                )}
                                            </TableCell>
                                            <TableCell className="min-w-80">
                                                <div className="grid gap-2 md:grid-cols-2">
                                                    {[1, 2].map((index) => (
                                                        <ModelSelect
                                                            key={`${row.operationKind}-${index}`}
                                                            models={options}
                                                            value={selected[index] || ""}
                                                            emptyLabel={t("app.admin.dashboard.creativeMedia.selectNone")}
                                                            placeholder={index === 1 ? t("app.admin.dashboard.creativeMedia.selectFallbackOne") : t("app.admin.dashboard.creativeMedia.selectFallbackTwo")}
                                                            onValueChange={(value) => setOperationModelRef(row.operationKind, index, value)}
                                                            showCompatibilityHint={false}
                                                        />
                                                    ))}
                                                </div>
                                            </TableCell>
                                            <TableCell>
                                                <Input
                                                    className="w-24"
                                                    type="number"
                                                    min={1}
                                                    max={999}
                                                    value={Number(row.priority || 100)}
                                                    onChange={(event) => updateOperationRow(row.operationKind, { priority: Number(event.target.value || 100) })}
                                                />
                                            </TableCell>
                                            <TableCell>
                                                <div className="flex items-center gap-2">
                                                    <Badge variant={options.length ? "default" : "secondary"}>
                                                        {options.length ? `${options.length}` : t("app.admin.dashboard.creativeMedia.unavailable")}
                                                    </Badge>
                                                    {rowWarningReason ? (
                                                        <span title={rowWarningReason} aria-label={rowWarningReason} className="shrink-0">
                                                            <AlertTriangle className="h-4 w-4 text-rose-500" />
                                                        </span>
                                                    ) : null}
                                                </div>
                                            </TableCell>
                                        </TableRow>
                                    );
                                }) : <EmptyRow colSpan={7} label={t("app.admin.dashboard.creativeMedia.emptyModelPreferences")} />}
                            </TableBody>
                        </Table>
                    </div>
                    <details className="mt-4 rounded-lg border bg-background p-3">
                        <summary className="cursor-pointer text-sm font-medium">
                            {t("app.admin.dashboard.creativeMedia.diagnosticCandidatesTitle")}
                        </summary>
                        <p className="mt-2 text-sm text-muted-foreground">
                            {t("app.admin.dashboard.creativeMedia.diagnosticCandidatesDescription")}
                        </p>
                        <div className="mt-3">
                            <CompactJson value={diagnosticCandidates.map((candidate) => ({
                                modality: candidate.modality,
                                operationKind: candidate.operationKind,
                                provider: candidate.providerName || candidate.providerId,
                                model: candidate.modelId,
                                source: candidate.source,
                                available: candidate.available,
                            }))} />
                        </div>
                    </details>
                </CardContent> : null}
            </Card>

            {debugMode && (
                <>
                    <div className="grid gap-6 xl:grid-cols-2">
                <Card>
                    <CardHeader>
                        <CardTitle>{t("app.admin.dashboard.creativeMedia.recipesTitle")}</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableId")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableType")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableStatus")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableUpdated")}</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {data.recipes.length ? data.recipes.slice(0, 8).map((recipe) => (
                                    <TableRow key={text(recipe.recipeId)}>
                                        <TableCell className="max-w-40 truncate font-mono text-xs">{text(recipe.recipeId)}</TableCell>
                                        <TableCell>
                                            <div className="flex flex-wrap gap-1">
                                                <Badge variant="outline">{text(recipe.modality)}</Badge>
                                                <Badge variant="secondary">{text(recipe.recipeKind)}</Badge>
                                                {recipe.modality === "music" ? <Badge>{text(recipe.musicKind)}</Badge> : null}
                                            </div>
                                        </TableCell>
                                        <TableCell>{creativeMediaStatusLabel(t, recipe.executionStatus)}</TableCell>
                                        <TableCell className="text-xs text-muted-foreground">{text(recipe.updatedAt)}</TableCell>
                                    </TableRow>
                                )) : <EmptyRow colSpan={4} label={t("app.admin.dashboard.creativeMedia.emptyRecipes")} />}
                            </TableBody>
                        </Table>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle>{t("app.admin.dashboard.creativeMedia.assetsTitle")}</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableId")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableRole")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableMedia")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableVersion")}</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {data.assets.length ? data.assets.slice(0, 8).map((asset) => (
                                    <TableRow key={text(asset.assetId)}>
                                        <TableCell className="max-w-40 truncate font-mono text-xs">{text(asset.assetId)}</TableCell>
                                        <TableCell>{text(asset.role)}</TableCell>
                                        <TableCell>
                                            <Badge variant="outline">{text(asset.modality)}</Badge>
                                        </TableCell>
                                        <TableCell>{text(asset.version)}</TableCell>
                                    </TableRow>
                                )) : <EmptyRow colSpan={4} label={t("app.admin.dashboard.creativeMedia.emptyAssets")} />}
                            </TableBody>
                        </Table>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle>{t("app.admin.dashboard.creativeMedia.charactersTitle")}</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableId")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableName")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableVersion")}</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {data.characterBibles.length ? data.characterBibles.slice(0, 8).map((bible) => (
                                    <TableRow key={text(bible.characterBibleId)}>
                                        <TableCell className="max-w-40 truncate font-mono text-xs">{text(bible.characterBibleId)}</TableCell>
                                        <TableCell>{text(bible.name)}</TableCell>
                                        <TableCell>{text(bible.version)}</TableCell>
                                    </TableRow>
                                )) : <EmptyRow colSpan={3} label={t("app.admin.dashboard.creativeMedia.emptyCharacters")} />}
                            </TableBody>
                        </Table>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle>{t("app.admin.dashboard.creativeMedia.keyframesTitle")}</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableId")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableRole")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableRecipe")}</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {data.keyframes.length ? data.keyframes.slice(0, 8).map((keyframe) => (
                                    <TableRow key={text(keyframe.keyframeId)}>
                                        <TableCell className="max-w-40 truncate font-mono text-xs">{text(keyframe.keyframeId)}</TableCell>
                                        <TableCell>{text(keyframe.role)}</TableCell>
                                        <TableCell className="max-w-40 truncate font-mono text-xs">{text(keyframe.recipeId)}</TableCell>
                                    </TableRow>
                                )) : <EmptyRow colSpan={3} label={t("app.admin.dashboard.creativeMedia.emptyKeyframes")} />}
                            </TableBody>
                        </Table>
                    </CardContent>
                </Card>
            </div>

            <div className="grid gap-6 xl:grid-cols-3">
                <Card>
                    <CardHeader>
                        <CardTitle>{t("app.admin.dashboard.creativeMedia.qualityJobsTitle")}</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableId")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableJob")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableStatus")}</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {data.qualityJobs.length ? data.qualityJobs.slice(0, 6).map((quality) => (
                                    <TableRow key={text(quality.qualityJobId)}>
                                        <TableCell className="max-w-32 truncate font-mono text-xs">{text(quality.qualityJobId)}</TableCell>
                                        <TableCell className="max-w-32 truncate font-mono text-xs">{text(quality.jobId)}</TableCell>
                                        <TableCell>{creativeMediaStatusLabel(t, quality.status)}</TableCell>
                                    </TableRow>
                                )) : <EmptyRow colSpan={3} label={t("app.admin.dashboard.creativeMedia.emptyQualityJobs")} />}
                            </TableBody>
                        </Table>
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader>
                        <CardTitle>{t("app.admin.dashboard.creativeMedia.costLedgerTitle")}</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableOperation")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableProvider")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableArtifacts")}</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {data.costEntries.length ? data.costEntries.slice(0, 6).map((entry) => (
                                    <TableRow key={text(entry.entryId)}>
                                        <TableCell className="font-mono text-xs">{text(entry.operationKind)}</TableCell>
                                        <TableCell>{text(entry.provider)}</TableCell>
                                        <TableCell>{text(entry.artifactCount)}</TableCell>
                                    </TableRow>
                                )) : <EmptyRow colSpan={3} label={t("app.admin.dashboard.creativeMedia.emptyCostLedger")} />}
                            </TableBody>
                        </Table>
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader>
                        <CardTitle>{t("app.admin.dashboard.creativeMedia.safetyEventsTitle")}</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableType")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableMedia")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableUpdated")}</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {data.safetyEvents.length ? data.safetyEvents.slice(0, 6).map((event) => (
                                    <TableRow key={text(event.eventId)}>
                                        <TableCell>{Array.isArray(event.events) ? text((event.events[0] as Record<string, unknown> | undefined)?.kind) : "-"}</TableCell>
                                        <TableCell>{text(event.modality)}</TableCell>
                                        <TableCell className="text-xs text-muted-foreground">{text(event.createdAt)}</TableCell>
                                    </TableRow>
                                )) : <EmptyRow colSpan={3} label={t("app.admin.dashboard.creativeMedia.emptySafetyEvents")} />}
                            </TableBody>
                        </Table>
                    </CardContent>
                </Card>
            </div>

            <div className="grid gap-6 xl:grid-cols-2">
                <Card>
                    <CardHeader>
                        <CardTitle>{t("app.admin.dashboard.creativeMedia.editPlansTitle")}</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableId")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableRecipe")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableStatus")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableUpdated")}</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {data.editPlans.length ? data.editPlans.slice(0, 8).map((plan) => (
                                    <TableRow key={text(plan.planId)}>
                                        <TableCell className="max-w-40 truncate font-mono text-xs">{text(plan.planId)}</TableCell>
                                        <TableCell className="max-w-40 truncate font-mono text-xs">{text(plan.recipeId)}</TableCell>
                                        <TableCell>{creativeMediaStatusLabel(t, plan.status)}</TableCell>
                                        <TableCell className="text-xs text-muted-foreground">{text(plan.updatedAt)}</TableCell>
                                    </TableRow>
                                )) : <EmptyRow colSpan={4} label={t("app.admin.dashboard.creativeMedia.emptyEditPlans")} />}
                            </TableBody>
                        </Table>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle>{t("app.admin.dashboard.creativeMedia.renderJobsTitle")}</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableId")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tablePlan")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableStatus")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableArtifacts")}</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {data.renders.length ? data.renders.slice(0, 8).map((render) => (
                                    <TableRow key={text(render.renderJobId)}>
                                        <TableCell className="max-w-40 truncate font-mono text-xs">{text(render.renderJobId)}</TableCell>
                                        <TableCell className="max-w-40 truncate font-mono text-xs">{text(render.planId)}</TableCell>
                                        <TableCell>{creativeMediaStatusLabel(t, render.status)}</TableCell>
                                        <TableCell>{Array.isArray(render.artifacts) ? render.artifacts.length : 0}</TableCell>
                                    </TableRow>
                                )) : <EmptyRow colSpan={4} label={t("app.admin.dashboard.creativeMedia.emptyRenderJobs")} />}
                            </TableBody>
                        </Table>
                    </CardContent>
                </Card>
            </div>

            <div className="grid gap-6 xl:grid-cols-2">
                <Card>
                    <CardHeader>
                        <CardTitle>{t("app.admin.dashboard.creativeMedia.governanceTitle")}</CardTitle>
                    </CardHeader>
                    <CardContent className="grid gap-3 md:grid-cols-3">
                        <div className="rounded-lg border p-3">
                            <div className="text-sm font-semibold">{t("app.admin.dashboard.creativeMedia.cleanableTitle")}</div>
                            <p className="mt-1 text-xs leading-5 text-muted-foreground">{t("app.admin.dashboard.creativeMedia.cleanableDescription")}</p>
                        </div>
                        <div className="rounded-lg border p-3">
                            <div className="text-sm font-semibold">{t("app.admin.dashboard.creativeMedia.archiveOnlyTitle")}</div>
                            <p className="mt-1 text-xs leading-5 text-muted-foreground">{t("app.admin.dashboard.creativeMedia.archiveOnlyDescription")}</p>
                        </div>
                        <div className="rounded-lg border p-3">
                            <div className="text-sm font-semibold">{t("app.admin.dashboard.creativeMedia.liveLimitTitle")}</div>
                            <p className="mt-1 text-xs leading-5 text-muted-foreground">{t("app.admin.dashboard.creativeMedia.liveLimitDescription")}</p>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader>
                        <CardTitle>{t("app.admin.dashboard.creativeMedia.diagnosticsTitle")}</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        <details className="rounded-lg border p-3">
                            <summary className="cursor-pointer text-sm font-medium">{t("app.admin.dashboard.creativeMedia.musicDiagnosticsTitle")}</summary>
                            <div className="mt-3">
                                {musicRecipes.length ? <CompactJson value={musicRecipes.slice(0, 3)} /> : (
                                    <div className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">{t("app.admin.dashboard.creativeMedia.emptyMusicRecipes")}</div>
                                )}
                            </div>
                        </details>
                        <details className="rounded-lg border p-3">
                            <summary className="cursor-pointer text-sm font-medium">{t("app.admin.dashboard.creativeMedia.providerCatalogTitle")}</summary>
                            <div className="mt-3"><CompactJson value={data.catalog} /></div>
                        </details>
                        <details className="rounded-lg border p-3">
                            <summary className="cursor-pointer text-sm font-medium">{t("app.admin.dashboard.creativeMedia.unavailableModelDiagnosticsTitle")}</summary>
                            <div className="mt-3">
                                <CompactJson value={diagnosticCandidates.map((candidate) => ({
                                    modality: candidate.modality,
                                    operationKind: candidate.operationKind,
                                    provider: candidate.providerName || candidate.providerId,
                                    model: candidate.modelId,
                                    source: candidate.source,
                                    available: candidate.available,
                                }))} />
                            </div>
                        </details>
                    </CardContent>
                </Card>
            </div>
            </>)}
        </AdminPageShell>
    );
}
