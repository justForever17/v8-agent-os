"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { FileAudio, FileCode2, FileImage, FileText, FileVideo, Link2, Loader2, RefreshCw, Search } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useToast } from "@/components/ui/use-toast";
import { useLocale, useT } from "@/components/providers/LocaleProvider";
import { TechnicalReferenceDetails } from "@/components/common/TechnicalReferenceDetails";
import { tg } from "@/i18n/admin-legacy";
import { fetchAdminJson, invalidateAdminJsonCache, peekAdminJsonCache } from "@/lib/admin-client-cache";

type ArtifactKind = "all" | "image" | "video" | "audio" | "document" | "code" | "file";

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
  origin?: string;
  artifactOrigin?: string;
  artifact_origin?: string;
  metadata?: Record<string, unknown>;
}

const ARTIFACT_KINDS: ArtifactKind[] = ["image", "video", "audio", "document", "code", "file"];
const ARTIFACTS_URL = "/api/memory/artifacts?limit=160";
const STORAGE_STATS_URL = "/api/storage-retention/stats";

interface ArtifactListPayload {
  artifacts?: ArtifactRecord[];
}

interface StorageStatsPayload {
  budgetComponents?: { artifacts?: { maxBytes?: number; usedBytes?: number } };
  config?: Record<string, unknown> & {
    budgets?: Record<string, { maxBytes?: number; usedBytes?: number }>;
  };
}

function bytesToMb(value?: number) {
  return Math.round(Number(value || 0) / 1024 / 1024);
}

function mbToBytes(value: string) {
  const mb = Number(value || 0);
  if (!Number.isFinite(mb) || mb <= 0) return 0;
  return Math.round(mb * 1024 * 1024);
}

function getArtifactKind(artifact: ArtifactRecord): ArtifactKind {
  const kind = String(artifact.kind || artifact.artifact_kind || "file") as ArtifactKind;
  return ARTIFACT_KINDS.includes(kind) ? kind : "file";
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

function getArtifactOrigin(artifact: ArtifactRecord): string {
  return String(artifact.origin || artifact.artifactOrigin || artifact.artifact_origin || artifact.metadata?.origin || "").trim();
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
    case "code":
      return FileCode2;
    default:
      return Link2;
  }
}

export function ArtifactExplorerPanel() {
  const { toast } = useToast();
  const t = useT();
  const { locale } = useLocale();
  const cachedArtifactPayload = peekAdminJsonCache<ArtifactListPayload>(ARTIFACTS_URL);
  const cachedArtifacts = Array.isArray(cachedArtifactPayload?.artifacts) ? cachedArtifactPayload.artifacts : [];
  const cachedStats = peekAdminJsonCache<StorageStatsPayload>(STORAGE_STATS_URL);
  const cachedBudget = cachedStats?.budgetComponents?.artifacts || cachedStats?.config?.budgets?.artifacts;
  const [loading, setLoading] = useState(cachedArtifactPayload === undefined);
  const [detailLoading, setDetailLoading] = useState(false);
  const [artifacts, setArtifacts] = useState<ArtifactRecord[]>(cachedArtifacts);
  const [selectedId, setSelectedId] = useState<string | null>(() => getArtifactId(cachedArtifacts[0] || {}) || null);
  const [selectedArtifact, setSelectedArtifact] = useState<ArtifactRecord | null>(cachedArtifacts[0] || null);
  const [query, setQuery] = useState("");
  const [kindFilter, setKindFilter] = useState<ArtifactKind>("all");
  const [artifactBudgetMb, setArtifactBudgetMb] = useState(() => cachedBudget?.maxBytes ? String(bytesToMb(cachedBudget.maxBytes)) : "");
  const [artifactBudgetUsedMb, setArtifactBudgetUsedMb] = useState<number | null>(() => cachedBudget?.usedBytes != null ? bytesToMb(cachedBudget.usedBytes) : null);

  const artifactLabel = useCallback(
    (artifact: ArtifactRecord) => artifact.displayLabel || artifact.title || getArtifactId(artifact) || t("components.memory.ArtifactExplorerPanel.kd43de6cf"),
    [t]
  );

  const artifactSubtitle = useCallback(
    (artifact: ArtifactRecord) => artifact.displaySubtitle || getArtifactSource(artifact) || getArtifactPreview(artifact) || t("components.memory.ArtifactExplorerPanel.k968c6ea6"),
    [t]
  );

  const formatDateTime = useCallback(
    (value?: string) => {
      if (!value) return t("components.memory.ArtifactExplorerPanel.k2be56351");
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return value;
      return date.toLocaleString(locale.startsWith("en") ? "en-US" : "zh-CN", {
        hour12: false
      });
    },
    [locale, t]
  );

  const artifactKindLabel = useCallback(
    (kind: ArtifactKind) =>
    kind === "image" ?
    t("components.memory.ArtifactExplorerPanel.k05527bea") :
    kind === "video" ?
    t("components.memory.ArtifactExplorerPanel.k7512b41f") :
    kind === "audio" ?
    t("components.memory.ArtifactExplorerPanel.kaeef0707") :
    kind === "document" ?
    t("components.memory.ArtifactExplorerPanel.k5fc5a6ad") :
    kind === "code" ?
    t("components.memory.ArtifactExplorerPanel.code") :
    t("components.memory.ArtifactExplorerPanel.ka9205a18"),
    [t]
  );

  const loadArtifacts = useCallback(async (force = false) => {
    if (peekAdminJsonCache<ArtifactListPayload>(ARTIFACTS_URL) === undefined) setLoading(true);
    try {
      const data = await fetchAdminJson<ArtifactListPayload>(ARTIFACTS_URL, { force });
      const list = Array.isArray(data.artifacts) ? data.artifacts : [];
      setArtifacts(list);
      setSelectedId((currentId) => currentId && list.some((artifact) => getArtifactId(artifact) === currentId)
        ? currentId
        : getArtifactId(list[0] || {}) || null);
      setSelectedArtifact((currentArtifact) => {
        const currentId = currentArtifact ? getArtifactId(currentArtifact) : "";
        return currentId && list.some((artifact) => getArtifactId(artifact) === currentId)
          ? currentArtifact
          : list[0] || null;
      });
    } catch (error) {
      console.error("Failed to load artifacts:", error);
      toast({
        title: t("components.memory.ArtifactExplorerPanel.k81ef3416"),
        description: t("components.memory.ArtifactExplorerPanel.k56ba1833"),
        variant: "destructive"
      });
    } finally {
      setLoading(false);
    }
  }, [t, toast]);

  const loadArtifactBudget = useCallback(async (force = false) => {
    const payload = await fetchAdminJson<StorageStatsPayload>(STORAGE_STATS_URL, { force }).catch(() => null);
    const budget = payload?.budgetComponents?.artifacts || payload?.config?.budgets?.artifacts;
    if (budget?.maxBytes) setArtifactBudgetMb(String(bytesToMb(Number(budget.maxBytes))));
    if (budget?.usedBytes != null) setArtifactBudgetUsedMb(bytesToMb(Number(budget.usedBytes)));
  }, []);

  const saveArtifactBudget = useCallback(async () => {
    if (!window.confirm(tg(t, "904b788f"))) return;
    const stats = await fetchAdminJson<StorageStatsPayload>(STORAGE_STATS_URL, { force: true }).catch(() => null);
    const config = stats?.config || {};
    const budgets = { ...(config.budgets || {}) };
    budgets.artifacts = { ...(budgets.artifacts || {}), maxBytes: mbToBytes(artifactBudgetMb) };
    const response = await fetch("/api/storage-retention/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...config, budgets })
    });
    if (!response.ok) {
      toast({
        title: t("components.memory.ArtifactExplorerPanel.k81ef3416"),
        description: `Storage config failed: ${response.status}`,
        variant: "destructive"
      });
      return;
    }
    toast({
      title: t("components.memory.ArtifactExplorerPanel.k876e8c06"),
      description: "Artifact budget saved."
    });
    invalidateAdminJsonCache("/api/storage-retention/");
    await loadArtifactBudget(true);
  }, [artifactBudgetMb, loadArtifactBudget, t, toast]);

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
        title: t("components.memory.ArtifactExplorerPanel.k115ce913"),
        description: t("components.memory.ArtifactExplorerPanel.k52473537"),
        variant: "destructive"
      });
    } finally {
      setDetailLoading(false);
    }
  }, [t, toast]);

  useEffect(() => {
    void loadArtifacts();
    void loadArtifactBudget();
  }, [loadArtifacts, loadArtifactBudget]);

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
      getArtifactPreview(artifact)].

      filter(Boolean).
      some((value) => String(value).toLowerCase().includes(normalized));
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
      code: 0,
      file: 0
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
                            {t("components.memory.ArtifactExplorerPanel.title")}
                        </span>
                        <Button variant="outline" size="sm" onClick={() => void loadArtifacts(true)}>
                            <RefreshCw className="mr-2 h-4 w-4" />
                            {t("components.memory.ArtifactExplorerPanel.k876e8c06")}
                        </Button>
                    </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-7">
                        <Card className="border-border/50 bg-muted/10">
                            <CardContent className="p-4">
                                <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Artifacts</p>
                                <p className="mt-3 text-2xl font-semibold">{artifactStats.all}</p>
                                <p className="mt-1 text-xs text-muted-foreground">{t("components.memory.ArtifactExplorerPanel.kb6803e57")}</p>
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
                                            {kind === "image" ? t("components.memory.ArtifactExplorerPanel.k83dd3d0f") :
                      kind === "video" ? t("components.memory.ArtifactExplorerPanel.kd0d9a483") :
                      kind === "audio" ? t("components.memory.ArtifactExplorerPanel.k05f8fe1a") :
                      kind === "document" ? t("components.memory.ArtifactExplorerPanel.k4b4dd70e") :
                      kind === "code" ? t("components.memory.ArtifactExplorerPanel.codeHint") :
                      t("components.memory.ArtifactExplorerPanel.k57ee4cb3")}
                                        </p>
                                    </CardContent>
                                </Card>);

            })}
                    </div>

                    <div className="flex flex-col gap-3 rounded-xl border border-border/60 bg-muted/10 p-3 text-xs text-muted-foreground md:flex-row md:items-center md:justify-between">
                        <div>
                            <div className="font-medium text-foreground">Artifact space budget</div>
                            <div>{tg(t, "297318c2")} {artifactBudgetUsedMb ?? "-"} {tg(t, "dde85c50")}</div>
                        </div>
                        <div className="flex items-center gap-2">
                            <Input
                className="h-8 w-32"
                type="number"
                min={1}
                value={artifactBudgetMb}
                onChange={(event) => setArtifactBudgetMb(event.target.value)} />

                            <span>MB</span>
                            <Button variant="outline" size="sm" onClick={() => void saveArtifactBudget()}>{t("components.memory.MemoryWorkflowsPanel.save")}</Button>
                        </div>
                    </div>

                    <div className="flex flex-col gap-3 lg:flex-row">
                        <div className="relative flex-1">
                            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                            <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={t("components.memory.ArtifactExplorerPanel.k7df1ad7e")}
                className="pl-9" />

                        </div>
                        <Select value={kindFilter} onValueChange={(value) => setKindFilter(value as ArtifactKind)}>
                            <SelectTrigger className="w-full lg:w-[220px]">
                                <SelectValue placeholder={t("components.memory.ArtifactExplorerPanel.k990c7dbc")} />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all">{t("components.memory.ArtifactExplorerPanel.k174a15d1")}</SelectItem>
                                <SelectItem value="image">{t("components.memory.ArtifactExplorerPanel.k05527bea")}</SelectItem>
                                <SelectItem value="video">{t("components.memory.ArtifactExplorerPanel.k7512b41f")}</SelectItem>
                                <SelectItem value="audio">{t("components.memory.ArtifactExplorerPanel.kaeef0707")}</SelectItem>
                                <SelectItem value="document">{t("components.memory.ArtifactExplorerPanel.k5fc5a6ad")}</SelectItem>
                                <SelectItem value="code">{t("components.memory.ArtifactExplorerPanel.code")}</SelectItem>
                                <SelectItem value="file">{t("components.memory.ArtifactExplorerPanel.ka9205a18")}</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>

                    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.25fr)_380px]">
                        <ScrollArea className="h-[720px] rounded-2xl border border-border/60 bg-muted/5">
                            <div className="grid gap-3 p-4 md:grid-cols-2">
                                {loading ?
                <div className="col-span-full flex h-40 items-center justify-center text-sm text-muted-foreground">
                                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                        {t("components.memory.ArtifactExplorerPanel.k0b1aab26")}
                                    </div> :
                filteredArtifacts.length === 0 ?
                <div className="col-span-full rounded-2xl border border-dashed p-8 text-center text-sm text-muted-foreground">
                                        {t("components.memory.ArtifactExplorerPanel.ka6179a25")}
                                    </div> :

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
                      active ?
                      "border-primary/40 bg-primary/5 shadow-lg shadow-primary/5" :
                      "border-border/60 bg-card hover:-translate-y-0.5 hover:border-primary/25 hover:shadow-md"}`
                      }>

                                                <div className="flex items-start gap-3">
                                                    <div className="rounded-2xl border border-primary/10 bg-primary/5 p-2.5 text-primary">
                                                        <Icon className="h-5 w-5" />
                                                    </div>
                                                    <div className="min-w-0 flex-1">
                                                        <div className="flex flex-wrap items-center gap-2">
                                                            <p className="truncate font-semibold">{artifactLabel(artifact)}</p>
                                                            <Badge variant="secondary">{artifactKindLabel(kind)}</Badge>
                                                            {getArtifactOrigin(artifact) ? <Badge variant="outline">{getArtifactOrigin(artifact)}</Badge> : null}
                                                            {artifact.hasPreview ? <Badge variant="outline">{t("components.memory.ArtifactExplorerPanel.k76932896")}</Badge> : null}
                                                        </div>
                                                        <p className="mt-2 line-clamp-2 text-xs leading-5 text-muted-foreground">
                                                            {artifactSubtitle(artifact)}
                                                        </p>
                                                        <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                                                            <span className="rounded-full border px-2 py-1">{getArtifactMime(artifact)}</span>
                                                        </div>
                                                        <p className="mt-3 text-[11px] text-muted-foreground/80">
                                                            {formatDateTime(getArtifactCreatedAt(artifact))}
                                                        </p>
                                                    </div>
                                                </div>
                                            </button>);

                })
                }
                            </div>
                        </ScrollArea>

                        <Card className="border-border/60 bg-gradient-to-b from-card to-muted/10">
                            <CardHeader>
                                <CardTitle className="flex items-center gap-2 text-base">
                                    <SelectedIcon className="h-4 w-4 text-primary" />
                                    {t("components.memory.ArtifactExplorerPanel.detailTitle")}
                                </CardTitle>
                            </CardHeader>
                            <CardContent className="space-y-4">
                                {!selectedArtifact ?
                <div className="rounded-2xl border border-dashed p-8 text-center text-sm text-muted-foreground">
                                        {t("components.memory.ArtifactExplorerPanel.k4664887c")}
                                    </div> :

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
                                                {getArtifactOrigin(selectedArtifact) ? <Badge variant="outline">{getArtifactOrigin(selectedArtifact)}</Badge> : null}
                                            </div>
                                        </div>

                                        {selectedPreview && selectedKind === "image" ?
                  <div className="overflow-hidden rounded-2xl border bg-black/5">
                                                {/* eslint-disable-next-line @next/next/no-img-element */}
                                                <img src={selectedPreview} alt={artifactLabel(selectedArtifact)} className="max-h-[260px] w-full object-contain" />
                                            </div> :
                  null}

                                        {selectedPreview && selectedKind === "video" ?
                  <div className="overflow-hidden rounded-2xl border bg-black/5">
                                                <video controls className="max-h-[260px] w-full" src={selectedPreview} />
                                            </div> :
                  null}

                                        {selectedPreview && selectedKind === "audio" ?
                  <div className="rounded-2xl border p-4">
                                                <audio controls className="w-full" src={selectedPreview} />
                                            </div> :
                  null}

                                        <div className="space-y-3 rounded-2xl border bg-muted/15 p-4 text-sm">
                                            <div>
                                                <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">{t("components.memory.ArtifactExplorerPanel.kc52804de")}</p>
                                                <p className="mt-1 text-xs text-foreground/90">{formatDateTime(getArtifactCreatedAt(selectedArtifact))}</p>
                                            </div>
                                            <TechnicalReferenceDetails items={[
                                                { label: t("components.common.sessionReference"), value: selectedArtifact.sessionId || selectedArtifact.session_id },
                                                { label: t("components.common.runReference"), value: selectedArtifact.runId || selectedArtifact.run_id },
                                                { label: t("components.common.messageReference"), value: selectedArtifact.messageId || selectedArtifact.message_id },
                                            ]} />
                                            <div>
                                                <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">{t("components.memory.ArtifactExplorerPanel.kea7f822f")}</p>
                                                <p className="mt-1 break-all text-xs text-foreground/90">{selectedArtifact.workspacePath || selectedArtifact.workspace_path || "—"}</p>
                                            </div>
                                            <div>
                                                <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">{t("components.memory.ArtifactExplorerPanel.ke7139376")}</p>
                                                <p className="mt-1 break-all text-xs text-foreground/90">{selectedArtifact.sourcePath || selectedArtifact.source_path || "—"}</p>
                                            </div>
                                            <div>
                                                <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">{t("components.memory.ArtifactExplorerPanel.k711160a4")}</p>
                                                {selectedPreview ?
                      <a
                        href={selectedPreview}
                        target="_blank"
                        rel="noreferrer"
                        className="mt-1 block break-all text-xs text-primary underline-offset-4 hover:underline">

                                                        {selectedPreview}
                                                    </a> :

                      <p className="mt-1 text-xs text-foreground/90">{t("components.memory.ArtifactExplorerPanel.k52e119f3")}</p>
                      }
                                            </div>
                                        </div>

                                        <div className="rounded-2xl border bg-card/60 p-4">
                                            <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">{t("components.memory.ArtifactExplorerPanel.k545ca57f")}</p>
                                            <pre className="mt-3 overflow-x-auto whitespace-pre-wrap break-all text-xs leading-6 text-foreground/85">
                                                {JSON.stringify(selectedArtifact.metadata || {}, null, 2)}
                                            </pre>
                                        </div>
                                    </>
                }
                            </CardContent>
                        </Card>
                    </div>
                </CardContent>
            </Card>
        </div>);

}
