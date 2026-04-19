"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
    BookOpen,
    Brain,
    CheckSquare,
    Database,
    Edit2,
    FolderTree,
    Loader2,
    Network,
    RefreshCw,
    Search,
    Tag,
    Trash2,
} from "lucide-react";

import AuditLogsPanel from "@/components/memory/AuditLogsPanel";
import { ArtifactExplorerPanel } from "@/components/memory/ArtifactExplorerPanel";
import DocumentUploader from "@/components/memory/DocumentUploader";
import { EditKnowledgeDialog } from "@/components/memory/EditKnowledgeDialog";
import GraphViewer from "@/components/memory/GraphViewer";
import MemoryAgentChat from "@/components/memory/MemoryAgentChat";
import MemoryConfigPanel from "@/components/memory/MemoryConfigPanel";
import MemoryRuntimeDiagnosticsPanel from "@/components/memory/MemoryRuntimeDiagnosticsPanel";
import MemorySectionNav, { type MemorySectionKey } from "@/components/memory/MemorySectionNav";
import { PreferencesManager } from "@/components/memory/PreferencesManager";
import { ProjectRegistryPanel } from "@/components/memory/ProjectRegistryPanel";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/use-toast";
import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type DashboardData = any;

interface KnowledgeItem {
    id: string;
    fact: string;
    category: string;
    scope: string;
    [key: string]: unknown;
}

const VALID_TABS = new Set(["preferences", "projects", "knowledge", "artifacts", "graph", "agent", "audit", "upload", "config", "runtime"]);

export default function MemoryDashboardPage() {
    const { toast } = useToast();
    const t = useT();
    const searchParams = useSearchParams();
    const requestedTab = searchParams.get("tab") || "preferences";
    const activeTab = VALID_TABS.has(requestedTab) ? requestedTab : "preferences";

    const [data, setData] = useState<DashboardData>(null);
    const [loading, setLoading] = useState(true);
    const [searchQuery, setSearchQuery] = useState("");
    const [searchResults, setSearchResults] = useState<KnowledgeItem[]>([]);
    const [searching, setSearching] = useState(false);
    const [knowledge, setKnowledge] = useState<KnowledgeItem[]>([]);
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
        } catch (err) {
            console.error("Failed to load memory dashboard:", err);
            toast({
                title: t("仪表盘加载失败"),
                description: t("未能读取记忆系统仪表盘。"),
                variant: "destructive",
            });
        } finally {
            setLoading(false);
        }
    }, [t, toast]);

    const loadKnowledge = useCallback(async () => {
        try {
            const res = await fetch("/api/memory/knowledge", { cache: "no-store" });
            if (!res.ok) {
                throw new Error(`Knowledge failed: ${res.status}`);
            }
            const json = await res.json();
            setKnowledge(json.items || []);
        } catch (err) {
            console.error("Failed to load knowledge:", err);
            toast({
                title: t("知识库加载失败"),
                description: t("未能读取知识条目。"),
                variant: "destructive",
            });
        }
    }, [t, toast]);

    useEffect(() => {
        void loadDashboard();
        void loadKnowledge();
    }, [loadDashboard, loadKnowledge]);

    const handleDeleteKnowledge = useCallback(async (id: string) => {
        if (!window.confirm(t("确定要删除这条知识吗？(同时会删除 FTS5 索引)"))) return;
        try {
            const res = await fetch(`/api/memory/knowledge/${id}`, { method: "DELETE" });
            if (!res.ok) {
                throw new Error(`Delete failed: ${res.status}`);
            }
            setKnowledge((prev) => prev.filter((k) => k.id !== id));
            setSearchResults((prev) => prev.filter((k) => k.id !== id));
        } catch (err) {
            console.error("Delete failed:", err);
            toast({
                title: t("删除失败"),
                description: t("知识条目删除失败，请稍后重试。"),
                variant: "destructive",
            });
        }
    }, [t, toast]);

    const handleEditKnowledge = useCallback((item: KnowledgeItem) => {
        setEditTarget(item);
        setEditDialogOpen(true);
    }, []);

    const handleSaveKnowledge = useCallback(async (id: string, updated: { fact: string; category: string; scope: string }) => {
        const res = await fetch(`/api/memory/knowledge/${id}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(updated),
        });
        if (!res.ok) {
            throw new Error(`Save failed: ${res.status}`);
        }
        setKnowledge((prev) => prev.map((item) => item.id === id ? { ...item, ...updated } : item));
        setSearchResults((prev) => prev.map((item) => item.id === id ? { ...item, ...updated } : item));
        toast({
            title: t("知识已更新"),
            description: t(lt(`${id} 已保存最新内容。`, `${id} has been updated.`)),
        });
    }, [t, toast]);

    const toggleSelect = useCallback((id: string) => {
        setSelectedIds((prev) => {
            const next = new Set(prev);
            if (next.has(id)) {
                next.delete(id);
            } else {
                next.add(id);
            }
            return next;
        });
    }, []);

    const handleBulkDelete = useCallback(async () => {
        const confirmMessage = t(
            lt(
                `确定要删除选中的 ${selectedIds.size} 条知识吗？`,
                `Delete ${selectedIds.size} selected knowledge items?`,
            ),
        );
        if (!window.confirm(confirmMessage)) return;
        setBulkDeleting(true);
        try {
            await Promise.all(
                Array.from(selectedIds).map((id) =>
                    fetch(`/api/memory/knowledge/${id}`, { method: "DELETE" }),
                ),
            );
            setKnowledge((prev) => prev.filter((item) => !selectedIds.has(item.id)));
            setSearchResults((prev) => prev.filter((item) => !selectedIds.has(item.id)));
            setSelectedIds(new Set());
            toast({
                title: t("批量删除完成"),
                description: t("选中的知识条目已从记忆库移除。"),
            });
        } finally {
            setBulkDeleting(false);
        }
    }, [selectedIds, t, toast]);

    const handleSelectAll = useCallback(() => {
        setSelectedIds((prev) => (
            prev.size === knowledge.length
                ? new Set()
                : new Set(knowledge.map((item) => item.id))
        ));
    }, [knowledge]);

    const handleSearch = useCallback(async () => {
        if (!searchQuery.trim()) return;
        setSearching(true);
        try {
            const res = await fetch(`/api/memory/search?q=${encodeURIComponent(searchQuery)}`);
            if (!res.ok) {
                throw new Error(`Search failed: ${res.status}`);
            }
            const json = await res.json();
            setSearchResults(json.results || []);
        } catch (err) {
            console.error("Search failed:", err);
            toast({
                title: t("检索失败"),
                description: t("当前无法搜索知识库，请稍后重试。"),
                variant: "destructive",
            });
        } finally {
            setSearching(false);
        }
    }, [searchQuery, t, toast]);

    const handleClearDiagnostics = useCallback(async () => {
        if (!window.confirm(t(lt("确定要清理 Memory Runtime 的诊断日志吗？这只会删除抽取/维护运行诊断记录，不会删除偏好、知识或图谱。", "Clear Memory Runtime diagnostic logs? This only deletes extraction and maintenance diagnostic records, not preferences, knowledge, or graph data.")))) {
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
                title: t(lt("诊断日志已清理", "Diagnostic logs cleared")),
                description: t(
                    lt(
                        `已删除 ${payload?.deletedRuns || 0} 条运行记录与 ${payload?.deletedInvocations || 0} 条模型调用记录。`,
                        `Removed ${payload?.deletedRuns || 0} run records and ${payload?.deletedInvocations || 0} model invocation records.`,
                    ),
                ),
            });
            await loadDashboard();
        } catch (err) {
            console.error("Failed to clear memory diagnostics:", err);
            toast({
                title: t(lt("清理失败", "Cleanup failed")),
                description: t(lt("未能清理 Memory Runtime 诊断日志，请稍后重试。", "Failed to clear Memory Runtime diagnostic logs. Please try again later.")),
                variant: "destructive",
            });
        } finally {
            setClearingDiagnostics(false);
        }
    }, [loadDashboard, t, toast]);

    const stats = useMemo(() => ({
        preferenceCount: data?.preferences?.total || 0,
        preferenceScopes: data?.preferences?.scopes?.length || 0,
        knowledgeCount: data?.knowledge?.count || 0,
        graphEntities: data?.graph?.entities || 0,
        graphRelations: data?.graph?.relations || 0,
    }), [data]);

    if (loading) {
        return (
            <div className="flex h-96 items-center justify-center">
                <Loader2 className="h-8 w-8 animate-spin" />
            </div>
        );
    }

    return (
        <div className="space-y-6">
            <EditKnowledgeDialog
                item={editTarget}
                open={editDialogOpen}
                onOpenChange={setEditDialogOpen}
                onSave={handleSaveKnowledge}
            />

            <div className="flex items-center justify-between gap-4">
                <div>
                    <h1 className="flex items-center gap-3 text-3xl font-bold">
                        <Brain className="h-8 w-8 text-primary" />
                        {t("记忆管理")}
                    </h1>
                    <p className="mt-1 text-muted-foreground">{t("分层记忆系统 • 偏好画像 • 项目注册表 • 知识库 • Artifact Explorer • 知识图谱")}</p>
                </div>
                <div className="flex items-center gap-2">
                    {activeTab === "runtime" ? (
                        <Button variant="destructive" size="sm" onClick={() => void handleClearDiagnostics()} disabled={clearingDiagnostics}>
                            {clearingDiagnostics ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Trash2 className="mr-2 h-4 w-4" />}
                            {t(lt("清理诊断日志", "Clear diagnostics"))}
                        </Button>
                    ) : null}
                    <Button variant="outline" size="sm" onClick={() => { void loadDashboard(); void loadKnowledge(); }}>
                        <RefreshCw className="mr-2 h-4 w-4" />
                        {t(lt("刷新", "Refresh"))}
                    </Button>
                </div>
            </div>

            <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
                <Card className="border-border/60">
                    <CardHeader className="pb-2">
                        <CardTitle className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                            <Tag className="h-4 w-4" /> {t("偏好项")}
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-3xl font-bold">{stats.preferenceCount}</p>
                        <p className="mt-1 text-xs text-muted-foreground">{stats.preferenceScopes} {t("个 scope")}</p>
                    </CardContent>
                </Card>

                <Card className="border-border/60">
                    <CardHeader className="pb-2">
                        <CardTitle className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                            <FolderTree className="h-4 w-4" /> {t("项目上下文")}
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-3xl font-bold">{data?.preferences?.scopes?.filter((scope: string) => scope.startsWith("project:")).length || 0}</p>
                        <p className="mt-1 text-xs text-muted-foreground">{t("project scope 已纳入记忆页管理")}</p>
                    </CardContent>
                </Card>

                <Card className="border-border/60">
                    <CardHeader className="pb-2">
                        <CardTitle className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                            <Database className="h-4 w-4" /> {t("知识条目")}
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-3xl font-bold">{stats.knowledgeCount}</p>
                        <p className="mt-1 text-xs text-muted-foreground">{t("FTS5 + jieba 已索引")}</p>
                    </CardContent>
                </Card>

                <Card className="border-border/60">
                    <CardHeader className="pb-2">
                        <CardTitle className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                            <Network className="h-4 w-4" /> {t("图谱实体")}
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-3xl font-bold">{stats.graphEntities}</p>
                        <p className="mt-1 text-xs text-muted-foreground">{stats.graphRelations} {t("条关系")}</p>
                    </CardContent>
                </Card>
            </div>

            <Tabs value={activeTab} className="space-y-4">
                <MemorySectionNav activeKey={activeTab as MemorySectionKey} />

                <TabsContent value="preferences" className="space-y-4">
                    <PreferencesManager />
                </TabsContent>

                <TabsContent value="projects" className="space-y-4">
                    <ProjectRegistryPanel />
                </TabsContent>

                <TabsContent value="knowledge" className="space-y-4">
                    <Card className="border-border/60">
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2 text-lg">
                                <Search className="h-5 w-5" /> {t("全文检索")}
                            </CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="flex gap-2">
                                <Input
                                    placeholder={t("搜索知识... 支持中英文")}
                                    value={searchQuery}
                                    onChange={(event) => setSearchQuery(event.target.value)}
                                    onKeyDown={(event) => event.key === "Enter" && void handleSearch()}
                                />
                                <Button onClick={() => void handleSearch()} disabled={searching}>
                                    {searching ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
                                </Button>
                            </div>

                            {searchResults.length > 0 ? (
                                <div className="mt-4 space-y-2">
                                    <p className="text-xs text-muted-foreground">{searchResults.length} {t("条命中")}</p>
                                    {searchResults.map((result, index) => (
                                        <div key={`${result.id}-${index}`} className="group flex items-start gap-3 rounded-lg border bg-muted/30 p-3">
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
                                        </div>
                                    ))}
                                </div>
                            ) : null}
                        </CardContent>
                    </Card>

                    <Card className="border-border/60">
                        <CardHeader>
                            <CardTitle className="flex items-center justify-between text-lg">
                                <span>{t("知识条目")} ({knowledge.length})</span>
                                {knowledge.length > 0 ? (
                                    <Button variant="ghost" size="sm" className="text-xs text-muted-foreground" onClick={handleSelectAll}>
                                        <CheckSquare className="mr-1 h-3.5 w-3.5" />
                                        {selectedIds.size === knowledge.length ? t("取消全选") : t("全选")}
                                    </Button>
                                ) : null}
                            </CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="max-h-[540px] space-y-2 overflow-y-auto">
                                {knowledge.length === 0 ? (
                                    <p className="py-8 text-center text-sm text-muted-foreground">{t("暂无知识条目")}</p>
                                ) : (
                                    knowledge.map((item) => (
                                        <div
                                            key={item.id}
                                            className={`group flex items-start gap-3 rounded-lg border p-3 transition-colors ${
                                                selectedIds.has(item.id)
                                                    ? "border-primary/30 bg-primary/5"
                                                    : "hover:bg-muted/20"
                                            }`}
                                        >
                                            <div className={`mt-0.5 transition-opacity ${
                                                selectedIds.has(item.id) ? "opacity-100" : "opacity-0 group-hover:opacity-100"
                                            }`}>
                                                <Checkbox
                                                    checked={selectedIds.has(item.id)}
                                                    onCheckedChange={() => toggleSelect(item.id)}
                                                    id={`chk-${item.id}`}
                                                />
                                            </div>
                                            <div className="min-w-0 flex-1">
                                                <p className="text-sm">{item.fact}</p>
                                                <div className="mt-1 flex items-center gap-2">
                                                    <span className="rounded bg-blue-500/10 px-1.5 py-0.5 font-mono text-xs text-blue-600">{item.scope}</span>
                                                    <span className="text-xs text-muted-foreground">{item.category}</span>
                                                    <span className="font-mono text-[10px] text-muted-foreground/40">{item.id}</span>
                                                </div>
                                            </div>
                                            <div className="flex gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                                                <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-primary" onClick={() => handleEditKnowledge(item)}>
                                                    <Edit2 className="h-4 w-4" />
                                                </Button>
                                                <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-destructive" onClick={() => void handleDeleteKnowledge(item.id)}>
                                                    <Trash2 className="h-4 w-4" />
                                                </Button>
                                            </div>
                                        </div>
                                    ))
                                )}
                            </div>
                        </CardContent>
                    </Card>

                    {selectedIds.size > 0 ? (
                        <div className="fixed bottom-6 left-1/2 z-50 flex -translate-x-1/2 items-center gap-4 rounded-full border bg-background px-6 py-3 shadow-2xl shadow-black/20 backdrop-blur-sm animate-in slide-in-from-bottom-4">
                            <span className="text-sm font-medium">
                                {t("已选")} <span className="font-bold text-primary">{selectedIds.size}</span> {t("条")}
                            </span>
                            <div className="h-4 w-px bg-border" />
                            <Button variant="ghost" size="sm" className="text-muted-foreground" onClick={() => setSelectedIds(new Set())}>
                                {t("取消选择")}
                            </Button>
                            <Button variant="destructive" size="sm" onClick={() => void handleBulkDelete()} disabled={bulkDeleting}>
                                {bulkDeleting ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Trash2 className="mr-1.5 h-3.5 w-3.5" />}
                                {t("批量删除")}
                            </Button>
                        </div>
                    ) : null}
                </TabsContent>

                <TabsContent value="artifacts" className="space-y-4">
                    {activeTab === "artifacts" ? <ArtifactExplorerPanel /> : null}
                </TabsContent>

                <TabsContent value="graph" className="space-y-4">
                    <Card className="border-border/60">
                        <CardContent className="pb-3 pt-4">
                            <div className="flex gap-2">
                                <Input
                                    placeholder={t("搜索节点... 匹配的节点会高亮放大")}
                                    value={graphFilter}
                                    onChange={(event) => setGraphFilter(event.target.value)}
                                />
                                {graphFilter ? (
                                    <Button variant="ghost" size="sm" onClick={() => setGraphFilter("")}>
                                        {t("清除")}
                                    </Button>
                                ) : null}
                            </div>
                        </CardContent>
                    </Card>

                    {activeTab === "graph" ? <GraphViewer filterNode={graphFilter} /> : null}

                    <div className="grid grid-cols-2 gap-4">
                        <Card className="border-border/60">
                            <CardContent className="pt-6">
                                <div className="text-center">
                                    <p className="text-4xl font-bold text-primary">{data?.graph?.entities || 0}</p>
                                    <p className="mt-2 text-sm text-muted-foreground">{t("实体总数")}</p>
                                </div>
                            </CardContent>
                        </Card>
                        <Card className="border-border/60">
                            <CardContent className="pt-6">
                                <div className="text-center">
                                    <p className="text-4xl font-bold text-primary">{data?.graph?.relations || 0}</p>
                                    <p className="mt-2 text-sm text-muted-foreground">{t("关系总数")}</p>
                                </div>
                            </CardContent>
                        </Card>
                    </div>

                    {data?.graph?.top_entities?.length > 0 ? (
                        <Card className="border-border/60">
                            <CardHeader>
                                <CardTitle className="text-lg">{t("热门实体 Top 10")}</CardTitle>
                                <CardDescription>{t("按关联度排序")}</CardDescription>
                            </CardHeader>
                            <CardContent>
                                <div className="space-y-2">
                                    {data.graph.top_entities.map((entity: { name: string; type: string; degree: number }, index: number) => (
                                        <div key={`${entity.name}-${index}`} className="flex items-center gap-3 border-b border-border/50 py-2 last:border-0">
                                            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/10 text-sm font-bold text-primary">
                                                {index + 1}
                                            </div>
                                            <div className="flex-1">
                                                <span className="text-sm font-medium">{entity.name}</span>
                                                <span className="ml-2 text-xs text-muted-foreground">({entity.type})</span>
                                            </div>
                                            <span className="font-mono text-sm text-muted-foreground">{entity.degree} {t("关系")}</span>
                                        </div>
                                    ))}
                                </div>
                            </CardContent>
                        </Card>
                    ) : null}

                    {data?.recent_logs ? (
                        <Card className="border-border/60">
                            <CardHeader>
                                <CardTitle className="flex items-center gap-2 text-lg">
                                    <BookOpen className="h-5 w-5" /> {t("近期日志")}
                                </CardTitle>
                            </CardHeader>
                            <CardContent>
                                <pre className="max-h-64 overflow-y-auto whitespace-pre-wrap rounded-lg bg-muted/30 p-4 font-mono text-xs leading-relaxed">
                                    {data.recent_logs || t("暂无日志")}
                                </pre>
                            </CardContent>
                        </Card>
                    ) : null}
                </TabsContent>

                <TabsContent value="agent" className="space-y-4">
                    {activeTab === "agent" ? <MemoryAgentChat /> : null}
                </TabsContent>

                <TabsContent value="audit" className="min-h-0 space-y-4">
                    {activeTab === "audit" ? <AuditLogsPanel /> : null}
                </TabsContent>

                <TabsContent value="upload" className="space-y-4">
                    {activeTab === "upload" ? <DocumentUploader /> : null}
                </TabsContent>

                <TabsContent value="config" className="space-y-4">
                    {activeTab === "config" ? <MemoryConfigPanel /> : null}
                </TabsContent>

                <TabsContent value="runtime" className="space-y-4">
                    {activeTab === "runtime" ? (
                        <div className="max-h-[calc(100vh-340px)] overflow-y-auto pr-1">
                            <MemoryRuntimeDiagnosticsPanel data={data} />
                        </div>
                    ) : null}
                </TabsContent>
            </Tabs>
        </div>
    );
}
