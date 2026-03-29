"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle2, ExternalLink, Loader2, PackageCheck, Plus, RefreshCw, Save, Server, Terminal, Wrench } from "lucide-react";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { DomainSummaryStrip } from "@/components/admin-shell/DomainSummaryStrip";
import { EmptyState } from "@/components/admin-shell/EmptyState";
import { InlineSaveState } from "@/components/admin-shell/InlineSaveState";
import { SourceMetaRow } from "@/components/admin-shell/SourceMetaRow";
import { StatusNotice } from "@/components/admin-shell/StatusNotice";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/use-toast";
import { fetchConfigDomain, saveConfigDomain, type ConfigRegistryEnvelope } from "@/lib/config-registry";

type ExtensionCatalogResponse = {
    startupState?: "cold" | "refreshing" | "ready" | "error";
    snapshotFreshness?: "cold" | "cached" | "live";
    lastRefreshAt?: string | null;
    lastRefreshError?: string | null;
    skillsStartupState?: string | null;
    mcpStartupState?: string | null;
    runtime?: {
        startupState?: string | null;
        snapshotFreshness?: string | null;
        lastRefreshAt?: string | null;
        lastRefreshError?: string | null;
        skillsStartupState?: string | null;
        mcpStartupState?: string | null;
    };
    summary: { skillCount: number; mcpServerCount: number; connectedMcpServerCount: number; mcpToolCount: number };
    skills: { root: string; items: Array<{ name: string; description: string; path: string }> };
    mcp: {
        servers: Array<{
            name: string;
            status: "connected" | "disabled" | "error";
            toolCount: number;
            tools: Array<{ name: string; description: string }>;
            transport: string;
            target: string;
        }>;
    };
};

type ExtensionHealthResponse = {
    startupState?: "cold" | "refreshing" | "ready" | "error";
    snapshotFreshness?: "cold" | "cached" | "live";
    lastRefreshAt?: string | null;
    lastRefreshError?: string | null;
    skillsStartupState?: string | null;
    mcpStartupState?: string | null;
    runtime?: {
        startupState?: string | null;
        snapshotFreshness?: string | null;
        lastRefreshAt?: string | null;
        lastRefreshError?: string | null;
        skillsStartupState?: string | null;
        mcpStartupState?: string | null;
        silk?: {
            available?: boolean;
            version?: string | null;
            toolRoot?: string;
        };
    };
    summary: ExtensionCatalogResponse["summary"];
    mcp: { statusBreakdown: Record<string, number> };
    silk?: {
        available?: boolean;
        version?: string | null;
        toolRoot?: string;
    };
};

type SkillInstallResult = {
    source: string;
    targetRoot: string;
    installed: Array<{ name: string; path: string }>;
    conflicts: Array<{ name?: string; path?: string; reason?: string }>;
    warnings: string[];
};

type SysModel = {
    id: string;
    modelId: string;
    name: string;
    type: string;
    provider?: { name?: string };
    providerName?: string;
};

type ExtensionsConfigData = {
    rerankPolicy?: { enabled?: boolean };
    modelBindings?: { rerankerModel?: string; fallbackRerankerModel?: string };
};

function statusLabel(status: string) {
    if (status === "connected") return "已连接";
    if (status === "disabled") return "已停用";
    return "连接异常";
}

function modelValue(model: SysModel) {
    return String(model.modelId || model.id || "").trim();
}

function modelLabel(model: SysModel) {
    const providerName = model.provider?.name || model.providerName || "";
    return `${model.name || modelValue(model)}${providerName ? ` (${providerName})` : ""}`;
}

function StatPill({ label, value }: { label: string; value: string | number }) {
    return (
        <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
            <div className="text-xs text-slate-500">{label}</div>
            <div className="mt-2 text-2xl font-semibold text-slate-900">{value}</div>
        </div>
    );
}

export default function ExtensionsPage() {
    const [catalog, setCatalog] = useState<ExtensionCatalogResponse | null>(null);
    const [health, setHealth] = useState<ExtensionHealthResponse | null>(null);
    const [configEnvelope, setConfigEnvelope] = useState<ConfigRegistryEnvelope<ExtensionsConfigData> | null>(null);
    const [models, setModels] = useState<SysModel[]>([]);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [reloading, setReloading] = useState(false);
    const [installingCommand, setInstallingCommand] = useState(false);
    const [uploadingZip, setUploadingZip] = useState(false);
    const [savingMcp, setSavingMcp] = useState(false);
    const [commandInput, setCommandInput] = useState("");
    const [mcpConfigInput, setMcpConfigInput] = useState("");
    const [installResult, setInstallResult] = useState<SkillInstallResult | null>(null);
    const [mcpDialogOpen, setMcpDialogOpen] = useState(false);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const { toast } = useToast();

    const loadData = async () => {
        setLoading(true);
        try {
            const [catalogResponse, healthResponse, config, modelList] = await Promise.all([
                fetch("/api/extensions/catalog", { cache: "no-store" }),
                fetch("/api/extensions/health", { cache: "no-store" }),
                fetchConfigDomain<ExtensionsConfigData>("extensions"),
                fetch("/api/models", { cache: "no-store" }).then((response) => response.json().catch(() => [])),
            ]);
            if (!catalogResponse.ok || !healthResponse.ok) {
                throw new Error("扩展信息读取失败");
            }
            const [catalogPayload, healthPayload] = await Promise.all([
                catalogResponse.json(),
                healthResponse.json(),
            ] as const);
            setCatalog(catalogPayload);
            setHealth(healthPayload);
            setConfigEnvelope(config);
            setModels(Array.isArray(modelList) ? modelList : []);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        void loadData();
    }, []);

    const rerankModels = useMemo(
        () => models.filter((model) => ["RERANK", "RERANKER"].includes((model.type || "").toUpperCase())),
        [models]
    );

    const summaryItems = useMemo(
        () => [
            { label: "Skills", value: catalog?.summary.skillCount ?? 0, description: "当前已安装并可读取的 Skills 数量。" },
            { label: "MCP 服务", value: catalog?.summary.mcpServerCount ?? 0, description: "当前登记的 MCP 服务数量。" },
            { label: "已连接 MCP", value: catalog?.summary.connectedMcpServerCount ?? 0, description: "当前已成功连接的 MCP 服务数量。" },
            { label: "MCP 工具", value: catalog?.summary.mcpToolCount ?? 0, description: "当前可直接调用的 MCP 工具总数。" },
        ],
        [catalog]
    );

    const updateConfig = (patch: Partial<ExtensionsConfigData>) => {
        if (!configEnvelope) return;
        setConfigEnvelope({
            ...configEnvelope,
            data: {
                ...configEnvelope.data,
                ...patch,
                rerankPolicy: { ...(configEnvelope.data?.rerankPolicy || {}), ...(patch.rerankPolicy || {}) },
                modelBindings: { ...(configEnvelope.data?.modelBindings || {}), ...(patch.modelBindings || {}) },
            },
        });
    };

    const handleSaveConfig = async () => {
        if (!configEnvelope) return;
        setSaving(true);
        try {
            const next = await saveConfigDomain<ExtensionsConfigData>("extensions", {
                data: {
                    rerankPolicy: { enabled: Boolean(configEnvelope.data?.rerankPolicy?.enabled) },
                    modelBindings: { rerankerModel: String(configEnvelope.data?.modelBindings?.rerankerModel || "").trim() },
                },
            });
            setConfigEnvelope(next);
            setSaved(true);
            window.setTimeout(() => setSaved(false), 1800);
            toast({ title: "扩展候选重排已保存" });
        } catch (error) {
            toast({
                title: "保存失败",
                description: error instanceof Error ? error.message : "请稍后重试。",
                variant: "destructive",
            });
        } finally {
            setSaving(false);
        }
    };

    const handleReloadSystem = async () => {
        setReloading(true);
        try {
            const res = await fetch("/api/extensions/reload", { method: "POST" });
            if (!res.ok) throw new Error("扩展刷新失败");
            await res.json();
            await loadData();
            toast({ title: "扩展已刷新" });
        } catch {
            toast({ title: "刷新失败", description: "请稍后重试。", variant: "destructive" });
        } finally {
            setReloading(false);
        }
    };

    const handleCommandInstall = async () => {
        if (!commandInput.trim()) return;
        setInstallingCommand(true);
        setInstallResult(null);
        try {
            const res = await fetch("/api/skills/install/command", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ command: commandInput }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(String(data?.detail || data?.error || "Skills 安装失败"));
            setInstallResult(data);
            setCommandInput("");
            toast({ title: "Skills 安装完成", description: `已安装 ${data.installed?.length ?? 0} 项。` });
            await loadData();
        } catch (error) {
            toast({
                title: "安装失败",
                description: error instanceof Error ? error.message : "执行失败",
                variant: "destructive",
            });
        } finally {
            setInstallingCommand(false);
        }
    };

    const handleZipUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
        const file = event.target.files?.[0];
        if (!file) return;
        setUploadingZip(true);
        try {
            const formData = new FormData();
            formData.append("file", file);
            const res = await fetch("/api/skills/install/zip", { method: "POST", body: formData });
            const data = await res.json();
            if (!res.ok) throw new Error(String(data?.detail || data?.error || "Skills 压缩包安装失败"));
            setInstallResult(data);
            toast({ title: "技能已导入" });
            await loadData();
        } catch (error) {
            toast({
                title: "上传失败",
                description: error instanceof Error ? error.message : "请检查压缩包结构后重试。",
                variant: "destructive",
            });
        } finally {
            setUploadingZip(false);
            if (fileInputRef.current) fileInputRef.current.value = "";
        }
    };

    const saveMcpConfig = async () => {
        if (!mcpConfigInput.trim()) return;
        setSavingMcp(true);
        try {
            JSON.parse(mcpConfigInput);
            const res = await fetch("/api/mcp/config", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: mcpConfigInput,
            });
            if (!res.ok) throw new Error("MCP 配置保存失败");
            setMcpDialogOpen(false);
            setMcpConfigInput("");
            toast({ title: "配置已合并", description: "新的 MCP 配置已写入系统。" });
            await loadData();
        } catch (error) {
            toast({
                title: "导入失败",
                description: error instanceof Error ? error.message : "请检查 JSON 格式。",
                variant: "destructive",
            });
        } finally {
            setSavingMcp(false);
        }
    };

    if (loading || !catalog || !health || !configEnvelope) {
        return (
            <div className="flex min-h-[320px] items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
            </div>
        );
    }

    const rerankEnabled = Boolean(configEnvelope.data?.rerankPolicy?.enabled);
    const rerankerModel = String(configEnvelope.data?.modelBindings?.rerankerModel || "").trim();
    const fallbackRerankerModel = String(configEnvelope.data?.modelBindings?.fallbackRerankerModel || "").trim();
    const runtimeStartupState = String(health.runtime?.startupState || catalog.startupState || "cold").trim().toLowerCase();
    const snapshotFreshness = String(health.runtime?.snapshotFreshness || catalog.snapshotFreshness || "cold").trim().toLowerCase();
    const silkAvailable = Boolean(health.silk?.available ?? health.runtime?.silk?.available);
    const silkVersion = String(health.silk?.version || health.runtime?.silk?.version || "").trim();
    const silkRoot = String(health.silk?.toolRoot || health.runtime?.silk?.toolRoot || "").trim();

    return (
        <AdminPageShell>
            <AdminPageHeader
                title="扩展生态"
                description="管理 Skills、MCP 服务和候选排序。"
                actions={
                    <div className="flex items-center gap-3">
                        <InlineSaveState saving={saving} saved={saved} label="候选重排" />
                        <Button onClick={() => void handleSaveConfig()} disabled={saving}>
                            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                            保存
                        </Button>
                        <Button variant="outline" onClick={() => void loadData()} disabled={reloading || saving}>
                            <RefreshCw className="mr-2 h-4 w-4" />
                            重新读取
                        </Button>
                        <Button onClick={() => void handleReloadSystem()} disabled={reloading || saving}>
                            {reloading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                            刷新扩展
                        </Button>
                    </div>
                }
            />

            <DomainSummaryStrip items={summaryItems} />
            {runtimeStartupState === "refreshing" ? (
                <StatusNotice
                    title="扩展运行时正在后台刷新"
                    description={`当前先展示${snapshotFreshness === "live" ? "live" : snapshotFreshness === "cached" ? "缓存" : "冷启动"}快照；Skills 与 MCP 会在后台继续完成加载。`}
                    tone="info"
                />
            ) : null}
            {runtimeStartupState === "error" ? (
                <StatusNotice
                    title="扩展运行时后台刷新失败"
                    description={health.lastRefreshError || catalog.lastRefreshError || "当前继续展示缓存快照，请稍后手动刷新。"}
                    tone="warning"
                />
            ) : null}
            <StatusNotice
                title={silkAvailable ? "Silk 工具链已就绪" : "Silk 工具链未就绪"}
                description={
                    silkAvailable
                        ? `当前会优先为 Weixin / QQ 生成 Silk 原生语音。${silkVersion ? ` 版本：${silkVersion}。` : ""}${silkRoot ? ` 根目录：${silkRoot}` : ""}`
                        : `Weixin / QQ 当前会显式降级为附件音频，不会误报原生语音成功。${silkRoot ? ` 期望安装根：${silkRoot}` : ""}`
                }
                tone={silkAvailable ? "success" : "warning"}
            />

            <ConfigCard
                title="候选重排"
                description="控制 Skills 与 MCP 候选排序。"
            >
                <div className="grid gap-6 xl:grid-cols-[1.2fr,1fr]">
                    <div className="space-y-5">
                        <div className="flex items-center justify-between gap-4 rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
                            <div className="space-y-1">
                                <div className="text-sm font-medium text-slate-900">启用扩展候选重排</div>
                                <div className="text-xs leading-5 text-slate-500">只对词面召回池做精排，rerank 出错时会自动退回 lexical。</div>
                            </div>
                            <Switch checked={rerankEnabled} onCheckedChange={(checked) => updateConfig({ rerankPolicy: { enabled: checked } })} />
                        </div>

                        <div className="space-y-2">
                            <Label>扩展候选重排模型</Label>
                            <Select
                                value={rerankerModel || "__empty__"}
                                onValueChange={(value) => updateConfig({ modelBindings: { rerankerModel: value === "__empty__" ? "" : value } })}
                            >
                                <SelectTrigger className="w-full">
                                    <SelectValue placeholder="未指定，回退全局重排模型" />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="__empty__">未指定，回退全局重排模型</SelectItem>
                                    {rerankModels.map((model) => (
                                        <SelectItem key={modelValue(model)} value={modelValue(model)}>
                                            {modelLabel(model)}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                            <p className="text-xs leading-5 text-slate-500">
                                推荐绑定本地 vLLM Rerank 服务。当前全局回退模型：{fallbackRerankerModel || "未指定"}。
                            </p>
                        </div>
                    </div>

                    <div className="space-y-3 rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-4 text-sm text-slate-600">
                        <div className="flex items-center justify-between gap-3"><span>当前策略</span><Badge variant={rerankEnabled ? "default" : "secondary"}>{rerankEnabled ? "已启用" : "已关闭"}</Badge></div>
                        <div className="flex items-center justify-between gap-3"><span>专用模型绑定</span><Badge variant="outline">{rerankerModel || "未指定"}</Badge></div>
                        <div className="flex items-center justify-between gap-3"><span>全局回退</span><Badge variant="outline">{fallbackRerankerModel || "未指定"}</Badge></div>
                        <div className="rounded-xl border border-slate-200 bg-white px-3 py-3 text-xs leading-6 text-slate-500">
                            提示：这里只有“排序权”，不会把 rerank 结果写进聊天正文，也不会替代 Skills 安装或 MCP 配置本身。
                        </div>
                    </div>
                </div>
            </ConfigCard>

            <SourceMetaRow source={configEnvelope.source} savePath={configEnvelope.savePath} reloadRequired={configEnvelope.reloadRequired} />

            <div className="grid auto-rows-fr gap-4 xl:grid-cols-2">
                <ConfigCard title="已安装的 Skills" description="查看当前可读取的 Skills。" variant="list" bodyHeight={420} bodyScroll="auto" className="h-full">
                    <div className="space-y-3">
                        <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3 text-sm text-slate-600">
                            Skills 目录：<span className="font-medium break-all text-slate-900">{catalog.skills.root}</span>
                        </div>
                        {catalog.skills.items.length === 0 ? (
                            <EmptyState title="还没有可用 Skills" description="你可以通过命令行安装或上传压缩包添加新的 Skills。" />
                        ) : (
                            catalog.skills.items.map((skill) => (
                                <div key={skill.name} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                                    <div className="flex items-start justify-between gap-3">
                                        <div className="min-w-0 space-y-2">
                                            <div className="flex items-center gap-2">
                                                <PackageCheck className="h-4 w-4 text-emerald-600" />
                                                <div className="text-sm font-semibold text-slate-900">{skill.name}</div>
                                            </div>
                                            <div className="line-clamp-2 text-sm leading-6 text-slate-600">{skill.description}</div>
                                            <div className="break-all rounded-xl bg-slate-50 px-3 py-2 text-xs text-slate-500">{skill.path}</div>
                                        </div>
                                        <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-500" />
                                    </div>
                                </div>
                            ))
                        )}
                    </div>
                </ConfigCard>

                <ConfigCard title="MCP 服务" description="查看服务状态和工具数量。" variant="list" bodyHeight={420} bodyScroll="auto" className="h-full">
                    <div className="space-y-3">
                        {catalog.mcp.servers.length === 0 ? (
                            <EmptyState title="还没有 MCP 服务" description="你可以导入一份 MCP JSON 配置，把新的 MCP 服务接到系统里。" />
                        ) : (
                            catalog.mcp.servers.map((server) => (
                                <div key={server.name} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                                    <div className="flex items-start justify-between gap-3">
                                        <div className="min-w-0 space-y-2">
                                            <div className="flex flex-wrap items-center gap-2">
                                                <div className="text-sm font-semibold text-slate-900">{server.name}</div>
                                                <Badge variant={server.status === "connected" ? "default" : server.status === "disabled" ? "secondary" : "destructive"}>{statusLabel(server.status)}</Badge>
                                                <Badge variant="outline">{server.transport}</Badge>
                                            </div>
                                            <div className="break-all text-xs text-slate-500">{server.target || "未提供命令或地址"}</div>
                                            <div className="text-xs text-slate-600">可用工具：{server.toolCount}</div>
                                            <div className="flex flex-wrap gap-2">
                                                {server.tools.slice(0, 6).map((tool) => (
                                                    <Badge key={tool.name} variant="secondary">{tool.name}</Badge>
                                                ))}
                                                {server.tools.length > 6 ? <Badge variant="secondary">+{server.tools.length - 6}</Badge> : null}
                                            </div>
                                        </div>
                                        <Server className="mt-0.5 h-5 w-5 shrink-0 text-sky-600" />
                                    </div>
                                </div>
                            ))
                        )}
                    </div>
                </ConfigCard>
            </div>

            <div className="grid auto-rows-fr gap-4 xl:grid-cols-2">
                <ConfigCard title="添加 Skills" description="通过命令或压缩包添加 Skills。" variant="editor" bodyHeight="clamp" bodyScroll="auto" className="h-full">
                    <div className="space-y-4">
                        <div className="space-y-2">
                            <Label>命令行安装 Skills</Label>
                            <Input value={commandInput} onChange={(event) => setCommandInput(event.target.value)} placeholder="npx skills add https://github.com/vercel-labs/skills --skill find-skills" />
                            <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3 text-xs leading-6 text-slate-600">
                                支持命令格式：<span className="font-mono text-slate-900">npx skills add &lt;source&gt; [--skill &lt;name&gt;] [--overwrite]</span>。安装器会把 Skill 放到 <span className="font-mono text-slate-900">~/.agents/skills</span>。
                            </div>
                            <div className="text-xs leading-5 text-slate-500">
                                也可以前往{" "}
                                <a href="https://skills.sh/" target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-sky-600 underline">
                                    skills.sh
                                    <ExternalLink className="h-3 w-3" />
                                </a>
                                查找灵感。
                            </div>
                        </div>
                        {installResult ? (
                            <div className="space-y-3 rounded-2xl border border-slate-200 bg-slate-50/80 p-4 text-sm text-slate-700">
                                <div className="flex flex-wrap items-center gap-2"><Badge variant="outline">来源</Badge><span className="break-all">{installResult.source}</span></div>
                                <div className="flex flex-wrap items-center gap-2"><Badge variant="outline">目标目录</Badge><span className="break-all">{installResult.targetRoot}</span></div>
                                <div className="grid gap-3 md:grid-cols-3">
                                    <StatPill label="已安装" value={installResult.installed.length} />
                                    <StatPill label="冲突" value={installResult.conflicts.length} />
                                    <StatPill label="警告" value={installResult.warnings.length} />
                                </div>
                            </div>
                        ) : null}
                        <div className="flex flex-wrap gap-3">
                            <Button onClick={() => void handleCommandInstall()} disabled={installingCommand || !commandInput.trim()}>
                                <Terminal className="mr-2 h-4 w-4" />
                                {installingCommand ? "安装中..." : "运行安装命令"}
                            </Button>
                            <div className="flex items-center gap-3">
                                <Input ref={fileInputRef} type="file" accept=".zip" onChange={handleZipUpload} disabled={uploadingZip} className="w-[260px] cursor-pointer" />
                                {uploadingZip ? <Loader2 className="h-4 w-4 animate-spin text-slate-400" /> : null}
                            </div>
                        </div>
                    </div>
                </ConfigCard>

                <ConfigCard title="MCP 配置" description="导入 MCP JSON 并刷新服务。" variant="editor" bodyHeight="clamp" bodyScroll="auto" className="h-full">
                    <div className="space-y-4">
                        <div className="grid gap-3 md:grid-cols-3">
                            <StatPill label="已连接服务" value={health.mcp.statusBreakdown.connected || 0} />
                            <StatPill label="已停用服务" value={health.mcp.statusBreakdown.disabled || 0} />
                            <StatPill label="异常服务" value={health.mcp.statusBreakdown.error || 0} />
                        </div>
                        <Dialog open={mcpDialogOpen} onOpenChange={setMcpDialogOpen}>
                            <DialogTrigger asChild>
                                <Button variant="outline">
                                    <Plus className="mr-2 h-4 w-4" />
                                    从 JSON 导入
                                </Button>
                            </DialogTrigger>
                            <DialogContent className="max-w-2xl">
                                <DialogHeader>
                                    <DialogTitle>导入 MCP 配置</DialogTitle>
                                    <DialogDescription>请把 MCP 服务提供方给出的 JSON 配置粘贴到输入框中，系统会自动合并到现有配置。</DialogDescription>
                                </DialogHeader>
                                <div className="py-4">
                                    <Textarea className="h-[300px] bg-slate-50 font-mono text-sm" value={mcpConfigInput} onChange={(event) => setMcpConfigInput(event.target.value)} />
                                </div>
                                <DialogFooter>
                                    <Button variant="outline" onClick={() => setMcpDialogOpen(false)}>取消</Button>
                                    <Button onClick={() => void saveMcpConfig()} disabled={savingMcp}>{savingMcp ? "导入中..." : "确认导入"}</Button>
                                </DialogFooter>
                            </DialogContent>
                        </Dialog>
                        <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3 text-sm text-slate-600">
                            <div className="flex items-start gap-2">
                                <Wrench className="mt-0.5 h-4 w-4 text-sky-600" />
                                <div>普通保护和均衡保护默认不会阻断 Skills 目录发现、Skills 读取、MCP 服务发现和 MCP 工具读取。</div>
                            </div>
                        </div>
                    </div>
                </ConfigCard>
            </div>

        </AdminPageShell>
    );
}
