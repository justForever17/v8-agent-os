"use client";

/* eslint-disable @next/next/no-img-element */

import { useMemo, useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, CardFooter } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Crown, Save, Loader2, ChevronDown, ChevronRight, Check, Play, Upload, X, Lock, Wrench } from "lucide-react";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/use-toast";
import { Input } from "@/components/ui/input";
import { useLocale, useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";

interface AIModel {
    id: string;
    name: string;
    provider?: { name: string; };
    type?: string;
}

interface MCPTool {
    name: string;
    description: string;
    serverName?: string;
}

interface LockedTool {
    name: string;
    description?: string;
    reason?: string;
    runtimeKind?: string;
    runtimeLabel?: string;
}

const NATIVE_TOOL_SERVER_NAME = "系统原生能力";

async function readResponseError(response: Response, fallback: string) {
    const data = await response.json().catch(() => null);
    const detail =
        typeof data?.detail === "string"
            ? data.detail
            : typeof data?.error === "string"
                ? data.error
                : "";
    return detail || fallback;
}

export default function SupervisorPage() {
    const t = useT();
    const { locale } = useLocale();
    const [systemPrompt, setSystemPrompt] = useState("");
    const [selectedModelId, setSelectedModelId] = useState<string>("default");
    const [visionModelId, setVisionModelId] = useState<string>("__empty__");
    const [visionModelSource, setVisionModelSource] = useState<string | null>(null);
    const [selectedTools, setSelectedTools] = useState<string[]>([]);
    
    const [name, setName] = useState("智能主管");
    const [roleLabel, setRoleLabel] = useState("主理人");
    const [avatar, setAvatar] = useState("");
    
    const [models, setModels] = useState<AIModel[]>([]);
    const [mcpTools, setMcpTools] = useState<MCPTool[]>([]);
    const [lockedNativeTools, setLockedNativeTools] = useState<LockedTool[]>([]);
    const [runtimeManagedTools, setRuntimeManagedTools] = useState<LockedTool[]>([]);
    const [expandedServers, setExpandedServers] = useState<Record<string, boolean>>({});
    const [testingServers, setTestingServers] = useState<Record<string, boolean>>({});
    
    const [isLoading, setIsLoading] = useState(true);
    const [isSaving, setIsSaving] = useState(false);
    const [isUploading, setIsUploading] = useState(false);
    const [defaultModelId, setDefaultModelId] = useState<string | null>(null);
    const { toast } = useToast();

    const handleTestConnection = async (e: React.MouseEvent, serverName: string) => {
        e.stopPropagation();
        setTestingServers(prev => ({ ...prev, [serverName]: true }));
        try {
            const res = await fetch("/api/mcp/tools");
            if (res.ok) {
                const data = await res.json();
                const hasTools = (data.mcpTools as MCPTool[]).some(t => t.serverName === serverName || (serverName === "通用工具" && !t.serverName));
                if (hasTools) {
                    toast({ title: t("连接成功"), description: `${t("已成功连接到 MCP 服务器")}: ${t(serverName)}` });
                } else {
                     toast({ variant: "destructive", title: t("连接异常"), description: `${t("未能从服务器获取到工具列表，请检查配置。")} (${t(serverName)})` });
                }
            } else {
                throw new Error("API Error");
            }
        } catch(err) {
            console.error(err);
            toast({ variant: "destructive", title: t("连接失败"), description: t("测试连接出错") });
        } finally {
            setTestingServers(prev => ({ ...prev, [serverName]: false }));
        }
    };

    useEffect(() => {
        Promise.all([
            fetch("/api/supervisor"),
            fetch("/api/models"),
            fetch("/api/mcp/tools"),
            fetch("/api/settings/vision-model"),
            fetch("/api/settings/default-agent-model"),
        ])
        .then(async ([supRes, modRes, mcpRes, visionRes, defaultModelRes]) => {
            if (supRes.ok) {
                const data = await supRes.json();
                if (data.systemPrompt !== undefined) setSystemPrompt(data.systemPrompt);
                if (data.model_id) setSelectedModelId(data.model_id);
                else setSelectedModelId("default");
                if (data.allowed_tools) setSelectedTools(data.allowed_tools);
                else setSelectedTools([]);
                setLockedNativeTools(Array.isArray(data.locked_native_tools) ? data.locked_native_tools : []);
                setRuntimeManagedTools(Array.isArray(data.runtime_managed_tools) ? data.runtime_managed_tools : []);
                if (data.name) setName(data.name);
                if (data.roleLabel) setRoleLabel(data.roleLabel);
                if (data.avatar) setAvatar(data.avatar);
            }
            if (modRes.ok) setModels(await modRes.json());
            if (mcpRes.ok) {
                const data = await mcpRes.json();
                setMcpTools(data.mcpTools || []);
            }
            if (visionRes.ok) {
                const data = await visionRes.json();
                setVisionModelId(data.value || "__empty__");
                setVisionModelSource(typeof data.source === "string" ? data.source : null);
            }
            if (defaultModelRes.ok) {
                const data = await defaultModelRes.json();
                setDefaultModelId(typeof data.modelId === "string" && data.modelId ? data.modelId : null);
            }
            setIsLoading(false);
        })
        .catch((err) => {
            console.error("Failed to load data", err);
            setIsLoading(false);
        });
    }, []);

    const runtimeManagedToolNames = useMemo(() => {
        const names = new Set<string>();
        for (const item of runtimeManagedTools) {
            names.add(item.name);
        }
        return names;
    }, [runtimeManagedTools]);

    const editableMcpTools = useMemo(
        () =>
            mcpTools.filter(
                (tool) =>
                    !String(tool.serverName || "").includes(NATIVE_TOOL_SERVER_NAME) &&
                    !runtimeManagedToolNames.has(tool.name)
            ),
        [mcpTools, runtimeManagedToolNames]
    );

    const editableMcpToolNames = useMemo(() => new Set(editableMcpTools.map((tool) => tool.name)), [editableMcpTools]);
    const detachedSelectedTools = useMemo(
        () => selectedTools.filter((toolName) => !editableMcpToolNames.has(toolName)),
        [editableMcpToolNames, selectedTools]
    );
    const visionCapableModels = useMemo(
        () =>
            models.filter((model) => {
                const type = String(model.type || "").toUpperCase();
                return !type || ["TEXT", "MULTIMODAL", "CHAT", "LLM"].includes(type);
            }),
        [models]
    );

    const handleSave = async () => {
        setIsSaving(true);
        let supervisorSaved = false;
        try {
            const res = await fetch("/api/supervisor", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ 
                    systemPrompt,
                    model_id: (selectedModelId && selectedModelId !== "default") ? selectedModelId : null,
                    allowed_tools: selectedTools.length > 0 ? selectedTools : null,
                    name,
                    roleLabel,
                    avatar
                }),
            });
            if (!res.ok) {
                const description = await readResponseError(res, t("请稍后重试"));
                toast({
                    variant: "destructive",
                    title: t("保存失败"),
                    description,
                });
                return;
            }
            supervisorSaved = true;

            const visionRes = await fetch("/api/settings/vision-model", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    value: visionModelId !== "__empty__" ? visionModelId : null,
                }),
            });
            if (!visionRes.ok) {
                const description = await readResponseError(
                    visionRes,
                    t(lt("视觉媒体分析模型保存失败，请稍后重试。", "Failed to save the vision media model. Please try again.")),
                );
                toast({
                    variant: "destructive",
                    title: t(lt("主理人设置已部分保存", "Lead settings partially saved")),
                    description,
                });
                return;
            }
            const visionData = await visionRes.json().catch(() => null);
            setVisionModelSource(typeof visionData?.source === "string" ? visionData.source : visionModelSource);
            toast({
                title: t("主理人设置已保存"),
                description: t(lt("新的昵称、头像、默认模型和视觉媒体分析模型已经生效。", "The lead profile, default model, and vision media model are now updated.")),
            });
        } catch (error) {
            console.error("Failed to save supervisor prompt", error);
            toast({
                variant: "destructive",
                title: t(supervisorSaved ? lt("主理人设置已部分保存", "Lead settings partially saved") : "保存失败"),
                description: error instanceof Error ? error.message : t("请稍后重试"),
            });
        } finally {
            setIsSaving(false);
        }
    };

    if (isLoading) {
        return (
            <div className="flex h-[50vh] items-center justify-center">
                <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
            </div>
        );
    }

    return (
        <div className="p-8 space-y-8 max-w-4xl mx-auto">
            <div>
                <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
                    <Crown className="w-8 h-8 text-amber-500" />
                    {t("主理人")}
                </h1>
                <p className="text-muted-foreground mt-2">
                    {t("这里设置主理人的昵称、头像、主理人模型、runtime orchestration prompt 和可用工具。Prompt 真相源是 ~/.v8-agent-os/V8_AGENT_OS.md；显示资料来自 config.json#supervisor；默认回复模型独立绑定到 config.json#models.roles.default。")}
                </p>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle>{t("主理人设置")}</CardTitle>
                    <CardDescription>{t("改这里会影响主理人的显示资料、默认模型和执行方式。")}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-6 pt-6">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6 pb-6 border-b">
                        <div className="space-y-4">
                            <div className="space-y-2">
                                <Label>{t("昵称")}</Label>
                                <Input value={name} onChange={e => setName(e.target.value)} placeholder={t("如：智能主管")} />
                            </div>
                            <div className="space-y-2">
                                <Label>{t("身份标签")}</Label>
                                <Input value={roleLabel} onChange={e => setRoleLabel(e.target.value)} placeholder={t("如：主理人")} />
                            </div>
                        </div>
                        <div className="space-y-3">
                            <Label>{t("头像")}</Label>
                            <div className="flex items-center gap-4">
                                <div className="relative flex h-24 w-24 shrink-0 items-center justify-center overflow-hidden rounded-2xl border bg-muted">
                                    {avatar ? (
                                        <>
                                            <img src={avatar} alt="Avatar Preview" className="h-full w-full object-cover" />
                                            <button
                                                type="button"
                                                className="absolute right-2 top-2 rounded-full bg-black/60 p-1 text-white transition-colors hover:bg-black/75"
                                                onClick={() => setAvatar("")}
                                            >
                                                <X className="h-5 w-5" />
                                            </button>
                                        </>
                                    ) : (
                                        <Crown className="h-10 w-10 text-slate-400" />
                                    )}
                                </div>
                                <div className="flex-1 space-y-3">
                                    <div className="flex flex-wrap gap-2">
                                        <Input
                                            id="supervisor-avatar-upload"
                                            type="file"
                                            accept="image/*"
                                            className="hidden"
                                            onChange={async (event) => {
                                                const file = event.target.files?.[0];
                                                if (!file) return;
                                                setIsUploading(true);
                                                const formData = new FormData();
                                                formData.append("file", file);
                                                try {
                                                    const response = await fetch("/api/avatar-upload", { method: "POST", body: formData });
                                                    const data = await response.json().catch(() => ({}));
                                                    if (!response.ok || !data.url) {
                                                        throw new Error(data.error || "上传失败");
                                                    }
                                                    setAvatar(String(data.url));
                                                } catch (error) {
                                                    toast({
                                                        variant: "destructive",
                                                        title: t("头像上传失败"),
                                                        description: error instanceof Error ? error.message : t("请稍后重试"),
                                                    });
                                                } finally {
                                                    setIsUploading(false);
                                                    event.target.value = "";
                                                }
                                            }}
                                        />
                                        <Button
                                            type="button"
                                            variant="outline"
                                            size="sm"
                                            onClick={() => document.getElementById("supervisor-avatar-upload")?.click()}
                                            disabled={isUploading}
                                        >
                                            <Upload className="mr-2 h-4 w-4" />
                                            {isUploading ? t("上传中...") : t("上传图片")}
                                        </Button>
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            size="sm"
                                            onClick={() => {
                                                const url = window.prompt(locale === "en" ? "Enter avatar image URL:" : "请输入头像图片地址：", avatar || "");
                                                if (url !== null) {
                                                    setAvatar(url.trim());
                                                }
                                            }}
                                        >
                                            {t("输入 URL")}
                                        </Button>
                                        {avatar ? (
                                            <Button type="button" variant="ghost" size="sm" onClick={() => setAvatar("")}>
                                                {t("清空头像")}
                                            </Button>
                                        ) : null}
                                    </div>
                                    <div className="space-y-2">
                                        <Label>{t("头像地址")}</Label>
                                        <Input value={avatar} onChange={e => setAvatar(e.target.value)} placeholder={t("如：https://...")} />
                                    </div>
                                    <p className="text-xs text-muted-foreground">{t("支持 JPG、PNG、WEBP、GIF，系统会统一处理成 256x256 的静态图片。")}</p>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div className="space-y-2">
                        <Label>{t("默认模型")}</Label>
                        <Select value={selectedModelId} onValueChange={setSelectedModelId}>
                            <SelectTrigger>
                                <SelectValue placeholder={t("跟随当前会话使用的模型")} />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="default">{t("跟随当前会话使用的模型")}</SelectItem>
                                {models.map(model => (
                                    <SelectItem key={model.id} value={model.id}>
                                        {model.name} {model.provider?.name ? `(${model.provider.name})` : `(${model.id.split('-')[0] || t("未知供应商")})`}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <p className="text-xs text-muted-foreground">
                            {locale === "en"
                                ? "If not specified, the lead follows the current session model or the default chat model."
                                : "不单独指定时，会跟随当前会话或默认聊天模型。"}
                        </p>
                        {defaultModelId ? (
                            <p className="text-xs text-muted-foreground">
                                {locale === "en" ? `Current default reply model: ${defaultModelId}` : `当前默认回复模型：${defaultModelId}`}
                            </p>
                        ) : null}
                    </div>

                    <div id="vision-media-model" className="space-y-2">
                        <Label>{t(lt("视觉媒体分析模型", "Vision media model"))}</Label>
                        <Select value={visionModelId} onValueChange={setVisionModelId}>
                            <SelectTrigger>
                                <SelectValue placeholder={t(lt("跟随视觉角色默认模型", "Follow the default vision role model"))} />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="__empty__">{t(lt("跟随视觉角色默认模型", "Follow the default vision role model"))}</SelectItem>
                                {visionCapableModels.map((model) => (
                                    <SelectItem key={model.id} value={model.id}>
                                        {model.name} {model.provider?.name ? `(${model.provider.name})` : `(${model.id.split("-")[0] || t("未知供应商")})`}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <p className="text-xs text-muted-foreground">
                            {t(
                                lt(
                                    "vision_media_analyzer 默认使用的模型。主理人在处理图片、截图和媒体理解时会优先走这里。",
                                    "Default model used by vision_media_analyzer. The lead uses this path first for screenshots, images, and media understanding.",
                                ),
                            )}
                        </p>
                        {visionModelSource ? (
                            <p className="text-xs text-muted-foreground">
                                {locale === "en" ? `Source: ${visionModelSource}` : `配置来源：${visionModelSource}`}
                            </p>
                        ) : null}
                    </div>

                    <div className="space-y-2">
                        <Label>{t("系统提示词")}</Label>
                        <p className="text-xs text-muted-foreground mb-2">{t("这里决定主理人如何理解任务、组织步骤和调用能力。")}</p>
                        <Textarea 
                            value={systemPrompt} 
                            onChange={(e) => setSystemPrompt(e.target.value)}
                            className="font-mono text-sm min-h-[250px] leading-relaxed resize-y"
                            placeholder={t("请描述主理人的职责、做事风格和能力边界。")}
                        />
                    </div>

                    <div className="space-y-2">
                        <Label>{t("工具权限概览")}</Label>
                        <p className="text-xs text-muted-foreground">{t("这里会显示主理人当前能直接使用的工具范围，避免误开高风险底层能力。")}</p>
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                            <div className="rounded-md border bg-background p-3">
                                <div className="text-xs text-muted-foreground">{t("系统固定能力")}</div>
                                <div className="mt-2 text-sm font-medium text-foreground">
                                    {t("由系统统一调度的核心能力")}
                                </div>
                                <p className="mt-2 text-xs text-muted-foreground">{t("这部分能力由主理人与各 runtime 主链自动获得，不在这里单独勾选或修改。")}</p>
                            </div>
                            <div className="rounded-md border bg-background p-3">
                                <div className="text-xs text-muted-foreground">{t("系统默认工具")}</div>
                                <div className="mt-2 text-2xl font-semibold">{lockedNativeTools.length}</div>
                                <p className="mt-2 text-xs text-muted-foreground">{t("这些工具由系统统一提供，主理人始终可用。")}</p>
                            </div>
                            <div className="rounded-md border bg-background p-3">
                                <div className="text-xs text-muted-foreground">{t("Runtime 默认能力")}</div>
                                <div className="mt-2 text-sm font-medium text-foreground">
                                    {runtimeManagedTools.length > 0
                                        ? (locale === "en"
                                            ? `Managed by ${new Set(runtimeManagedTools.map((tool) => tool.runtimeLabel || tool.runtimeKind || "runtime")).size} runtime paths`
                                            : `当前由 ${new Set(runtimeManagedTools.map((tool) => tool.runtimeLabel || tool.runtimeKind || "runtime")).size} 条运行时主链统一接管`)
                                        : t("当前没有额外的运行时默认能力分组")}
                                </div>
                                <p className="mt-2 text-xs text-muted-foreground">{t("这部分能力由各 runtime 统一调度，不在这里单独勾选。")}</p>
                            </div>
                        </div>
                        <div className="rounded-md border border-dashed border-border/70 bg-muted/20 p-3">
                            <p className="text-sm font-medium text-foreground">{t("主理人的职责")}</p>
                            <p className="mt-2 text-xs text-muted-foreground">{t("主理人会负责理解任务、安排步骤，并在需要时调用桌面操作、自动化运行时任务、插件宿主渠道能力和其他专项能力。这里主要决定它的显示资料、默认模型和可直接使用的工具。")}</p>
                        </div>
                    </div>

                    <div className="space-y-2">
                        <Label>{t("系统默认可用的工具")}</Label>
                        <p className="text-xs text-muted-foreground">{t("这些工具由系统统一提供，主理人默认可用，这里只做展示。")}</p>
                        <div className="rounded-md border bg-muted/20 p-3">
                            {lockedNativeTools.length > 0 ? (
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                                    {lockedNativeTools.map((tool) => (
                                        <div key={tool.name} className="rounded border bg-background px-3 py-2">
                                            <div className="flex items-start gap-2">
                                                <Lock className="mt-0.5 h-4 w-4 shrink-0 text-sky-600" />
                                                <div className="min-w-0">
                                                    <div className="font-medium text-sm break-all">{tool.name}</div>
                                                    <p className="mt-1 text-[10px] text-muted-foreground line-clamp-2" title={tool.description}>
                                                        {tool.description || t("暂无说明。")}
                                                    </p>
                                                </div>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            ) : (
                                <p className="text-xs text-muted-foreground">{t("当前没有默认展示的系统工具。")}</p>
                            )}
                        </div>
                    </div>

                    <div className="space-y-2">
                        <Label>{t("Runtime 默认能力")}</Label>
                        <p className="text-xs text-muted-foreground">{t("这部分能力由各 runtime 统一调度，不在这里单独勾选。")}</p>
                        <div className="rounded-md border border-dashed border-border/70 bg-background/70 p-3 text-xs text-muted-foreground">
                            {runtimeManagedTools.length > 0 ? (
                                <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                                    {runtimeManagedTools.map((tool) => (
                                        <div key={tool.name} className="rounded border border-border/60 px-3 py-2">
                                            <div className="flex items-start gap-2">
                                                <Wrench className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                                                <div className="min-w-0">
                                                    <div className="font-medium text-foreground break-all">{tool.name}</div>
                                                    <div className="mt-1 text-[10px] leading-5">{tool.reason || tool.description || t("由 runtime 主链统一管理。")}</div>
                                                </div>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            ) : (
                                <p>{t("当前没有额外的 runtime 默认能力。")}</p>
                            )}
                        </div>
                    </div>

                    <div className="space-y-2">
                        <Label>{t("外部 / MCP 补充工具")}</Label>
                        <p className="text-xs text-muted-foreground">{t("这里只配置主理人可直接使用的补充 MCP 工具。系统原生工具和 runtime 默认能力不会在这里单独保存。")}</p>
                        {detachedSelectedTools.length > 0 ? (
                            <div className="rounded-md border border-violet-300/50 bg-violet-50 px-3 py-2 text-xs text-violet-900">
                                {locale === "en"
                                    ? `${detachedSelectedTools.length} saved MCP tools are currently offline: ${detachedSelectedTools.join(", ")}. They stay preserved until you remove them or the service reconnects.`
                                    : `当前配置里有 ${detachedSelectedTools.length} 个补充工具暂未在可连接列表中出现：${detachedSelectedTools.join(", ")}。保存时会继续保留它们，直到你主动取消或服务重新连上。`}
                            </div>
                        ) : null}
                        <div className="border rounded-md bg-muted/30 max-h-[400px] overflow-y-auto mt-2">
                            {editableMcpTools.length > 0 ? (
                                (() => {
                                    const grouped = editableMcpTools.reduce((acc, tool) => {
                                        const srv = tool.serverName || "通用工具";
                                        if (!acc[srv]) acc[srv] = [];
                                        acc[srv].push(tool);
                                        return acc;
                                    }, {} as Record<string, MCPTool[]>);

                                    return Object.entries(grouped).map(([server, tools]) => {
                                        const isExpanded = expandedServers[server] ?? true;
                                        const isAllSelected = tools.every(t => selectedTools.includes(t.name));
                                        const isSomeSelected = tools.some(t => selectedTools.includes(t.name));

                                        return (
                                            <div key={server} className="border-b last:border-b-0">
                                                <div className="flex items-center gap-2 p-2 bg-muted/50 hover:bg-muted/80 transition-colors">
                                                    <Button
                                                        type="button"
                                                        variant="ghost"
                                                        size="icon"
                                                        className="h-6 w-6 shrink-0"
                                                        onClick={() => setExpandedServers(prev => ({ ...prev, [server]: !isExpanded }))}
                                                    >
                                                        {isExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                                                    </Button>
                                                    <div className="flex-1 cursor-pointer font-medium text-sm flex items-center" onClick={() => setExpandedServers(prev => ({ ...prev, [server]: !isExpanded }))}>
                                                        <span>{t(server)}</span>
                                                        <Button 
                                                            variant="outline" 
                                                            size="sm" 
                                                            className="ml-4 gap-1 h-6 text-[10px] px-2"
                                                            disabled={testingServers[server]}
                                                            onClick={(e) => handleTestConnection(e, server)}
                                                        >
                                                            {testingServers[server] ? <Loader2 className="w-3 h-3 animate-spin"/> : <Play className="w-3 h-3" />}
                                                            {t("测试连接")}
                                                        </Button>
                                                    </div>
                                                    <span className="text-xs text-muted-foreground mr-2 font-normal">{tools.length} {t("工具")}</span>
                                                    <Button
                                                        type="button"
                                                        variant={isAllSelected ? "default" : (isSomeSelected ? "secondary" : "outline")}
                                                        size="sm"
                                                        className="h-6 text-[10px] px-2"
                                                        onClick={(e) => {
                                                            e.stopPropagation();
                                                            if (isAllSelected) {
                                                                setSelectedTools(prev => prev.filter(name => !tools.some(t => t.name === name)));
                                                            } else {
                                                                const toAdd = tools.filter(t => !selectedTools.includes(t.name)).map(t => t.name);
                                                                setSelectedTools(prev => [...prev, ...toAdd]);
                                                            }
                                                        }}
                                                    >
                                                        {isAllSelected ? (
                                                            <><Check className="w-3 h-3 mr-1" /> {t("已全选")}</>
                                                        ) : isSomeSelected ? t("取消全选") : t("全选")}
                                                    </Button>
                                                </div>
                                                
                                                {isExpanded && (
                                                    <div className="p-3 grid grid-cols-1 md:grid-cols-2 gap-3 bg-background/50">
                                                        {tools.map(tool => (
                                                            <div key={tool.name} className="flex items-start space-x-2">
                                                                <Checkbox
                                                                    id={`tool-${tool.name}`}
                                                                    name="tools"
                                                                    value={tool.name}
                                                                    checked={selectedTools.includes(tool.name)}
                                                                    onCheckedChange={(checked: boolean) => {
                                                                        if (checked) setSelectedTools(prev => [...prev, tool.name]);
                                                                        else setSelectedTools(prev => prev.filter(t => t !== tool.name));
                                                                    }}
                                                                    className="mt-1"
                                                                />
                                                                <div className="grid gap-1.5 leading-none flex-1">
                                                                    <Label htmlFor={`tool-${tool.name}`} className="font-medium cursor-pointer break-all text-sm">
                                                                        {tool.name}
                                                                    </Label>
                                                                    <p className="text-[10px] text-muted-foreground line-clamp-2" title={tool.description}>
                                                                        {tool.description || t("暂无说明。")}
                                                                    </p>
                                                                </div>
                                                            </div>
                                                        ))}
                                                    </div>
                                                )}
                                            </div>
                                        );
                                    });
                                })()
                            ) : (
                                <div className="p-4"><p className="text-xs text-muted-foreground col-span-full">{t("当前没有可单独配置的 MCP 补充工具。")}</p></div>
                            )}
                        </div>
                    </div>
                </CardContent>
                <CardFooter className="flex justify-end pt-4 border-t">
                    <Button onClick={handleSave} disabled={isSaving} size="lg">
                        {isSaving ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Save className="w-4 h-4 mr-2" />}
                        {t("保存配置")}
                    </Button>
                </CardFooter>
            </Card>
        </div>
    );
}
