"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Brain, CheckSquare, Database, Edit2, Loader2, Network, RefreshCw, Search, Tag, Trash2, Workflow } from "lucide-react";
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
  [key: string]: unknown;
}
const VALID_TABS = new Set(["context", "preferences", "logs", "knowledge", "workflows", "artifacts", "graph", "agent", "upload", "config", "runtime"]);
export default function MemoryDashboardClient({ initialRequestedTab = "preferences" }: {initialRequestedTab?: string;}) {
  const { toast } = useToast();
  const t = useT();
  const requestedTab = initialRequestedTab || "preferences";
  const activeTab = VALID_TABS.has(requestedTab) ? requestedTab : "preferences";
  const [data, setData] = useState<DashboardData>(null);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<KnowledgeItem[]>([]);
  const [searching, setSearching] = useState(false);
  const [knowledge, setKnowledge] = useState<KnowledgeItem[]>([]);
  const [quarantinedGlobalKnowledge, setQuarantinedGlobalKnowledge] = useState<KnowledgeItem[]>([]);
  const [editTarget, setEditTarget] = useState<KnowledgeItem | null>(null);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [graphFilter, setGraphFilter] = useState("");
  const [clearingDiagnostics, setClearingDiagnostics] = useState(false);
  const loadDashboard = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/memory/dashboard", { cache: "no-store" });
      if (!res.ok) {
        throw new Error(`Dashboard failed: ${res.status}`);
      }
      const json = await res.json();
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
  const loadKnowledge = useCallback(async () => {
    try {
      const [activeRes, quarantinedRes] = await Promise.all([
      fetch("/api/memory/knowledge", { cache: "no-store" }),
      fetch("/api/memory/knowledge?scope=global&status=quarantined&limit=100", { cache: "no-store" })]
      );
      if (!activeRes.ok) {
        throw new Error(`Knowledge failed: ${activeRes.status}`);
      }
      if (!quarantinedRes.ok) {
        throw new Error(`Quarantined knowledge failed: ${quarantinedRes.status}`);
      }
      const [activeJson, quarantinedJson] = await Promise.all([activeRes.json(), quarantinedRes.json()]);
      setKnowledge(activeJson.items || []);
      setQuarantinedGlobalKnowledge(quarantinedJson.items || []);
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
    }
    catch (err) {
      console.error("Delete failed:", err);
      toast({
        title: t("app.admin.dashboard.memory.page.k0915ccdf"),
        description: t("app.admin.dashboard.memory.page.kac10f278"),
        variant: "destructive"
      });
    }
  }, [t, toast]);
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
      await loadKnowledge();
      toast({
        title: tg(t, "8564783d"),
        description: tg(t, "76752b5b", { value1: item.id })
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
      await loadKnowledge();
      toast({
        title: tg(t, "ec481ba6"),
        description: tg(t, "b93d054c", { value1: item.id })
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
    setKnowledge((prev) => prev.map((item) => item.id === id ? { ...item, ...updated } : item));
    setSearchResults((prev) => prev.map((item) => item.id === id ? { ...item, ...updated } : item));
    toast({
      title: t("app.admin.dashboard.memory.page.kddf0235b"),
      description: t("app.admin.dashboard.memory.page.k9409cebf", {
        id: id
      })
    });
  }, [t, toast]);
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
      setSelectedIds(new Set());
      toast({
        title: t("app.admin.dashboard.memory.page.k19c9dddb"),
        description: t("app.admin.dashboard.memory.page.k52850139")
      });
    } finally
    {
      setBulkDeleting(false);
    }
  }, [selectedIds, t, toast]);
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
      await loadDashboard();
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
    workflowCandidateCount: data?.workflows?.candidateCount || 0,
    workflowActiveCount: data?.workflows?.byStatus?.active_hint || 0,
    workflowHintEventCount: data?.workflows?.hintEventCount || 0,
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
                    <Button variant="outline" size="sm" onClick={() => {void loadDashboard();void loadKnowledge();}}>
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
              activeCount: stats.workflowActiveCount,
              hintEventCount: stats.workflowHintEventCount
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
                                                    <span className="font-mono text-[10px] text-muted-foreground/40">{item.id}</span>
                                                </div>
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
                                                    <span className="rounded bg-blue-500/10 px-1.5 py-0.5 font-mono text-xs text-blue-600">{item.scope}</span>
                                                    <span className="text-xs text-muted-foreground">{item.category}</span>
                                                    {item.lifecycle_state && item.lifecycle_state !== "active" ?
                    <span className="rounded bg-amber-500/10 px-1.5 py-0.5 font-mono text-xs text-amber-700">{item.lifecycle_state}</span> :
                    null}
                                                    {item.maintainer_source ?
                    <span className="rounded bg-emerald-500/10 px-1.5 py-0.5 font-mono text-xs text-emerald-700">{item.maintainer_source}</span> :
                    null}
                                                    {typeof item.effective_confidence === "number" ?
                    <span className="font-mono text-[10px] text-muted-foreground">eff {item.effective_confidence.toFixed(2)}</span> :
                    null}
                                                    <span className="font-mono text-[10px] text-muted-foreground/40">{item.id}</span>
                                                </div>
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
