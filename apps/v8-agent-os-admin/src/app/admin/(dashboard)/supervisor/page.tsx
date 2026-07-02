"use client";

/* eslint-disable @next/next/no-img-element */
import Link from "next/link";
import { useMemo, useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, CardFooter } from "@/components/ui/card";
import { ModelSelect } from "@/components/models/ModelSelect";
import { Textarea } from "@/components/ui/textarea";
import { ArrowLeft, Crown, Save, Loader2, ChevronDown, ChevronRight, Check, Play, Upload, X, Lock, Wrench } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/use-toast";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { useT } from "@/components/providers/LocaleProvider";
import { ir, tg, ti } from "@/i18n/admin-legacy";
import { useDebugMode } from "@/lib/useDebugMode";
import { AdminHoverInfo } from "@/components/admin-shell/AdminHoverInfo";
interface AIModel {
  id: string;
  modelRef?: string;
  providerId?: string;
  modelId?: string;
  name: string;
  provider?: {
    id?: string;
    name: string;
  };
  providerName?: string;
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
type PromptBudgetDiagnostic = {
  source?: string;
  estimatedTokens?: number;
  budgetTokens?: number;
  truncated?: boolean;
  saveRejected?: boolean;
  omittedReason?: string;
};
const NATIVE_TOOL_SERVER_NAME = ir("ka6155cb12a");
const SUPERVISOR_PROMPT_BUDGET_TOKENS = 10000;
const TEMPERATURE_PRESET = 0.7;
const MIN_CONFIG_TEMPERATURE = 0.05;
function estimatePromptTokens(text: string) {
  let cjk = 0;
  let otherVisible = 0;
  for (const char of String(text || "")) {
    const code = char.charCodeAt(0);
    if (code >= 0x4e00 && code <= 0x9fff || code >= 0x3400 && code <= 0x4dbf || code >= 0x3040 && code <= 0x30ff || code >= 0xac00 && code <= 0xd7af) {
      cjk += 1;
    } else if (!/\s/.test(char)) {
      otherVisible += 1;
    }
  }
  return cjk + Math.ceil(otherVisible / 4);
}
async function readResponseError(response: Response, fallback: string) {
  const data = await response.json().catch(() => null);
  const detail = typeof data?.detail === "string" ? data.detail : typeof data?.error === "string" ? data.error : "";
  return detail || fallback;
}
function parseOptionalTemperature(value: string) {
  const trimmed = String(value || "").trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed)) return null;
  if (parsed <= 0) return null;
  return Math.max(Math.min(parsed, 2), MIN_CONFIG_TEMPERATURE);
}
function formatDecimal(value: number) {
  return value.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
}
function temperatureSliderValue(value: string) {
  const parsed = Number(String(value || "").trim());
  if (!Number.isFinite(parsed)) return TEMPERATURE_PRESET;
  return Math.max(Math.min(parsed, 2), MIN_CONFIG_TEMPERATURE);
}
function temperatureStatusText(t: ReturnType<typeof useT>, value: string) {
  if (String(value || "").trim()) {
    return `${ti(t, "k7d84e1319b")}${formatDecimal(temperatureSliderValue(value))}`;
  }
  return `${ti(t, "k630e24cd54")}${formatDecimal(TEMPERATURE_PRESET)}${ti(t, "kc10e7c65e4")}`;
}
function temperatureDefaultText(t: ReturnType<typeof useT>) {
  return ti(t, "k833e316858");
}
export default function SupervisorPage() {
  const t = useT();
  const [debugMode] = useDebugMode();
  const [systemPrompt, setSystemPrompt] = useState("");
  const [selectedModelId, setSelectedModelId] = useState<string>("default");
  const [visionModelId, setVisionModelId] = useState<string>("__empty__");
  const [visionModelSource, setVisionModelSource] = useState<string | null>(null);
  const [selectedTools, setSelectedTools] = useState<string[]>([]);
  const [name, setName] = useState(tg(t, "8537cbcf"));
  const [roleLabel, setRoleLabel] = useState(t("components.models.ModelCardV2.kf45c6152"));
  const [avatar, setAvatar] = useState("");
  const [supervisorTemperature, setSupervisorTemperature] = useState("");
  const [subagentTemperature, setSubagentTemperature] = useState("");
  const [models, setModels] = useState<AIModel[]>([]);
  const [mcpTools, setMcpTools] = useState<MCPTool[]>([]);
  const [lockedNativeTools, setLockedNativeTools] = useState<LockedTool[]>([]);
  const [runtimeManagedTools, setRuntimeManagedTools] = useState<LockedTool[]>([]);
  const [promptBudgetDiagnostics, setPromptBudgetDiagnostics] = useState<PromptBudgetDiagnostic[]>([]);
  const [expandedServers, setExpandedServers] = useState<Record<string, boolean>>({});
  const [testingServers, setTestingServers] = useState<Record<string, boolean>>({});
  const [showManualMcpTools, setShowManualMcpTools] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [defaultModelId, setDefaultModelId] = useState<string | null>(null);
  const {
    toast
  } = useToast();
  const handleTestConnection = async (e: React.MouseEvent, serverName: string) => {
    e.stopPropagation();
    setTestingServers(prev => ({
      ...prev,
      [serverName]: true
    }));
    try {
      const res = await fetch("/api/mcp/tools");
      if (res.ok) {
        const data = await res.json();
        const hasTools = (data.mcpTools as MCPTool[]).some(tool => tool.serverName === serverName || serverName === tg(t, "c740945b") && !tool.serverName);
        if (hasTools) {
          toast({
            title: t("app.admin.dashboard.supervisor.page.k40bd808e"),
            description: `${t("app.admin.dashboard.supervisor.page.k99cb7895")}: ${serverName}`
          });
        } else {
          toast({
            variant: "destructive",
            title: t("app.admin.dashboard.supervisor.page.k5797988b"),
            description: `${t("app.admin.dashboard.supervisor.page.k15cf84b7")} (${serverName})`
          });
        }
      } else {
        throw new Error("API Error");
      }
    } catch (err) {
      console.error(err);
      toast({
        variant: "destructive",
        title: t("app.admin.dashboard.supervisor.page.k4c8b33a9"),
        description: t("app.admin.dashboard.supervisor.page.k91724fb2")
      });
    } finally {
      setTestingServers(prev => ({
        ...prev,
        [serverName]: false
      }));
    }
  };
  useEffect(() => {
    Promise.all([fetch("/api/supervisor"), fetch("/api/models"), fetch("/api/mcp/tools"), fetch("/api/settings/vision-model"), fetch("/api/settings/default-agent-model")]).then(async ([supRes, modRes, mcpRes, visionRes, defaultModelRes]) => {
      if (supRes.ok) {
        const data = await supRes.json();
        if (data.systemPrompt !== undefined) setSystemPrompt(data.systemPrompt);
        setPromptBudgetDiagnostics(Array.isArray(data.prompt_budget_diagnostics) ? data.prompt_budget_diagnostics : []);
        if (data.model_id) setSelectedModelId(data.model_id);else setSelectedModelId("default");
        if (data.allowed_tools) setSelectedTools(data.allowed_tools);else setSelectedTools([]);
        setLockedNativeTools(Array.isArray(data.locked_native_tools) ? data.locked_native_tools : []);
        setRuntimeManagedTools(Array.isArray(data.runtime_managed_tools) ? data.runtime_managed_tools : []);
        if (data.name) setName(data.name);
        if (data.roleLabel) setRoleLabel(data.roleLabel);
        if (data.avatar) setAvatar(data.avatar);
        setSupervisorTemperature(data.supervisor_temperature === null || data.supervisor_temperature === undefined ? "" : String(data.supervisor_temperature));
        setSubagentTemperature(data.subagent_temperature === null || data.subagent_temperature === undefined ? "" : String(data.subagent_temperature));
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
    }).catch(err => {
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
  const editableMcpTools = useMemo(() => mcpTools.filter(tool => !String(tool.serverName || "").includes(NATIVE_TOOL_SERVER_NAME) && !runtimeManagedToolNames.has(tool.name)), [mcpTools, runtimeManagedToolNames]);
  const editableMcpToolNames = useMemo(() => new Set(editableMcpTools.map(tool => tool.name)), [editableMcpTools]);
  const detachedSelectedTools = useMemo(() => selectedTools.filter(toolName => !editableMcpToolNames.has(toolName)), [editableMcpToolNames, selectedTools]);
  const promptEstimatedTokens = useMemo(() => estimatePromptTokens(systemPrompt), [systemPrompt]);
  const promptBudgetOverLimit = promptEstimatedTokens > SUPERVISOR_PROMPT_BUDGET_TOKENS;
  const promptBudgetNearLimit = promptEstimatedTokens > SUPERVISOR_PROMPT_BUDGET_TOKENS * 0.85;
  const runtimePromptTruncated = promptBudgetDiagnostics.some(item => item?.truncated);
  const promptBudgetParams = useMemo(() => ({
    estimated: promptEstimatedTokens,
    budget: SUPERVISOR_PROMPT_BUDGET_TOKENS
  }), [promptEstimatedTokens]);
  const visionCapableModels = useMemo(() => models.filter(model => {
    const type = String(model.type || "").toUpperCase();
    return !type || ["TEXT", "MULTIMODAL", "CHAT", "LLM"].includes(type);
  }), [models]);
  const handleSave = async () => {
    if (promptBudgetOverLimit) {
      toast({
        variant: "destructive",
        title: t("app.admin.dashboard.supervisor.page.promptBudget.overTitle"),
        description: t("app.admin.dashboard.supervisor.page.promptBudget.overDescription", promptBudgetParams)
      });
      return;
    }
    setIsSaving(true);
    let supervisorSaved = false;
    try {
      const res = await fetch("/api/supervisor", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          systemPrompt,
          model_id: selectedModelId && selectedModelId !== "default" ? selectedModelId : null,
          allowed_tools: selectedTools.length > 0 ? selectedTools : null,
          name,
          roleLabel,
          avatar,
          supervisor_temperature: parseOptionalTemperature(supervisorTemperature),
          subagent_temperature: parseOptionalTemperature(subagentTemperature)
        })
      });
      if (!res.ok) {
        const description = await readResponseError(res, t("app.admin.dashboard.supervisor.page.k2eee8863"));
        toast({
          variant: "destructive",
          title: t("app.admin.dashboard.supervisor.page.k12769ce1"),
          description
        });
        return;
      }
      supervisorSaved = true;
      const visionRes = await fetch("/api/settings/vision-model", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          value: visionModelId !== "__empty__" ? visionModelId : null
        })
      });
      if (!visionRes.ok) {
        const description = await readResponseError(visionRes, t("app.admin.dashboard.supervisor.page.k97d91670"));
        toast({
          variant: "destructive",
          title: t("app.admin.dashboard.supervisor.page.k23038732"),
          description
        });
        return;
      }
      const visionData = await visionRes.json().catch(() => null);
      setVisionModelSource(typeof visionData?.source === "string" ? visionData.source : visionModelSource);
      toast({
        title: t("app.admin.dashboard.supervisor.page.k86a2d3ea"),
        description: t("app.admin.dashboard.supervisor.page.k7b317041")
      });
      const savedData = await fetch("/api/supervisor").then(response => response.json()).catch(() => null);
      if (savedData && Array.isArray(savedData.prompt_budget_diagnostics)) {
        setPromptBudgetDiagnostics(savedData.prompt_budget_diagnostics);
      }
    } catch (error) {
      console.error("Failed to save supervisor prompt", error);
      toast({
        variant: "destructive",
        title: t(supervisorSaved ? "app.admin.dashboard.supervisor.page.k23038732" : "app.admin.dashboard.supervisor.page.k12769ce1"),
        description: error instanceof Error ? error.message : t("app.admin.dashboard.supervisor.page.k2eee8863")
      });
    } finally {
      setIsSaving(false);
    }
  };
  if (isLoading) {
    return <div className="flex h-[50vh] items-center justify-center">
                <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
            </div>;
  }
  return <div className="w-full space-y-8 p-6 lg:p-8">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div className="flex items-start gap-4">
                    <div>
                        <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
                            <Crown className="w-8 h-8 text-amber-500" />
                            {t("app.admin.dashboard.supervisor.page.kf45c6152")}
                        </h1>
                        <p className="text-muted-foreground mt-2 flex items-center gap-1.5 flex-wrap">
                            {t("app.admin.dashboard.supervisor.page.k48284369")}
                            {debugMode && (
                                <AdminHoverInfo content="配置源：~/.v8-agent-os/V8_AGENT_OS.md (Prompt)、config.json#supervisor (资料)、config.json#models.roles.default (模型)">
                                    <span className="cursor-help rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500 hover:bg-slate-200">❓ 详细路径</span>
                                </AdminHoverInfo>
                            )}
                        </p>
                    </div>
                </div>
                <Button onClick={handleSave} disabled={isSaving || promptBudgetOverLimit} size="lg" className="shrink-0">
                    {isSaving ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Save className="w-4 h-4 mr-2" />}
                    {t("app.admin.dashboard.supervisor.page.kaf9b5430")}
                </Button>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-[3fr_2fr] gap-6 items-start">
                {/* 左侧栏：核心配置与提示词策略 */}
                <div className="space-y-6">
                    {/* 卡片 1：基本设定与主模型参数 */}
                    <Card>
                        <CardHeader>
                            <CardTitle>{t("app.admin.dashboard.supervisor.page.k74e8cae6")}</CardTitle>
                            <CardDescription>{t("app.admin.dashboard.supervisor.page.k2a6c03f1")}</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-6">
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 pb-6 border-b">
                                <div className="space-y-4">
                                    <div className="space-y-2">
                                        <Label>{t("app.admin.dashboard.supervisor.page.kb6f6dc96")}</Label>
                                        <Input value={name} onChange={e => setName(e.target.value)} placeholder={t("app.admin.dashboard.supervisor.page.k1fa30a0f")} />
                                    </div>
                                    <div className="space-y-2">
                                        <Label>{t("app.admin.dashboard.supervisor.page.k38bbe3a5")}</Label>
                                        <Input value={roleLabel} onChange={e => setRoleLabel(e.target.value)} placeholder={t("app.admin.dashboard.supervisor.page.kc76b7a64")} />
                                    </div>
                                </div>
                                <div className="space-y-3">
                                    <Label>{t("app.admin.dashboard.supervisor.page.k19d90be6")}</Label>
                                    <div className="flex items-center gap-4">
                                        <div className="relative flex h-24 w-24 shrink-0 items-center justify-center overflow-hidden rounded-2xl border bg-muted">
                                            {avatar ? <>
                                                    <img src={avatar} alt="Avatar Preview" className="h-full w-full object-cover" />
                                                    <button type="button" className="absolute right-2 top-2 rounded-full bg-black/60 p-1 text-white transition-colors hover:bg-black/75" onClick={() => setAvatar("")}>
                                                        <X className="h-5 w-5" />
                                                    </button>
                                                </> : <Crown className="h-10 w-10 text-slate-400" />}
                                        </div>
                                        <div className="flex-1 space-y-3">
                                            <div className="flex flex-wrap gap-2">
                                                <Input id="supervisor-avatar-upload" type="file" accept="image/*" className="hidden" onChange={async event => {
                                                    const file = event.target.files?.[0];
                                                    if (!file) return;
                                                    setIsUploading(true);
                                                    const formData = new FormData();
                                                    formData.append("file", file);
                                                    try {
                                                        const response = await fetch("/api/avatar-upload", {
                                                            method: "POST",
                                                            body: formData
                                                        });
                                                        const data = await response.json().catch(() => ({}));
                                                        if (!response.ok || !data.url) {
                                                            throw new Error(data.error || t("app.admin.dashboard.extensions.page.k0dc966ec"));
                                                        }
                                                        setAvatar(String(data.url));
                                                    } catch (error) {
                                                        toast({
                                                            variant: "destructive",
                                                            title: t("app.admin.dashboard.supervisor.page.k6faec809"),
                                                            description: error instanceof Error ? error.message : t("app.admin.dashboard.supervisor.page.k2eee8863")
                                                        });
                                                    } finally {
                                                        setIsUploading(false);
                                                        event.target.value = "";
                                                    }
                                                }} />
                                                <Button type="button" variant="outline" size="sm" onClick={() => document.getElementById("supervisor-avatar-upload")?.click()} disabled={isUploading}>
                                                    <Upload className="mr-2 h-4 w-4" />
                                                    {isUploading ? t("app.admin.dashboard.supervisor.page.k94dfe70e") : t("app.admin.dashboard.supervisor.page.k819aecae")}
                                                </Button>
                                                <Button type="button" variant="ghost" size="sm" onClick={() => {
                                                    const url = window.prompt(tg(t, "67cc1ee4"), avatar || "");
                                                    if (url !== null) {
                                                        setAvatar(url.trim());
                                                    }
                                                }}>
                                                    {t("app.admin.dashboard.supervisor.page.kcb7b1896")}
                                                </Button>
                                                {avatar ? <Button type="button" variant="ghost" size="sm" onClick={() => setAvatar("")}>
                                                        {t("app.admin.dashboard.supervisor.page.kffee2f55")}
                                                    </Button> : null}
                                            </div>
                                            <div className="space-y-2">
                                                <Label>{t("app.admin.dashboard.supervisor.page.kc933bd04")}</Label>
                                                <Input value={avatar} onChange={e => setAvatar(e.target.value)} placeholder={t("app.admin.dashboard.supervisor.page.k19e35ac4")} />
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>

                            <div className="space-y-2 pt-2">
                                <Label>{t("app.admin.dashboard.supervisor.page.kba259bc3")}</Label>
                                <ModelSelect models={models} value={selectedModelId} emptyValue="default" emptyLabel={t("app.admin.dashboard.supervisor.page.k534ef300")} emptyOutputValue="default" placeholder={t("app.admin.dashboard.supervisor.page.k534ef300")} onValueChange={setSelectedModelId} />

                                {defaultModelId ? <p className="text-xs text-muted-foreground">
                                        {tg(t, "4c85d452", {
                                            value1: defaultModelId
                                        })}
                                    </p> : null}
                            </div>

                            <div className="grid gap-4 rounded-2xl border border-dashed border-slate-200 bg-slate-50/70 p-4 md:grid-cols-2">
                                <div className="space-y-3">
                                    <div className="flex items-center justify-between gap-3">
                                        <AdminHoverInfo
                                            content={
                                                <span className="text-xs font-normal leading-relaxed text-slate-200">
                                                    {tg(t, "e3097bad", {
                                                        value1: temperatureDefaultText(t)
                                                    })}
                                                </span>
                                            }
                                        >
                                            <Label className="cursor-help">{tg(t, "bd753fce")}</Label>
                                        </AdminHoverInfo>
                                        <span className="text-xs font-medium text-muted-foreground">
                                            {temperatureStatusText(t, supervisorTemperature)}
                                        </span>
                                    </div>
                                    <Slider value={[temperatureSliderValue(supervisorTemperature)]} min={MIN_CONFIG_TEMPERATURE} max={2} step={0.05} onValueChange={([value]) => setSupervisorTemperature(formatDecimal(value))} />

                                    <Button type="button" variant="outline" size="sm" onClick={() => setSupervisorTemperature("")}>
                                        {tg(t, "89699da9")}
                                    </Button>
                                </div>
                                <div className="space-y-3">
                                    <div className="flex items-center justify-between gap-3">
                                        <AdminHoverInfo
                                            content={
                                                <span className="text-xs font-normal leading-relaxed text-slate-200">
                                                    {tg(t, "3949675c")}
                                                </span>
                                            }
                                        >
                                            <Label className="cursor-help">{tg(t, "4cc6e647")}</Label>
                                        </AdminHoverInfo>
                                        <span className="text-xs font-medium text-muted-foreground">
                                            {temperatureStatusText(t, subagentTemperature)}
                                        </span>
                                    </div>
                                    <Slider value={[temperatureSliderValue(subagentTemperature)]} min={MIN_CONFIG_TEMPERATURE} max={2} step={0.05} onValueChange={([value]) => setSubagentTemperature(formatDecimal(value))} />

                                    <Button type="button" variant="outline" size="sm" onClick={() => setSubagentTemperature("")}>
                                        {tg(t, "89699da9")}
                                    </Button>
                                </div>
                            </div>
                        </CardContent>
                    </Card>

                    {/* 卡片 2：全局系统提示词 (System Prompt) */}
                    <Card>
                        <CardHeader>
                            <CardTitle>{t("app.admin.dashboard.supervisor.page.kc2dd0474")}</CardTitle>
                            <CardDescription>{t("app.admin.dashboard.supervisor.page.kd096e672")}</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className={`rounded-md border px-3 py-2 text-xs ${promptBudgetOverLimit ? "border-rose-300 bg-rose-50 text-rose-700" : promptBudgetNearLimit || runtimePromptTruncated ? "border-amber-300 bg-amber-50 text-amber-800" : "border-emerald-200 bg-emerald-50 text-emerald-700"}`}>
                                {promptBudgetOverLimit ? t("app.admin.dashboard.supervisor.page.promptBudget.overStatus", promptBudgetParams) : runtimePromptTruncated ? t("app.admin.dashboard.supervisor.page.promptBudget.runtimeTruncated") : promptBudgetNearLimit ? t("app.admin.dashboard.supervisor.page.promptBudget.nearStatus", promptBudgetParams) : t("app.admin.dashboard.supervisor.page.promptBudget.okStatus", promptBudgetParams)}
                            </div>
                            <Textarea value={systemPrompt} onChange={e => setSystemPrompt(e.target.value)} className="font-mono text-sm min-h-[300px] leading-relaxed resize-y" placeholder={t("app.admin.dashboard.supervisor.page.k59c88769")} />
                        </CardContent>
                    </Card>
                </div>

                {/* 右侧栏：辅助模型与工具箱授权 */}
                <div className="space-y-6">
                    {/* 卡片 3：视觉模型绑定 */}
                    <Card>
                        <CardHeader>
                            <CardTitle>{t("app.admin.dashboard.supervisor.page.kf558439c")}</CardTitle>
                            <CardDescription>{t("app.admin.dashboard.supervisor.page.k948f683d")}</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-3">
                            <ModelSelect models={visionCapableModels} value={visionModelId || "__empty__"} emptyLabel={t("app.admin.dashboard.supervisor.page.k3930f0e4")} placeholder={t("app.admin.dashboard.supervisor.page.k3930f0e4")} onValueChange={setVisionModelId} />

                            {visionModelSource ? <p className="text-xs text-muted-foreground">
                                    {tg(t, "83b1a316", {
                                        value1: visionModelSource
                                    })}
                                </p> : null}
                        </CardContent>
                    </Card>

                    {/* 卡片 4：工具箱授权与 MCP 服务 */}
                    <Card>
                        <CardHeader>
                            <CardTitle>{t("app.admin.dashboard.supervisor.page.k4da74fc8")}</CardTitle>
                            <CardDescription>{t("app.admin.dashboard.supervisor.page.k0201cb86")}</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-6">
                            {/* 核心工具统计 */}
                            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                                <div className="rounded-md border bg-background p-3">
                                    <div className="text-xs text-muted-foreground">{t("app.admin.dashboard.supervisor.page.k0638c7d8")}</div>
                                    <div className="mt-2 text-sm font-medium text-foreground">
                                        {t("app.admin.dashboard.supervisor.page.k187de0c5")}
                                    </div>
                                    <p className="mt-2 text-xs text-muted-foreground">{t("app.admin.dashboard.supervisor.page.k4dc4cf4d")}</p>
                                </div>
                                <div className="rounded-md border bg-background p-3">
                                    <div className="text-xs text-muted-foreground">{t("app.admin.dashboard.supervisor.page.kcfd39f1a")}</div>
                                    <div className="mt-2 text-2xl font-semibold">{lockedNativeTools.length}</div>
                                    <p className="mt-2 text-xs text-muted-foreground">{t("app.admin.dashboard.supervisor.page.ked4287a1")}</p>
                                </div>
                                <div className="rounded-md border bg-background p-3">
                                    <div className="text-xs text-muted-foreground">{t("app.admin.dashboard.supervisor.page.k988cfeb5")}</div>
                                    <div className="mt-2 text-sm font-medium text-foreground">
                                        {runtimeManagedTools.length > 0 ? tg(t, "372bd827", {
                                            value1: new Set(runtimeManagedTools.map(tool => tool.runtimeLabel || tool.runtimeKind || "runtime")).size
                                        }) : t("app.admin.dashboard.supervisor.page.k3a8f88de")}
                                    </div>
                                    <p className="mt-2 text-xs text-muted-foreground">{t("app.admin.dashboard.supervisor.page.kf48a48f7")}</p>
                                </div>
                            </div>

                            <div className="rounded-md border border-dashed border-border/70 bg-muted/20 p-3">
                                <p className="text-sm font-medium text-foreground">{t("app.admin.dashboard.supervisor.page.k7d574fe0")}</p>
                                <p className="mt-2 text-xs text-muted-foreground">{t("app.admin.dashboard.supervisor.page.ka8658654")}</p>
                            </div>

                            {/* 锁定工具 */}
                            <div className="space-y-2">
                                <Label className="text-xs text-muted-foreground font-semibold uppercase tracking-wider">{t("app.admin.dashboard.supervisor.page.k6e509c9a")}</Label>
                                <div className="rounded-md border bg-muted/20 p-3 max-h-[280px] overflow-y-auto scrollbar-thin">
                                    {lockedNativeTools.length > 0 ? <div className="grid grid-cols-1 gap-3">
                                            {lockedNativeTools.map(tool => <div key={tool.name} className="rounded border bg-background px-3 py-2">
                                                    <div className="flex items-start gap-2">
                                                        <Lock className="mt-0.5 h-4 w-4 shrink-0 text-sky-600" />
                                                        <div className="min-w-0">
                                                            <div className="font-medium text-sm break-all">{tool.name}</div>
                                                            <p className="mt-1 text-[10px] text-muted-foreground line-clamp-2" title={tool.description}>
                                                                {tool.description || t("app.admin.dashboard.supervisor.page.k86e9a787")}
                                                            </p>
                                                        </div>
                                                    </div>
                                                </div>)}
                                        </div> : <p className="text-xs text-muted-foreground">{t("app.admin.dashboard.supervisor.page.kca19dcd0")}</p>}
                                </div>
                            </div>

                            {/* 受管工具 */}
                            <div className="space-y-2">
                                <Label className="text-xs text-muted-foreground font-semibold uppercase tracking-wider">{t("app.admin.dashboard.supervisor.page.k988cfeb5")}</Label>
                                <div className="rounded-md border border-dashed border-border/70 bg-background/70 p-3 text-xs text-muted-foreground max-h-[280px] overflow-y-auto scrollbar-thin">
                                    {runtimeManagedTools.length > 0 ? <div className="grid grid-cols-1 gap-2">
                                            {runtimeManagedTools.map(tool => <div key={tool.name} className="rounded border border-border/60 px-3 py-2 bg-background">
                                                    <div className="flex items-start gap-2">
                                                        <Wrench className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                                                        <div className="min-w-0">
                                                            <div className="font-medium text-foreground break-all text-sm">{tool.name}</div>
                                                            <div className="mt-1 text-[10px] leading-5">{tool.reason || tool.description || t("app.admin.dashboard.supervisor.page.k4ac23e30")}</div>
                                                        </div>
                                                    </div>
                                                </div>)}
                                        </div> : <p>{t("app.admin.dashboard.supervisor.page.kc1580290")}</p>}
                                </div>
                            </div>

                            {/* MCP 工具授权 */}
                            <div className="space-y-2">
                                <Label className="text-xs text-muted-foreground font-semibold uppercase tracking-wider">{t("app.admin.dashboard.supervisor.page.kd6a7fff1")}</Label>
                                {detachedSelectedTools.length > 0 ? <div className="rounded-md border border-violet-300/50 bg-violet-50 px-3 py-2 text-xs text-violet-900 mb-2">
                                        {tg(t, "6322f71f", {
                                            value1: detachedSelectedTools.length,
                                            value2: detachedSelectedTools.join(", ")
                                        })}
                                    </div> : null}
                                <div className="rounded-md border border-dashed border-border/70 bg-muted/20 p-3">
                                    <div className="flex flex-wrap items-center justify-between gap-3">
                                        <div className="text-xs leading-5 text-muted-foreground">
                                            {t("app.admin.dashboard.supervisor.page.manualMcpMode.description")}
                                        </div>
                                        <Button type="button" variant="outline" size="sm" onClick={() => setShowManualMcpTools(current => !current)}>
                                            {showManualMcpTools ? <ChevronDown className="mr-2 h-4 w-4" /> : <ChevronRight className="mr-2 h-4 w-4" />}
                                            {showManualMcpTools ? t("app.admin.dashboard.supervisor.page.manualMcpMode.hideButton") : t("app.admin.dashboard.supervisor.page.manualMcpMode.showButton")}
                                        </Button>
                                    </div>
                                </div>
                                {showManualMcpTools ? <div className="border rounded-md bg-muted/30 max-h-[300px] overflow-y-auto mt-2">
                                    {editableMcpTools.length > 0 ? (() => {
                                        const grouped = editableMcpTools.reduce((acc, tool) => {
                                            const srv = tool.serverName || tg(t, "c740945b");
                                            if (!acc[srv]) acc[srv] = [];
                                            acc[srv].push(tool);
                                            return acc;
                                        }, {} as Record<string, MCPTool[]>);
                                        return Object.entries(grouped).map(([server, tools]) => {
                                            const isExpanded = expandedServers[server] ?? true;
                                            const isAllSelected = tools.every(t => selectedTools.includes(t.name));
                                            const isSomeSelected = tools.some(t => selectedTools.includes(t.name));
                                            return <div key={server} className="border-b last:border-b-0">
                                                    <div className="flex items-center gap-2 p-2 bg-muted/50 hover:bg-muted/80 transition-colors">
                                                        <Button type="button" variant="ghost" size="icon" className="h-6 w-6 shrink-0" onClick={() => setExpandedServers(prev => ({
                                                            ...prev,
                                                            [server]: !isExpanded
                                                        }))}>
                                                            {isExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                                                        </Button>
                                                        <div className="flex-1 cursor-pointer font-medium text-sm flex items-center" onClick={() => setExpandedServers(prev => ({
                                                            ...prev,
                                                            [server]: !isExpanded
                                                        }))}>
                                                            <span>{server}</span>
                                                            <Button variant="outline" size="sm" className="ml-4 gap-1 h-6 text-[10px] px-2" disabled={testingServers[server]} onClick={e => handleTestConnection(e, server)}>
                                                                {testingServers[server] ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
                                                                {t("app.admin.dashboard.supervisor.page.k4b0ac62c")}
                                                            </Button>
                                                        </div>
                                                        <span className="text-xs text-muted-foreground mr-2 font-normal">{tools.length} {t("app.admin.dashboard.supervisor.page.kf2e24950")}</span>
                                                        <Button type="button" variant={isAllSelected ? "default" : isSomeSelected ? "secondary" : "outline"} size="sm" className="h-6 text-[10px] px-2" onClick={e => {
                                                            e.stopPropagation();
                                                            if (isAllSelected) {
                                                                setSelectedTools(prev => prev.filter(name => !tools.some(t => t.name === name)));
                                                            } else {
                                                                const toAdd = tools.filter(t => !selectedTools.includes(t.name)).map(t => t.name);
                                                                setSelectedTools(prev => [...prev, ...toAdd]);
                                                            }
                                                        }}>
                                                            {isAllSelected ? <><Check className="w-3 h-3 mr-1" /> {t("app.admin.dashboard.supervisor.page.kaf4bbc27")}</> : isSomeSelected ? t("app.admin.dashboard.supervisor.page.kd1299571") : t("app.admin.dashboard.supervisor.page.k02d0d287")}
                                                        </Button>
                                                    </div>

                                                    {isExpanded && <div className="p-3 grid grid-cols-1 gap-3 bg-background/50">
                                                            {tools.map(tool => <div key={tool.name} className="flex items-start space-x-2">
                                                                    <Checkbox id={`tool-${tool.name}`} name="tools" value={tool.name} checked={selectedTools.includes(tool.name)} onCheckedChange={(checked: boolean) => {
                                                                        if (checked) setSelectedTools(prev => [...prev, tool.name]);else setSelectedTools(prev => prev.filter(t => t !== tool.name));
                                                                    }} className="mt-1" />
                                                                    <div className="grid gap-1.5 leading-none flex-1">
                                                                        <Label htmlFor={`tool-${tool.name}`} className="font-medium cursor-pointer break-all text-sm">
                                                                            {tool.name}
                                                                        </Label>
                                                                        <p className="text-[10px] text-muted-foreground line-clamp-2" title={tool.description}>
                                                                            {tool.description || t("app.admin.dashboard.supervisor.page.k86e9a787")}
                                                                        </p>
                                                                    </div>
                                                                </div>)}
                                                        </div>}
                                                </div>;
                                        });
                                    })() : <div className="p-4"><p className="text-xs text-muted-foreground col-span-full">{t("app.admin.dashboard.supervisor.page.k58c74c39")}</p></div>}
                                </div> : null}
                            </div>
                        </CardContent>
                    </Card>
                </div>
            </div>
        </div>;
}
