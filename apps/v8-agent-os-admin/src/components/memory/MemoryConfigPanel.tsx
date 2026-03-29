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
}

export default function MemoryConfigPanel() {
    const t = useT();
    const [config, setConfig] = useState<MemoryConfig>({});
    const [models, setModels] = useState<SysModel[]>([]);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);

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
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle className="text-base">{t("上下文窗口")}</CardTitle>
                    <CardDescription>{t("控制近期日志与记忆注入窗口，让 recall 与长期记忆注入保持节奏一致。")}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-5">
                    <div className="grid grid-cols-2 gap-4">
                        <div className="space-y-1.5">
                            <Label>{t("近期日志天数")}</Label>
                            <Input
                                type="number"
                                value={config.max_recent_days ?? 2}
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

