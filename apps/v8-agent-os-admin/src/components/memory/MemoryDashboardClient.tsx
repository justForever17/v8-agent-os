"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Brain, CheckSquare, Database, Edit2, GitCompareArrows, Loader2, Network, RefreshCw, Search, Tag, Trash2, Workflow } from "lucide-react";
import { ArtifactExplorerPanel } from "@/components/memory/ArtifactExplorerPanel";
import DocumentUploader from "@/components/memory/DocumentUploader";
import { EditKnowledgeDialog } from "@/components/memory/EditKnowledgeDialog";
import GraphViewer from "@/components/memory/GraphViewer";
import MemoryAgentChat from "@/components/memory/MemoryAgentChat";
import MemoryConfigPanel from "@/components/memory/MemoryConfigPanel";
import MemoryContextPanel from "@/components/memory/MemoryContextPanel";
import MemoryLogsPanel from "@/components/memory/MemoryLogsPanel";
import MemoryRuntimeDiagnosticsPanel from "@/components/memory/MemoryRuntimeDiagnosticsPanel";
import MemorySectionNav, { type MemorySectionKey } from "@/components/memory/MemorySectionNav";
import MemoryWorkflowsPanel from "@/components/memory/MemoryWorkflowsPanel";
import { PreferencesManager } from "@/components/memory/PreferencesManager";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/use-toast";
import { useT } from "@/components/providers/LocaleProvider";
import { tg } from "@/i18n/admin-legacy";
import { TechnicalReferenceDetails } from "@/components/common/TechnicalReferenceDetails";
import { fetchAdminJson, invalidateAdminJsonCache, peekAdminJsonCache } from "@/lib/admin-client-cache";
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type DashboardData = any;
interface KnowledgeItem {
  id: string;
  fact: string;
  category: string;
  scope: string;
  status?: string;
  lifecycle_state?: string;
  maintainer_source?: string;
  confidence?: number;
  effective_confidence?: number;
  lineage_id?: string;
  revision_no?: number;
  importance?: number;
  durability?: string;
  [key: string]: unknown;
}
interface ResolutionCandidate {
  id: string;
  proposed_relation: string;
  similarity?: number;
  reason?: string;
  candidate_fact: string;
  candidate_scope: string;
  candidate_category: string;
  target_fact?: string;
  target_scope?: string;
  target_category?: string;
  source_kind?: "human_edit" | "document" | "network" | "historical_migration" | "conversation";
  source_observation_count?: number;
  candidate_source_session?: string;
}
interface KnowledgeHealth {
  projection?: {
    state?: string;
    backlog?: number;
    pendingResolutionCount?: number;
    outbox?: Record<string, number>;
    canonical?: {
      active?: number;
      total?: number;
    };
    json?: {
      state?: string;
      driftScopeCount?: number;
    };
    vector?: {
      state?: string;
      missing?: number;
      orphaned?: number;
    };
  };
  graph?: {
    relations?: number;
    legacyArchivedRelations?: number;
    sourceCoverage?: number;
  };
}
interface KnowledgeListPayload {
  items?: KnowledgeItem[];
}
interface ResolutionCandidatesPayload {
  items?: ResolutionCandidate[];
}
const MEMORY_DASHBOARD_URL = "/api/memory/dashboard";
const MEMORY_KNOWLEDGE_URL = "/api/memory/knowledge";
const MEMORY_QUARANTINE_URL = "/api/memory/knowledge?scope=global&status=quarantined&limit=100";
const MEMORY_CANDIDATES_URL = "/api/memory/knowledge-resolution-candidates?limit=100";
const MEMORY_HEALTH_URL = "/api/memory/knowledge-health";
function formatScopeLabel(scope: string | undefined, labels: { global: string; project: string; workspace: string; channel: string }) {
  const value = String(scope || "global");
  if (value === "global") return labels.global;
  if (value.startsWith("project:")) return `${labels.project} · ${value.slice(8)}`;
  if (value.startsWith("workspace:")) return `${labels.workspace} · ${value.slice(10)}`;
  if (value.startsWith("channel:")) return `${labels.channel} · ${value.slice(8)}`;
  return value;
}
function lifecycleLabel(value: string | undefined, labels: Record<string, string>) {
  return labels[String(value || "active")] || String(value || labels.active);
}
const VALID_TABS = new Set(["context", "preferences", "logs", "knowledge", "workflows", "artifacts", "graph", "agent", "upload", "config", "runtime"]);
export default function MemoryDashboardClient({ initialRequestedTab = "preferences" }: {initialRequestedTab?: string;}) {
  const { toast } = useToast();
  const t = useT();
  const requestedTab = initialRequestedTab || "preferences";
  const scopeLabels = useMemo(() => ({
    global: t("app.admin.dashboard.memory.scope.global"),
    project: t("app.admin.dashboard.memory.scope.project"),
    workspace: t("app.admin.dashboard.memory.scope.workspace"),
    channel: t("app.admin.dashboard.memory.scope.channel")
  }), [t]);
  const lifecycleLabels = useMemo(() => ({
    active: t("app.admin.dashboard.memory.lifecycle.active"),
    stale: t("app.admin.dashboard.memory.lifecycle.stale"),
    superseded: t("app.admin.dashboard.memory.lifecycle.superseded"),
    tombstoned: t("app.admin.dashboard.memory.lifecycle.tombstoned"),
    quarantined: t("app.admin.dashboard.memory.lifecycle.quarantined")
  }), [t]);
  const activeTab = VALID_TABS.has(requestedTab) ? requestedTab : "preferences";
  const cachedDashboard = peekAdminJsonCache<DashboardData>(MEMORY_DASHBOARD_URL);
  const cachedKnowledge = peekAdminJsonCache<KnowledgeListPayload>(MEMORY_KNOWLEDGE_URL);
  const cachedQuarantine = peekAdminJsonCache<KnowledgeListPayload>(MEMORY_QUARANTINE_URL);
  const cachedCandidates = peekAdminJsonCache<ResolutionCandidatesPayload>(MEMORY_CANDIDATES_URL);
  const cachedHealth = peekAdminJsonCache<KnowledgeHealth>(MEMORY_HEALTH_URL);
  const [data, setData] = useState<DashboardData>(cachedDashboard ?? null);
  const [loading, setLoading] = useState(cachedDashboard === undefined);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<KnowledgeItem[]>([]);
  const [searching, setSearching] = useState(false);
  const [knowledge, setKnowledge] = useState<KnowledgeItem[]>(cachedKnowledge?.items || []);
  const [quarantinedGlobalKnowledge, setQuarantinedGlobalKnowledge] = useState<KnowledgeItem[]>(cachedQuarantine?.items || []);
  const [resolutionCandidates, setResolutionCandidates] = useState<ResolutionCandidate[]>(cachedCandidates?.items || []);
  const [knowledgeHealth, setKnowledgeHealth] = useState<KnowledgeHealth | null>(cachedHealth ?? null);
  const [resolvingCandidateId, setResolvingCandidateId] = useState<string | null>(null);
  const [editTarget, setEditTarget] = useState<KnowledgeItem | null>(null);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [graphFilter, setGraphFilter] = useState("");
  const [clearingDiagnostics, setClearingDiagnostics] = useState(false);
  const loadDashboard = useCallback(async (force = false) => {
    if (peekAdminJsonCache(MEMORY_DASHBOARD_URL) === undefined) setLoading(true);
    try {
      const json = await fetchAdminJson<DashboardData>(MEMORY_DASHBOARD_URL, { force });
      setData(json);
    }
    catch (err) {
      console.error("Failed to load memory dashboard:", err);
      toast({
        title: t("app.admin.dashboard.memory.page.k68f06052"),
        description: t("app.admin.dashboard.memory.page.kb94dad69"),
        variant: "destructive"
      });
    } finally
    {
      setLoading(false);
    }
  }, [t, toast]);
  const loadKnowledge = useCallback(async (force = false) => {
    try {
      const [activeJson, quarantinedJson, candidateJson, healthJson] = await Promise.all([
        fetchAdminJson<KnowledgeListPayload>(MEMORY_KNOWLEDGE_URL, { force }),
        fetchAdminJson<KnowledgeListPayload>(MEMORY_QUARANTINE_URL, { force }),
        fetchAdminJson<ResolutionCandidatesPayload>(MEMORY_CANDIDATES_URL, { force }).catch(() => peekAdminJsonCache<ResolutionCandidatesPayload>(MEMORY_CANDIDATES_URL) || { items: [] }),
        fetchAdminJson<KnowledgeHealth>(MEMORY_HEALTH_URL, { force }).catch(() => peekAdminJsonCache<KnowledgeHealth>(MEMORY_HEALTH_URL) || null)
      ]);
      setKnowledge(activeJson.items || []);
      setQuarantinedGlobalKnowledge(quarantinedJson.items || []);
      setResolutionCandidates(candidateJson?.items || []);
      setKnowledgeHealth(healthJson);
    }
    catch (err) {
      console.error("Failed to load knowledge:", err);
      toast({
        title: t("app.admin.dashboard.memory.page.kfd3185e2"),
        description: t("app.admin.dashboard.memory.page.kfd129a51"),
        variant: "destructive"
      });
    }
  }, [t, toast]);
  const handleResolveCandidate = useCallback(async (candidateId: string, resolution: "reinforce" | "replace" | "refine" | "discard") => {
    setResolvingCandidateId(candidateId);
    try {
      const response = await fetch(`/api/memory/knowledge-resolution-candidates/${candidateId}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resolution })
      });
      if (!response.ok) throw new Error(`Resolve failed: ${response.status}`);
      await loadKnowledge(true);
      toast({ title: t("app.admin.dashboard.memory.pendingUpdates.resolvedTitle"), description: t("app.admin.dashboard.memory.pendingUpdates.resolvedDescription") });
    } catch (error) {
      console.error("Resolve knowledge candidate failed:", error);
      toast({ title: t("app.admin.dashboard.memory.pendingUpdates.failedTitle"), description: t("app.admin.dashboard.memory.pendingUpdates.failedDescription"), variant: "destructive" });
    } finally {
      setResolvingCandidateId(null);
    }
  }, [loadKnowledge, t, toast]);
  useEffect(() => {
    void loadDashboard();
    void loadKnowledge();
  }, [loadDashboard, loadKnowledge]);
  const handleDeleteKnowledge = useCallback(async (id: string) => {
    if (!window.confirm(t("app.admin.dashboard.memory.page.k67c6eae2")))
    return;
    try {
      const res = await fetch(`/api/memory/knowledge/${id}`, { method: "DELETE" });
      if (!res.ok) {
        throw new Error(`Delete failed: ${res.status}`);
      }
      setKnowledge((prev) => prev.filter((k) => k.id !== id));
      setQuarantinedGlobalKnowledge((prev) => prev.filter((k) => k.id !== id));
      setSearchResults((prev) => prev.filter((k) => k.id !== id));
      invalidateAdminJsonCache(MEMORY_KNOWLEDGE_URL);
      void loadKnowledge(true);
    }
    catch (err) {
      console.error("Delete failed:", err);
      toast({
        title: t("app.admin.dashboard.memory.page.k0915ccdf"),
        description: t("app.admin.dashboard.memory.page.kac10f278"),
        variant: "destructive"
      });
    }
  }, [loadKnowledge, t, toast]);
  const handleEditKnowledge = useCallback((item: KnowledgeItem) => {
    setEditTarget(item);
    setEditDialogOpen(true);
  }, []);
  const handleRestoreKnowledge = useCallback(async (item: KnowledgeItem) => {
    try {
      const res = await fetch(`/api/memory/knowledge/${item.id}/restore`, { method: "POST" });
      if (!res.ok) {
        throw new Error(`Restore failed: ${res.status}`);
      }
      await loadKnowledge(true);
      toast({
        title: tg(t, "8564783d"),
        description: t("app.admin.dashboard.memory.restoreCreatedRevision")
      });
    }
    catch (err) {
      console.error("Restore knowledge failed:", err);
      toast({
        title: tg(t, "76842a03"),
        description: tg(t, "1968e3d4"),
        variant: "destructive"
      });
    }
  }, [loadKnowledge, t, toast]);
  const handleRevalidateKnowledge = useCallback(async (item: KnowledgeItem) => {
    try {
      const res = await fetch(`/api/memory/knowledge/${item.id}/revalidate`, { method: "POST" });
      if (!res.ok) {
        throw new Error(`Revalidate failed: ${res.status}`);
      }
      await loadKnowledge(true);
      toast({
        title: tg(t, "ec481ba6"),
        description: t("app.admin.dashboard.memory.revalidated")
      });
    }
    catch (err) {
      console.error("Revalidate knowledge failed:", err);
      toast({
        title: tg(t, "86bc5b62"),
        description: tg(t, "d3334e5d"),
        variant: "destructive"
      });
    }
  }, [loadKnowledge, t, toast]);
  const handleSaveKnowledge = useCallback(async (id: string, updated: {
    fact: string;
    category: string;
    scope: string;
  }) => {
    const res = await fetch(`/api/memory/knowledge/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...updated, maintainerSource: "human_admin" })
    });
    if (!res.ok) {
      throw new Error(`Save failed: ${res.status}`);
    }
    await loadKnowledge(true);
    toast({
      title: t("app.admin.dashboard.memory.page.kddf0235b"),
      description: t("app.admin.dashboard.memory.knowledgeVersionCreated")
    });
  }, [loadKnowledge, t, toast]);
  const toggleSelect = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else
      {
        next.add(id);
      }
      return next;
    });
  }, []);
  const handleBulkDelete = useCallback(async () => {
    const confirmMessage = t("app.admin.dashboard.memory.page.keaaa6351", {
      selectedIds_size: selectedIds.size
    });
    if (!window.confirm(confirmMessage))
    return;
    setBulkDeleting(true);
    try {
      await Promise.all(Array.from(selectedIds).map((id) => fetch(`/api/memory/knowledge/${id}`, { method: "DELETE" })));
      setKnowledge((prev) => prev.filter((item) => !selectedIds.has(item.id)));
      setSearchResults((prev) => prev.filter((item) => !selectedIds.has(item.id)));
      invalidateAdminJsonCache(MEMORY_KNOWLEDGE_URL);
      setSelectedIds(new Set());
      void loadKnowledge(true);
      toast({
        title: t("app.admin.dashboard.memory.page.k19c9dddb"),
        description: t("app.admin.dashboard.memory.page.k52850139")
      });
    } finally
    {
      setBulkDeleting(false);
    }
  }, [loadKnowledge, selectedIds, t, toast]);
  const handleSelectAll = useCallback(() => {
    setSelectedIds((prev) => prev.size === knowledge.length ?
    new Set() :
    new Set(knowledge.map((item) => item.id)));
  }, [knowledge]);
  const handleSearch = useCallback(async () => {
    if (!searchQuery.trim())
    return;
    setSearching(true);
    try {
      const res = await fetch(`/api/memory/search?q=${encodeURIComponent(searchQuery)}`);
      if (!res.ok) {
        throw new Error(`Search failed: ${res.status}`);
      }
      const json = await res.json();
      setSearchResults(json.results || []);
    }
    catch (err) {
      console.error("Search failed:", err);
      toast({
        title: t("app.admin.dashboard.memory.page.k03675db6"),
        description: t("app.admin.dashboard.memory.page.k2178e002"),
        variant: "destructive"
      });
    } finally
    {
      setSearching(false);
    }
  }, [searchQuery, t, toast]);
  const handleClearDiagnostics = useCallback(async () => {
    if (!window.confirm(t("app.admin.dashboard.memory.page.kf93c285a"))) {
      return;
    }
    setClearingDiagnostics(true);
    try {
      const res = await fetch("/api/memory/dashboard", { method: "DELETE" });
      if (!res.ok) {
        throw new Error(`Clear diagnostics failed: ${res.status}`);
      }
      const payload = await res.json();
      toast({
        title: t("app.admin.dashboard.memory.page.kbefe1442"),
        description: t("app.admin.dashboard.memory.page.k9597b4d0", {
          payload_deletedRuns_0: payload?.deletedRuns || 0,
          payload_deletedInvocations_0: payload?.deletedInvocations || 0
        })
      });
      await loadDashboard(true);
    }
    catch (err) {
      console.error("Failed to clear memory diagnostics:", err);
      toast({
        title: t("app.admin.dashboard.memory.page.k63924a8e"),
        description: t("app.admin.dashboard.memory.page.ke465f890"),
        variant: "destructive"
      });
    } finally
    {
      setClearingDiagnostics(false);
    }
  }, [loadDashboard, t, toast]);
  const stats = useMemo(() => ({
    preferenceCount: data?.preferences?.total || 0,
    preferenceScopes: data?.preferences?.scopes?.length || 0,
    workflowCandidateCount: data?.workflows?.reusableCandidateCount || 0,
    workflowPendingCandidateCount: data?.workflows?.candidateCount || 0,
    workflowHintDeliveryCount7d: data?.workflows?.hintDeliveryCount7d || 0,
    knowledgeCount: data?.knowledge?.count || 0,
    graphEntities: data?.graph?.entities || 0,
    graphRelations: data?.graph?.relations || 0
  }), [data]);
  if (loading) {
    return <div className="flex h-96 items-center justify-center">
                <Loader2 className="h-8 w-8 animate-spin" />
            </div>;
  }
  return <div className="space-y-6">
            <EditKnowledgeDialog item={editTarget} open={editDialogOpen} onOpenChange={setEditDialogOpen} onSave={handleSaveKnowledge} />

            <div className="flex items-center justify-between gap-4">
                <div>
                    <h1 className="flex items-center gap-3 text-3xl font-bold">
                        <Brain className="h-8 w-8 text-primary" />
                        {t("app.admin.dashboard.memory.page.kd5b4901a")}
                    </h1>
                    <p className="mt-1 text-muted-foreground">{t("app.admin.dashboard.memory.page.memorySevenLayersSubtitle")}</p>
                </div>
                <div className="flex items-center gap-2">
                    {activeTab === "runtime" ? <Button variant="destructive" size="sm" onClick={() => void handleClearDiagnostics()} disabled={clearingDiagnostics}>
                            {clearingDiagnostics ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Trash2 className="mr-2 h-4 w-4" />}
                            {t("app.admin.dashboard.memory.page.k0c1ae6a0")}
                        </Button> : null}
                    <Button variant="outline" size="sm" onClick={() => {void loadDashboard(true);void loadKnowledge(true);}}>
                        <RefreshCw className="mr-2 h-4 w-4" />
                        {t("app.admin.dashboard.memory.page.k876e8c06")}
                    </Button>
                </div>
            </div>

            <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
                <Card className="border-border/60">
                    <CardHeader className="pb-2">
                        <CardTitle className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                            <Tag className="h-4 w-4" /> {t("app.admin.dashboard.memory.page.ka5cb9483")}
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-3xl font-bold">{stats.preferenceCount}</p>
                        <p className="mt-1 text-xs text-muted-foreground">{stats.preferenceScopes} {t("app.admin.dashboard.memory.page.kc232e82c")}</p>
                    </CardContent>
                </Card>

                <Card className="border-border/60">
                    <CardHeader className="pb-2">
                        <CardTitle className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                            <Workflow className="h-4 w-4" /> {t("app.admin.dashboard.memory.page.kaaf4b147")}
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-3xl font-bold">{stats.workflowCandidateCount}</p>
                        <p className="mt-1 text-xs text-muted-foreground">
                            {t("app.admin.dashboard.memory.page.workflowSummary", {
              candidateCount: stats.workflowPendingCandidateCount,
              deliveryCount: stats.workflowHintDeliveryCount7d
            })}
                        </p>
                    </CardContent>
                </Card>

                <Card className="border-border/60">
                    <CardHeader className="pb-2">
                        <CardTitle className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                            <Database className="h-4 w-4" /> {t("app.admin.dashboard.memory.page.k9a019ed3")}
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-3xl font-bold">{stats.knowledgeCount}</p>
                        <p className="mt-1 text-xs text-muted-foreground">{t("app.admin.dashboard.memory.page.k3c631c87")}</p>
                    </CardContent>
                </Card>

                <Card className="border-border/60">
                    <CardHeader className="pb-2">
                        <CardTitle className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                            <Network className="h-4 w-4" /> {t("app.admin.dashboard.memory.page.k7833c539")}
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-3xl font-bold">{stats.graphEntities}</p>
                        <p className="mt-1 text-xs text-muted-foreground">{stats.graphRelations} {t("app.admin.dashboard.memory.page.ke037a1d4")}</p>
                    </CardContent>
                </Card>
            </div>

            <Tabs value={activeTab} className="space-y-4">
                <MemorySectionNav activeKey={activeTab as MemorySectionKey} />

                <TabsContent value="context" className="space-y-4">
                    {activeTab === "context" ? <MemoryContextPanel /> : null}
                </TabsContent>

                <TabsContent value="preferences" className="space-y-4">
                    <PreferencesManager />
                </TabsContent>

                <TabsContent value="logs" className="space-y-4">
                    {activeTab === "logs" ? <MemoryLogsPanel /> : null}
                </TabsContent>

                <TabsContent value="knowledge" className="space-y-4">
                    <Card className="border-border/60">
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2 text-lg">
                                <Search className="h-5 w-5" /> {t("app.admin.dashboard.memory.page.kd0c31f99")}
                            </CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="flex gap-2">
                                <Input placeholder={t("app.admin.dashboard.memory.page.ke11a452c")} value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} onKeyDown={(event) => event.key === "Enter" && void handleSearch()} />
                                <Button onClick={() => void handleSearch()} disabled={searching}>
                                    {searching ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
                                </Button>
                            </div>

                            {searchResults.length > 0 ? <div className="mt-4 space-y-2">
                                    <p className="text-xs text-muted-foreground">{searchResults.length} {t("app.admin.dashboard.memory.page.k8aefd04a")}</p>
                                    {searchResults.map((result, index) => <div key={`${result.id}-${index}`} className="group flex items-start gap-3 rounded-lg border bg-muted/30 p-3">
                                            <div className="min-w-0 flex-1">
                                                <div className="mb-1 flex items-center gap-2">
                                                    <span className="rounded bg-primary/10 px-1.5 py-0.5 font-mono text-xs text-primary">{result.scope}</span>
                                                    <span className="text-xs text-muted-foreground">{result.category}</span>
                                                </div>
                                                <p className="text-sm">{result.fact}</p>
                                            </div>
                                            <div className="flex gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                                                <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-primary" onClick={() => handleEditKnowledge(result)}>
                                                    <Edit2 className="h-4 w-4" />
                                                </Button>
                                                <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-destructive" onClick={() => void handleDeleteKnowledge(result.id)}>
                                                    <Trash2 className="h-4 w-4" />
                                                </Button>
                                            </div>
                                        </div>)}
                                </div> : null}
                        </CardContent>
                    </Card>

                    <Card className="border-border/60">
                        <CardHeader className="pb-3">
                            <CardTitle className="flex items-center gap-2 text-lg">
                                <GitCompareArrows className="h-5 w-5" /> {t("app.admin.dashboard.memory.knowledgeHealth.title")}
                            </CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                                <div className="rounded-xl border border-border bg-muted/30 p-3">
                                    <div className="text-xs text-muted-foreground">{t("app.admin.dashboard.memory.knowledgeHealth.canonical")}</div>
                                    <div className="mt-1 text-sm font-semibold text-foreground">
                                        {t("app.admin.dashboard.memory.knowledgeHealth.canonicalCount", {
                                          active: knowledgeHealth?.projection?.canonical?.active || 0,
                                          total: knowledgeHealth?.projection?.canonical?.total || 0
                                        })}
                                    </div>
                                </div>
                                <div className="rounded-xl border border-border bg-muted/30 p-3">
                                    <div className="text-xs text-muted-foreground">{t("app.admin.dashboard.memory.knowledgeHealth.projection")}</div>
                                    <div className="mt-1 text-sm font-semibold text-foreground">
                                        {knowledgeHealth?.projection?.state === "ready" ? t("app.admin.dashboard.memory.knowledgeHealth.synced") : knowledgeHealth?.projection?.state === "degraded" ? t("app.admin.dashboard.memory.knowledgeHealth.degraded", { count: knowledgeHealth?.projection?.backlog || 0 }) : t("app.admin.dashboard.memory.knowledgeHealth.syncing", { count: knowledgeHealth?.projection?.backlog || 0 })}
                                    </div>
                                </div>
                                <div className="rounded-xl border border-border bg-muted/30 p-3">
                                    <div className="text-xs text-muted-foreground">{t("app.admin.dashboard.memory.knowledgeHealth.pending")}</div>
                                    <div className="mt-1 text-sm font-semibold text-foreground">{resolutionCandidates.length}</div>
                                </div>
                                <div className="rounded-xl border border-border bg-muted/30 p-3">
                                    <div className="text-xs text-muted-foreground">{t("app.admin.dashboard.memory.knowledgeHealth.graphCoverage")}</div>
                                    <div className="mt-1 text-sm font-semibold text-foreground">{Math.round(Number(knowledgeHealth?.graph?.sourceCoverage ?? 1) * 100)}%</div>
                                    {Number(knowledgeHealth?.graph?.legacyArchivedRelations || 0) > 0 ? <div className="mt-1 text-xs text-muted-foreground">{t("app.admin.dashboard.memory.knowledgeHealth.legacyArchived", { count: knowledgeHealth?.graph?.legacyArchivedRelations || 0 })}</div> : null}
                                </div>
                            </div>
                        </CardContent>
                    </Card>

                    {resolutionCandidates.length > 0 ? <Card className="border-amber-500/30 bg-amber-50/40 dark:bg-amber-950/10">
                        <CardHeader>
                            <CardTitle className="text-lg">{t("app.admin.dashboard.memory.pendingUpdates.title", { count: resolutionCandidates.length })}</CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-3">
                            {resolutionCandidates.map(candidate => <div key={candidate.id} className="rounded-xl border border-amber-500/20 bg-background/90 p-4">
                                <div className="grid gap-3 lg:grid-cols-2">
                                    <div>
                                        <div className="mb-1 text-xs font-medium text-muted-foreground">{t("app.admin.dashboard.memory.pendingUpdates.current")}</div>
                                        <p className="text-sm leading-6 text-foreground">{candidate.target_fact || t("app.admin.dashboard.memory.pendingUpdates.noTarget")}</p>
                                        <div className="mt-2 text-xs text-muted-foreground">{formatScopeLabel(candidate.target_scope, scopeLabels)} · {candidate.target_category || "general"}</div>
                                    </div>
                                    <div>
                                        <div className="mb-1 text-xs font-medium text-muted-foreground">{t("app.admin.dashboard.memory.pendingUpdates.observed")}</div>
                                        <p className="text-sm leading-6 text-foreground">{candidate.candidate_fact}</p>
                                        <div className="mt-2 text-xs text-muted-foreground">{formatScopeLabel(candidate.candidate_scope, scopeLabels)} · {candidate.candidate_category || "general"}</div>
                                        <div className="mt-1 text-xs text-muted-foreground">
                                            {t("app.admin.dashboard.memory.pendingUpdates.source", {
                                              source: t(`app.admin.dashboard.memory.pendingUpdates.source.${candidate.source_kind || "conversation"}`),
                                              count: candidate.source_observation_count || 1
                                            })}
                                        </div>
                                    </div>
                                </div>
                                <div className="mt-4 flex flex-wrap gap-2">
                                    <Button variant="outline" size="sm" disabled={resolvingCandidateId === candidate.id} onClick={() => void handleResolveCandidate(candidate.id, "reinforce")}>{t("app.admin.dashboard.memory.pendingUpdates.same")}</Button>
                                    <Button size="sm" disabled={resolvingCandidateId === candidate.id} onClick={() => void handleResolveCandidate(candidate.id, "replace")}>{t("app.admin.dashboard.memory.pendingUpdates.replace")}</Button>
                                    <Button variant="outline" size="sm" disabled={resolvingCandidateId === candidate.id} onClick={() => void handleResolveCandidate(candidate.id, "refine")}>{t("app.admin.dashboard.memory.pendingUpdates.refine")}</Button>
                                    <Button variant="ghost" size="sm" className="text-muted-foreground hover:text-destructive" disabled={resolvingCandidateId === candidate.id} onClick={() => void handleResolveCandidate(candidate.id, "discard")}>{t("app.admin.dashboard.memory.pendingUpdates.discard")}</Button>
                                    {resolvingCandidateId === candidate.id ? <Loader2 className="h-4 w-4 animate-spin self-center text-muted-foreground" /> : null}
                                </div>
                                <TechnicalReferenceDetails
                                  items={[
                                    { label: "candidate", value: candidate.id },
                                    { label: "suggestion", value: candidate.proposed_relation },
                                    { label: "similarity", value: typeof candidate.similarity === "number" ? candidate.similarity.toFixed(3) : "-" },
                                    { label: "reason", value: candidate.reason || "-" },
                                    { label: "sourceSession", value: candidate.candidate_source_session || "-" }
                                  ]}
                                />
                            </div>)}
                        </CardContent>
                    </Card> : null}

                    {quarantinedGlobalKnowledge.length > 0 ?
        <Card className="border-amber-500/30 bg-amber-50/40 dark:bg-amber-950/10">
                            <CardHeader>
                                <CardTitle className="text-lg">{tg(t, "f803638e")}{quarantinedGlobalKnowledge.length})</CardTitle>
                            </CardHeader>
                            <CardContent>
                                <div className="max-h-[280px] space-y-2 overflow-y-auto">
                                    {quarantinedGlobalKnowledge.map((item) =>
              <div key={item.id} className="group flex items-start gap-3 rounded-lg border border-amber-500/20 bg-background/80 p-3">
                                            <div className="min-w-0 flex-1">
                                                <p className="text-sm">{item.fact}</p>
                                                <div className="mt-1 flex items-center gap-2">
                                                    <span className="rounded bg-amber-500/10 px-1.5 py-0.5 font-mono text-xs text-amber-700 dark:text-amber-300">{item.scope}</span>
                                                    <span className="text-xs text-muted-foreground">{item.category}</span>
                                                </div>
                                                <TechnicalReferenceDetails items={[{ label: "knowledge", value: item.id }, { label: "lineage", value: String(item.lineage_id || "-") }]} />
                                            </div>
                                            <div className="flex gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                                                <Button variant="outline" size="sm" onClick={() => void handleRestoreKnowledge(item)}>
                                                    <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
                                                    {tg(t, "79748ca1")}
                                                </Button>
                                                <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-destructive" onClick={() => void handleDeleteKnowledge(item.id)}>
                                                    <Trash2 className="h-4 w-4" />
                                                </Button>
                                            </div>
                                        </div>
              )}
                                </div>
                            </CardContent>
                        </Card> :
        null}

                    <Card className="border-border/60">
                        <CardHeader>
                            <CardTitle className="flex items-center justify-between text-lg">
                                <span>{t("app.admin.dashboard.memory.page.k9a019ed3")} ({knowledge.length})</span>
                                {knowledge.length > 0 ? <Button variant="ghost" size="sm" className="text-xs text-muted-foreground" onClick={handleSelectAll}>
                                        <CheckSquare className="mr-1 h-3.5 w-3.5" />
                                        {selectedIds.size === knowledge.length ? t("app.admin.dashboard.memory.page.kd1299571") : t("app.admin.dashboard.memory.page.k02d0d287")}
                                    </Button> : null}
                            </CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="max-h-[540px] space-y-2 overflow-y-auto">
                                {knowledge.length === 0 ? <p className="py-8 text-center text-sm text-muted-foreground">{t("app.admin.dashboard.memory.page.k8ae4b7c3")}</p> : knowledge.map((item) => <div key={item.id} className={`group flex items-start gap-3 rounded-lg border p-3 transition-colors ${selectedIds.has(item.id) ?
              "border-primary/30 bg-primary/5" :
              "hover:bg-muted/20"}`}>
                                            <div className={`mt-0.5 transition-opacity ${selectedIds.has(item.id) ? "opacity-100" : "opacity-0 group-hover:opacity-100"}`}>
                                                <Checkbox checked={selectedIds.has(item.id)} onCheckedChange={() => toggleSelect(item.id)} id={`chk-${item.id}`} />
                                            </div>
                                            <div className="min-w-0 flex-1">
                                                <p className="text-sm">{item.fact}</p>
                                                <div className="mt-1 flex items-center gap-2">
                                                    <span className="rounded bg-blue-500/10 px-1.5 py-0.5 text-xs text-blue-600 dark:text-blue-300">{formatScopeLabel(item.scope, scopeLabels)}</span>
                                                    <span className="text-xs text-muted-foreground">{item.category}</span>
                                                    {item.lifecycle_state && item.lifecycle_state !== "active" ?
                    <span className="rounded bg-amber-500/10 px-1.5 py-0.5 text-xs text-amber-700 dark:text-amber-300">{lifecycleLabel(item.lifecycle_state, lifecycleLabels)}</span> :
                    null}
                                                </div>
                                                <TechnicalReferenceDetails
                                                  items={[
                                                    { label: "knowledge", value: item.id },
                                                    { label: "lineage", value: String(item.lineage_id || "-") },
                                                    { label: "revision", value: String(item.revision_no || 1) },
                                                    { label: "source", value: String(item.maintainer_source || "-") },
                                                    { label: "confidence", value: typeof item.effective_confidence === "number" ? item.effective_confidence.toFixed(2) : "-" }
                                                  ]}
                                                />
                                            </div>
                                            <div className="flex gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                                                {item.lifecycle_state === "stale" ?
                  <Button variant="outline" size="sm" onClick={() => void handleRevalidateKnowledge(item)}>
                                                        <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
                                                        {tg(t, "b38c92c3")}
                                                    </Button> :
                  null}
                                                <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-primary" onClick={() => handleEditKnowledge(item)}>
                                                    <Edit2 className="h-4 w-4" />
                                                </Button>
                                                <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-destructive" onClick={() => void handleDeleteKnowledge(item.id)}>
                                                    <Trash2 className="h-4 w-4" />
                                                </Button>
                                            </div>
                                        </div>)}
                            </div>
                        </CardContent>
                    </Card>

                    {selectedIds.size > 0 ? <div className="fixed bottom-6 left-1/2 z-50 flex -translate-x-1/2 items-center gap-4 rounded-full border bg-background px-6 py-3 shadow-2xl shadow-black/20 backdrop-blur-sm animate-in slide-in-from-bottom-4">
                            <span className="text-sm font-medium">
                                {t("app.admin.dashboard.memory.page.k715ef203")} <span className="font-bold text-primary">{selectedIds.size}</span> {t("app.admin.dashboard.memory.page.kbcc46b75")}
                            </span>
                            <div className="h-4 w-px bg-border" />
                            <Button variant="ghost" size="sm" className="text-muted-foreground" onClick={() => setSelectedIds(new Set())}>
                                {t("app.admin.dashboard.memory.page.k06d9a187")}
                            </Button>
                            <Button variant="destructive" size="sm" onClick={() => void handleBulkDelete()} disabled={bulkDeleting}>
                                {bulkDeleting ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Trash2 className="mr-1.5 h-3.5 w-3.5" />}
                                {t("app.admin.dashboard.memory.page.ke9fc70e2")}
                            </Button>
                        </div> : null}
                </TabsContent>

                <TabsContent value="workflows" className="space-y-4">
                    {activeTab === "workflows" ? <MemoryWorkflowsPanel /> : null}
                </TabsContent>

                <TabsContent value="artifacts" className="space-y-4">
                    {activeTab === "artifacts" ? <ArtifactExplorerPanel /> : null}
                </TabsContent>

                <TabsContent value="graph" className="space-y-4">
                    <Card className="border-border/60">
                        <CardContent className="pb-3 pt-4">
                            <div className="flex gap-2">
                                <Input placeholder={t("app.admin.dashboard.memory.page.kd6e7a940")} value={graphFilter} onChange={(event) => setGraphFilter(event.target.value)} />
                                {graphFilter ? <Button variant="ghost" size="sm" onClick={() => setGraphFilter("")}>
                                        {t("app.admin.dashboard.memory.page.k8809fdce")}
                                    </Button> : null}
                            </div>
                        </CardContent>
                    </Card>

                    {activeTab === "graph" ? <GraphViewer filterNode={graphFilter} /> : null}

                    <div className="grid grid-cols-2 gap-4">
                        <Card className="border-border/60">
                            <CardContent className="pt-6">
                                <div className="text-center">
                                    <p className="text-4xl font-bold text-primary">{data?.graph?.entities || 0}</p>
                                    <p className="mt-2 text-sm text-muted-foreground">{t("app.admin.dashboard.memory.page.k17253f9c")}</p>
                                </div>
                            </CardContent>
                        </Card>
                        <Card className="border-border/60">
                            <CardContent className="pt-6">
                                <div className="text-center">
                                    <p className="text-4xl font-bold text-primary">{data?.graph?.relations || 0}</p>
                                    <p className="mt-2 text-sm text-muted-foreground">{t("app.admin.dashboard.memory.page.k0fb61318")}</p>
                                </div>
                            </CardContent>
                        </Card>
                    </div>

                    {data?.graph?.top_entities?.length > 0 ? <Card className="border-border/60">
                            <CardHeader>
                                <CardTitle className="text-lg">{t("app.admin.dashboard.memory.page.k61c52134")}</CardTitle>
                            </CardHeader>
                            <CardContent>
                                <div className="space-y-2">
                                    {data.graph.top_entities.map((entity: {
                name: string;
                type: string;
                degree: number;
              }, index: number) => <div key={`${entity.name}-${index}`} className="flex items-center gap-3 border-b border-border/50 py-2 last:border-0">
                                            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/10 text-sm font-bold text-primary">
                                                {index + 1}
                                            </div>
                                            <div className="flex-1">
                                                <span className="text-sm font-medium">{entity.name}</span>
                                                <span className="ml-2 text-xs text-muted-foreground">({entity.type})</span>
                                            </div>
                                            <span className="font-mono text-sm text-muted-foreground">{entity.degree} {t("app.admin.dashboard.memory.page.k2d2d1ecf")}</span>
                                        </div>)}
                                </div>
                            </CardContent>
                        </Card> : null}

                </TabsContent>

                <TabsContent value="agent" className="space-y-4">
                    {activeTab === "agent" ? <MemoryAgentChat /> : null}
                </TabsContent>

                <TabsContent value="upload" className="space-y-4">
                    {activeTab === "upload" ? <DocumentUploader /> : null}
                </TabsContent>

                <TabsContent value="config" className="space-y-4">
                    {activeTab === "config" ? <MemoryConfigPanel /> : null}
                </TabsContent>

                <TabsContent value="runtime" className="space-y-4">
                    {activeTab === "runtime" ? <div className="max-h-[calc(100vh-340px)] overflow-y-auto pr-1">
                            <MemoryRuntimeDiagnosticsPanel data={data} />
                        </div> : null}
                </TabsContent>
            </Tabs>
        </div>;
}
