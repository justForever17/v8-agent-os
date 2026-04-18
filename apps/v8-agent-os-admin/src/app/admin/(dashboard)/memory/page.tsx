"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
    AlertCircle,
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
import { PreferencesManager } from "@/components/memory/PreferencesManager";
import { ProjectRegistryPanel } from "@/components/memory/ProjectRegistryPanel";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/use-toast";
import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type DashboardData = any;

interface ExtractionRun {
    runId?: string;
    sessionId?: string;
    status?: string;
    startedAt?: string;
    finishedAt?: string;
    triggerSource?: string;
    extractorModel?: string;
    extractionFailureStage?: string | null;
    extractionFailureReason?: string | null;
    skipReason?: string | null;
    extractionMode?: string | null;
    transcriptSource?: string | null;
    latestSeq?: number | null;
    rawOutputPreview?: string | null;
    parserErrorPreview?: string | null;
    summary?: string | null;
    resolvedScope?: string | null;
    effectiveMemoryScope?: string | null;
    memoryPolicy?: string | null;
    provenanceClass?: string | null;
    noPersistedMemoryReason?: string | null;
    extractedPreferenceCount?: number;
    extractedKnowledgeCount?: number;
    persistedPreferenceCount?: number;
    persistedKnowledgeCount?: number;
    persistedRelationCount?: number;
    filterReasons?: Record<string, number>;
    invocationStatus?: string | null;
    invocationError?: string | null;
}

interface MaintenanceRun {
    runId?: string;
    status?: string;
    startedAt?: string;
    finishedAt?: string;
    triggerSource?: string;
    summaryMissingCountBefore?: number;
    summaryMissingCountAfter?: number;
    summaryBackfilledCount?: number;
    summaryStaleCountBefore?: number;
    summaryStaleCountAfter?: number;
    touchedRefs?: string[];
    resultReason?: string | null;
}

interface KnowledgeItem {
    id: string;
    fact: string;
    category: string;
    scope: string;
    [key: string]: unknown;
}

const VALID_TABS = new Set(["preferences", "projects", "knowledge", "artifacts", "graph", "agent", "audit", "upload", "config"]);

function formatExtractionOutcome(run: ExtractionRun, t: ReturnType<typeof useT>) {
    if ((run.status || "").toLowerCase() === "skipped" || run.skipReason) {
        const labels: Record<string, string> = {
            duplicate_transcript: t("重复 transcript，已跳过"),
            duplicate_increment: t("重复增量，已跳过"),
            no_semantic_content: t("语义内容过短，已跳过"),
            no_messages: t("无可用消息，已跳过"),
            no_user_message: t("缺少用户消息，已跳过"),
        };
        const skipKey = run.skipReason || run.extractionMode || "";
        return {
            title: labels[skipKey] || t("已跳过"),
            tone: "bg-slate-500/10 text-slate-700 border-slate-500/20",
            detail: t("这不是 durable policy 太严，而是本轮没有新的可抽取增量，或者内容本身不满足抽取前置条件。"),
        };
    }
    if (run.extractionFailureStage) {
        const labels: Record<string, string> = {
            extractor_config_missing: t("抽取器配置缺失"),
            llm_response_empty: t("模型空响应"),
            parser_failed: t("结构解析失败"),
            repair_parser_failed: t("修复解析失败"),
            llm_invoke_failed: t("模型调用失败"),
        };
        return {
            title: labels[run.extractionFailureStage] || run.extractionFailureStage,
            tone: "bg-red-500/10 text-red-600 border-red-500/20",
            detail: run.extractionFailureReason || run.invocationError || t("本轮抽取未成功完成。"),
        };
    }
    if (run.noPersistedMemoryReason === "policy_filtered") {
        return {
            title: t("已抽取，但被策略过滤"),
            tone: "bg-amber-500/10 text-amber-700 border-amber-500/20",
            detail: t("当前会话抽取到了候选项，但 durable policy 没有允许它们落库。"),
        };
    }
    if ((run.persistedKnowledgeCount || 0) > 0 || (run.persistedPreferenceCount || 0) > 0) {
        return {
            title: t("已持久化"),
            tone: "bg-emerald-500/10 text-emerald-700 border-emerald-500/20",
            detail: t("本轮 memory extraction 已经成功写入 durable memory。"),
        };
    }
    if ((run.extractedKnowledgeCount || 0) > 0 || (run.extractedPreferenceCount || 0) > 0) {
        return {
            title: t("已抽取，等待进一步判断"),
            tone: "bg-sky-500/10 text-sky-700 border-sky-500/20",
            detail: t("当前已有候选项，但未形成可持久化写入。"),
        };
    }
    return {
        title: t("无有效抽取"),
        tone: "bg-muted text-muted-foreground border-border/60",
        detail: t("当前会话没有产生可供 durable memory 使用的结构化结果。"),
    };
}

function formatRelativeTimestamp(value: string | null | undefined) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString("zh-CN", {
        hour12: false,
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
    });
}

export default function MemoryDashboardPage() {
    const { toast } = useToast();
    const t = useT();
    const router = useRouter();
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

    const stats = useMemo(() => ({
        preferenceCount: data?.preferences?.total || 0,
        preferenceScopes: data?.preferences?.scopes?.length || 0,
        knowledgeCount: data?.knowledge?.count || 0,
        graphEntities: data?.graph?.entities || 0,
        graphRelations: data?.graph?.relations || 0,
    }), [data]);

    const extractionSummary = data?.extractions?.summary || {};
    const recentExtractions = (data?.extractions?.recent || []) as ExtractionRun[];
    const memoryMapHealth = data?.memoryMap || {};
    const maintenanceSummary = data?.maintenance?.summary || {};
    const recentMaintenanceRuns = (data?.maintenance?.recent || []) as MaintenanceRun[];

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
                <Button variant="outline" size="sm" onClick={() => { void loadDashboard(); void loadKnowledge(); }}>
                    <RefreshCw className="mr-2 h-4 w-4" />
                    {t("刷新")}
                </Button>
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

            <Card className="border-border/60">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-lg">
                        <AlertCircle className="h-5 w-5 text-primary" />
                        {t("记忆抽取诊断")}
                    </CardTitle>
                    <CardDescription>
                        {t("明确区分抽取失败、模型空响应、解析失败和策略过滤，避免把所有问题都误判成阈值设置。")}
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
                        {[
                            [t("已完成"), extractionSummary.completed || 0],
                            [t("已跳过"), extractionSummary.skipped || 0],
                            [t("已持久化"), extractionSummary.persisted || 0],
                            [t("策略过滤"), extractionSummary.policyFiltered || 0],
                            [t("模型空响应"), extractionSummary.llmResponseEmpty || 0],
                            [t("解析失败"), (extractionSummary.parserFailed || 0) + (extractionSummary.repairParserFailed || 0)],
                            [t("模型调用失败"), extractionSummary.llmInvokeFailed || 0],
                            [t("重复 transcript"), extractionSummary.duplicateTranscript || 0],
                            [t("配置缺失"), extractionSummary.extractorConfigMissing || 0],
                            [t("短内容跳过"), extractionSummary.noSemanticContent || 0],
                        ].map(([label, value]) => (
                            <div key={String(label)} className="rounded-xl border bg-muted/20 p-3">
                                <p className="text-xs text-muted-foreground">{label}</p>
                                <p className="mt-2 text-2xl font-semibold">{value}</p>
                            </div>
                        ))}
                    </div>

                    {recentExtractions.length > 0 ? (
                        <div className="space-y-3">
                            {recentExtractions.map((run) => {
                                const outcome = formatExtractionOutcome(run, t);
                                return (
                                    <div key={run.runId || `${run.sessionId}-${run.startedAt}`} className="rounded-xl border bg-background p-4">
                                        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                                            <div className="min-w-0 flex-1 space-y-2">
                                                <div className="flex flex-wrap items-center gap-2">
                                                    <span className={`rounded-full border px-2.5 py-1 text-xs font-medium ${outcome.tone}`}>
                                                        {outcome.title}
                                                    </span>
                                                    <span className="font-mono text-xs text-muted-foreground">
                                                        session {run.sessionId || "—"}
                                                    </span>
                                                    <span className="font-mono text-xs text-muted-foreground">
                                                        run {run.runId || "—"}
                                                    </span>
                                                </div>
                                                <p className="text-sm text-foreground">{outcome.detail}</p>
                                                {run.summary ? (
                                                    <p className="rounded-lg bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
                                                        {run.summary}
                                                    </p>
                                                ) : null}
                                                <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                                                    <span>{t("开始")}：{formatRelativeTimestamp(run.startedAt)}</span>
                                                    <span>{t("完成")}：{formatRelativeTimestamp(run.finishedAt)}</span>
                                                    <span>{t("模型")}：{run.extractorModel || "—"}</span>
                                                    <span>{t("scope")}：{run.effectiveMemoryScope || run.resolvedScope || "—"}</span>
                                                    <span>{t("policy")}：{run.memoryPolicy || "—"}</span>
                                                    <span>{t("模式")}：{run.extractionMode || "—"}</span>
                                                    <span>{t("transcript")}：{run.transcriptSource || "—"}</span>
                                                </div>
                                                <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                                                    <span>{t("抽取偏好")}：{run.extractedPreferenceCount || 0}</span>
                                                    <span>{t("抽取知识")}：{run.extractedKnowledgeCount || 0}</span>
                                                    <span>{t("持久化偏好")}：{run.persistedPreferenceCount || 0}</span>
                                                    <span>{t("持久化知识")}：{run.persistedKnowledgeCount || 0}</span>
                                                    <span>{t("图谱关系")}：{run.persistedRelationCount || 0}</span>
                                                </div>
                                                {(run.rawOutputPreview || run.parserErrorPreview || run.invocationError) ? (
                                                    <div className="grid gap-2 xl:grid-cols-3">
                                                        {run.rawOutputPreview ? (
                                                            <div className="rounded-lg border bg-muted/20 p-3">
                                                                <p className="mb-2 text-xs font-medium text-muted-foreground">{t("模型原始输出预览")}</p>
                                                                <pre className="overflow-x-auto whitespace-pre-wrap break-all font-mono text-xs leading-relaxed">{run.rawOutputPreview}</pre>
                                                            </div>
                                                        ) : null}
                                                        {run.parserErrorPreview ? (
                                                            <div className="rounded-lg border bg-muted/20 p-3">
                                                                <p className="mb-2 text-xs font-medium text-muted-foreground">{t("解析错误预览")}</p>
                                                                <pre className="overflow-x-auto whitespace-pre-wrap break-all font-mono text-xs leading-relaxed">{run.parserErrorPreview}</pre>
                                                            </div>
                                                        ) : null}
                                                        {run.invocationError ? (
                                                            <div className="rounded-lg border bg-muted/20 p-3">
                                                                <p className="mb-2 text-xs font-medium text-muted-foreground">{t("模型调用错误")}</p>
                                                                <pre className="overflow-x-auto whitespace-pre-wrap break-all font-mono text-xs leading-relaxed">{run.invocationError}</pre>
                                                            </div>
                                                        ) : null}
                                                    </div>
                                                ) : null}
                                            </div>
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    ) : (
                        <div className="rounded-xl border border-dashed bg-muted/20 p-6 text-sm text-muted-foreground">
                            {t("近期还没有可展示的 memory extraction 运行样本。")}
                        </div>
                    )}
                </CardContent>
            </Card>

            <Card className="border-border/60">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-lg">
                        <FolderTree className="h-5 w-5 text-primary" />
                        {t("记忆地图与维护状态")}
                    </CardTitle>
                    <CardDescription>
                        {t("这里展示 brokered memory map 的摘要健康度，以及最近一次 Memory Maintenance 是否已经补齐缺失的周/月/年摘要。")}
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
                        {[
                            [t("年节点"), memoryMapHealth?.counts?.year || 0],
                            [t("月节点"), memoryMapHealth?.counts?.month || 0],
                            [t("周节点"), memoryMapHealth?.counts?.week || 0],
                            [t("天节点"), memoryMapHealth?.counts?.day || 0],
                            [t("缺摘要"), memoryMapHealth?.counts?.missing || 0],
                            [t("摘要陈旧"), memoryMapHealth?.counts?.stale || 0],
                            [t("已补齐"), maintenanceSummary.summaryBackfilled || 0],
                        ].map(([label, value]) => (
                            <div key={String(label)} className="rounded-xl border bg-muted/20 p-3">
                                <p className="text-xs text-muted-foreground">{label}</p>
                                <p className="mt-2 text-2xl font-semibold">{value}</p>
                            </div>
                        ))}
                    </div>

                    <div className="grid gap-4 xl:grid-cols-2">
                        <div className="rounded-xl border bg-background p-4">
                            <div className="mb-3 text-sm font-medium">{t("缺失的摘要节点")}</div>
                            {(memoryMapHealth?.missingRefs || []).length > 0 ? (
                                <div className="space-y-2">
                                    {(memoryMapHealth.missingRefs || []).map((ref: string) => (
                                        <div key={ref} className="rounded-lg bg-muted/30 px-3 py-2 font-mono text-xs text-muted-foreground">
                                            {ref}
                                        </div>
                                    ))}
                                </div>
                            ) : (
                                <div className="text-sm text-muted-foreground">{t("当前没有缺失的 week/month/year summary。")}</div>
                            )}
                        </div>

                        <div className="rounded-xl border bg-background p-4">
                            <div className="mb-3 text-sm font-medium">{t("陈旧的摘要节点")}</div>
                            {(memoryMapHealth?.staleRefs || []).length > 0 ? (
                                <div className="space-y-2">
                                    {(memoryMapHealth.staleRefs || []).map((ref: string) => (
                                        <div key={ref} className="rounded-lg bg-muted/30 px-3 py-2 font-mono text-xs text-muted-foreground">
                                            {ref}
                                        </div>
                                    ))}
                                </div>
                            ) : (
                                <div className="text-sm text-muted-foreground">{t("当前没有需要重刷的陈旧摘要。")}</div>
                            )}
                        </div>
                    </div>

                    {recentMaintenanceRuns.length > 0 ? (
                        <div className="space-y-3">
                            {recentMaintenanceRuns.map((run) => (
                                <div key={run.runId || `${run.startedAt}`} className="rounded-xl border bg-background p-4">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <span className="rounded-full border border-border/60 px-2.5 py-1 text-xs font-medium">
                                            {run.status || "unknown"}
                                        </span>
                                        <span className="font-mono text-xs text-muted-foreground">run {run.runId || "—"}</span>
                                        <span className="text-xs text-muted-foreground">{t("开始")}：{formatRelativeTimestamp(run.startedAt)}</span>
                                        <span className="text-xs text-muted-foreground">{t("完成")}：{formatRelativeTimestamp(run.finishedAt)}</span>
                                    </div>
                                    <div className="mt-3 flex flex-wrap gap-3 text-xs text-muted-foreground">
                                        <span>{t("缺摘要前")}：{run.summaryMissingCountBefore || 0}</span>
                                        <span>{t("缺摘要后")}：{run.summaryMissingCountAfter || 0}</span>
                                        <span>{t("陈旧前")}：{run.summaryStaleCountBefore || 0}</span>
                                        <span>{t("陈旧后")}：{run.summaryStaleCountAfter || 0}</span>
                                        <span>{t("补齐数量")}：{run.summaryBackfilledCount || 0}</span>
                                    </div>
                                    {(run.touchedRefs || []).length > 0 ? (
                                        <div className="mt-3 space-y-2">
                                            {(run.touchedRefs || []).map((ref) => (
                                                <div key={ref} className="rounded-lg bg-muted/30 px-3 py-2 font-mono text-xs text-muted-foreground">
                                                    {ref}
                                                </div>
                                            ))}
                                        </div>
                                    ) : null}
                                </div>
                            ))}
                        </div>
                    ) : (
                        <div className="text-sm text-muted-foreground">{t("还没有 Memory Maintenance 运行记录。")}</div>
                    )}
                </CardContent>
            </Card>

            <Tabs value={activeTab} onValueChange={(value) => router.replace(`/admin/memory?tab=${value}`)} className="space-y-4">
                <TabsList className="flex h-auto flex-wrap gap-2 bg-transparent p-0">
                    <TabsTrigger value="preferences">{t("偏好管理")}</TabsTrigger>
                    <TabsTrigger value="projects">{t("项目注册表")}</TabsTrigger>
                    <TabsTrigger value="knowledge">{t(lt("知识库", "Knowledge"))}</TabsTrigger>
                    <TabsTrigger value="artifacts">Artifacts</TabsTrigger>
                    <TabsTrigger value="graph">{t("知识图谱")}</TabsTrigger>
                    <TabsTrigger value="agent">{t("记忆助手")}</TabsTrigger>
                    <TabsTrigger value="audit">{t("系统日志")}</TabsTrigger>
                    <TabsTrigger value="upload">{t("文档上传")}</TabsTrigger>
                    <TabsTrigger value="config">{t("配置")}</TabsTrigger>
                </TabsList>

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
            </Tabs>
        </div>
    );
}
