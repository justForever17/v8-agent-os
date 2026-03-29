"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { FileAudio, FileImage, FileText, FileVideo, Link2, Loader2, RefreshCw, Search } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useToast } from "@/components/ui/use-toast";
import { useLocale, useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";

type ArtifactKind = "all" | "image" | "video" | "audio" | "document" | "file";

interface ArtifactRecord {
    id?: string;
    artifactId?: string;
    artifact_kind?: string;
    kind?: string;
    mime_type?: string;
    mimeType?: string;
    title?: string;
    displayLabel?: string;
    displaySubtitle?: string;
    session_id?: string;
    sessionId?: string;
    run_id?: string;
    runId?: string;
    message_id?: string;
    messageId?: string;
    source_path?: string;
    sourcePath?: string;
    workspace_path?: string;
    workspacePath?: string;
    external_url?: string;
    externalUrl?: string;
    preview_url?: string;
    previewUrl?: string;
    created_at?: string;
    createdAt?: string;
    hasPreview?: boolean;
    metadata?: Record<string, unknown>;
}

const ARTIFACT_KINDS: ArtifactKind[] = ["image", "video", "audio", "document", "file"];

function getArtifactKind(artifact: ArtifactRecord): ArtifactKind {
    return ((artifact.kind || artifact.artifact_kind || "file") as ArtifactKind) || "file";
}

function getArtifactMime(artifact: ArtifactRecord): string {
    return artifact.mimeType || artifact.mime_type || "application/octet-stream";
}

function getArtifactPreview(artifact: ArtifactRecord): string | undefined {
    return artifact.previewUrl || artifact.preview_url || artifact.externalUrl || artifact.external_url;
}

function getArtifactSource(artifact: ArtifactRecord): string | undefined {
    return artifact.workspacePath || artifact.workspace_path || artifact.sourcePath || artifact.source_path;
}

function getArtifactCreatedAt(artifact: ArtifactRecord): string | undefined {
    return artifact.createdAt || artifact.created_at;
}

function getArtifactId(artifact: ArtifactRecord): string {
    return String(artifact.artifactId || artifact.id || "");
}

function getArtifactIcon(kind: ArtifactKind) {
    switch (kind) {
        case "image":
            return FileImage;
        case "video":
            return FileVideo;
        case "audio":
            return FileAudio;
        case "document":
            return FileText;
        default:
            return Link2;
    }
}

export function ArtifactExplorerPanel() {
    const { toast } = useToast();
    const t = useT();
    const { locale } = useLocale();
    const [loading, setLoading] = useState(true);
    const [detailLoading, setDetailLoading] = useState(false);
    const [artifacts, setArtifacts] = useState<ArtifactRecord[]>([]);
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [selectedArtifact, setSelectedArtifact] = useState<ArtifactRecord | null>(null);
    const [query, setQuery] = useState("");
    const [kindFilter, setKindFilter] = useState<ArtifactKind>("all");

    const artifactLabel = useCallback(
        (artifact: ArtifactRecord) => artifact.displayLabel || artifact.title || getArtifactId(artifact) || t(lt("未命名产物", "Unnamed artifact")),
        [t],
    );

    const artifactSubtitle = useCallback(
        (artifact: ArtifactRecord) => artifact.displaySubtitle || getArtifactSource(artifact) || getArtifactPreview(artifact) || t(lt("暂无路径信息", "No path yet")),
        [t],
    );

    const formatDateTime = useCallback(
        (value?: string) => {
            if (!value) return t(lt("未知时间", "Unknown time"));
            const date = new Date(value);
            if (Number.isNaN(date.getTime())) return value;
            return date.toLocaleString(locale === "en" ? "en-US" : "zh-CN", {
                hour12: false,
            });
        },
        [locale, t],
    );

    const artifactKindLabel = useCallback(
        (kind: ArtifactKind) =>
            kind === "image"
                ? t("图片")
                : kind === "video"
                    ? t("视频")
                    : kind === "audio"
                        ? t("音频")
                        : kind === "document"
                            ? t("文档")
                            : t("文件"),
        [t],
    );

    const loadArtifacts = useCallback(async () => {
        setLoading(true);
        try {
            const response = await fetch("/api/memory/artifacts?limit=160", { cache: "no-store" });
            if (!response.ok) {
                throw new Error(`Artifacts failed: ${response.status}`);
            }
            const data = await response.json();
            const list = Array.isArray(data?.artifacts) ? data.artifacts : [];
            setArtifacts(list);
            if (!selectedId && list.length > 0) {
                const firstId = getArtifactId(list[0]);
                if (firstId) {
                    setSelectedId(firstId);
                    setSelectedArtifact(list[0]);
                }
            }
        } catch (error) {
            console.error("Failed to load artifacts:", error);
            toast({
                title: t("Artifact Explorer 加载失败"),
                description: t("未能读取当前运行中的多模态产物。"),
                variant: "destructive",
            });
        } finally {
            setLoading(false);
        }
    }, [selectedId, t, toast]);

    const loadArtifactDetail = useCallback(async (artifactId: string, fallback?: ArtifactRecord) => {
        setDetailLoading(true);
        try {
            const response = await fetch(`/api/memory/artifacts/${encodeURIComponent(artifactId)}`, { cache: "no-store" });
            if (!response.ok) {
                throw new Error(`Artifact detail failed: ${response.status}`);
            }
            const data = await response.json();
            setSelectedArtifact(data);
        } catch (error) {
            console.error("Failed to load artifact detail:", error);
            if (fallback) {
                setSelectedArtifact(fallback);
            }
            toast({
                title: t("Artifact 详情加载失败"),
                description: t("当前先展示已缓存信息，你可以稍后重试。"),
                variant: "destructive",
            });
        } finally {
            setDetailLoading(false);
        }
    }, [t, toast]);

    useEffect(() => {
        void loadArtifacts();
    }, [loadArtifacts]);

    const filteredArtifacts = useMemo(() => {
        const normalized = query.trim().toLowerCase();
        return artifacts.filter((artifact) => {
            const kind = getArtifactKind(artifact);
            const keywordHit = !normalized || [
                artifactLabel(artifact),
                getArtifactId(artifact),
                artifact.session_id,
                artifact.sessionId,
                artifact.run_id,
                artifact.runId,
                artifact.message_id,
                artifact.messageId,
                getArtifactSource(artifact),
                getArtifactPreview(artifact),
            ]
                .filter(Boolean)
                .some((value) => String(value).toLowerCase().includes(normalized));
            const kindHit = kindFilter === "all" || kind === kindFilter;
            return keywordHit && kindHit;
        });
    }, [artifactLabel, artifacts, kindFilter, query]);

    const artifactStats = useMemo(() => {
        const counts: Record<ArtifactKind, number> = {
            all: artifacts.length,
            image: 0,
            video: 0,
            audio: 0,
            document: 0,
            file: 0,
        };
        for (const artifact of artifacts) {
            counts[getArtifactKind(artifact)] += 1;
        }
        return counts;
    }, [artifacts]);

    const selectedPreview = selectedArtifact ? getArtifactPreview(selectedArtifact) : undefined;
    const selectedKind = selectedArtifact ? getArtifactKind(selectedArtifact) : "file";
    const SelectedIcon = getArtifactIcon(selectedKind);

    return (
        <div className="space-y-4">
            <Card className="border-border/60">
                <CardHeader className="pb-4">
                    <CardTitle className="flex items-center justify-between gap-3 text-lg">
                        <span className="flex items-center gap-2">
                            <FileImage className="h-5 w-5 text-primary" />
                            {t("Artifact Explorer")}
                        </span>
                        <Button variant="outline" size="sm" onClick={() => void loadArtifacts()}>
                            <RefreshCw className="mr-2 h-4 w-4" />
                            {t("刷新")}
                        </Button>
                    </CardTitle>
                    <CardDescription>
                        {t("查看运行中产生的图片、视频、音频、文档和文件挂载记录。")}
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">
                        <Card className="border-border/50 bg-muted/10">
                            <CardContent className="p-4">
                                <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Artifacts</p>
                                <p className="mt-3 text-2xl font-semibold">{artifactStats.all}</p>
                                <p className="mt-1 text-xs text-muted-foreground">{t("统一挂载层总量")}</p>
                            </CardContent>
                        </Card>
                        {ARTIFACT_KINDS.map((kind) => {
                            const Icon = getArtifactIcon(kind);
                            return (
                                <Card key={kind} className="border-border/50 bg-muted/10">
                                    <CardContent className="p-4">
                                        <div className="flex items-center gap-2">
                                            <Icon className="h-4 w-4 text-primary" />
                                            <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">{kind}</p>
                                        </div>
                                        <p className="mt-3 text-2xl font-semibold">{artifactStats[kind]}</p>
                                        <p className="mt-1 text-xs text-muted-foreground">
                                            {kind === "image" ? t("图片与截图") :
                                                kind === "video" ? t("视频与录屏") :
                                                    kind === "audio" ? t("音频与语音") :
                                                        kind === "document" ? t("文档与结构化材料") :
                                                            t("其它二进制文件")}
                                        </p>
                                    </CardContent>
                                </Card>
                            );
                        })}
                    </div>

                    <div className="flex flex-col gap-3 lg:flex-row">
                        <div className="relative flex-1">
                            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                            <Input
                                value={query}
                                onChange={(event) => setQuery(event.target.value)}
                                placeholder={t("搜索标题、artifactId、sessionId、路径...")}
                                className="pl-9"
                            />
                        </div>
                        <Select value={kindFilter} onValueChange={(value) => setKindFilter(value as ArtifactKind)}>
                            <SelectTrigger className="w-full lg:w-[220px]">
                                <SelectValue placeholder={t("筛选类型")} />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all">{t("全部类型")}</SelectItem>
                                <SelectItem value="image">{t("图片")}</SelectItem>
                                <SelectItem value="video">{t("视频")}</SelectItem>
                                <SelectItem value="audio">{t("音频")}</SelectItem>
                                <SelectItem value="document">{t("文档")}</SelectItem>
                                <SelectItem value="file">{t("文件")}</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>

                    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.25fr)_380px]">
                        <ScrollArea className="h-[720px] rounded-2xl border border-border/60 bg-muted/5">
                            <div className="grid gap-3 p-4 md:grid-cols-2">
                                {loading ? (
                                    <div className="col-span-full flex h-40 items-center justify-center text-sm text-muted-foreground">
                                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                        {t("Artifact Explorer 加载中...")}
                                    </div>
                                ) : filteredArtifacts.length === 0 ? (
                                    <div className="col-span-full rounded-2xl border border-dashed p-8 text-center text-sm text-muted-foreground">
                                        {t("当前没有命中的 artifact。等图片/视频/文档进入运行链后，这里会自动出现。")}
                                    </div>
                                ) : (
                                    filteredArtifacts.map((artifact) => {
                                        const artifactId = getArtifactId(artifact);
                                        const kind = getArtifactKind(artifact);
                                        const Icon = getArtifactIcon(kind);
                                        const active = selectedId === artifactId;
                                        return (
                                            <button
                                                key={artifactId}
                                                type="button"
                                                onClick={() => {
                                                    setSelectedId(artifactId);
                                                    setSelectedArtifact(artifact);
                                                    void loadArtifactDetail(artifactId, artifact);
                                                }}
                                                className={`rounded-2xl border p-4 text-left transition-all ${
                                                    active
                                                        ? "border-primary/40 bg-primary/5 shadow-lg shadow-primary/5"
                                                        : "border-border/60 bg-card hover:-translate-y-0.5 hover:border-primary/25 hover:shadow-md"
                                                }`}
                                            >
                                                <div className="flex items-start gap-3">
                                                    <div className="rounded-2xl border border-primary/10 bg-primary/5 p-2.5 text-primary">
                                                        <Icon className="h-5 w-5" />
                                                    </div>
                                                    <div className="min-w-0 flex-1">
                                                        <div className="flex flex-wrap items-center gap-2">
                                                            <p className="truncate font-semibold">{artifactLabel(artifact)}</p>
                                                            <Badge variant="secondary">{artifactKindLabel(kind)}</Badge>
                                                            {artifact.hasPreview ? <Badge variant="outline">{t(lt("预览", "Preview"))}</Badge> : null}
                                                        </div>
                                                        <p className="mt-2 line-clamp-2 text-xs leading-5 text-muted-foreground">
                                                            {artifactSubtitle(artifact)}
                                                        </p>
                                                        <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                                                            <span className="rounded-full border px-2 py-1">{getArtifactMime(artifact)}</span>
                                                            {(artifact.sessionId || artifact.session_id) ? (
                                                                <span className="rounded-full border px-2 py-1 font-mono">
                                                                    {artifact.sessionId || artifact.session_id}
                                                                </span>
                                                            ) : null}
                                                        </div>
                                                        <p className="mt-3 text-[11px] text-muted-foreground/80">
                                                            {formatDateTime(getArtifactCreatedAt(artifact))}
                                                        </p>
                                                    </div>
                                                </div>
                                            </button>
                                        );
                                    })
                                )}
                            </div>
                        </ScrollArea>

                        <Card className="border-border/60 bg-gradient-to-b from-card to-muted/10">
                            <CardHeader>
                                <CardTitle className="flex items-center gap-2 text-base">
                                    <SelectedIcon className="h-4 w-4 text-primary" />
                                    {t("Artifact Detail")}
                                </CardTitle>
                                <CardDescription>
                                    {selectedArtifact ? t("查看当前选中 artifact 的完整路径、元数据和预览。") : t("点击左侧卡片查看详情。")}
                                </CardDescription>
                            </CardHeader>
                            <CardContent className="space-y-4">
                                {!selectedArtifact ? (
                                    <div className="rounded-2xl border border-dashed p-8 text-center text-sm text-muted-foreground">
                                        {t("请选择左侧 artifact 卡片。")}
                                    </div>
                                ) : (
                                    <>
                                        <div className="rounded-2xl border bg-muted/20 p-4">
                                            <div className="flex items-start justify-between gap-3">
                                                <div className="min-w-0">
                                                    <p className="truncate text-base font-semibold">{artifactLabel(selectedArtifact)}</p>
                                                    <p className="mt-1 font-mono text-xs text-muted-foreground">
                                                        {getArtifactId(selectedArtifact)}
                                                    </p>
                                                </div>
                                                {detailLoading ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /> : null}
                                            </div>
                                            <div className="mt-3 flex flex-wrap gap-2">
                                                <Badge>{artifactKindLabel(selectedKind)}</Badge>
                                                <Badge variant="secondary">{getArtifactMime(selectedArtifact)}</Badge>
                                                {(selectedArtifact.sessionId || selectedArtifact.session_id) ? (
                                                    <Badge variant="outline">
                                                        {t(lt("会话", "Session"))} {selectedArtifact.sessionId || selectedArtifact.session_id}
                                                    </Badge>
                                                ) : null}
                                            </div>
                                        </div>

                                        {selectedPreview && selectedKind === "image" ? (
                                            <div className="overflow-hidden rounded-2xl border bg-black/5">
                                                {/* eslint-disable-next-line @next/next/no-img-element */}
                                                <img src={selectedPreview} alt={artifactLabel(selectedArtifact)} className="max-h-[260px] w-full object-contain" />
                                            </div>
                                        ) : null}

                                        {selectedPreview && selectedKind === "video" ? (
                                            <div className="overflow-hidden rounded-2xl border bg-black/5">
                                                <video controls className="max-h-[260px] w-full" src={selectedPreview} />
                                            </div>
                                        ) : null}

                                        {selectedPreview && selectedKind === "audio" ? (
                                            <div className="rounded-2xl border p-4">
                                                <audio controls className="w-full" src={selectedPreview} />
                                            </div>
                                        ) : null}

                                        <div className="space-y-3 rounded-2xl border bg-muted/15 p-4 text-sm">
                                            <div>
                                                <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">{t("创建时间")}</p>
                                                <p className="mt-1 text-xs text-foreground/90">{formatDateTime(getArtifactCreatedAt(selectedArtifact))}</p>
                                            </div>
                                            <div>
                                                <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">{t(lt("会话", "Session"))}</p>
                                                <p className="mt-1 font-mono text-xs text-foreground/90">{selectedArtifact.sessionId || selectedArtifact.session_id || "—"}</p>
                                            </div>
                                            <div>
                                                <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Run</p>
                                                <p className="mt-1 font-mono text-xs text-foreground/90">{selectedArtifact.runId || selectedArtifact.run_id || "—"}</p>
                                            </div>
                                            <div>
                                                <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">{t(lt("消息", "Message"))}</p>
                                                <p className="mt-1 font-mono text-xs text-foreground/90">{selectedArtifact.messageId || selectedArtifact.message_id || "—"}</p>
                                            </div>
                                            <div>
                                                <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">{t(lt("工作区", "Workspace"))}</p>
                                                <p className="mt-1 break-all text-xs text-foreground/90">{selectedArtifact.workspacePath || selectedArtifact.workspace_path || "—"}</p>
                                            </div>
                                            <div>
                                                <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">{t(lt("来源", "Source"))}</p>
                                                <p className="mt-1 break-all text-xs text-foreground/90">{selectedArtifact.sourcePath || selectedArtifact.source_path || "—"}</p>
                                            </div>
                                            <div>
                                                <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">{t(lt("预览链接", "Preview URL"))}</p>
                                                {selectedPreview ? (
                                                    <a
                                                        href={selectedPreview}
                                                        target="_blank"
                                                        rel="noreferrer"
                                                        className="mt-1 block break-all text-xs text-primary underline-offset-4 hover:underline"
                                                    >
                                                        {selectedPreview}
                                                    </a>
                                                ) : (
                                                    <p className="mt-1 text-xs text-foreground/90">{t("暂无预览链接")}</p>
                                                )}
                                            </div>
                                        </div>

                                        <div className="rounded-2xl border bg-card/60 p-4">
                                            <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">{t(lt("元数据", "Metadata"))}</p>
                                            <pre className="mt-3 overflow-x-auto whitespace-pre-wrap break-all text-xs leading-6 text-foreground/85">
                                                {JSON.stringify(selectedArtifact.metadata || {}, null, 2)}
                                            </pre>
                                        </div>
                                    </>
                                )}
                            </CardContent>
                        </Card>
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}
