"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Box, Clapperboard, Music2, RefreshCw, Sparkles, UserRound } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

type CreativeMediaData = {
    catalog?: Record<string, unknown>;
    resolutions?: Record<string, unknown>;
    recipes: Array<Record<string, unknown>>;
    assets: Array<Record<string, unknown>>;
    characterBibles: Array<Record<string, unknown>>;
    keyframes: Array<Record<string, unknown>>;
    jobs: Array<Record<string, unknown>>;
};

const EMPTY_DATA: CreativeMediaData = {
    recipes: [],
    assets: [],
    characterBibles: [],
    keyframes: [],
    jobs: [],
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
    const [data, setData] = useState<CreativeMediaData>(EMPTY_DATA);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    const fetchData = useCallback(async () => {
        setLoading(true);
        setError("");
        try {
            const [catalog, resolutions, recipes, assets, characterBibles, keyframes, jobs] = await Promise.all([
                fetch("/api/creative-media/catalog", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
                fetch("/api/creative-media/resolutions", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
                fetch("/api/creative-media/recipes", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
                fetch("/api/creative-media/assets", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
                fetch("/api/creative-media/character-bibles", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
                fetch("/api/creative-media/keyframes", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
                fetch("/api/creative-media/jobs", { cache: "no-store" }).then((res) => res.json().catch(() => ({}))),
            ]);
            setData({
                catalog,
                resolutions,
                recipes: Array.isArray(recipes.recipes) ? recipes.recipes : [],
                assets: Array.isArray(assets.assets) ? assets.assets : [],
                characterBibles: Array.isArray(characterBibles.characterBibles) ? characterBibles.characterBibles : [],
                keyframes: Array.isArray(keyframes.keyframes) ? keyframes.keyframes : [],
                jobs: Array.isArray(jobs.jobs) ? jobs.jobs : [],
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

    return (
        <div className="space-y-6">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <div className="flex items-center gap-2">
                        <Sparkles className="h-7 w-7 text-violet-600" />
                        <h1 className="text-3xl font-bold tracking-tight">Creative Media Runtime</h1>
                    </div>
                    <p className="mt-2 text-muted-foreground">
                        只读治理面板：查看 recipe、asset ledger、角色 bible、关键帧、job 与 provider catalog。
                    </p>
                </div>
                <Button variant="outline" onClick={() => void fetchData()} disabled={loading}>
                    <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
                    刷新
                </Button>
            </div>

            {error ? (
                <Card className="border-rose-200 bg-rose-50">
                    <CardContent className="p-4 text-sm text-rose-700">{error}</CardContent>
                </Card>
            ) : null}

            <Card className="border-violet-200 bg-violet-50/70">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Music2 className="h-5 w-5" />
                        音乐边界
                    </CardTitle>
                    <CardDescription>
                        Creative Media 的音乐是 cue sheet、score brief、music reference 与未来生成计划；旧 MusicTrack URL 播放器只保留兼容 API。
                    </CardDescription>
                </CardHeader>
                <CardContent className="grid gap-3 text-sm text-slate-700 md:grid-cols-3">
                    <div className="rounded-md border bg-white p-3">不写入 /api/music</div>
                    <div className="rounded-md border bg-white p-3">不生成 audioUrl / musicUrl / tracks</div>
                    <div className="rounded-md border bg-white p-3">真实媒体输出必须进入 artifact / asset ledger</div>
                </CardContent>
            </Card>

            <div className="grid gap-3 md:grid-cols-5">
                {[
                    { label: "Catalog Modalities", value: countCatalogModalities(data.catalog), icon: Box },
                    { label: "Recipes", value: data.recipes.length, icon: Sparkles },
                    { label: "Assets", value: data.assets.length, icon: Box },
                    { label: "Characters", value: data.characterBibles.length, icon: UserRound },
                    { label: "Keyframes / Jobs", value: `${data.keyframes.length} / ${data.jobs.length}`, icon: Clapperboard },
                ].map((item) => (
                    <Card key={item.label}>
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

            <div className="grid gap-6 xl:grid-cols-2">
                <Card>
                    <CardHeader>
                        <CardTitle>Recipes</CardTitle>
                        <CardDescription>编译后的 provider-neutral recipe。音乐 recipe 使用双层类型。</CardDescription>
                    </CardHeader>
                    <CardContent>
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>ID</TableHead>
                                    <TableHead>类型</TableHead>
                                    <TableHead>状态</TableHead>
                                    <TableHead>更新</TableHead>
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
                                )) : <EmptyRow colSpan={4} label="暂无 recipe" />}
                            </TableBody>
                        </Table>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle>Assets</CardTitle>
                        <CardDescription>Creative Media 资产账本，不复制文件，不进入旧播放器曲库。</CardDescription>
                    </CardHeader>
                    <CardContent>
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>ID</TableHead>
                                    <TableHead>用途</TableHead>
                                    <TableHead>媒体</TableHead>
                                    <TableHead>版本</TableHead>
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
                                )) : <EmptyRow colSpan={4} label="暂无资产" />}
                            </TableBody>
                        </Table>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle>Character Bibles</CardTitle>
                        <CardDescription>角色一致性设定，供 recipe 与关键帧引用。</CardDescription>
                    </CardHeader>
                    <CardContent>
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>ID</TableHead>
                                    <TableHead>名称</TableHead>
                                    <TableHead>版本</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {data.characterBibles.length ? data.characterBibles.slice(0, 8).map((bible) => (
                                    <TableRow key={text(bible.characterBibleId)}>
                                        <TableCell className="max-w-40 truncate font-mono text-xs">{text(bible.characterBibleId)}</TableCell>
                                        <TableCell>{text(bible.name)}</TableCell>
                                        <TableCell>{text(bible.version)}</TableCell>
                                    </TableRow>
                                )) : <EmptyRow colSpan={3} label="暂无角色 bible" />}
                            </TableBody>
                        </Table>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle>Keyframes</CardTitle>
                        <CardDescription>首帧、尾帧、桥接帧和参考帧。</CardDescription>
                    </CardHeader>
                    <CardContent>
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>ID</TableHead>
                                    <TableHead>角色</TableHead>
                                    <TableHead>Recipe</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {data.keyframes.length ? data.keyframes.slice(0, 8).map((keyframe) => (
                                    <TableRow key={text(keyframe.keyframeId)}>
                                        <TableCell className="max-w-40 truncate font-mono text-xs">{text(keyframe.keyframeId)}</TableCell>
                                        <TableCell>{text(keyframe.role)}</TableCell>
                                        <TableCell className="max-w-40 truncate font-mono text-xs">{text(keyframe.recipeId)}</TableCell>
                                    </TableRow>
                                )) : <EmptyRow colSpan={3} label="暂无关键帧" />}
                            </TableBody>
                        </Table>
                    </CardContent>
                </Card>
            </div>

            <div className="grid gap-6 xl:grid-cols-2">
                <Card>
                    <CardHeader>
                        <CardTitle>Music Recipes</CardTitle>
                        <CardDescription>检查音乐计划是否保持 Creative Media 双层类型。</CardDescription>
                    </CardHeader>
                    <CardContent>
                        {musicRecipes.length ? <CompactJson value={musicRecipes.slice(0, 3)} /> : (
                            <div className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">暂无 music recipe</div>
                        )}
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader>
                        <CardTitle>Catalog</CardTitle>
                        <CardDescription>Provider matrix 摘要，只读。</CardDescription>
                    </CardHeader>
                    <CardContent>
                        <CompactJson value={data.catalog} />
                    </CardContent>
                </Card>
            </div>
        </div>
    );
}
