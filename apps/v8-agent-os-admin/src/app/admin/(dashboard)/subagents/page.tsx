"use client";

import Link from "next/link";
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
import {
    ArrowLeft,
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
    capabilitySnapshot?: Record<string, unknown> | null;
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

type BaselineSystemTool = {
    name: string;
    description?: string;
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

type SupervisorConfigRegistryPayload = {
    data?: {
        modelParameters?: {
            subagent?: {
                temperature?: number | null;
            };
        } | null;
        delegation?: {
            externalWorkers?: unknown[];
        } | null;
    } | null;
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

type AgentToolSurfacePayload = {
    baselineSystemTools?: BaselineSystemTool[];
    toolModes?: {
        recommended?: string;
        modes?: Record<string, { status?: string; selectorPolicy?: string }>;
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
    capabilitySnapshotJson: string;
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
    capabilitySnapshotJson: "{}",
    reflectionEnabled: false,
    maxReflections: 3,
};

function parseOptionalTemperature(value: string) {
    const trimmed = String(value || "").trim();
    if (!trimmed) return null;
    const parsed = Number(trimmed);
    if (!Number.isFinite(parsed)) return null;
    return Math.max(Math.min(parsed, 2), 0);
}

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
    const [baselineSystemTools, setBaselineSystemTools] = useState<BaselineSystemTool[]>([]);
    const [defaultModelId, setDefaultModelId] = useState("");
    const [supervisorDomainData, setSupervisorDomainData] = useState<SupervisorConfigRegistryPayload | null>(null);
    const [externalWorkersJson, setExternalWorkersJson] = useState("[]");
    const [subagentTemperature, setSubagentTemperature] = useState("");
    const [isLoading, setIsLoading] = useState(false);
    const [isDialogOpen, setIsDialogOpen] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [isSavingExternalWorkers, setIsSavingExternalWorkers] = useState(false);
    const [isSavingSubagentTemperature, setIsSavingSubagentTemperature] = useState(false);
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
    const baselineToolNames = useMemo(
        () => baselineSystemTools.map((item) => String(item.name || "").trim()).filter(Boolean),
        [baselineSystemTools],
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

    const resolveToolModeLabel = useCallback((value?: string | null) => {
        const normalized = String(value || "").trim().toLowerCase();
        if (normalized === "contextual_auto") {
            return t("app.admin.dashboard.subagents.page.toolMode.contextualAuto");
        }
        if (normalized === "explicit") {
            return t("app.admin.dashboard.subagents.page.toolMode.explicit");
        }
        if (!normalized) {
            return t("app.admin.dashboard.subagents.page.toolMode.unknown");
        }
        return normalized.replace(/[_-]+/g, " ");
    }, [t]);

    const mcpServiceCount = extensionsSummary.mcpServerCount;
    const connectedMcpServiceCount = extensionsSummary.connectedMcpServerCount;
    const availableMcpToolCount = extensionsSummary.mcpToolCount;

    const resetForm = useCallback(
        (agent?: Agent | null) => {
            if (!agent) {
                setForm({ ...DEFAULT_FORM_STATE, modelId: defaultModelId || "" });
                return;
            }
            const capabilitySnapshot = agent.capabilitySnapshot && typeof agent.capabilitySnapshot === "object" && !Array.isArray(agent.capabilitySnapshot)
                ? agent.capabilitySnapshot
                : {};
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
                capabilitySnapshotJson: JSON.stringify(capabilitySnapshot, null, 2),
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
                    <Badge variant="secondary">{counts.skill} {t("app.admin.dashboard.subagents.page.k6440d98e")}</Badge>
                    <Badge variant="secondary">{counts.mcp} MCP</Badge>
                    <Badge variant="secondary">{counts.plugin_host} PluginHost</Badge>
                    {counts.other > 0 ? <Badge variant="outline">{counts.other} {t("app.admin.dashboard.subagents.page.kd7875daf")}</Badge> : null}
                </div>
            );
        },
        [mcpNames, pluginHostNames, skillNames, t],
    );

    const fetchData = useCallback(async () => {
        setIsLoading(true);
        try {
            const [agentsRes, modelsRes, defaultModelRes, extensionsRes, bridgeRes, supervisorRes, toolSurfaceRes] = await Promise.all([
                fetch("/api/agents", { cache: "no-store" }),
                fetch("/api/models", { cache: "no-store" }),
                fetch("/api/settings/default-agent-model", { cache: "no-store" }),
                fetch("/api/extensions/catalog", { cache: "no-store" }),
                fetch("/api/plugin-host/bridge/tools?limit=24", { cache: "no-store" }),
                fetch("/api/config-registry/supervisor", { cache: "no-store" }),
                fetch("/api/agents/tool-surface", { cache: "no-store" }),
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
                setBridgeError(typeof data?.error === "string" ? data.error : t("app.admin.dashboard.subagents.page.keff963b3"));
            }
            if (supervisorRes.ok) {
                const data: SupervisorConfigRegistryPayload = await supervisorRes.json();
                setSupervisorDomainData(data);
                const temperature = data?.data?.modelParameters?.subagent?.temperature;
                setSubagentTemperature(temperature === null || temperature === undefined ? "" : String(temperature));
                setExternalWorkersJson(
                    JSON.stringify(
                        Array.isArray(data?.data?.delegation?.externalWorkers) ? data.data!.delegation!.externalWorkers : [],
                        null,
                        2,
                    ),
                );
            }
            if (toolSurfaceRes.ok) {
                const data: AgentToolSurfacePayload = await toolSurfaceRes.json();
                setBaselineSystemTools(Array.isArray(data.baselineSystemTools) ? data.baselineSystemTools : []);
            }
        } catch (error) {
            console.error("Failed to fetch subagent data", error);
            toast({
                title: t("app.admin.dashboard.subagents.page.k65ed1d75"),
                description: t("app.admin.dashboard.subagents.page.k6ccb2f21"),
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
            toast({ title: t("app.admin.dashboard.subagents.page.k2ba9f8cf"), description: t("app.admin.dashboard.subagents.page.kda9e4fc0"), variant: "destructive" });
            return;
        }
        if (!form.modelId.trim()) {
            toast({ title: t("app.admin.dashboard.subagents.page.k24a5ad1b"), description: t("app.admin.dashboard.subagents.page.ka092e243"), variant: "destructive" });
            return;
        }
        let capabilitySnapshot: Record<string, unknown> = {};
        try {
            const parsed = JSON.parse(form.capabilitySnapshotJson || "{}");
            if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
                throw new Error("capabilitySnapshot must be a JSON object.");
            }
            capabilitySnapshot = parsed as Record<string, unknown>;
        } catch (error) {
            toast({
                title: t("app.admin.dashboard.subagents.page.externalWorkers.capabilityJsonInvalidTitle"),
                description: error instanceof Error ? error.message : t("app.admin.dashboard.subagents.page.externalWorkers.capabilityJsonInvalidDescription"),
                variant: "destructive",
            });
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
                capabilitySnapshot,
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
                title: editingAgent ? t("app.admin.dashboard.subagents.page.kfeb7fab7") : t("app.admin.dashboard.subagents.page.kbd2c49ab"),
                description: form.toolMode === "contextual_auto"
                    ? t("app.admin.dashboard.subagents.page.k6693a150")
                    : t("app.admin.dashboard.subagents.page.kd4ec2786"),
            });
            setIsDialogOpen(false);
            setEditingAgent(null);
            await fetchData();
        } catch (error) {
            console.error("Failed to save subagent", error);
            toast({
                title: t("app.admin.dashboard.subagents.page.k12769ce1"),
                description: error instanceof Error ? error.message : t("app.admin.dashboard.subagents.page.ke0d2c647"),
                variant: "destructive",
            });
        } finally {
            setIsSaving(false);
        }
    }, [editingAgent, fetchData, form, t, toast]);

    const handleSaveExternalWorkers = useCallback(async () => {
        let parsed: unknown;
        try {
            parsed = JSON.parse(externalWorkersJson || "[]");
        } catch (error) {
            toast({
                title: t("app.admin.dashboard.subagents.page.externalWorkers.invalidJsonTitle"),
                description: error instanceof Error ? error.message : t("app.admin.dashboard.subagents.page.externalWorkers.invalidJsonDescription"),
                variant: "destructive",
            });
            return;
        }
        if (!Array.isArray(parsed)) {
            toast({
                title: t("app.admin.dashboard.subagents.page.externalWorkers.arrayJsonTitle"),
                description: t("app.admin.dashboard.subagents.page.externalWorkers.arrayJsonDescription"),
                variant: "destructive",
            });
            return;
        }
        setIsSavingExternalWorkers(true);
        try {
            const nextPayload = {
                data: {
                    ...(supervisorDomainData?.data || {}),
                    delegation: {
                        ...((supervisorDomainData?.data?.delegation || {}) as Record<string, unknown>),
                        externalWorkers: parsed,
                    },
                },
            };
            const response = await fetch("/api/config-registry/supervisor", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(nextPayload),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(String(data?.detail || data?.error || response.status));
            }
            setSupervisorDomainData(data);
            setExternalWorkersJson(
                JSON.stringify(
                    Array.isArray(data?.data?.delegation?.externalWorkers) ? data.data.delegation.externalWorkers : [],
                    null,
                    2,
                ),
            );
            toast({
                title: t("app.admin.dashboard.subagents.page.externalWorkers.savedTitle"),
                description: t("app.admin.dashboard.subagents.page.externalWorkers.savedDescription"),
            });
        } catch (error) {
            console.error("Failed to save external workers", error);
            toast({
                title: t("app.admin.dashboard.subagents.page.externalWorkers.saveFailedTitle"),
                description: error instanceof Error ? error.message : t("app.admin.dashboard.subagents.page.externalWorkers.unknownError"),
                variant: "destructive",
            });
        } finally {
            setIsSavingExternalWorkers(false);
        }
    }, [externalWorkersJson, supervisorDomainData, toast]);

    const handleSaveSubagentTemperature = useCallback(async () => {
        setIsSavingSubagentTemperature(true);
        try {
            const nextPayload = {
                data: {
                    ...(supervisorDomainData?.data || {}),
                    modelParameters: {
                        ...((supervisorDomainData?.data?.modelParameters || {}) as Record<string, unknown>),
                        subagent: {
                            ...(((supervisorDomainData?.data?.modelParameters?.subagent || {}) as Record<string, unknown>)),
                            temperature: parseOptionalTemperature(subagentTemperature),
                        },
                    },
                },
            };
            const response = await fetch("/api/config-registry/supervisor", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(nextPayload),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(String(data?.detail || data?.error || response.status));
            }
            setSupervisorDomainData(data);
            const temperature = data?.data?.modelParameters?.subagent?.temperature;
            setSubagentTemperature(temperature === null || temperature === undefined ? "" : String(temperature));
            toast({
                title: "Subagent 温度已保存",
                description: subagentTemperature.trim()
                    ? "后续 agent/reviewer 调用会使用该应用面 temperature 覆盖。"
                    : "已恢复为模型/供应商默认温度，不强行注入 temperature。",
            });
        } catch (error) {
            console.error("Failed to save subagent temperature", error);
            toast({
                title: "Subagent 温度保存失败",
                description: error instanceof Error ? error.message : "未知错误",
                variant: "destructive",
            });
        } finally {
            setIsSavingSubagentTemperature(false);
        }
    }, [subagentTemperature, supervisorDomainData, toast]);

    const handleDelete = useCallback(async (id: string) => {
        if (!confirm(t("app.admin.dashboard.subagents.page.ka7d365b9"))) return;
        try {
            const response = await fetch(`/api/agents/${id}`, { method: "DELETE" });
            if (!response.ok) {
                throw new Error(String(response.status));
            }
            toast({ title: t("app.admin.dashboard.subagents.page.k1b2c89e7") });
            await fetchData();
        } catch (error) {
            console.error("Failed to delete subagent", error);
            toast({
                title: t("app.admin.dashboard.subagents.page.k0915ccdf"),
                description: t("app.admin.dashboard.subagents.page.k5d01859a"),
                variant: "destructive",
            });
        }
    }, [fetchData, t, toast]);

    return (
        <div className="mx-auto max-w-7xl space-y-8 p-8">
            <div className="flex items-center justify-between gap-3">
                <div className="flex items-start gap-4">
                    <Button variant="ghost" size="icon" asChild className="mt-1 shrink-0">
                        <Link href="/admin/chat-runtime" aria-label={t("app.admin.dashboard.common.backToChatRuntime")}>
                            <ArrowLeft className="h-4 w-4" />
                        </Link>
                    </Button>
                    <div>
                        <h1 className="text-3xl font-bold tracking-tight">{t("app.admin.dashboard.subagents.page.k6c291586")}</h1>
                        <p className="mt-1 text-muted-foreground">
                            {t("app.admin.dashboard.subagents.page.k790af087")}
                        </p>
                    </div>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" onClick={() => void fetchData()} disabled={isLoading}>
                        <RefreshCw className={`mr-2 h-4 w-4 ${isLoading ? "animate-spin" : ""}`} />
                        {t("app.admin.dashboard.subagents.page.k876e8c06")}
                    </Button>
                    <Button
                        onClick={() => {
                            setEditingAgent(null);
                            setIsDialogOpen(true);
                            resetForm(null);
                        }}
                    >
                        <Plus className="mr-2 h-4 w-4" />
                        {t("app.admin.dashboard.subagents.page.k5ae562aa")}
                    </Button>
                </div>
            </div>

            <div className="grid gap-4 lg:grid-cols-4">
                <Card className="rounded-3xl border-slate-200 bg-white/95 shadow-sm">
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-base"><ShieldCheck className="h-4 w-4 text-sky-600" />{t("app.admin.dashboard.subagents.page.k00bf2013")}</CardTitle>
                        <CardDescription>{t("app.admin.dashboard.subagents.page.k32187022")}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        <div className="flex flex-wrap gap-2">
                            {baselineToolNames.slice(0, 8).map((name) => (
                                <Badge key={name} variant="outline" className="font-mono text-[11px]">{name}</Badge>
                            ))}
                            {baselineToolNames.length > 8 ? <Badge variant="secondary">+{baselineToolNames.length - 8}</Badge> : null}
                        </div>
                    </CardContent>
                </Card>
                <Card className="rounded-3xl border-slate-200 bg-white/95 shadow-sm">
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-base"><Sparkles className="h-4 w-4 text-violet-600" />{t("app.admin.dashboard.subagents.page.k9764402c")}</CardTitle>
                        <CardDescription>{t("app.admin.dashboard.subagents.page.k90999eb9")}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-2 text-sm text-slate-500">
                        <div>{t("app.admin.dashboard.subagents.page.kfe41d471")}：<span className="font-medium text-slate-900">{skills.length}</span></div>
                        <div>{t("app.admin.dashboard.subagents.page.k28701d18")}：<span className="font-medium text-slate-900">{connectedMcpServiceCount}</span></div>
                        <div>{t("app.admin.dashboard.subagents.page.kac4fbea7")}：<span className="font-medium text-slate-900">{availableMcpToolCount}</span></div>
                        <div>{t("app.admin.dashboard.subagents.page.k98864e9e")}：<span className="font-medium text-slate-900">{mcpServiceCount}</span></div>
                        <div>{t("app.admin.dashboard.subagents.page.k2b971b98")}：<span className="font-medium text-slate-900">{pluginHostTools.length}</span></div>
                        <div className="text-xs leading-5">
                            {t("app.admin.dashboard.subagents.page.k6aba54ed")}
                        </div>
                    </CardContent>
                </Card>
                <Card className="rounded-3xl border-slate-200 bg-white/95 shadow-sm">
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-base"><Cable className="h-4 w-4 text-emerald-600" />{t("app.admin.dashboard.subagents.page.k11cd990c")}</CardTitle>
                        <CardDescription>{t("app.admin.dashboard.subagents.page.kdd510546")}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-2 text-sm text-slate-500">
                        <div>{t("app.admin.dashboard.subagents.page.ke60b3448")}：<span className="font-medium text-slate-900">{t("app.admin.dashboard.subagents.page.kdb6c0cc1")}</span></div>
                        <div>{t("app.admin.dashboard.subagents.page.k150a33d0")}：<span className="font-medium text-slate-900">2</span></div>
                        <div className="text-xs leading-5">
                            {t("app.admin.dashboard.subagents.page.k6b4be373")}
                        </div>
                    </CardContent>
                </Card>
                <Card className="rounded-3xl border-slate-200 bg-white/95 shadow-sm">
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-base"><BrainCircuit className="h-4 w-4 text-indigo-600" />Subagent 温度</CardTitle>
                        <CardDescription>应用面覆盖值；留空时不注入 temperature。</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        <Input
                            value={subagentTemperature}
                            onChange={(event) => setSubagentTemperature(event.target.value)}
                            inputMode="decimal"
                            placeholder="留空 = 模型/供应商默认"
                        />
                        <p className="text-xs leading-5 text-slate-500">
                            仅影响 agent/reviewer 运行时角色；显式调用参数仍优先。
                        </p>
                        <Button size="sm" onClick={() => void handleSaveSubagentTemperature()} disabled={isSavingSubagentTemperature}>
                            {isSavingSubagentTemperature ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                            保存覆盖
                        </Button>
                    </CardContent>
                </Card>
            </div>

            <div className="grid gap-6 md:grid-cols-2 xl:grid-cols-3">
                {agents.map((agent) => {
                    const selectors = Array.isArray(agent.tools) ? agent.tools : [];
                    const toolMode = agent.tool_mode === "explicit" ? "explicit" : "contextual_auto";
                    const capabilitySnapshot = agent.capabilitySnapshot && typeof agent.capabilitySnapshot === "object" && !Array.isArray(agent.capabilitySnapshot)
                        ? agent.capabilitySnapshot
                        : {};
                    const agentClass = typeof capabilitySnapshot.agentClass === "string" ? capabilitySnapshot.agentClass : "";
                    const domainTags = Array.isArray(capabilitySnapshot.domainTags)
                        ? capabilitySnapshot.domainTags.filter((item): item is string => typeof item === "string").slice(0, 3)
                        : [];
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
                                                <CardDescription className="truncate">{agent.model?.name || agent.modelId || t("app.admin.dashboard.subagents.page.kb1fcabf9")}</CardDescription>
                                            </div>
                                        </div>
                                        <div className="flex gap-1">
                                            <Button type="button" variant="ghost" size="sm" onClick={() => { setEditingAgent(agent); setIsDialogOpen(true); }}>
                                                {t("app.admin.dashboard.subagents.page.k75997619")}
                                            </Button>
                                        <Button type="button" variant="ghost" size="sm" className="text-rose-600" onClick={() => void handleDelete(agent.id)}>
                                            <Trash2 className="h-4 w-4" />
                                        </Button>
                                    </div>
                                </div>
                                <div className="flex flex-wrap gap-2">
                                    <Badge variant={toolMode === "explicit" ? "default" : "secondary"}>{resolveToolModeLabel(toolMode)}</Badge>
                                    {agentClass ? <Badge variant="outline">{agentClass}</Badge> : null}
                                    {domainTags.map((tag) => <Badge key={tag} variant="outline">{tag}</Badge>)}
                                    {agent.createdBy === "supervisor" ? <Badge className="bg-indigo-600 hover:bg-indigo-600">{t("app.admin.dashboard.subagents.page.kcec0f2f4")}</Badge> : null}
                                    {agent.reflection_enabled ? <Badge variant="outline">{t("app.admin.dashboard.subagents.page.k1599cdff")} × {agent.max_reflections || 3}</Badge> : null}
                                </div>
                            </CardHeader>
                            <CardContent className="space-y-4">
                                <p className="min-h-[3rem] text-sm leading-6 text-slate-500">{agent.description || t("app.admin.dashboard.subagents.page.k70eaab39")}</p>
                                {toolMode === "explicit" ? (
                                    <div className="space-y-2">
                                        <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{t("app.admin.dashboard.subagents.page.k1dc6b253")}</div>
                                        {renderToolBadgeSummary(selectors)}
                                    </div>
                                ) : (
                                    <div className="space-y-2 rounded-2xl border border-slate-200 bg-slate-50/70 p-3 text-xs leading-5 text-slate-500">
                                        <div className="font-medium text-slate-900">{t("app.admin.dashboard.subagents.page.toolMode.contextualAuto")}</div>
                                        <div>{t("app.admin.dashboard.subagents.page.kf913a2e6")}</div>
                                    </div>
                                )}
                            </CardContent>
                        </Card>
                    );
                })}
                {agents.length === 0 ? (
                    <div className="col-span-full rounded-3xl border border-dashed border-slate-200 bg-slate-50/80 py-12 text-center text-sm text-slate-500">
                        {t("app.admin.dashboard.subagents.page.kc6380706")}
                    </div>
                ) : null}
            </div>

            <Card className="rounded-3xl border-slate-200 bg-white/95 shadow-sm">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-base">
                        <Cable className="h-4 w-4 text-emerald-600" />
                        {t("app.admin.dashboard.subagents.page.externalWorkers.title")}
                    </CardTitle>
                    <CardDescription>
                        {t("app.admin.dashboard.subagents.page.externalWorkers.description")} <code>config.json#supervisor.delegation.externalWorkers</code>.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <Textarea
                        value={externalWorkersJson}
                        onChange={(event) => setExternalWorkersJson(event.target.value)}
                        className="min-h-[220px] font-mono text-xs"
                        placeholder='[{"id":"coding-cli-worker","enabled":false}]'
                    />
                    <div className="flex items-center justify-between gap-3">
                        <p className="text-xs leading-5 text-slate-500">
                            {t("app.admin.dashboard.subagents.page.externalWorkers.hintPrefix")} <code>launchProfile.commandTemplate</code> {t("app.admin.dashboard.subagents.page.externalWorkers.hintMiddle")} <code>resultSchema.markers</code>.
                        </p>
                        <Button onClick={() => void handleSaveExternalWorkers()} disabled={isSavingExternalWorkers}>
                            {isSavingExternalWorkers ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                            {t("app.admin.dashboard.subagents.page.externalWorkers.saveButton")}
                        </Button>
                    </div>
                </CardContent>
            </Card>

            <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
                <DialogContent className="flex h-[min(92vh,960px)] max-w-4xl flex-col overflow-hidden p-0">
                    <DialogHeader className="shrink-0 border-b border-slate-200 px-6 py-5">
                        <DialogTitle>{editingAgent ? t("app.admin.dashboard.subagents.page.k74a55357") : t("app.admin.dashboard.subagents.page.k5ae562aa")}</DialogTitle>
                    </DialogHeader>
                    <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-6 pt-5">
                        <div className="space-y-6">
                        <div className="grid gap-4 md:grid-cols-2">
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.subagents.page.k6a80aac6")}</Label>
                                <Input value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} placeholder={t("app.admin.dashboard.subagents.page.kffd7236f")} />
                            </div>
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.subagents.page.kc4e7d695")}</Label>
                                <Input value={form.roleLabel} onChange={(event) => setForm((current) => ({ ...current, roleLabel: event.target.value }))} placeholder={t("app.admin.dashboard.subagents.page.k49570350")} />
                            </div>
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.subagents.page.k5888282d")}</Label>
                                <Input value={form.icon} onChange={(event) => setForm((current) => ({ ...current, icon: event.target.value }))} placeholder="🤖" />
                            </div>
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.subagents.page.kc2c1e310")}</Label>
                                <Input value={form.avatar} onChange={(event) => setForm((current) => ({ ...current, avatar: event.target.value }))} placeholder={t("app.admin.dashboard.subagents.page.kf714bed3")} />
                            </div>
                        </div>

                        <div className="space-y-2">
                            <Label>{t("app.admin.dashboard.subagents.page.k4218ea5a")}</Label>
                            <Textarea value={form.description} onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))} placeholder={t("app.admin.dashboard.subagents.page.kdd73d22c")} />
                        </div>

                        <div className="grid gap-4 md:grid-cols-2">
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.subagents.page.kca695f8f")}</Label>
                                <Select value={form.modelId} onValueChange={(value) => setForm((current) => ({ ...current, modelId: value }))}>
                                    <SelectTrigger>
                                        <SelectValue placeholder={t("app.admin.dashboard.subagents.page.k9e6fdf0a")} />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {models.map((model) => (
                                            <SelectItem key={model.id} value={model.id}>
                                                {model.id} {model.provider?.name ? `(${model.provider.name})` : ""}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.subagents.page.k963f9424")}</Label>
                                <Select value={form.toolMode} onValueChange={(value: "explicit" | "contextual_auto") => setForm((current) => ({ ...current, toolMode: value }))}>
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="contextual_auto">{t("app.admin.dashboard.subagents.page.toolMode.contextualAuto")}</SelectItem>
                                        <SelectItem value="explicit">{t("app.admin.dashboard.subagents.page.toolMode.explicit")}</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                        </div>

                        <div className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4 text-sm leading-6 text-slate-500">
                            {form.toolMode === "contextual_auto" ? (
                                <>
                                    <div className="font-medium text-slate-900">{t("app.admin.dashboard.subagents.page.k9431e8c4")}</div>
                                    <div>{t("app.admin.dashboard.subagents.page.k3b6c2a75")}</div>
                                </>
                            ) : (
                                <>
                                    <div className="font-medium text-slate-900">{t("app.admin.dashboard.subagents.page.kaaa3ff24")}</div>
                                    <div>{t("app.admin.dashboard.subagents.page.k502a06d7")}</div>
                                </>
                            )}
                        </div>

                        <div className="space-y-2">
                            <Label>capabilitySnapshot JSON</Label>
                            <Textarea
                                value={form.capabilitySnapshotJson}
                                onChange={(event) => setForm((current) => ({ ...current, capabilitySnapshotJson: event.target.value }))}
                                className="min-h-[180px] font-mono text-xs"
                                placeholder='{"agentClass":"executor","domainTags":["software_engineering"]}'
                            />
                            <p className="text-xs leading-5 text-slate-500">
                                Routing metadata for planner/subagent selection. This is separate from roleLabel, which remains presentation-only.
                            </p>
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
                                                    {t("app.admin.dashboard.subagents.page.k8cf0c430")}
                                                    <Badge variant="outline">{baselineToolNames.length}</Badge>
                                                </CardTitle>
                                                <CardDescription>
                                                    {t("app.admin.dashboard.subagents.page.k82df509a")}
                                                </CardDescription>
                                            </div>
                                            <ChevronDown className={`mt-1 h-4 w-4 shrink-0 text-slate-400 transition-transform ${toolPanels.baseline ? "rotate-180" : ""}`} />
                                        </button>
                                    </CardHeader>
                                    {toolPanels.baseline ? (
                                        <CardContent className="max-h-[148px] space-y-3 overflow-y-auto overscroll-contain pr-2">
                                            {baselineSystemTools.length > 0 ? (
                                                <div className="grid gap-2">
                                                    {baselineSystemTools.map((tool) => (
                                                        <div key={tool.name} className="rounded-2xl border border-slate-200 bg-slate-50/70 px-3 py-2">
                                                            <div className="font-mono text-[11px] font-medium text-slate-900">{tool.name}</div>
                                                            <div className="mt-1 text-xs leading-5 text-slate-500">
                                                                {tool.description || t("app.admin.dashboard.subagents.page.k86e9a787")}
                                                            </div>
                                                        </div>
                                                    ))}
                                                </div>
                                            ) : (
                                                <div className="text-xs text-slate-500">{t("app.admin.dashboard.subagents.page.kca19dcd0")}</div>
                                            )}
                                            <p className="text-xs leading-5 text-slate-500">
                                                {t("app.admin.dashboard.subagents.page.k76e62da1")}
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
                                                <CardTitle className="flex items-center gap-2 text-base"><Sparkles className="h-4 w-4 text-violet-600" />{t("app.admin.dashboard.subagents.page.ke431abc9")}<Badge variant="outline">{skills.length}</Badge></CardTitle>
                                                <CardDescription>{t("app.admin.dashboard.subagents.page.k62807e10")}</CardDescription>
                                            </div>
                                            <ChevronDown className={`mt-1 h-4 w-4 shrink-0 text-slate-400 transition-transform ${toolPanels.skills ? "rotate-180" : ""}`} />
                                        </button>
                                    </CardHeader>
                                    {toolPanels.skills ? (
                                    <CardContent className="max-h-[224px] space-y-3 overflow-y-auto overscroll-contain pr-2">
                                        {skills.length === 0 ? <div className="text-xs text-slate-500">{t("app.admin.dashboard.subagents.page.k2b7b1954")}</div> : null}
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
                                                <CardDescription>{t("app.admin.dashboard.subagents.page.k6490c720")}</CardDescription>
                                            </div>
                                            <ChevronDown className={`mt-1 h-4 w-4 shrink-0 text-slate-400 transition-transform ${toolPanels.mcp ? "rotate-180" : ""}`} />
                                        </button>
                                    </CardHeader>
                                    {toolPanels.mcp ? (
                                    <CardContent className="max-h-[224px] space-y-4 overflow-y-auto overscroll-contain pr-2">
                                        {Object.keys(groupedMcpTools).length === 0 ? <div className="text-xs text-slate-500">{t("app.admin.dashboard.subagents.page.k57c2bf93")}</div> : null}
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
                                                                <div className="text-xs leading-5 text-slate-500">{tool.description || t("app.admin.dashboard.subagents.page.k86e9a787")}</div>
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
                                                <CardDescription>{t("app.admin.dashboard.subagents.page.k5f711f45")}</CardDescription>
                                            </div>
                                            <ChevronDown className={`mt-1 h-4 w-4 shrink-0 text-slate-400 transition-transform ${toolPanels.plugin_host ? "rotate-180" : ""}`} />
                                        </button>
                                    </CardHeader>
                                    {toolPanels.plugin_host ? (
                                    <CardContent className="max-h-[224px] space-y-4 overflow-y-auto overscroll-contain pr-2">
                                        {bridgeError ? <div className="rounded-2xl border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-700">{bridgeError}</div> : null}
                                        {!bridgeError && Object.keys(groupedPluginHostTools).length === 0 ? <div className="text-xs text-slate-500">{t("app.admin.dashboard.subagents.page.kc9324cc5")}</div> : null}
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
                            <Label>{t("app.admin.dashboard.subagents.page.kc2dd0474")}</Label>
                            <Textarea
                                value={form.systemPrompt}
                                onChange={(event) => setForm((current) => ({ ...current, systemPrompt: event.target.value }))}
                                className="min-h-[180px] font-mono text-sm"
                                placeholder={t("app.admin.dashboard.subagents.page.ke7295552")}
                            />
                        </div>

                        <div className="space-y-4 rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                            <label className="flex items-center gap-3">
                                <Checkbox
                                    checked={form.reflectionEnabled}
                                    onCheckedChange={(checked) => setForm((current) => ({ ...current, reflectionEnabled: Boolean(checked) }))}
                                />
                                <div>
                                    <div className="text-sm font-medium text-slate-900">{t("app.admin.dashboard.subagents.page.kdaaa0859")}</div>
                                    <div className="text-xs text-slate-500">{t("app.admin.dashboard.subagents.page.k45e15cc8")}</div>
                                </div>
                            </label>
                            {form.reflectionEnabled ? (
                                <div className="space-y-2">
                                    <Label>{t("app.admin.dashboard.subagents.page.k372414e4")}</Label>
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
                            {t("app.admin.dashboard.subagents.page.k7171a69c")}
                        </Button>
                        </div>
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    );
}
