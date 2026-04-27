"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Box, Clapperboard, RefreshCw, Save, Sparkles, UserRound } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
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
    adapter: string;
    source?: string;
    available?: boolean;
    enabled?: boolean;
    priority?: number;
};

type CreativeModelPreferences = {
    version?: number;
    updatedAt?: string;
    candidates: CreativeModelCandidate[];
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
            const [catalog, resolutions, recipes, assets, characterBibles, keyframes, jobs, editPlans, renders, modelPreferences] = await Promise.all([
                fetch("/api/creative-media/catalog", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
                fetch("/api/creative-media/resolutions", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
                fetch("/api/creative-media/recipes", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
                fetch("/api/creative-media/assets", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
                fetch("/api/creative-media/character-bibles", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
                fetch("/api/creative-media/keyframes", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
                fetch("/api/creative-media/jobs", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
                fetch("/api/creative-media/edit-plans", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
                fetch("/api/creative-media/renders", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
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
                modelPreferences: {
                    candidates: Array.isArray(modelPreferences.candidates) ? modelPreferences.candidates : [],
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
    const modelCandidates = data.modelPreferences?.candidates || [];
    const updateModelCandidate = useCallback((candidateId: string, patch: Partial<CreativeModelCandidate>) => {
        setData((current) => ({
            ...current,
            modelPreferences: {
                ...(current.modelPreferences || { candidates: [] }),
                candidates: (current.modelPreferences?.candidates || []).map((candidate) => (
                    candidate.candidateId === candidateId ? { ...candidate, ...patch } : candidate
                )),
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
                    models: (data.modelPreferences?.candidates || []).map((candidate) => ({
                        candidateId: candidate.candidateId,
                        enabled: candidate.enabled !== false,
                        priority: Number(candidate.priority || 100),
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
    }, [data.modelPreferences?.candidates]);

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
                    { key: "editRender", label: t("app.admin.dashboard.creativeMedia.statEditRender"), value: `${data.editPlans.length} / ${data.renders.length}`, icon: Clapperboard },
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
                    <Table>
                        <TableHeader>
                                <TableRow>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableEnabled")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableMedia")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableOperation")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableProvider")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableModel")}</TableHead>
                                    <TableHead>{t("app.admin.dashboard.creativeMedia.tableAdapter")}</TableHead>
                                <TableHead>{t("app.admin.dashboard.creativeMedia.tablePriority")}</TableHead>
                                <TableHead>{t("app.admin.dashboard.creativeMedia.tableAvailable")}</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {modelCandidates.length ? modelCandidates.map((candidate) => (
                                <TableRow key={candidate.candidateId}>
                                    <TableCell>
                                        <Switch
                                            checked={candidate.enabled !== false}
                                            onCheckedChange={(checked) => updateModelCandidate(candidate.candidateId, { enabled: checked })}
                                        />
                                    </TableCell>
                                    <TableCell>
                                        <Badge variant="outline">{modalityLabel(t, candidate.modality)}</Badge>
                                    </TableCell>
                                    <TableCell className="font-mono text-xs">{text(candidate.operationKind)}</TableCell>
                                    <TableCell>
                                        <div className="font-medium">{text(candidate.providerName || candidate.providerId)}</div>
                                        <div className="text-xs text-muted-foreground">{text(candidate.source)}</div>
                                    </TableCell>
                                    <TableCell className="max-w-64 truncate font-mono text-xs">{text(candidate.modelId)}</TableCell>
                                    <TableCell className="font-mono text-xs">{text(candidate.adapter)}</TableCell>
                                    <TableCell>
                                        <Input
                                            className="w-24"
                                            type="number"
                                            min={1}
                                            max={999}
                                            value={Number(candidate.priority || 100)}
                                            onChange={(event) => updateModelCandidate(candidate.candidateId, { priority: Number(event.target.value || 100) })}
                                        />
                                    </TableCell>
                                    <TableCell>
                                        <Badge variant={candidate.available === false ? "secondary" : "default"}>
                                            {candidate.available === false ? t("app.admin.dashboard.creativeMedia.unavailable") : t("app.admin.dashboard.creativeMedia.available")}
                                        </Badge>
                                    </TableCell>
                                </TableRow>
                            )) : <EmptyRow colSpan={8} label={t("app.admin.dashboard.creativeMedia.emptyModelPreferences")} />}
                        </TableBody>
                    </Table>
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
