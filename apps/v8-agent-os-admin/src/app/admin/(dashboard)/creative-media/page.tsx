"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Box, Clapperboard, RefreshCw, Save, Sparkles, UserRound } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ModelSelect, type AdminModelSelectOption } from "@/components/models/ModelSelect";
import { useT } from "@/components/providers/LocaleProvider";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

type CreativeMediaData = {
    catalog?: Record<string, unknown>;
    resolutions?: Record<string, unknown>;
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

function asModelSelectOption(candidate: CreativeModelCandidate): AdminModelSelectOption {
    return {
        id: candidate.modelRef || candidate.candidateId,
        modelRef: candidate.modelRef,
        providerId: candidate.providerId,
        modelId: candidate.modelId,
        type: candidate.modality?.toUpperCase(),
        provider: {
            id: candidate.providerId,
            name: candidate.providerName,
        },
        providerName: candidate.providerName,
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

export default function CreativeMediaPage() {
    const t = useT();
    const [data, setData] = useState<CreativeMediaData>(EMPTY_DATA);
    const [loading, setLoading] = useState(true);
    const [savingModels, setSavingModels] = useState(false);
    const [error, setError] = useState("");

    const fetchData = useCallback(async () => {
        setLoading(true);
        setError("");
        try {
            const [catalog, resolutions, recipes, assets, characterBibles, keyframes, jobs, editPlans, renders, qualityJobs, costLedger, safetyEvents, modelPreferences] = await Promise.all([
                fetch("/api/creative-media/catalog", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
                fetch("/api/creative-media/resolutions", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
                fetch("/api/creative-media/recipes", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
                fetch("/api/creative-media/assets", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
                fetch("/api/creative-media/character-bibles", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
                fetch("/api/creative-media/keyframes", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
                fetch("/api/creative-media/jobs", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
                fetch("/api/creative-media/edit-plans", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
                fetch("/api/creative-media/renders", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
                fetch("/api/creative-media/quality-jobs", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
                fetch("/api/creative-media/cost-ledger", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
                fetch("/api/creative-media/safety-events", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
                fetch("/api/creative-media/model-preferences", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
            ]);
            setData({
                catalog,
                resolutions,
                recipes: Array.isArray(recipes.recipes) ? recipes.recipes : [],
                assets: Array.isArray(assets.assets) ? assets.assets : [],
                characterBibles: Array.isArray(characterBibles.characterBibles) ? characterBibles.characterBibles : [],
                keyframes: Array.isArray(keyframes.keyframes) ? keyframes.keyframes : [],
                jobs: Array.isArray(jobs.jobs) ? jobs.jobs : [],
                editPlans: Array.isArray(editPlans.editPlans) ? editPlans.editPlans : [],
                renders: Array.isArray(renders.renders) ? renders.renders : [],
                qualityJobs: Array.isArray(qualityJobs.qualityJobs) ? qualityJobs.qualityJobs : [],
                costEntries: Array.isArray(costLedger.entries) ? costLedger.entries : [],
                safetyEvents: Array.isArray(safetyEvents.events) ? safetyEvents.events : [],
                modelPreferences: {
                    candidates: Array.isArray(modelPreferences.candidates) ? modelPreferences.candidates : [],
                    connectedOptions: Array.isArray(modelPreferences.connectedOptions) ? modelPreferences.connectedOptions : [],
                    diagnosticCandidates: Array.isArray(modelPreferences.diagnosticCandidates) ? modelPreferences.diagnosticCandidates : [],
                    operationRows: Array.isArray(modelPreferences.operationRows) ? modelPreferences.operationRows : [],
                    policies: modelPreferences.policies || {},
                    updatedAt: modelPreferences.updatedAt || "",
                    version: modelPreferences.version,
                },
            });
        } catch (err) {
            setError(String(err));
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        void fetchData();
    }, [fetchData]);

    const musicRecipes = data.recipes.filter((item) => text(item.modality, "") === "music");
    const connectedModelOptions = data.modelPreferences?.connectedOptions || [];
    const diagnosticCandidates = data.modelPreferences?.diagnosticCandidates || [];
    const operationRows = data.modelPreferences?.operationRows || [];
    const updateOperationRow = useCallback((operationKind: string, patch: Partial<CreativeOperationRow>) => {
        setData((current) => ({
            ...current,
            modelPreferences: {
                ...(current.modelPreferences || { candidates: [] }),
                operationRows: (current.modelPreferences?.operationRows || []).map((row) => (
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
                operationRows: (current.modelPreferences?.operationRows || []).map((row) => {
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
                    selections: (data.modelPreferences?.operationRows || []).map((row) => ({
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
    }, [data.modelPreferences?.operationRows]);

    return (
        <div className="space-y-6">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <div className="flex items-center gap-2">
                        <Sparkles className="h-7 w-7 text-violet-600" />
                        <h1 className="text-3xl font-bold tracking-tight">{t("app.admin.dashboard.creativeMedia.title")}</h1>
                    </div>
                    <p className="mt-2 text-muted-foreground">
                        {t("app.admin.dashboard.creativeMedia.description")}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                        {t("app.admin.dashboard.creativeMedia.musicBoundaryInline")}
                    </p>
                </div>
                <Button variant="outline" onClick={() => void fetchData()} disabled={loading}>
                    <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
                    {t("app.admin.dashboard.creativeMedia.refresh")}
                </Button>
            </div>

            {error ? (
                <Card className="border-rose-200 bg-rose-50">
                    <CardContent className="p-4 text-sm text-rose-700">{error}</CardContent>
                </Card>
            ) : null}

            <div className="grid gap-3 md:grid-cols-6">
                {[
                    { key: "catalog", label: t("app.admin.dashboard.creativeMedia.statCatalog"), value: countCatalogModalities(data.catalog), icon: Box },
                    { key: "recipes", label: t("app.admin.dashboard.creativeMedia.statRecipes"), value: data.recipes.length, icon: Sparkles },
                    { key: "assets", label: t("app.admin.dashboard.creativeMedia.statAssets"), value: data.assets.length, icon: Box },
                    { key: "characters", label: t("app.admin.dashboard.creativeMedia.statCharacters"), value: data.characterBibles.length, icon: UserRound },
                    { key: "keyframes", label: t("app.admin.dashboard.creativeMedia.statKeyframesJobs"), value: `${data.keyframes.length} / ${data.jobs.length}`, icon: Clapperboard },
                    { key: "qualityCost", label: t("app.admin.dashboard.creativeMedia.statQualityCost"), value: `${data.qualityJobs.length} / ${data.costEntries.length}`, icon: Clapperboard },
                ].map((item) => (
                    <Card key={item.key}>
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

            <Card>
                <CardHeader>
                    <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                            <CardTitle>{t("app.admin.dashboard.creativeMedia.modelPreferencesTitle")}</CardTitle>
                            <CardDescription>{t("app.admin.dashboard.creativeMedia.modelPreferencesDescription")}</CardDescription>
                        </div>
                        <Button onClick={() => void saveModelPreferences()} disabled={savingModels || loading}>
                            <Save className="mr-2 h-4 w-4" />
                            {savingModels ? t("app.admin.dashboard.creativeMedia.saving") : t("app.admin.dashboard.creativeMedia.saveModelPreferences")}
                        </Button>
                    </div>
                </CardHeader>
                <CardContent>
                    <div className="mb-4 rounded-lg border bg-muted/30 p-3 text-sm text-muted-foreground">
                        {t("app.admin.dashboard.creativeMedia.modelPreferencesConnectedHint")}
                    </div>
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
                                const options = connectedModelOptions
                                    .filter((candidate) => candidate.operationKind === row.operationKind)
                                    .map(asModelSelectOption);
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
                                                <ModelSelect
                                                    models={options}
                                                    value={selected[0] || ""}
                                                    emptyLabel={t("app.admin.dashboard.creativeMedia.selectNone")}
                                                    placeholder={t("app.admin.dashboard.creativeMedia.selectPrimaryModel")}
                                                    onValueChange={(value) => setOperationModelRef(row.operationKind, 0, value)}
                                                    showCompatibilityHint={false}
                                                />
                                            ) : (
                                                <div className="space-y-2">
                                                    <div className="text-sm text-muted-foreground">
                                                        {t("app.admin.dashboard.creativeMedia.noConnectedModels")}
                                                    </div>
                                                    <a className="text-sm font-medium text-primary hover:underline" href="/admin/model-hub">
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
                                            <Badge variant={options.length ? "default" : "secondary"}>
                                                {options.length ? `${options.length}` : t("app.admin.dashboard.creativeMedia.unavailable")}
                                            </Badge>
                                        </TableCell>
                                    </TableRow>
                                );
                            }) : <EmptyRow colSpan={7} label={t("app.admin.dashboard.creativeMedia.emptyModelPreferences")} />}
                        </TableBody>
                    </Table>
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
                </CardContent>
            </Card>

            <div className="grid gap-6 xl:grid-cols-2">
                <Card>
                    <CardHeader>
                        <CardTitle>{t("app.admin.dashboard.creativeMedia.recipesTitle")}</CardTitle>
                        <CardDescription>{t("app.admin.dashboard.creativeMedia.recipesDescription")}</CardDescription>
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
                                        <TableCell>{text(recipe.executionStatus)}</TableCell>
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
                        <CardDescription>{t("app.admin.dashboard.creativeMedia.assetsDescription")}</CardDescription>
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
                        <CardDescription>{t("app.admin.dashboard.creativeMedia.charactersDescription")}</CardDescription>
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
                        <CardDescription>{t("app.admin.dashboard.creativeMedia.keyframesDescription")}</CardDescription>
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
                        <CardDescription>{t("app.admin.dashboard.creativeMedia.qualityJobsDescription")}</CardDescription>
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
                                        <TableCell>{text(quality.status)}</TableCell>
                                    </TableRow>
                                )) : <EmptyRow colSpan={3} label={t("app.admin.dashboard.creativeMedia.emptyQualityJobs")} />}
                            </TableBody>
                        </Table>
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader>
                        <CardTitle>{t("app.admin.dashboard.creativeMedia.costLedgerTitle")}</CardTitle>
                        <CardDescription>{t("app.admin.dashboard.creativeMedia.costLedgerDescription")}</CardDescription>
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
                        <CardDescription>{t("app.admin.dashboard.creativeMedia.safetyEventsDescription")}</CardDescription>
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
                        <CardDescription>{t("app.admin.dashboard.creativeMedia.editPlansDescription")}</CardDescription>
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
                                        <TableCell>{text(plan.status)}</TableCell>
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
                        <CardDescription>{t("app.admin.dashboard.creativeMedia.renderJobsDescription")}</CardDescription>
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
                                        <TableCell>{text(render.status)}</TableCell>
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
                        <CardTitle>{t("app.admin.dashboard.creativeMedia.musicRecipesTitle")}</CardTitle>
                        <CardDescription>{t("app.admin.dashboard.creativeMedia.musicRecipesDescription")}</CardDescription>
                    </CardHeader>
                    <CardContent>
                        {musicRecipes.length ? <CompactJson value={musicRecipes.slice(0, 3)} /> : (
                            <div className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">{t("app.admin.dashboard.creativeMedia.emptyMusicRecipes")}</div>
                        )}
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader>
                        <CardTitle>{t("app.admin.dashboard.creativeMedia.catalogTitle")}</CardTitle>
                        <CardDescription>{t("app.admin.dashboard.creativeMedia.catalogDescription")}</CardDescription>
                    </CardHeader>
                    <CardContent>
                        <CompactJson value={data.catalog} />
                    </CardContent>
                </Card>
            </div>
        </div>
    );
}
