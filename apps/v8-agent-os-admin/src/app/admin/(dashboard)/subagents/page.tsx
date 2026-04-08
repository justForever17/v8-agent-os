"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { useToast } from "@/components/ui/use-toast";
import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";
import {
    BrainCircuit,
    Cable,
    ChevronDown,
    Loader2,
    Plus,
    RefreshCw,
    Save,
    ShieldCheck,
    Sparkles,
    Trash2,
    Wrench,
} from "lucide-react";
import { BASELINE_SYSTEM_TOOLS } from "@/lib/runtime-baseline-tools";

type Agent = {
    id: string;
    name: string;
    description: string | null;
    icon: string | null;
    avatar: string | null;
    modelId: string;
    systemPrompt: string | null;
    tools: string[] | null;
    skills: string[] | null;
    roleLabel: string | null;
    createdBy?: string;
    isEnabled: boolean;
    reflection_enabled?: boolean;
    max_reflections?: number;
    tool_mode?: "explicit" | "contextual_auto" | string | null;
    model?: {
        name: string;
        provider?: { name?: string | null } | null;
    } | null;
};

type AIModel = {
    id: string;
    name: string;
    provider?: { name?: string | null } | null;
};

type MCPTool = {
    name: string;
    description: string;
    serverName?: string;
};

type SkillEntry = {
    name: string;
    description: string;
    path: string;
};

type PluginHostTool = {
    canonicalName: string;
    toolName?: string;
    pluginId?: string | null;
    label?: string;
    description?: string;
};

type BridgeToolsPayload = {
    inventory?: PluginHostTool[];
};

type ExtensionsCatalogPayload = {
    fingerprint?: string | null;
    changedAt?: string | null;
    lastSkillInventoryChange?: {
        reason?: string | null;
        changedAt?: string | null;
        fingerprint?: string | null;
        addedSkills?: string[];
        removedSkills?: string[];
        updatedSkills?: string[];
    } | null;
    summary?: {
        mcpServerCount?: number;
        connectedMcpServerCount?: number;
        mcpToolCount?: number;
    };
    skills?: {
        root?: string;
        roots?: string[];
        fingerprint?: string | null;
        changedAt?: string | null;
        items?: SkillEntry[];
    };
    mcp?: {
        servers?: Array<{
            name?: string;
            status?: "connected" | "disabled" | "error";
            tools?: Array<{ name?: string; description?: string }>;
        }>;
    };
};

type AgentFormState = {
    name: string;
    description: string;
    roleLabel: string;
    icon: string;
    avatar: string;
    modelId: string;
    systemPrompt: string;
    tools: string[];
    toolMode: "explicit" | "contextual_auto";
    reflectionEnabled: boolean;
    maxReflections: number;
};

type ToolPanelKey = "baseline" | "skills" | "mcp" | "plugin_host";

const DEFAULT_FORM_STATE: AgentFormState = {
    name: "",
    description: "",
    roleLabel: "",
    icon: "🤖",
    avatar: "",
    modelId: "",
    systemPrompt: "",
    tools: [],
    toolMode: "contextual_auto",
    reflectionEnabled: false,
    maxReflections: 3,
};

function classifySelector(
    selector: string,
    skillNames: Set<string>,
    mcpNames: Set<string>,
    pluginHostNames: Set<string>,
) {
    if (skillNames.has(selector)) return "skill";
    if (mcpNames.has(selector)) return "mcp";
    if (pluginHostNames.has(selector)) return "plugin_host";
    return "other";
}

export default function SubagentsPage() {
    const t = useT();
    const { toast } = useToast();
    const [agents, setAgents] = useState<Agent[]>([]);
    const [models, setModels] = useState<AIModel[]>([]);
    const [mcpTools, setMcpTools] = useState<MCPTool[]>([]);
    const [skills, setSkills] = useState<SkillEntry[]>([]);
    const [pluginHostTools, setPluginHostTools] = useState<PluginHostTool[]>([]);
    const [extensionsSummary, setExtensionsSummary] = useState<{ mcpServerCount: number; connectedMcpServerCount: number; mcpToolCount: number }>({
        mcpServerCount: 0,
        connectedMcpServerCount: 0,
        mcpToolCount: 0,
    });
    const [bridgeError, setBridgeError] = useState<string | null>(null);
    const [defaultModelId, setDefaultModelId] = useState("");
    const [isLoading, setIsLoading] = useState(false);
    const [isDialogOpen, setIsDialogOpen] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [editingAgent, setEditingAgent] = useState<Agent | null>(null);
    const [form, setForm] = useState<AgentFormState>(DEFAULT_FORM_STATE);
    const [toolPanels, setToolPanels] = useState<Record<ToolPanelKey, boolean>>({
        baseline: false,
        skills: true,
        mcp: false,
        plugin_host: false,
    });

    const skillNames = useMemo(() => new Set(skills.map((item) => item.name)), [skills]);
    const mcpNames = useMemo(() => new Set(mcpTools.map((item) => item.name)), [mcpTools]);
    const pluginHostNames = useMemo(
        () => new Set(pluginHostTools.map((item) => String(item.canonicalName || item.toolName || "").trim()).filter(Boolean)),
        [pluginHostTools],
    );

    const groupedMcpTools = useMemo(() => {
        return mcpTools.reduce<Record<string, MCPTool[]>>((acc, tool) => {
            const key = String(tool.serverName || "MCP").trim() || "MCP";
            acc[key] = acc[key] || [];
            acc[key].push(tool);
            return acc;
        }, {});
    }, [mcpTools]);

    const groupedPluginHostTools = useMemo(() => {
        return pluginHostTools.reduce<Record<string, PluginHostTool[]>>((acc, tool) => {
            const key = String(tool.pluginId || "gateway").trim() || "gateway";
            acc[key] = acc[key] || [];
            acc[key].push(tool);
            return acc;
        }, {});
    }, [pluginHostTools]);

    const mcpServiceCount = extensionsSummary.mcpServerCount;
    const connectedMcpServiceCount = extensionsSummary.connectedMcpServerCount;
    const availableMcpToolCount = extensionsSummary.mcpToolCount;

    const resetForm = useCallback(
        (agent?: Agent | null) => {
            if (!agent) {
                setForm({ ...DEFAULT_FORM_STATE, modelId: defaultModelId || "" });
                return;
            }
            setForm({
                name: agent.name || "",
                description: agent.description || "",
                roleLabel: agent.roleLabel || "",
                icon: agent.icon || "🤖",
                avatar: agent.avatar || "",
                modelId: agent.modelId || defaultModelId,
                systemPrompt: agent.systemPrompt || "",
                tools: Array.isArray(agent.tools) ? agent.tools : [],
                toolMode: agent.tool_mode === "explicit" ? "explicit" : "contextual_auto",
                reflectionEnabled: Boolean(agent.reflection_enabled),
                maxReflections: agent.max_reflections || 3,
            });
        },
        [defaultModelId],
    );

    const toggleSelector = useCallback((selector: string, checked: boolean) => {
        setForm((current) => ({
            ...current,
            tools: checked
                ? Array.from(new Set([...current.tools, selector]))
                : current.tools.filter((item) => item !== selector),
        }));
    }, []);

    const toggleToolPanel = useCallback((panel: ToolPanelKey) => {
        setToolPanels((current) => ({ ...current, [panel]: !current[panel] }));
    }, []);

    const renderToolBadgeSummary = useCallback(
        (selectors: string[]) => {
            const counts = selectors.reduce(
                (acc, selector) => {
                    const kind = classifySelector(selector, skillNames, mcpNames, pluginHostNames);
                    acc[kind] += 1;
                    return acc;
                },
                { skill: 0, mcp: 0, plugin_host: 0, other: 0 },
            );
            return (
                <div className="flex flex-wrap gap-2">
                    <Badge variant="secondary">{counts.skill} {t(lt("技能", "Skills"))}</Badge>
                    <Badge variant="secondary">{counts.mcp} MCP</Badge>
                    <Badge variant="secondary">{counts.plugin_host} PluginHost</Badge>
                    {counts.other > 0 ? <Badge variant="outline">{counts.other} {t(lt("其他", "Other"))}</Badge> : null}
                </div>
            );
        },
        [mcpNames, pluginHostNames, skillNames, t],
    );

    const fetchData = useCallback(async () => {
        setIsLoading(true);
        try {
            const [agentsRes, modelsRes, defaultModelRes, extensionsRes, bridgeRes] = await Promise.all([
                fetch("/api/agents", { cache: "no-store" }),
                fetch("/api/models", { cache: "no-store" }),
                fetch("/api/settings/default-agent-model", { cache: "no-store" }),
                fetch("/api/extensions/catalog", { cache: "no-store" }),
                fetch("/api/plugin-host/bridge/tools?limit=24", { cache: "no-store" }),
            ]);

            if (agentsRes.ok) setAgents(await agentsRes.json());
            if (modelsRes.ok) setModels(await modelsRes.json());
            if (defaultModelRes.ok) {
                const data = await defaultModelRes.json();
                setDefaultModelId(String(data.modelId || "").trim());
            }
            if (extensionsRes.ok) {
                const data: ExtensionsCatalogPayload = await extensionsRes.json();
                setSkills(Array.isArray(data.skills?.items) ? data.skills!.items! : []);
                const flattenedMcpTools = Array.isArray(data.mcp?.servers)
                    ? data.mcp!.servers!
                          .flatMap((server) =>
                              Array.isArray(server.tools)
                                  ? server.tools.map((tool) => ({
                                        name: String(tool.name || "").trim(),
                                        description: String(tool.description || "").trim(),
                                        serverName: String(server.name || "MCP").trim() || "MCP",
                                    }))
                                  : []
                          )
                          .filter((tool) => tool.name)
                    : [];
                setMcpTools(flattenedMcpTools);
                setExtensionsSummary({
                    mcpServerCount: Number(data.summary?.mcpServerCount || 0) || 0,
                    connectedMcpServerCount: Number(data.summary?.connectedMcpServerCount || 0) || 0,
                    mcpToolCount: Number(data.summary?.mcpToolCount || 0) || 0,
                });
            }
            if (bridgeRes.ok) {
                const data: BridgeToolsPayload = await bridgeRes.json();
                setPluginHostTools(Array.isArray(data.inventory) ? data.inventory : []);
                setBridgeError(null);
            } else {
                const data = await bridgeRes.json().catch(() => ({}));
                setPluginHostTools([]);
                setBridgeError(typeof data?.error === "string" ? data.error : t(lt("当前无法读取 PluginHostRuntime bridge 工具目录。", "Unable to read the PluginHostRuntime bridge tool catalog right now.")));
            }
        } catch (error) {
            console.error("Failed to fetch subagent data", error);
            toast({
                title: t(lt("加载失败", "Load failed")),
                description: t(lt("当前无法读取子 Agent 配置。", "Unable to read subagent configuration right now.")),
                variant: "destructive",
            });
        } finally {
            setIsLoading(false);
        }
    }, [t, toast]);

    useEffect(() => {
        void fetchData();
    }, [fetchData]);

    useEffect(() => {
        if (isDialogOpen) {
            resetForm(editingAgent);
        }
    }, [defaultModelId, editingAgent, isDialogOpen, resetForm]);

    const handleSave = useCallback(async () => {
        if (!form.name.trim()) {
            toast({ title: t(lt("名称必填", "Name required")), description: t(lt("请先填写子 Agent 名称。", "Please enter a subagent name first.")), variant: "destructive" });
            return;
        }
        if (!form.modelId.trim()) {
            toast({ title: t(lt("模型必填", "Model required")), description: t(lt("请先为子 Agent 选择模型。", "Please choose a model for the subagent first.")), variant: "destructive" });
            return;
        }
        setIsSaving(true);
        try {
            const payload = {
                name: form.name.trim(),
                description: form.description.trim(),
                icon: form.icon.trim(),
                avatar: form.avatar.trim(),
                roleLabel: form.roleLabel.trim(),
                modelId: form.modelId.trim(),
                systemPrompt: form.systemPrompt,
                tools: form.toolMode === "explicit" ? form.tools : [],
                tool_mode: form.toolMode,
                reflection_enabled: form.reflectionEnabled,
                max_reflections: form.maxReflections,
                isEnabled: true,
                createdBy: editingAgent?.createdBy || "human",
            };
            const url = editingAgent ? `/api/agents/${editingAgent.id}` : "/api/agents";
            const method = editingAgent ? "PUT" : "POST";
            const response = await fetch(url, {
                method,
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(String(data?.detail || data?.error || response.status));
            }
            toast({
                title: editingAgent ? t(lt("子 Agent 已更新", "Subagent updated")) : t(lt("子 Agent 已创建", "Subagent created")),
                description: form.toolMode === "contextual_auto"
                    ? t(lt("该子 Agent 会继承当前 route 的 skills、MCP、PluginHost 与基础系统工具候选。", "This subagent inherits the current route's skills, MCP candidates, PluginHost candidates, and baseline system tools."))
                    : t(lt("该子 Agent 会按显式 selector 使用 skills、MCP 与 PluginHost 工具。", "This subagent uses explicitly selected skills, MCP tools, and PluginHost selectors.")),
            });
            setIsDialogOpen(false);
            setEditingAgent(null);
            await fetchData();
        } catch (error) {
            console.error("Failed to save subagent", error);
            toast({
                title: t(lt("保存失败", "Save failed")),
                description: error instanceof Error ? error.message : t(lt("请稍后重试。", "Please try again later.")),
                variant: "destructive",
            });
        } finally {
            setIsSaving(false);
        }
    }, [editingAgent, fetchData, form, t, toast]);

    const handleDelete = useCallback(async (id: string) => {
        if (!confirm(t(lt("确定要删除这个子 Agent 吗？", "Delete this subagent?")))) return;
        try {
            const response = await fetch(`/api/agents/${id}`, { method: "DELETE" });
            if (!response.ok) {
                throw new Error(String(response.status));
            }
            toast({ title: t(lt("子 Agent 已删除", "Subagent deleted")) });
            await fetchData();
        } catch (error) {
            console.error("Failed to delete subagent", error);
            toast({
                title: t(lt("删除失败", "Delete failed")),
                description: t(lt("当前无法删除该子 Agent。", "Unable to delete this subagent right now.")),
                variant: "destructive",
            });
        }
    }, [fetchData, t, toast]);

    return (
        <div className="mx-auto max-w-7xl space-y-8 p-8">
            <div className="flex items-center justify-between gap-3">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">{t(lt("子智能体", "Subagents"))}</h1>
                    <p className="mt-1 text-muted-foreground">
                        {t(lt("这里管理子 Agent 的 tool_mode、显式 selector、skills 继承和并发委派能力。", "Manage subagent tool modes, explicit selectors, skill inheritance, and parallel delegation here."))}
                    </p>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" onClick={() => void fetchData()} disabled={isLoading}>
                        <RefreshCw className={`mr-2 h-4 w-4 ${isLoading ? "animate-spin" : ""}`} />
                        {t(lt("刷新", "Refresh"))}
                    </Button>
                    <Button
                        onClick={() => {
                            setEditingAgent(null);
                            setIsDialogOpen(true);
                            resetForm(null);
                        }}
                    >
                        <Plus className="mr-2 h-4 w-4" />
                        {t(lt("新建子 Agent", "Create subagent"))}
                    </Button>
                </div>
            </div>

            <div className="grid gap-4 lg:grid-cols-3">
                <Card className="rounded-3xl border-slate-200 bg-white/95 shadow-sm">
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-base"><ShieldCheck className="h-4 w-4 text-sky-600" />{t(lt("基础系统工具", "Baseline System Tools"))}</CardTitle>
                        <CardDescription>{t(lt("所有子 Agent 默认继承的基础读写、命令、检索与媒体分析能力。", "These are the baseline read/write, command, search, and media-analysis capabilities every subagent inherits by default."))}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        <div className="flex flex-wrap gap-2">
                            {BASELINE_SYSTEM_TOOLS.slice(0, 8).map((name) => (
                                <Badge key={name} variant="outline" className="font-mono text-[11px]">{name}</Badge>
                            ))}
                            {BASELINE_SYSTEM_TOOLS.length > 8 ? <Badge variant="secondary">+{BASELINE_SYSTEM_TOOLS.length - 8}</Badge> : null}
                        </div>
                        <p className="text-xs leading-5 text-slate-500">
                            {t(lt("这些工具不会因为 runtime route 没命中而完全消失；高风险的底层桌面/进程治理工具仍保持显式保护。", "These tools do not disappear just because a runtime route is not selected; high-risk low-level desktop and process controls still stay explicitly protected."))}
                        </p>
                    </CardContent>
                </Card>
                <Card className="rounded-3xl border-slate-200 bg-white/95 shadow-sm">
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-base"><Sparkles className="h-4 w-4 text-violet-600" />{t(lt("Skills / 路由继承", "Skills / Route Inheritance"))}</CardTitle>
                        <CardDescription>{t(lt("contextual_auto 子 Agent 会继承当前 route 的 skill、MCP 与 PluginHost 候选。", "contextual_auto subagents inherit the current route's skill, MCP, and PluginHost candidates."))}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-2 text-sm text-slate-500">
                        <div>{t(lt("已加载 Skills", "Loaded skills"))}：<span className="font-medium text-slate-900">{skills.length}</span></div>
                        <div>{t(lt("已连接 MCP 服务", "Connected MCP servers"))}：<span className="font-medium text-slate-900">{connectedMcpServiceCount}</span></div>
                        <div>{t(lt("可用 MCP 工具", "Available MCP tools"))}：<span className="font-medium text-slate-900">{availableMcpToolCount}</span></div>
                        <div>{t(lt("已登记 MCP 服务", "Registered MCP servers"))}：<span className="font-medium text-slate-900">{mcpServiceCount}</span></div>
                        <div>{t(lt("已发现 PluginHost 工具", "Discovered PluginHost tools"))}：<span className="font-medium text-slate-900">{pluginHostTools.length}</span></div>
                        <div className="text-xs leading-5">
                            {t(lt("子 Agent 会共享同一套 extensions runtime 路由，不再出现 Supervisor 懂了但子 Agent 没候选、没技能的断层。", "Subagents share the same extensions-runtime routing surface, so you no longer get the split where Supervisor sees candidates but subagents do not."))}
                        </div>
                    </CardContent>
                </Card>
                <Card className="rounded-3xl border-slate-200 bg-white/95 shadow-sm">
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-base"><Cable className="h-4 w-4 text-emerald-600" />{t(lt("委派 / 并行", "Delegation / Parallel"))}</CardTitle>
                        <CardDescription>{t(lt("Supervisor 现在支持受控并发 fan-out / join。", "Supervisor now supports controlled parallel fan-out / join."))}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-2 text-sm text-slate-500">
                        <div>{t(lt("delegate_parallel", "delegate_parallel"))}：<span className="font-medium text-slate-900">{t(lt("已启用", "Enabled"))}</span></div>
                        <div>{t(lt("并发上限", "Parallel limit"))}：<span className="font-medium text-slate-900">2</span></div>
                        <div className="text-xs leading-5">
                            {t(lt("并发委派只作用于已注册子 Agent，不支持嵌套并发；失败结果会和成功结果一起回收给 Supervisor。", "Parallel delegation only targets registered subagents, does not support nested parallelism, and returns failed results together with successful ones to Supervisor."))}
                        </div>
                    </CardContent>
                </Card>
            </div>

            <div className="grid gap-6 md:grid-cols-2 xl:grid-cols-3">
                {agents.map((agent) => {
                    const selectors = Array.isArray(agent.tools) ? agent.tools : [];
                    const toolMode = agent.tool_mode === "explicit" ? "explicit" : "contextual_auto";
                    return (
                        <Card key={agent.id} className="rounded-3xl border-slate-200 bg-white/95 shadow-sm">
                            <CardHeader className="space-y-4">
                                <div className="flex items-start justify-between gap-3">
                                    <div className="flex min-w-0 items-center gap-3">
                                        {agent.avatar ? (
                                            // eslint-disable-next-line @next/next/no-img-element
                                            <img src={agent.avatar} alt={agent.name} className="h-11 w-11 rounded-2xl object-cover" />
                                        ) : (
                                            <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-sky-50 text-lg text-sky-700">
                                                {agent.icon || "🤖"}
                                            </div>
                                        )}
                                            <div className="min-w-0">
                                                <CardTitle className="truncate text-lg">{agent.name}</CardTitle>
                                                <CardDescription className="truncate">{agent.model?.name || agent.modelId || t(lt("未知模型", "Unknown model"))}</CardDescription>
                                            </div>
                                        </div>
                                        <div className="flex gap-1">
                                            <Button type="button" variant="ghost" size="sm" onClick={() => { setEditingAgent(agent); setIsDialogOpen(true); }}>
                                                {t(lt("编辑", "Edit"))}
                                            </Button>
                                        <Button type="button" variant="ghost" size="sm" className="text-rose-600" onClick={() => void handleDelete(agent.id)}>
                                            <Trash2 className="h-4 w-4" />
                                        </Button>
                                    </div>
                                </div>
                                <div className="flex flex-wrap gap-2">
                                    <Badge variant={toolMode === "explicit" ? "default" : "secondary"}>{toolMode}</Badge>
                                    {agent.createdBy === "supervisor" ? <Badge className="bg-indigo-600 hover:bg-indigo-600">{t(lt("主理人创建", "Created by lead"))}</Badge> : null}
                                    {agent.reflection_enabled ? <Badge variant="outline">{t(lt("自我检查", "Self-check"))} × {agent.max_reflections || 3}</Badge> : null}
                                </div>
                            </CardHeader>
                            <CardContent className="space-y-4">
                                <p className="min-h-[3rem] text-sm leading-6 text-slate-500">{agent.description || t(lt("暂无描述", "No description"))}</p>
                                {toolMode === "explicit" ? (
                                    <div className="space-y-2">
                                        <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{t(lt("显式能力预览", "Explicit capability preview"))}</div>
                                        {renderToolBadgeSummary(selectors)}
                                    </div>
                                ) : (
                                    <div className="space-y-2 rounded-2xl border border-slate-200 bg-slate-50/70 p-3 text-xs leading-5 text-slate-500">
                                        <div className="font-medium text-slate-900">{t("contextual_auto")}</div>
                                        <div>{t(lt("默认继承当前 route 的 Skills / MCP / PluginHost 候选，并始终保留 baseline system tools。", "By default, this inherits the current route's Skills / MCP / PluginHost candidates and always keeps baseline system tools."))}</div>
                                    </div>
                                )}
                            </CardContent>
                        </Card>
                    );
                })}
                {agents.length === 0 ? (
                    <div className="col-span-full rounded-3xl border border-dashed border-slate-200 bg-slate-50/80 py-12 text-center text-sm text-slate-500">
                        {t(lt("暂无子 Agent。创建后，Supervisor 就可以把明确分工的任务交给它们。", "No subagents yet. Once you create them, Supervisor can delegate clearly scoped work."))}
                    </div>
                ) : null}
            </div>

            <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
                <DialogContent className="flex h-[min(92vh,960px)] max-w-4xl flex-col overflow-hidden p-0">
                    <DialogHeader className="shrink-0 border-b border-slate-200 px-6 py-5">
                        <DialogTitle>{editingAgent ? t(lt("编辑子 Agent", "Edit subagent")) : t(lt("新建子 Agent", "Create subagent"))}</DialogTitle>
                    </DialogHeader>
                    <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-6 pt-5">
                        <div className="space-y-6">
                        <div className="grid gap-4 md:grid-cols-2">
                            <div className="space-y-2">
                                <Label>{t(lt("名称", "Name"))}</Label>
                                <Input value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} placeholder={t(lt("例如：代码助手", "For example: code assistant"))} />
                            </div>
                            <div className="space-y-2">
                                <Label>{t(lt("角色标签", "Role label"))}</Label>
                                <Input value={form.roleLabel} onChange={(event) => setForm((current) => ({ ...current, roleLabel: event.target.value }))} placeholder={t(lt("例如：专家", "For example: specialist"))} />
                            </div>
                            <div className="space-y-2">
                                <Label>{t(lt("图标", "Icon"))}</Label>
                                <Input value={form.icon} onChange={(event) => setForm((current) => ({ ...current, icon: event.target.value }))} placeholder="🤖" />
                            </div>
                            <div className="space-y-2">
                                <Label>{t(lt("头像 URL", "Avatar URL"))}</Label>
                                <Input value={form.avatar} onChange={(event) => setForm((current) => ({ ...current, avatar: event.target.value }))} placeholder={t(lt("可留空，使用图标作为展示。", "Optional. Leave empty to use the icon instead."))} />
                            </div>
                        </div>

                        <div className="space-y-2">
                            <Label>{t(lt("职责说明", "Description"))}</Label>
                            <Textarea value={form.description} onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))} placeholder={t(lt("简要说明这个子 Agent 负责什么。", "Briefly describe what this subagent is responsible for."))} />
                        </div>

                        <div className="grid gap-4 md:grid-cols-2">
                            <div className="space-y-2">
                                <Label>{t(lt("模型", "Model"))}</Label>
                                <Select value={form.modelId} onValueChange={(value) => setForm((current) => ({ ...current, modelId: value }))}>
                                    <SelectTrigger>
                                        <SelectValue placeholder={t(lt("选择一个模型", "Choose a model"))} />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {models.map((model) => (
                                            <SelectItem key={model.id} value={model.id}>
                                                {model.name} {model.provider?.name ? `(${model.provider.name})` : ""}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-2">
                                <Label>{t(lt("tool_mode", "tool_mode"))}</Label>
                                <Select value={form.toolMode} onValueChange={(value: "explicit" | "contextual_auto") => setForm((current) => ({ ...current, toolMode: value }))}>
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="contextual_auto">contextual_auto</SelectItem>
                                        <SelectItem value="explicit">explicit</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                        </div>

                        <div className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4 text-sm leading-6 text-slate-500">
                            {form.toolMode === "contextual_auto" ? (
                                <>
                                    <div className="font-medium text-slate-900">{t(lt("contextual_auto 会做什么", "What contextual_auto does"))}</div>
                                    <div>{t(lt("子 Agent 会继承 Supervisor 当轮 route 已筛出的 skills、MCP、PluginHost 候选，再按 delegated task 继续收窄。同时 baseline system tools 默认常开。", "The subagent inherits the skills, MCP, and PluginHost candidates already screened by Supervisor on the current route, then narrows them further for the delegated task. Baseline system tools stay on by default."))}</div>
                                </>
                            ) : (
                                <>
                                    <div className="font-medium text-slate-900">{t(lt("explicit 会做什么", "What explicit does"))}</div>
                                    <div>{t(lt("子 Agent 只会使用你在下面显式选择的 skills、MCP 与 PluginHost selector；系统原生工具会继续默认保留，不会因为这里没勾选而失效。", "The subagent only uses the skills, MCP tools, and PluginHost selectors you explicitly choose below. Baseline system tools still remain available and do not disappear just because they are not checked here."))}</div>
                                </>
                            )}
                        </div>

                        {form.toolMode === "explicit" ? (
                            <div className="grid gap-4 xl:grid-cols-2 xl:items-start">
                                <Card className="rounded-3xl border-slate-200 xl:col-span-2">
                                    <CardHeader className="space-y-0">
                                        <button
                                            type="button"
                                            className="flex w-full items-start justify-between gap-3 text-left"
                                            onClick={() => toggleToolPanel("baseline")}
                                        >
                                            <div className="space-y-1">
                                                <CardTitle className="flex items-center gap-2 text-base">
                                                    <ShieldCheck className="h-4 w-4 text-sky-600" />
                                                    {t(lt("系统原生工具", "Baseline system tools"))}
                                                    <Badge variant="outline">{BASELINE_SYSTEM_TOOLS.length}</Badge>
                                                </CardTitle>
                                                <CardDescription>
                                                    {t(lt("这些工具始终保留给子 Agent，不需要在 explicit 模式下额外勾选。", "These tools always remain available to the subagent and do not need to be selected again in explicit mode."))}
                                                </CardDescription>
                                            </div>
                                            <ChevronDown className={`mt-1 h-4 w-4 shrink-0 text-slate-400 transition-transform ${toolPanels.baseline ? "rotate-180" : ""}`} />
                                        </button>
                                    </CardHeader>
                                    {toolPanels.baseline ? (
                                        <CardContent className="max-h-[148px] space-y-3 overflow-y-auto overscroll-contain pr-2">
                                            <div className="flex flex-wrap gap-2">
                                                {BASELINE_SYSTEM_TOOLS.map((name) => (
                                                    <Badge key={name} variant="secondary" className="font-mono text-[11px]">
                                                        {name}
                                                    </Badge>
                                                ))}
                                            </div>
                                            <p className="text-xs leading-5 text-slate-500">
                                                {t(lt("也就是说，下面的 Skills / MCP / PluginHost 只决定附加能力面，不会把读写、命令、搜索和后台命令会话这些基础系统能力拿掉。", "In other words, the sections below only control additional capability surfaces. They do not remove the baseline read/write, command, search, or background command session tools."))}
                                            </p>
                                        </CardContent>
                                    ) : null}
                                </Card>
                                <Card className="rounded-3xl border-slate-200">
                                    <CardHeader className="space-y-0">
                                        <button
                                            type="button"
                                            className="flex w-full items-start justify-between gap-3 text-left"
                                            onClick={() => toggleToolPanel("skills")}
                                        >
                                            <div className="space-y-1">
                                                <CardTitle className="flex items-center gap-2 text-base"><Sparkles className="h-4 w-4 text-violet-600" />{t(lt("Skills", "Skills"))}<Badge variant="outline">{skills.length}</Badge></CardTitle>
                                                <CardDescription>{t(lt("只读当前已安装 skills 的原生 metadata。", "Read-only view of the native metadata for currently installed skills."))}</CardDescription>
                                            </div>
                                            <ChevronDown className={`mt-1 h-4 w-4 shrink-0 text-slate-400 transition-transform ${toolPanels.skills ? "rotate-180" : ""}`} />
                                        </button>
                                    </CardHeader>
                                    {toolPanels.skills ? (
                                    <CardContent className="max-h-[224px] space-y-3 overflow-y-auto overscroll-contain pr-2">
                                        {skills.length === 0 ? <div className="text-xs text-slate-500">{t(lt("当前没有可选 skills。", "No selectable skills are available right now."))}</div> : null}
                                        {skills.map((skill) => {
                                            const checked = form.tools.includes(skill.name);
                                            return (
                                                <label key={skill.path} className="flex items-start gap-3">
                                                    <Checkbox checked={checked} onCheckedChange={(next) => toggleSelector(skill.name, Boolean(next))} className="mt-1" />
                                                    <div className="min-w-0">
                                                        <div className="text-sm font-medium text-slate-900">{skill.name}</div>
                                                        <div className="text-xs leading-5 text-slate-500">{skill.description || skill.path}</div>
                                                    </div>
                                                </label>
                                            );
                                        })}
                                    </CardContent>
                                    ) : null}
                                </Card>
                                <Card className="rounded-3xl border-slate-200">
                                    <CardHeader className="space-y-0">
                                        <button
                                            type="button"
                                            className="flex w-full items-start justify-between gap-3 text-left"
                                            onClick={() => toggleToolPanel("mcp")}
                                        >
                                            <div className="space-y-1">
                                                <CardTitle className="flex items-center gap-2 text-base"><Wrench className="h-4 w-4 text-sky-600" />MCP<Badge variant="outline">{availableMcpToolCount}</Badge></CardTitle>
                                                <CardDescription>{t(lt("显式绑定的 MCP 工具。", "Explicitly bound MCP tools."))}</CardDescription>
                                            </div>
                                            <ChevronDown className={`mt-1 h-4 w-4 shrink-0 text-slate-400 transition-transform ${toolPanels.mcp ? "rotate-180" : ""}`} />
                                        </button>
                                    </CardHeader>
                                    {toolPanels.mcp ? (
                                    <CardContent className="max-h-[224px] space-y-4 overflow-y-auto overscroll-contain pr-2">
                                        {Object.keys(groupedMcpTools).length === 0 ? <div className="text-xs text-slate-500">{t(lt("暂无可选 MCP 工具。", "No selectable MCP tools are available."))}</div> : null}
                                        {Object.entries(groupedMcpTools).map(([serverName, items]) => (
                                            <div key={serverName} className="space-y-2">
                                                <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{serverName}</div>
                                                {items.map((tool) => {
                                                    const checked = form.tools.includes(tool.name);
                                                    return (
                                                        <label key={tool.name} className="flex items-start gap-3">
                                                            <Checkbox checked={checked} onCheckedChange={(next) => toggleSelector(tool.name, Boolean(next))} className="mt-1" />
                                                            <div className="min-w-0">
                                                                <div className="break-all text-sm font-medium text-slate-900">{tool.name}</div>
                                                                <div className="text-xs leading-5 text-slate-500">{tool.description || t(lt("暂无说明。", "No description."))}</div>
                                                            </div>
                                                        </label>
                                                    );
                                                })}
                                            </div>
                                        ))}
                                    </CardContent>
                                    ) : null}
                                </Card>
                                <Card className="rounded-3xl border-slate-200">
                                    <CardHeader className="space-y-0">
                                        <button
                                            type="button"
                                            className="flex w-full items-start justify-between gap-3 text-left"
                                            onClick={() => toggleToolPanel("plugin_host")}
                                        >
                                            <div className="space-y-1">
                                                <CardTitle className="flex items-center gap-2 text-base"><BrainCircuit className="h-4 w-4 text-emerald-600" />PluginHost<Badge variant="outline">{pluginHostTools.length}</Badge></CardTitle>
                                                <CardDescription>{t(lt("显式绑定的 OpenClaw / PluginHost 工具 selector。", "Explicitly bound OpenClaw / PluginHost tool selectors."))}</CardDescription>
                                            </div>
                                            <ChevronDown className={`mt-1 h-4 w-4 shrink-0 text-slate-400 transition-transform ${toolPanels.plugin_host ? "rotate-180" : ""}`} />
                                        </button>
                                    </CardHeader>
                                    {toolPanels.plugin_host ? (
                                    <CardContent className="max-h-[224px] space-y-4 overflow-y-auto overscroll-contain pr-2">
                                        {bridgeError ? <div className="rounded-2xl border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-700">{bridgeError}</div> : null}
                                        {!bridgeError && Object.keys(groupedPluginHostTools).length === 0 ? <div className="text-xs text-slate-500">{t(lt("暂无可选 PluginHost 工具。", "No selectable PluginHost tools are available."))}</div> : null}
                                        {Object.entries(groupedPluginHostTools).map(([pluginId, items]) => (
                                            <div key={pluginId} className="space-y-2">
                                                <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{pluginId}</div>
                                                {items.map((tool) => {
                                                    const selector = String(tool.canonicalName || tool.toolName || "").trim();
                                                    const checked = form.tools.includes(selector);
                                                    return (
                                                        <label key={selector} className="flex items-start gap-3">
                                                            <Checkbox checked={checked} onCheckedChange={(next) => toggleSelector(selector, Boolean(next))} className="mt-1" />
                                                            <div className="min-w-0">
                                                                <div className="break-all text-sm font-medium text-slate-900">{selector}</div>
                                                                <div className="text-xs leading-5 text-slate-500">{tool.description || tool.label || selector}</div>
                                                            </div>
                                                        </label>
                                                    );
                                                })}
                                            </div>
                                        ))}
                                    </CardContent>
                                    ) : null}
                                </Card>
                            </div>
                        ) : null}

                        <div className="space-y-2">
                            <Label>{t(lt("系统提示词", "System prompt"))}</Label>
                            <Textarea
                                value={form.systemPrompt}
                                onChange={(event) => setForm((current) => ({ ...current, systemPrompt: event.target.value }))}
                                className="min-h-[180px] font-mono text-sm"
                                placeholder={t(lt("描述这个子 Agent 的职责边界、风格和限制。", "Describe this subagent's responsibility boundary, tone, and constraints."))}
                            />
                        </div>

                        <div className="space-y-4 rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                            <label className="flex items-center gap-3">
                                <Checkbox
                                    checked={form.reflectionEnabled}
                                    onCheckedChange={(checked) => setForm((current) => ({ ...current, reflectionEnabled: Boolean(checked) }))}
                                />
                                <div>
                                    <div className="text-sm font-medium text-slate-900">{t(lt("开启自我检查", "Enable self-check"))}</div>
                                    <div className="text-xs text-slate-500">{t(lt("工具执行失败或效果不佳时，允许子 Agent 做受控反思。", "Allow the subagent to perform controlled reflection when tool execution fails or performs poorly."))}</div>
                                </div>
                            </label>
                            {form.reflectionEnabled ? (
                                <div className="space-y-2">
                                    <Label>{t(lt("最大反思次数", "Max reflections"))}</Label>
                                    <Input
                                        type="number"
                                        min={1}
                                        max={10}
                                        value={form.maxReflections}
                                        onChange={(event) => setForm((current) => ({ ...current, maxReflections: Math.max(1, Math.min(10, Number(event.target.value) || 1)) }))}
                                        className="w-32"
                                    />
                                </div>
                            ) : null}
                        </div>

                        <Button className="w-full" onClick={() => void handleSave()} disabled={isSaving}>
                            {isSaving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                            {t(lt("保存子 Agent", "Save subagent"))}
                        </Button>
                        </div>
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    );
}
