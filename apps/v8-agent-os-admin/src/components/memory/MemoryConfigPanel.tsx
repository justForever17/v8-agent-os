"use client";

import { useState, useEffect, useCallback } from "react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Loader2, Save } from "lucide-react";
import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";

interface SysModel {
    id: string;
    modelId: string;
    name: string;
    type: string;
    provider: { name: string; icon?: string };
}

interface MemoryConfig {
    extraction_model?: string;
    extraction_temperature?: number;
    embedding_model?: string;
    reranker_model?: string;
    recall_strategy?: string;
    recall_top_k?: number;
    retrieval_threshold?: number;
    passive_injection_enabled?: boolean;
    max_recent_days?: number;
    max_context_tokens?: number;
    extraction_enabled?: boolean;
    graph_enabled?: boolean;
    fts_enabled?: boolean;
    recommended_retrieval_threshold?: number;
    retrieval_threshold_source?: string;
    retrieval_threshold_is_default?: boolean;
}

interface RecallPreviewItem {
    id: string;
    fact: string;
    source?: string;
    scope?: string;
    category?: string;
    raw_relevance_score?: number;
    final_relevance_score?: number;
    accepted?: boolean;
    reject_reason?: string;
}

interface RecallPreviewResponse {
    query?: string;
    threshold_snapshot?: number;
    effective_acceptance_threshold?: number;
    threshold_source?: string;
    recommended_retrieval_threshold?: number;
    retrieval_threshold_is_default?: boolean;
    diagnostics?: {
        graph_allowed?: boolean;
        graph_reject_reason?: string;
        graph_entities?: string[];
        recall_strategy?: string;
        accepted_count?: number;
        rejected_count?: number;
    };
    items?: RecallPreviewItem[];
}

export default function MemoryConfigPanel() {
    const t = useT();
    const [config, setConfig] = useState<MemoryConfig>({});
    const [models, setModels] = useState<SysModel[]>([]);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [recallQuery, setRecallQuery] = useState("");
    const [recallPreviewLoading, setRecallPreviewLoading] = useState(false);
    const [recallPreviewError, setRecallPreviewError] = useState<string | null>(null);
    const [recallPreview, setRecallPreview] = useState<RecallPreviewResponse | null>(null);

    const loadData = useCallback(async () => {
        setLoading(true);
        try {
            const [confRes, modRes] = await Promise.all([
                fetch("/api/settings/memory-config"),
                fetch("/api/models")
            ]);
            
            if (confRes.ok) {
                const fetchedConfig = await confRes.json();
                setConfig(fetchedConfig);
            }
            if (modRes.ok) setModels(await modRes.json());
        } catch { /* ignore */ }
        finally { setLoading(false); }
    }, []);

    useEffect(() => { loadData(); }, [loadData]);

    const handleRecallPreview = useCallback(async () => {
        const normalizedQuery = recallQuery.trim();
        if (!normalizedQuery) {
            setRecallPreview(null);
            setRecallPreviewError("请先输入一条要诊断的查询。");
            return;
        }
        setRecallPreviewLoading(true);
        setRecallPreviewError(null);
        try {
            const params = new URLSearchParams({ q: normalizedQuery });
            const response = await fetch(`/api/memory/recall-preview?${params.toString()}`);
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(typeof payload.error === "string" ? payload.error : `Request failed (${response.status})`);
            }
            setRecallPreview(payload);
        } catch (error) {
            setRecallPreview(null);
            setRecallPreviewError(error instanceof Error ? error.message : String(error));
        } finally {
            setRecallPreviewLoading(false);
        }
    }, [recallQuery]);

    const handleSave = async () => {
        setSaving(true);
        try {
            await fetch("/api/settings/memory-config", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(config),
            });
            setSaved(true);
            setTimeout(() => setSaved(false), 2000);
        } finally { setSaving(false); }
    };

    if (loading) {
        return (
            <div className="flex items-center justify-center h-48">
                <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
            </div>
        );
    }

    const llmModels = models.filter(m => ['TEXT', 'MULTIMODAL', 'chat', 'LLM'].includes(m.type.toUpperCase() || 'LLM'));
    const embedModels = models.filter(m => ['EMBEDDING'].includes(m.type.toUpperCase()));
    const rerankModels = models.filter(m => ['RERANK'].includes(m.type.toUpperCase()));

    return (
        <div className="space-y-4">
            <Card>
                <CardHeader>
                    <CardTitle className="text-base">{t("模型绑定")}</CardTitle>
                    <CardDescription>{t("记忆提取、Embedding 与 Rerank 都从这里指定，并最终写回统一的 models.json 事实源。")}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-5">
                    <div className="space-y-1.5">
                        <Label>{t("抽取模型 (LLM)")}</Label>
                        <Select
                            value={config.extraction_model || ""}
                            onValueChange={(val) => setConfig(prev => ({ ...prev, extraction_model: val }))}
                        >
                            <SelectTrigger className="w-full">
                                <SelectValue placeholder={t("请选择语言模型以进行实体与偏好抽取")} />
                            </SelectTrigger>
                            <SelectContent>
                                {llmModels.map(m => (
                                    <SelectItem key={m.id} value={m.id}>
                                        {m.name || m.id} ({m.provider?.name})
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <p className="text-xs text-muted-foreground">{t("用于读取对话记录并萃取出知识图谱实体、用户偏好的基础模型。")}</p>
                    </div>

                    <div className="space-y-2">
                        <div className="flex items-center justify-between">
                            <Label>{t("抽取温度 (Temperature)")}</Label>
                            <span className="text-sm font-mono text-muted-foreground">
                                {(config.extraction_temperature ?? 0.3).toFixed(2)}
                            </span>
                        </div>
                        <Slider
                            value={[config.extraction_temperature ?? 0.3]}
                            onValueChange={([v]) => setConfig(prev => ({ ...prev, extraction_temperature: v }))}
                            min={0} max={1} step={0.05}
                            className="w-full"
                        />
                        <p className="text-xs text-muted-foreground">{t("低值（0~0.3）使提取结果更稳定，高值更有创意")}</p>
                    </div>

                    <div className="space-y-1.5">
                        <Label>{t("嵌入模型 (Embedding)")}</Label>
                        <Select
                            value={config.embedding_model || ""}
                            onValueChange={(val) => setConfig(prev => ({ ...prev, embedding_model: val }))}
                        >
                            <SelectTrigger className="w-full">
                                <SelectValue placeholder={t("请选择向量嵌入模型")} />
                            </SelectTrigger>
                            <SelectContent>
                                {embedModels.map(m => (
                                    <SelectItem key={m.id} value={m.id}>
                                        {m.name || m.id} ({m.provider?.name})
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <p className="text-xs text-muted-foreground">{t("用于生成用户查询和记忆条目的向量特征，支持语义相似度检索。")}</p>
                    </div>

                    <div className="space-y-1.5">
                        <Label>{t("全局检索重排模型")}</Label>
                        <Select
                            value={config.reranker_model || ""}
                            onValueChange={(val) => setConfig(prev => ({ ...prev, reranker_model: val }))}
                        >
                            <SelectTrigger className="w-full">
                                <SelectValue placeholder={t("请选择重排序模型（可选）")} />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="none">{t("无 (None)")}</SelectItem>
                                {rerankModels.map(m => (
                                    <SelectItem key={m.id} value={m.id}>
                                        {m.name || m.id} ({m.provider?.name})
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <p className="text-xs text-muted-foreground">{t("主要服务 memory / RAG 的二阶段精排；若扩展生态或桌面候选没有单独指定，也会回退使用这里的全局重排模型。")}</p>
                    </div>
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle className="text-base">{t("RAG 检索协同")}</CardTitle>
                    <CardDescription>{t("控制 recall 的召回方式、Top-K、阈值和被动注入行为，让模型绑定和检索策略一起生效。")}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-5">
                    <div className="grid grid-cols-2 gap-4">
                        <div className="space-y-1.5">
                            <Label>{t("Recall 策略")}</Label>
                            <Select
                                value={config.recall_strategy || "balanced"}
                                onValueChange={(val) => setConfig(prev => ({ ...prev, recall_strategy: val }))}
                            >
                                <SelectTrigger className="w-full">
                                    <SelectValue placeholder={t("请选择 recall 策略")} />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="balanced">{t("平衡混合检索")}</SelectItem>
                                    <SelectItem value="semantic">{t("语义优先")}</SelectItem>
                                    <SelectItem value="keyword">{t("关键词优先")}</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="space-y-1.5">
                            <Label>{t("Recall Top-K")}</Label>
                            <Input
                                type="number"
                                value={config.recall_top_k ?? 3}
                                onChange={(e) => setConfig(prev => ({ ...prev, recall_top_k: Number(e.target.value) }))}
                                min={1}
                                max={10}
                            />
                        </div>
                    </div>

                    <div className="space-y-2">
                        <div className="flex items-center justify-between">
                            <Label>{t("检索阈值")}</Label>
                            <span className="text-sm font-mono text-muted-foreground">
                                {(config.retrieval_threshold ?? 0).toFixed(2)}
                            </span>
                        </div>
                        <Slider
                            value={[config.retrieval_threshold ?? 0]}
                            onValueChange={([v]) => setConfig(prev => ({ ...prev, retrieval_threshold: v }))}
                            min={0}
                            max={1}
                            step={0.05}
                            className="w-full"
                        />
                        <p className="text-xs text-muted-foreground">{t("用于过滤低相关度 recall 结果。值越高，注入内容越克制。")}</p>
                        <p className="text-xs text-muted-foreground">
                            {config.retrieval_threshold_source === "user"
                                ? t("当前值来自你的手动配置。")
                                : t(`当前值来自 engine 推荐默认值 ${(config.recommended_retrieval_threshold ?? 0.2).toFixed(2)}；只有缺省时才会自动写入。`)}
                        </p>
                    </div>

                    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                        <div className="flex items-center justify-between rounded-lg border p-3">
                            <div className="space-y-1">
                                <Label>{t("被动注入 RAG")}</Label>
                                <p className="text-xs text-muted-foreground">{t("允许主理人在高相关度时静默注入相关记忆结果。")}</p>
                            </div>
                            <Switch
                                checked={config.passive_injection_enabled ?? true}
                                onCheckedChange={(checked) => setConfig(prev => ({ ...prev, passive_injection_enabled: checked }))}
                            />
                        </div>
                        <div className="flex items-center justify-between rounded-lg border p-3">
                            <div className="space-y-1">
                                <Label>{t("图谱扩展")}</Label>
                                <p className="text-xs text-muted-foreground">{t("在 recall 里追加知识图谱的一跳上下文，适合关系型问题。")}</p>
                            </div>
                            <Switch
                                checked={config.graph_enabled ?? true}
                                onCheckedChange={(checked) => setConfig(prev => ({ ...prev, graph_enabled: checked }))}
                            />
                        </div>
                        <div className="flex items-center justify-between rounded-lg border p-3">
                            <div className="space-y-1">
                                <Label>{t("FTS 关键词检索")}</Label>
                                <p className="text-xs text-muted-foreground">{t("为 recall 增加全文搜索命中，适合精确关键词和文件名。")}</p>
                            </div>
                            <Switch
                                checked={config.fts_enabled ?? true}
                                onCheckedChange={(checked) => setConfig(prev => ({ ...prev, fts_enabled: checked }))}
                            />
                        </div>
                        <div className="flex items-center justify-between rounded-lg border p-3">
                            <div className="space-y-1">
                                <Label>{t("会话后自动提取")}</Label>
                                <p className="text-xs text-muted-foreground">{t("控制 Memory Agent 是否在会话完成后自动提取偏好和知识。")}</p>
                            </div>
                            <Switch
                                checked={config.extraction_enabled ?? true}
                                onCheckedChange={(checked) => setConfig(prev => ({ ...prev, extraction_enabled: checked }))}
                            />
                        </div>
                    </div>

                    <div className="space-y-3 rounded-lg border p-4">
                        <div className="space-y-1">
                            <Label>{t("Recall 预览 / 诊断")}</Label>
                            <p className="text-xs text-muted-foreground">
                                {t("这里调用真实 unified recall，而不是 FTS-only 搜索，可直接看到阈值过滤、graph 扩展和最终入选结果。")}
                            </p>
                        </div>
                        <div className="flex gap-2">
                            <Input
                                value={recallQuery}
                                onChange={(e) => setRecallQuery(e.target.value)}
                                placeholder={t("输入一条要诊断的查询，例如：为什么记忆和RAG会命中不相关内容")}
                            />
                            <Button onClick={handleRecallPreview} disabled={recallPreviewLoading}>
                                {recallPreviewLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : t("预览")}
                            </Button>
                        </div>
                        {recallPreviewError ? (
                            <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                                {recallPreviewError}
                            </div>
                        ) : null}
                        {recallPreview ? (
                            <div className="space-y-3">
                                <div className="grid grid-cols-1 gap-2 text-xs text-muted-foreground md:grid-cols-2">
                                    <div>{t("阈值快照")}: {(recallPreview.threshold_snapshot ?? 0).toFixed(2)}</div>
                                    <div>{t("实际过滤地板")}: {(recallPreview.effective_acceptance_threshold ?? 0).toFixed(2)}</div>
                                    <div>{t("阈值来源")}: {recallPreview.threshold_source === "user" ? t("用户手动配置") : t("engine 推荐默认值")}</div>
                                    <div>{t("策略")}: {recallPreview.diagnostics?.recall_strategy || "balanced"}</div>
                                    <div>{t("Graph 扩展")}: {recallPreview.diagnostics?.graph_allowed ? t("已启用") : (recallPreview.diagnostics?.graph_reject_reason || t("未启用"))}</div>
                                </div>
                                <div className="max-h-72 space-y-2 overflow-auto rounded-md border p-3">
                                    {(recallPreview.items || []).length ? (
                                        recallPreview.items?.map((item) => (
                                            <div key={item.id} className="rounded-md border p-3 text-sm">
                                                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                                                    <span>{item.source || "unknown"}</span>
                                                    <span>{item.scope || "global"}</span>
                                                    <span>{item.category || "general"}</span>
                                                    <span>{t("raw")} {(item.raw_relevance_score ?? 0).toFixed(4)}</span>
                                                    <span>{t("final")} {(item.final_relevance_score ?? 0).toFixed(4)}</span>
                                                    <span>{item.accepted ? t("已入选") : t(`已拒绝${item.reject_reason ? ` (${item.reject_reason})` : ""}`)}</span>
                                                </div>
                                                <p className="mt-2 whitespace-pre-wrap break-words text-sm">{item.fact}</p>
                                            </div>
                                        ))
                                    ) : (
                                        <div className="text-xs text-muted-foreground">{t("当前查询没有通过阈值的 recall 结果。")}</div>
                                    )}
                                </div>
                            </div>
                        ) : null}
                    </div>
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle className="text-base">{t(lt("上下文窗口", "Context window"))}</CardTitle>
                <CardDescription>
                    {t(
                        lt(
                                "控制详细日志注入窗口（映射 memory.max_recent_days）；窗口外的前一日记忆会自动降级为 summaries + path。",
                                "Controls the detailed log injection window (maps to memory.max_recent_days); the prior day outside that window is automatically reduced to summaries + path.",
                            ),
                        )}
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-5">
                    <div className="rounded-xl border border-border/60 bg-muted/30 px-4 py-3 text-xs leading-6 text-muted-foreground">
                        {t(
                            lt(
                                "长期记忆 scope 现只保留 global、project:{id} 与 channel:{type}:{remote_id}。workspace/workflow/app 不再作为长期记忆域；日志与审计会额外记录 effectiveMemoryScope、provenanceClass 和 memoryPolicy。",
                                "Long-term memory scopes are now limited to global, project:{id}, and channel:{type}:{remote_id}. Workspace/workflow/app no longer count as durable memory scopes; logs and audits also record effectiveMemoryScope, provenanceClass, and memoryPolicy.",
                            ),
                        )}
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                        <div className="space-y-1.5">
                            <Label>{t(lt("详细日志天数", "Detailed log days"))}</Label>
                            <Input
                                type="number"
                                value={config.max_recent_days ?? 1}
                                onChange={(e) => setConfig(prev => ({ ...prev, max_recent_days: Number(e.target.value) }))}
                                min={1} max={14}
                            />
                        </div>
                        <div className="space-y-1.5">
                            <Label>{t("最大上下文 Token")}</Label>
                            <Input
                                type="number"
                                value={config.max_context_tokens ?? 2000}
                                onChange={(e) => setConfig(prev => ({ ...prev, max_context_tokens: Number(e.target.value) }))}
                                min={500} max={8000} step={100}
                            />
                        </div>
                    </div>
                </CardContent>
            </Card>

            <div className="flex justify-end">
                <Button onClick={handleSave} disabled={saving}>
                    {saving ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Save className="w-4 h-4 mr-2" />}
                    {saved ? t(lt("✓ 已保存", "✓ Saved")) : t("保存配置")}
                </Button>
            </div>
        </div>
    );
}

