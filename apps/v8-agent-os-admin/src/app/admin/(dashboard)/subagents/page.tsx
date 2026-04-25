"use client";

import Link from "next/link";
import { type CSSProperties, type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { Switch } from "@/components/ui/switch";
import { useToast } from "@/components/ui/use-toast";
import { useLocale, useT } from "@/components/providers/LocaleProvider";
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
    globalExposure?: boolean;
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
        specialistRegistry?: {
            familyModeEnabled?: boolean;
            maxMembersPerFamily?: number;
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
    specialistFamily: string;
    globalExposure: boolean;
    agentClass: string;
    domainTagsText: string;
    operationCapabilitiesText: string;
    runtimeAffinitiesText: string;
    toolExposurePolicy: string;
    capabilitySnapshotJson: string;
    reflectionEnabled: boolean;
    maxReflections: number;
};

type ExternalWorkerDescriptor = {
    id: string;
    name: string;
    description: string;
    enabled: boolean;
    workerType: string;
    capabilitySnapshot: Record<string, unknown>;
    launchProfile: {
        commandTemplate: string;
        cwdPolicy: string;
        envPassThrough: string[];
        startupTimeoutSeconds: number;
    };
    sessionMode: string;
    allowedSideEffects: string[];
    resultSchema: {
        type: string;
        markers: string[];
    };
};

type ExternalWorkerFormState = {
    id: string;
    name: string;
    description: string;
    enabled: boolean;
    workerType: string;
    commandTemplate: string;
    cwdPolicy: string;
    startupTimeoutSeconds: string;
    sessionMode: string;
    envPassThroughText: string;
    allowedSideEffectsText: string;
    resultMarkersText: string;
    agentClass: string;
    domainTagsText: string;
    operationCapabilitiesText: string;
    runtimeAffinitiesText: string;
    toolExposurePolicy: string;
    capabilitySnapshotJson: string;
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
    specialistFamily: "engineering",
    globalExposure: false,
    agentClass: "specialist",
    domainTagsText: "",
    operationCapabilitiesText: "",
    runtimeAffinitiesText: "",
    toolExposurePolicy: "contextual_auto",
    capabilitySnapshotJson: "{}",
    reflectionEnabled: false,
    maxReflections: 3,
};

const DEFAULT_EXTERNAL_WORKER_FORM: ExternalWorkerFormState = {
    id: "",
    name: "",
    description: "",
    enabled: false,
    workerType: "custom",
    commandTemplate: "",
    cwdPolicy: "inherit_workspace",
    startupTimeoutSeconds: "10",
    sessionMode: "interactive",
    envPassThroughText: "",
    allowedSideEffectsText: "",
    resultMarkersText: "<V8_WORKER_RESULT>, </V8_WORKER_RESULT>",
    agentClass: "external_worker",
    domainTagsText: "",
    operationCapabilitiesText: "",
    runtimeAffinitiesText: "chat, command_session",
    toolExposurePolicy: "task_brief_driven",
    capabilitySnapshotJson: "{}",
};

const CLAUDE_CODE_COMMAND_TEMPLATE = 'claude -p --permission-mode acceptEdits --output-format text "V8 external worker task. Decode this taskBrief base64 JSON: {task_brief_b64}. Obey writeSet, behaviorScope, requiredCapabilities, and acceptanceContract. Work only in the current workspace. When finished, print exactly one <V8_WORKER_RESULT> JSON object with keys summary, localSelfCheck, artifactRefs, and acceptanceHint </V8_WORKER_RESULT> block."';
const TEMPERATURE_PRESET = 0.7;
const MIN_CONFIG_TEMPERATURE = 0.05;
const MAX_SPECIALIST_FAMILY_MEMBERS = 50;
const FAMILY_AVATAR_COLORS = [
    { backgroundColor: "#E0F2FE", borderColor: "#38BDF8", color: "#075985" },
    { backgroundColor: "#DCFCE7", borderColor: "#4ADE80", color: "#166534" },
    { backgroundColor: "#FEF3C7", borderColor: "#FBBF24", color: "#92400E" },
    { backgroundColor: "#FCE7F3", borderColor: "#F472B6", color: "#9D174D" },
    { backgroundColor: "#EDE9FE", borderColor: "#A78BFA", color: "#5B21B6" },
    { backgroundColor: "#CCFBF1", borderColor: "#2DD4BF", color: "#115E59" },
    { backgroundColor: "#FFE4E6", borderColor: "#FB7185", color: "#9F1239" },
    { backgroundColor: "#DBEAFE", borderColor: "#60A5FA", color: "#1E3A8A" },
];
const GLOBAL_AVATAR_STYLE: CSSProperties = {
    backgroundColor: "#059669",
    borderColor: "#047857",
    color: "#FFFFFF",
};
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

function temperatureStatusText(value: string, locale: string) {
    if (String(value || "").trim()) {
        return locale === "en"
            ? `Override ${formatDecimal(temperatureSliderValue(value))}`
            : `已启用覆盖 ${formatDecimal(temperatureSliderValue(value))}`;
    }
    return locale === "en"
        ? `Recommended ${formatDecimal(TEMPERATURE_PRESET)} (not enabled)`
        : `推荐值 ${formatDecimal(TEMPERATURE_PRESET)}（未启用）`;
}

function temperatureDefaultText(locale: string) {
    return locale === "en" ? "model config / provider default" : "模型配置 / 供应商默认";
}

function splitListText(value: string) {
    return String(value || "")
        .split(/[,，\n]/)
        .map((item) => item.trim())
        .filter(Boolean);
}

function stringListFromSnapshot(value: unknown) {
    return Array.isArray(value)
        ? value.map((item) => String(item || "").trim()).filter(Boolean)
        : [];
}

function normalizeExternalWorkerDescriptor(value: unknown): ExternalWorkerDescriptor {
    const payload = value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : {};
    const launchProfile = payload.launchProfile && typeof payload.launchProfile === "object" && !Array.isArray(payload.launchProfile)
        ? payload.launchProfile as Record<string, unknown>
        : {};
    const resultSchema = payload.resultSchema && typeof payload.resultSchema === "object" && !Array.isArray(payload.resultSchema)
        ? payload.resultSchema as Record<string, unknown>
        : {};
    const capabilitySnapshot = payload.capabilitySnapshot && typeof payload.capabilitySnapshot === "object" && !Array.isArray(payload.capabilitySnapshot)
        ? payload.capabilitySnapshot as Record<string, unknown>
        : {};
    return {
        id: String(payload.id || "").trim(),
        name: String(payload.name || "").trim(),
        description: String(payload.description || "").trim(),
        enabled: Boolean(payload.enabled),
        workerType: String(payload.workerType || "").trim() || "custom",
        capabilitySnapshot,
        launchProfile: {
            commandTemplate: String(launchProfile.commandTemplate || "").trim(),
            cwdPolicy: String(launchProfile.cwdPolicy || "inherit_workspace").trim() || "inherit_workspace",
            envPassThrough: stringListFromSnapshot(launchProfile.envPassThrough),
            startupTimeoutSeconds: Math.max(3, Math.min(Number(launchProfile.startupTimeoutSeconds || 10) || 10, 120)),
        },
        sessionMode: String(payload.sessionMode || "interactive").trim() || "interactive",
        allowedSideEffects: stringListFromSnapshot(payload.allowedSideEffects),
        resultSchema: {
            type: String(resultSchema.type || "v8_worker_result_v1").trim() || "v8_worker_result_v1",
            markers: stringListFromSnapshot(resultSchema.markers).length > 0
                ? stringListFromSnapshot(resultSchema.markers)
                : ["<V8_WORKER_RESULT>", "</V8_WORKER_RESULT>"],
        },
    };
}

function normalizeExternalWorkers(values: unknown): ExternalWorkerDescriptor[] {
    if (!Array.isArray(values)) return [];
    const seen = new Set<string>();
    const items: ExternalWorkerDescriptor[] = [];
    for (const value of values) {
        const descriptor = normalizeExternalWorkerDescriptor(value);
        if (!descriptor.id || seen.has(descriptor.id)) continue;
        seen.add(descriptor.id);
        items.push(descriptor);
    }
    return items;
}

function externalWorkerToForm(worker?: ExternalWorkerDescriptor | null): ExternalWorkerFormState {
    if (!worker) return { ...DEFAULT_EXTERNAL_WORKER_FORM };
    const snapshot = worker.capabilitySnapshot && typeof worker.capabilitySnapshot === "object" ? worker.capabilitySnapshot : {};
    return {
        id: worker.id,
        name: worker.name,
        description: worker.description,
        enabled: Boolean(worker.enabled),
        workerType: worker.workerType || "custom",
        commandTemplate: worker.launchProfile.commandTemplate || "",
        cwdPolicy: worker.launchProfile.cwdPolicy || "inherit_workspace",
        startupTimeoutSeconds: String(worker.launchProfile.startupTimeoutSeconds || 10),
        sessionMode: worker.sessionMode || "interactive",
        envPassThroughText: worker.launchProfile.envPassThrough.join(", "),
        allowedSideEffectsText: worker.allowedSideEffects.join(", "),
        resultMarkersText: worker.resultSchema.markers.join(", "),
        agentClass: typeof snapshot.agentClass === "string" && snapshot.agentClass.trim() ? snapshot.agentClass.trim() : "external_worker",
        domainTagsText: stringListFromSnapshot(snapshot.domainTags).join(", "),
        operationCapabilitiesText: stringListFromSnapshot(snapshot.operationCapabilities).join(", "),
        runtimeAffinitiesText: stringListFromSnapshot(snapshot.runtimeAffinities).join(", "),
        toolExposurePolicy: typeof snapshot.toolExposurePolicy === "string" && snapshot.toolExposurePolicy.trim()
            ? snapshot.toolExposurePolicy.trim()
            : "task_brief_driven",
        capabilitySnapshotJson: JSON.stringify(snapshot, null, 2),
    };
}

function externalWorkerFormToDescriptor(form: ExternalWorkerFormState): ExternalWorkerDescriptor {
    let capabilitySnapshot: Record<string, unknown> = {};
    try {
        const parsed = JSON.parse(form.capabilitySnapshotJson || "{}");
        capabilitySnapshot = parsed && typeof parsed === "object" && !Array.isArray(parsed)
            ? parsed as Record<string, unknown>
            : {};
    } catch {
        capabilitySnapshot = {};
    }
    capabilitySnapshot = {
        ...capabilitySnapshot,
        agentClass: form.agentClass.trim() || "external_worker",
        domainTags: splitListText(form.domainTagsText),
        operationCapabilities: splitListText(form.operationCapabilitiesText),
        runtimeAffinities: splitListText(form.runtimeAffinitiesText),
        toolExposurePolicy: form.toolExposurePolicy.trim() || "task_brief_driven",
    };
    return normalizeExternalWorkerDescriptor({
        id: form.id,
        name: form.name,
        description: form.description,
        enabled: form.enabled,
        workerType: form.workerType,
        capabilitySnapshot,
        launchProfile: {
            commandTemplate: form.commandTemplate,
            cwdPolicy: form.cwdPolicy,
            envPassThrough: splitListText(form.envPassThroughText),
            startupTimeoutSeconds: Number(form.startupTimeoutSeconds || 10) || 10,
        },
        sessionMode: form.sessionMode,
        allowedSideEffects: splitListText(form.allowedSideEffectsText),
        resultSchema: {
            type: "v8_worker_result_v1",
            markers: splitListText(form.resultMarkersText),
        },
    });
}

function uniqueWorkerId(baseId: string, workers: ExternalWorkerDescriptor[]) {
    const base = String(baseId || "external-worker").trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "") || "external-worker";
    const existing = new Set(workers.map((worker) => worker.id));
    if (!existing.has(base)) return base;
    for (let index = 2; index < 100; index += 1) {
        const candidate = `${base}-${index}`;
        if (!existing.has(candidate)) return candidate;
    }
    return `${base}-${Date.now()}`;
}

function firstGrapheme(value: string, fallback: string) {
    return Array.from(String(value || "").trim())[0]?.toUpperCase() || fallback;
}

function StatusCardTitle({
    icon,
    title,
    tooltip,
}: {
    icon: ReactNode;
    title: string;
    tooltip: ReactNode;
}) {
    return (
        <div className="group/status-title relative inline-flex max-w-full items-center gap-2">
            <CardTitle className="flex max-w-full cursor-help items-center gap-2 truncate text-sm font-bold text-slate-950">
                {icon}
                <span className="truncate">{title}</span>
            </CardTitle>
            <div className="pointer-events-none absolute left-0 top-full z-50 mt-2 hidden w-80 rounded-2xl bg-slate-950 p-4 text-sm leading-7 text-white shadow-2xl ring-1 ring-white/10 group-hover/status-title:block">
                {tooltip}
            </div>
        </div>
    );
}

function HoverHelpLabel({
    label,
    tooltip,
}: {
    label: ReactNode;
    tooltip: ReactNode;
}) {
    return (
        <div className="group/help-label relative inline-flex w-fit items-center">
            <Label className="cursor-help font-medium text-slate-950">{label}</Label>
            <div className="pointer-events-none absolute left-0 top-full z-50 mt-2 hidden w-80 rounded-2xl bg-slate-950 p-4 text-sm leading-7 text-white shadow-2xl ring-1 ring-white/10 group-hover/help-label:block">
                {tooltip}
            </div>
        </div>
    );
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
    const { locale } = useLocale();
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
    const [externalWorkers, setExternalWorkers] = useState<ExternalWorkerDescriptor[]>([]);
    const [externalWorkersJson, setExternalWorkersJson] = useState("[]");
    const [showExternalWorkersJson, setShowExternalWorkersJson] = useState(false);
    const [editingExternalWorkerId, setEditingExternalWorkerId] = useState("");
    const [externalWorkerForm, setExternalWorkerForm] = useState<ExternalWorkerFormState>(DEFAULT_EXTERNAL_WORKER_FORM);
    const [subagentTemperature, setSubagentTemperature] = useState("");
    const [familyModeEnabled, setFamilyModeEnabled] = useState(true);
    const [maxMembersPerFamily, setMaxMembersPerFamily] = useState(10);
    const [isLoading, setIsLoading] = useState(false);
    const [isDialogOpen, setIsDialogOpen] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [isSavingExternalWorkers, setIsSavingExternalWorkers] = useState(false);
    const [isSavingSubagentTemperature, setIsSavingSubagentTemperature] = useState(false);
    const [isSavingSpecialistRegistry, setIsSavingSpecialistRegistry] = useState(false);
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
    const familyColorMap = useMemo(() => {
        const families = Array.from(new Set(
            agents
                .map((agent) => {
                    const snapshot = agent.capabilitySnapshot && typeof agent.capabilitySnapshot === "object" && !Array.isArray(agent.capabilitySnapshot)
                        ? agent.capabilitySnapshot
                        : {};
                    return String(snapshot.specialistFamily || "engineering").trim().toLowerCase() || "engineering";
                })
                .filter(Boolean),
        )).sort();
        return families.reduce<Record<string, CSSProperties>>((acc, family, index) => {
            const base = FAMILY_AVATAR_COLORS[index % FAMILY_AVATAR_COLORS.length];
            const cycle = Math.floor(index / FAMILY_AVATAR_COLORS.length);
            acc[family] = cycle === 0
                ? base
                : {
                    backgroundColor: `hsl(${(index * 47) % 360} 82% 92%)`,
                    borderColor: `hsl(${(index * 47) % 360} 72% 48%)`,
                    color: `hsl(${(index * 47) % 360} 82% 24%)`,
                };
            return acc;
        }, {});
    }, [agents]);

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
    const enabledSubagentCount = agents.filter((agent) => agent.isEnabled !== false).length;
    const externalWorkerDescriptors = externalWorkers;
    const enabledExternalWorkerCount = externalWorkerDescriptors.filter((item) => (
        Boolean(item.enabled) && Boolean(item.launchProfile.commandTemplate.trim())
    )).length;
    const externalWorkerTemplateCount = externalWorkerDescriptors.length;

    const syncExternalWorkers = useCallback((values: unknown) => {
        const normalized = normalizeExternalWorkers(values);
        setExternalWorkers(normalized);
        setExternalWorkersJson(JSON.stringify(normalized, null, 2));
        if (normalized.length > 0) {
            setEditingExternalWorkerId((current) => current && normalized.some((item) => item.id === current) ? current : normalized[0].id);
            setExternalWorkerForm((current) => {
                const target = normalized.find((item) => item.id === current.id) || normalized[0];
                return externalWorkerToForm(target);
            });
        } else {
            setEditingExternalWorkerId("");
            setExternalWorkerForm({ ...DEFAULT_EXTERNAL_WORKER_FORM });
        }
    }, []);

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
                specialistFamily: typeof capabilitySnapshot.specialistFamily === "string" && capabilitySnapshot.specialistFamily.trim()
                    ? capabilitySnapshot.specialistFamily.trim()
                    : "engineering",
                globalExposure: Boolean(agent.globalExposure),
                agentClass: typeof capabilitySnapshot.agentClass === "string" && capabilitySnapshot.agentClass.trim()
                    ? capabilitySnapshot.agentClass.trim()
                    : "specialist",
                domainTagsText: stringListFromSnapshot(capabilitySnapshot.domainTags).join(", "),
                operationCapabilitiesText: stringListFromSnapshot(capabilitySnapshot.operationCapabilities).join(", "),
                runtimeAffinitiesText: stringListFromSnapshot(capabilitySnapshot.runtimeAffinities).join(", "),
                toolExposurePolicy: typeof capabilitySnapshot.toolExposurePolicy === "string" && capabilitySnapshot.toolExposurePolicy.trim()
                    ? capabilitySnapshot.toolExposurePolicy.trim()
                    : "contextual_auto",
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
                const registry = data?.data?.specialistRegistry || {};
                setFamilyModeEnabled(registry.familyModeEnabled !== false);
                setMaxMembersPerFamily(Math.max(1, Math.min(MAX_SPECIALIST_FAMILY_MEMBERS, Number(registry.maxMembersPerFamily || 10) || 10)));
                syncExternalWorkers(data?.data?.delegation?.externalWorkers);
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
    }, [syncExternalWorkers, t, toast]);

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
        const specialistFamily = form.specialistFamily.trim() || "engineering";
        capabilitySnapshot = {
            ...capabilitySnapshot,
            specialistFamily,
            agentClass: form.agentClass.trim() || "specialist",
            domainTags: splitListText(form.domainTagsText),
            operationCapabilities: splitListText(form.operationCapabilitiesText),
            runtimeAffinities: splitListText(form.runtimeAffinitiesText),
            toolExposurePolicy: form.toolExposurePolicy.trim() || "contextual_auto",
        };
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
                globalExposure: form.globalExposure,
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

    const handleSelectExternalWorker = useCallback((workerId: string) => {
        const worker = externalWorkers.find((item) => item.id === workerId);
        if (!worker) return;
        setEditingExternalWorkerId(worker.id);
        setExternalWorkerForm(externalWorkerToForm(worker));
    }, [externalWorkers]);

    const handleStartExternalWorkerTemplate = useCallback((template: "custom" | "claude_code") => {
        const templateId = template === "claude_code"
            ? "claude-code-worker"
            : "external-worker";
        const existing = externalWorkers.find((item) => item.id === templateId);
        if (existing) {
            handleSelectExternalWorker(existing.id);
            return;
        }
        const baseForm: ExternalWorkerFormState = {
            ...DEFAULT_EXTERNAL_WORKER_FORM,
            id: uniqueWorkerId(templateId, externalWorkers),
            name: template === "claude_code" ? "Claude Code Worker" : "Custom External Worker",
            description: template === "claude_code"
                ? "Real Claude Code CLI worker for bounded implementation, debugging, review, or verification tasks."
                : "",
            workerType: template,
            commandTemplate: template === "claude_code" ? CLAUDE_CODE_COMMAND_TEMPLATE : "",
            domainTagsText: template === "claude_code"
                ? "software_engineering, implementation, debugging, code_review"
                : "",
            operationCapabilitiesText: template === "claude_code"
                ? "implement, debug, review, verify"
                : "",
            runtimeAffinitiesText: template === "claude_code"
                ? "chat, command_session, claude_code"
                : DEFAULT_EXTERNAL_WORKER_FORM.runtimeAffinitiesText,
            allowedSideEffectsText: template === "claude_code"
                ? "workspace_write, tool_use, long_running_cli"
                : "",
        };
        setEditingExternalWorkerId("");
        setExternalWorkerForm(baseForm);
    }, [externalWorkers, handleSelectExternalWorker]);

    const handleApplyExternalWorkerForm = useCallback(() => {
        const descriptor = externalWorkerFormToDescriptor(externalWorkerForm);
        if (!descriptor.id) {
            toast({
                title: locale === "en" ? "Worker ID is required" : "需要填写 Worker ID",
                description: locale === "en" ? "External worker descriptors must have a stable id." : "远端 worker 需要一个稳定 ID 才能保存。",
                variant: "destructive",
            });
            return;
        }
        const nextWorkers = externalWorkers.some((item) => item.id === editingExternalWorkerId || item.id === descriptor.id)
            ? externalWorkers.map((item) => (item.id === editingExternalWorkerId || item.id === descriptor.id ? descriptor : item))
            : [...externalWorkers, descriptor];
        syncExternalWorkers(nextWorkers);
        setEditingExternalWorkerId(descriptor.id);
    }, [editingExternalWorkerId, externalWorkerForm, externalWorkers, locale, syncExternalWorkers, toast]);

    const handleDeleteExternalWorker = useCallback((workerId: string) => {
        syncExternalWorkers(externalWorkers.filter((item) => item.id !== workerId));
    }, [externalWorkers, syncExternalWorkers]);

    const handleApplyExternalWorkersJson = useCallback(() => {
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
        syncExternalWorkers(parsed);
    }, [externalWorkersJson, syncExternalWorkers, t, toast]);

    const handleSaveExternalWorkers = useCallback(async () => {
        let workersToSave: ExternalWorkerDescriptor[] = externalWorkers;
        if (showExternalWorkersJson) {
            try {
                const parsed = JSON.parse(externalWorkersJson || "[]");
                if (!Array.isArray(parsed)) {
                    throw new Error(t("app.admin.dashboard.subagents.page.externalWorkers.arrayJsonDescription"));
                }
                workersToSave = normalizeExternalWorkers(parsed);
            } catch (error) {
                toast({
                    title: t("app.admin.dashboard.subagents.page.externalWorkers.invalidJsonTitle"),
                    description: error instanceof Error ? error.message : t("app.admin.dashboard.subagents.page.externalWorkers.invalidJsonDescription"),
                    variant: "destructive",
                });
                return;
            }
        }
        setIsSavingExternalWorkers(true);
        try {
            const nextPayload = {
                data: {
                    ...(supervisorDomainData?.data || {}),
                    delegation: {
                        ...((supervisorDomainData?.data?.delegation || {}) as Record<string, unknown>),
                        externalWorkers: workersToSave,
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
            syncExternalWorkers(data?.data?.delegation?.externalWorkers);
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
    }, [externalWorkers, externalWorkersJson, showExternalWorkersJson, supervisorDomainData, syncExternalWorkers, t, toast]);

    const handleSaveSpecialistRegistry = useCallback(async () => {
        setIsSavingSpecialistRegistry(true);
        try {
            const nextPayload = {
                data: {
                    ...(supervisorDomainData?.data || {}),
                    specialistRegistry: {
                        ...((supervisorDomainData?.data?.specialistRegistry || {}) as Record<string, unknown>),
                        familyModeEnabled,
                        maxMembersPerFamily: Math.max(1, Math.min(MAX_SPECIALIST_FAMILY_MEMBERS, maxMembersPerFamily)),
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
            const registry = data?.data?.specialistRegistry || {};
            setFamilyModeEnabled(registry.familyModeEnabled !== false);
            setMaxMembersPerFamily(Math.max(1, Math.min(MAX_SPECIALIST_FAMILY_MEMBERS, Number(registry.maxMembersPerFamily || 10) || 10)));
            toast({
                title: locale === "en" ? "Specialist registry saved" : "专家族注册表已保存",
                description: registry.familyModeEnabled === false
                    ? (locale === "en" ? "Family mode is off; all subagents are visible to the supervisor registry." : "Family 模式已关闭；Supervisor registry 将全量暴露所有 subagent。")
                    : (locale === "en" ? "Family-scoped compact exposure remains enabled." : "已保持按专家族收口的 compact 暴露模式。"),
            });
        } catch (error) {
            console.error("Failed to save specialist registry config", error);
            toast({
                title: locale === "en" ? "Failed to save specialist registry" : "专家族注册表保存失败",
                description: error instanceof Error ? error.message : (locale === "en" ? "Unknown error" : "未知错误"),
                variant: "destructive",
            });
        } finally {
            setIsSavingSpecialistRegistry(false);
        }
    }, [familyModeEnabled, locale, maxMembersPerFamily, supervisorDomainData, toast]);

    const handleSaveSubagentTemperature = useCallback(async () => {
        setIsSavingSubagentTemperature(true);
        const parsedTemperature = parseOptionalTemperature(subagentTemperature);
        try {
            const nextPayload = {
                data: {
                    ...(supervisorDomainData?.data || {}),
                    modelParameters: {
                        ...((supervisorDomainData?.data?.modelParameters || {}) as Record<string, unknown>),
                        subagent: {
                            ...(((supervisorDomainData?.data?.modelParameters?.subagent || {}) as Record<string, unknown>)),
                            temperature: parsedTemperature,
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
                description: parsedTemperature !== null
                    ? "后续 agent/reviewer 调用会使用该应用面 temperature 覆盖。"
                    : "已保存为空值：走模型配置或供应商默认，不注入 temperature；0 不作为可配置温度。",
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
                <Card className="h-28 overflow-visible rounded-2xl border-slate-200 bg-white/95 shadow-sm">
                    <CardHeader className="space-y-1 p-3 pb-1">
                        <StatusCardTitle
                            icon={<ShieldCheck className="h-4 w-4 shrink-0 text-sky-600" />}
                            title={t("app.admin.dashboard.subagents.page.k00bf2013")}
                            tooltip={(
                                <div>
                                    <div>{locale === "en" ? "Loaded native baseline tools" : "已加载基础系统工具"}: {baselineToolNames.length}</div>
                                    <div className="mt-1 break-words font-mono text-xs text-slate-200">
                                        {baselineToolNames.slice(0, 12).join(", ") || "none"}
                                    </div>
                                </div>
                            )}
                        />
                        <CardDescription className="truncate text-xs">{baselineToolNames.length} loaded</CardDescription>
                    </CardHeader>
                    <CardContent className="px-3 pb-3">
                        <div className="truncate font-mono text-[11px] text-slate-700">
                            {baselineToolNames.slice(0, 2).join(" · ") || "none"}
                            {baselineToolNames.length > 2 ? ` · +${baselineToolNames.length - 2}` : ""}
                        </div>
                    </CardContent>
                </Card>
                <Card className="h-28 overflow-visible rounded-2xl border-slate-200 bg-white/95 shadow-sm">
                    <CardHeader className="space-y-1 p-3 pb-1">
                        <StatusCardTitle
                            icon={<Sparkles className="h-4 w-4 shrink-0 text-violet-600" />}
                            title={t("app.admin.dashboard.subagents.page.k9764402c")}
                            tooltip={(
                                <div>
                                    <div>Skills: {skills.length}</div>
                                    <div>MCP servers: {connectedMcpServiceCount}/{mcpServiceCount}</div>
                                    <div>MCP tools: {availableMcpToolCount}</div>
                                    <div>PluginHost tools: {pluginHostTools.length}</div>
                                </div>
                            )}
                        />
                        <CardDescription className="truncate text-xs">{t("app.admin.dashboard.subagents.page.k90999eb9")}</CardDescription>
                    </CardHeader>
                    <CardContent className="grid grid-cols-2 gap-x-3 gap-y-1 px-3 pb-3 text-xs text-slate-500">
                        <div>Skills <span className="font-medium text-slate-900">{skills.length}</span></div>
                        <div>MCP <span className="font-medium text-slate-900">{connectedMcpServiceCount}/{mcpServiceCount}</span></div>
                    </CardContent>
                </Card>
                <Card className="h-28 overflow-visible rounded-2xl border-slate-200 bg-white/95 shadow-sm">
                    <CardHeader className="space-y-1 p-3 pb-1">
                        <StatusCardTitle
                            icon={<Cable className="h-4 w-4 shrink-0 text-emerald-600" />}
                            title={t("app.admin.dashboard.subagents.page.k11cd990c")}
                            tooltip={(
                                <div>
                                    <div>Broker: fan-out / join</div>
                                    <div>{locale === "en" ? "Max supported concurrency: ∞" : "最大支持 ∞ 并发"}</div>
                                    <div>{locale === "en" ? "Local targets" : "本地可委派目标"}: {enabledSubagentCount}</div>
                                    <div>{locale === "en" ? "External enabled / templates" : "远端已启用 / 模板"}: {enabledExternalWorkerCount} / {externalWorkerTemplateCount}</div>
                                    <div className="mt-1 text-xs text-slate-300">
                                        {locale === "en"
                                            ? "There is no hard-coded broker cap here; actual fan-out is bounded by task briefs, workset conflicts, routing matches, and runtime resources."
                                            : "当前没有硬编码 broker 并发上限；实际受 task briefs、workset 冲突、路由匹配和运行时资源约束。"}
                                    </div>
                                </div>
                            )}
                        />
                        <CardDescription className="truncate text-xs">
                            {locale === "en" ? "No hard-coded cap; broker/runtime gate fan-out." : "无硬编码上限；broker/runtime 约束 fan-out。"}
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-1 px-3 pb-3 text-xs text-slate-500">
                        <div><span className="font-medium text-slate-900">fan-out / join</span></div>
                        <div>{locale === "en" ? "Targets" : "目标"} <span className="font-medium text-slate-900">{enabledSubagentCount} local / {enabledExternalWorkerCount} remote</span></div>
                    </CardContent>
                </Card>
                <Card className="h-28 overflow-visible rounded-2xl border-slate-200 bg-white/95 shadow-sm">
                    <CardHeader className="space-y-1 p-3 pb-1">
                        <StatusCardTitle
                            icon={<BrainCircuit className="h-4 w-4 shrink-0 text-indigo-600" />}
                            title={locale === "en" ? "Subagent temperature" : "Subagent 温度"}
                            tooltip={(
                                <div>
                                    <div>{locale === "en" ? "Recommended value" : "推荐值"}: {formatDecimal(TEMPERATURE_PRESET)}</div>
                                    <div>{locale === "en" ? "Current override" : "当前覆盖"}: {subagentTemperature.trim() ? formatDecimal(temperatureSliderValue(subagentTemperature)) : temperatureDefaultText(locale)}</div>
                                    <div className="mt-1 text-xs text-slate-300">
                                        {locale === "en"
                                            ? "Default saves null. User config cannot save 0; explicit runtime calls may still use 0."
                                            : "默认会保存 null；用户配置不能保存 0，只有运行时代码显式调用仍可使用 0。"}
                                    </div>
                                </div>
                            )}
                        />
                        <CardDescription className="truncate text-xs">{temperatureStatusText(subagentTemperature, locale)}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-2 px-3 pb-3">
                        <Slider
                            value={[temperatureSliderValue(subagentTemperature)]}
                            min={MIN_CONFIG_TEMPERATURE}
                            max={2}
                            step={0.05}
                            onValueChange={([value]) => setSubagentTemperature(formatDecimal(value))}
                        />
                        <div className="flex items-center justify-between gap-2">
                            <Button size="sm" variant="outline" className="h-7 px-2 text-xs" onClick={() => setSubagentTemperature("")}>
                                {locale === "en" ? "Default" : "默认"}
                            </Button>
                            <Button size="sm" className="h-7 px-2 text-xs" onClick={() => void handleSaveSubagentTemperature()} disabled={isSavingSubagentTemperature}>
                                {isSavingSubagentTemperature ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Save className="mr-1 h-3 w-3" />}
                                {locale === "en" ? "Save" : "保存"}
                            </Button>
                        </div>
                    </CardContent>
                </Card>
            </div>

            <Card className="rounded-2xl border-slate-200 bg-white/95 shadow-sm">
                <CardContent className="grid gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_minmax(260px,360px)_auto] lg:items-center">
                    <div className="space-y-1">
                        <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                            <BrainCircuit className="h-4 w-4 text-indigo-600" />
                            {locale === "en" ? "Specialist family mode" : "专家族 Family 模式"}
                            <Badge variant={familyModeEnabled ? "secondary" : "destructive"}>{familyModeEnabled ? "compact" : "full"}</Badge>
                        </div>
                        <p className="text-xs leading-5 text-slate-500">
                            {familyModeEnabled
                                ? (locale === "en" ? "Only matched families plus global subagents enter the supervisor prompt." : "Supervisor prompt 只暴露命中专家族与 global subagent。")
                                : (locale === "en" ? "Warning: all subagents are exposed to the supervisor registry." : "警告：关闭后 Supervisor registry 会全量暴露所有 subagent。")}
                        </p>
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center justify-between gap-3">
                            <Label className="text-xs">{locale === "en" ? "Non-global family limit" : "非 global family 暴露上限"}：{maxMembersPerFamily}</Label>
                            <Switch checked={familyModeEnabled} onCheckedChange={setFamilyModeEnabled} />
                        </div>
                        <Slider
                            value={[maxMembersPerFamily]}
                            min={1}
                            max={MAX_SPECIALIST_FAMILY_MEMBERS}
                            step={1}
                            disabled={!familyModeEnabled}
                            onValueChange={([value]) => setMaxMembersPerFamily(Math.max(1, Math.min(MAX_SPECIALIST_FAMILY_MEMBERS, Math.round(value))))}
                        />
                    </div>
                    <Button onClick={() => void handleSaveSpecialistRegistry()} disabled={isSavingSpecialistRegistry}>
                        {isSavingSpecialistRegistry ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                        {locale === "en" ? "Save registry" : "保存注册表"}
                    </Button>
                </CardContent>
            </Card>

            <div className="grid gap-6 md:grid-cols-2 xl:grid-cols-3">
                {agents.map((agent) => {
                    const selectors = Array.isArray(agent.tools) ? agent.tools : [];
                    const toolMode = agent.tool_mode === "explicit" ? "explicit" : "contextual_auto";
                    const capabilitySnapshot = agent.capabilitySnapshot && typeof agent.capabilitySnapshot === "object" && !Array.isArray(agent.capabilitySnapshot)
                        ? agent.capabilitySnapshot
                        : {};
                    const agentClass = typeof capabilitySnapshot.agentClass === "string" ? capabilitySnapshot.agentClass : "";
                    const specialistFamily = typeof capabilitySnapshot.specialistFamily === "string" ? capabilitySnapshot.specialistFamily : "";
                    const domainTags = Array.isArray(capabilitySnapshot.domainTags)
                        ? capabilitySnapshot.domainTags.filter((item): item is string => typeof item === "string").slice(0, 3)
                        : [];
                    const familyKey = (specialistFamily || "engineering").trim().toLowerCase() || "engineering";
                    const avatarStyle = agent.globalExposure ? GLOBAL_AVATAR_STYLE : (familyColorMap[familyKey] || FAMILY_AVATAR_COLORS[0]);
                    const avatarLabel = agent.globalExposure ? "G" : firstGrapheme(specialistFamily || "engineering", "E");
                    return (
                        <Card key={agent.id} className="rounded-3xl border-slate-200 bg-white/95 shadow-sm">
                            <CardHeader className="space-y-4">
                                <div className="flex items-start justify-between gap-3">
                                    <div className="flex min-w-0 items-center gap-3">
                                        <div
                                            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border text-lg font-bold"
                                            style={avatarStyle}
                                            title={agent.globalExposure ? "globalExposure" : `family:${familyKey}`}
                                        >
                                            {avatarLabel}
                                        </div>
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
                                    {specialistFamily ? <Badge variant="secondary">family:{specialistFamily}</Badge> : null}
                                    {agent.globalExposure ? <Badge className="bg-emerald-600 hover:bg-emerald-600">globalExposure</Badge> : null}
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
                <CardContent className="space-y-5">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                        <div className="flex flex-wrap gap-2">
                            <Button type="button" variant="outline" size="sm" onClick={() => handleStartExternalWorkerTemplate("claude_code")}>
                                Claude Code
                            </Button>
                            <Button type="button" variant="outline" size="sm" onClick={() => handleStartExternalWorkerTemplate("custom")}>
                                <Plus className="mr-2 h-4 w-4" />
                                {locale === "en" ? "Custom" : "自定义"}
                            </Button>
                        </div>
                        <Badge variant="secondary">
                            {locale === "en" ? "Enabled remote targets" : "远端已启用目标"} {enabledExternalWorkerCount}/{externalWorkerTemplateCount}
                        </Badge>
                    </div>

                    <div className="grid gap-5 lg:grid-cols-[minmax(260px,0.9fr)_minmax(0,1.35fr)]">
                        <div className="space-y-3">
                            {externalWorkers.length === 0 ? (
                                <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/80 p-5 text-sm text-slate-500">
                                    {locale === "en" ? "No external worker templates yet." : "还没有远端 worker 模板。"}
                                </div>
                            ) : null}
                            {externalWorkers.map((worker) => {
                                const isActive = worker.id === editingExternalWorkerId;
                                const isEnabledTarget = Boolean(worker.enabled && worker.launchProfile.commandTemplate.trim());
                                return (
                                    <button
                                        key={worker.id}
                                        type="button"
                                        className={`w-full rounded-2xl border p-4 text-left transition ${isActive ? "border-emerald-400 bg-emerald-50/70" : "border-slate-200 bg-slate-50/70 hover:border-slate-300"}`}
                                        onClick={() => handleSelectExternalWorker(worker.id)}
                                    >
                                        <div className="flex items-start justify-between gap-3">
                                            <div className="min-w-0">
                                                <div className="truncate text-sm font-semibold text-slate-950">{worker.name || worker.id}</div>
                                                <div className="mt-1 truncate font-mono text-xs text-slate-500">{worker.id}</div>
                                            </div>
                                            <Badge variant={isEnabledTarget ? "default" : "secondary"}>
                                                {isEnabledTarget ? (locale === "en" ? "enabled" : "已启用") : (locale === "en" ? "template" : "模板")}
                                            </Badge>
                                        </div>
                                        <div className="mt-3 text-xs leading-5 text-slate-500">
                                            {worker.workerType || "custom"} · {worker.launchProfile.cwdPolicy || "inherit_workspace"}
                                        </div>
                                    </button>
                                );
                            })}
                        </div>

                        <div className="space-y-4 rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                            <div className="grid gap-4 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label>Worker ID</Label>
                                    <Input
                                        value={externalWorkerForm.id}
                                        onChange={(event) => setExternalWorkerForm((current) => ({ ...current, id: event.target.value }))}
                                        placeholder="coding-cli-worker"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>{locale === "en" ? "Name" : "名称"}</Label>
                                    <Input
                                        value={externalWorkerForm.name}
                                        onChange={(event) => setExternalWorkerForm((current) => ({ ...current, name: event.target.value }))}
                                        placeholder="Claude Code Worker"
                                    />
                                </div>
                                <div className="space-y-2 md:col-span-2">
                                    <Label>{locale === "en" ? "Description" : "描述"}</Label>
                                    <Input
                                        value={externalWorkerForm.description}
                                        onChange={(event) => setExternalWorkerForm((current) => ({ ...current, description: event.target.value }))}
                                        placeholder={locale === "en" ? "What tasks should this worker receive?" : "这个 worker 适合接什么任务？"}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>{locale === "en" ? "Worker type" : "Worker 类型"}</Label>
                                    <Input
                                        value={externalWorkerForm.workerType}
                                        onChange={(event) => setExternalWorkerForm((current) => ({ ...current, workerType: event.target.value }))}
                                        placeholder="custom / claude_code"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>{locale === "en" ? "Startup timeout" : "启动超时（秒）"}</Label>
                                    <Input
                                        type="number"
                                        min={3}
                                        max={120}
                                        value={externalWorkerForm.startupTimeoutSeconds}
                                        onChange={(event) => setExternalWorkerForm((current) => ({ ...current, startupTimeoutSeconds: event.target.value }))}
                                    />
                                </div>
                                <div className="space-y-2 md:col-span-2">
                                    <Label>{locale === "en" ? "Command template" : "命令模板"}</Label>
                                    <Textarea
                                        value={externalWorkerForm.commandTemplate}
                                        onChange={(event) => setExternalWorkerForm((current) => ({ ...current, commandTemplate: event.target.value }))}
                                        className="min-h-[84px] font-mono text-xs"
                                        placeholder='codex exec --json "{task_brief_b64}"'
                                    />
                                    <p className="text-xs leading-5 text-slate-500">
                                        {locale === "en"
                                            ? "Only enabled workers with a non-empty command template count as remote delegation targets."
                                            : "只有 enabled=true 且命令模板非空，才计为远端可委派目标。"}
                                    </p>
                                </div>
                                <div className="space-y-2">
                                    <Label>{locale === "en" ? "Working directory policy" : "工作目录策略"}</Label>
                                    <Select value={externalWorkerForm.cwdPolicy} onValueChange={(value) => setExternalWorkerForm((current) => ({ ...current, cwdPolicy: value }))}>
                                        <SelectTrigger><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="inherit_workspace">inherit_workspace</SelectItem>
                                            <SelectItem value="runtime_temp">runtime_temp</SelectItem>
                                            <SelectItem value="explicit">explicit</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div className="space-y-2">
                                    <Label>{locale === "en" ? "Session mode" : "会话模式"}</Label>
                                    <Select value={externalWorkerForm.sessionMode} onValueChange={(value) => setExternalWorkerForm((current) => ({ ...current, sessionMode: value }))}>
                                        <SelectTrigger><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="interactive">interactive</SelectItem>
                                            <SelectItem value="oneshot">oneshot</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div className="space-y-2">
                                    <Label>env passthrough</Label>
                                    <Input
                                        value={externalWorkerForm.envPassThroughText}
                                        onChange={(event) => setExternalWorkerForm((current) => ({ ...current, envPassThroughText: event.target.value }))}
                                        placeholder="PATH, HOME"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>{locale === "en" ? "Allowed side effects" : "允许副作用"}</Label>
                                    <Input
                                        value={externalWorkerForm.allowedSideEffectsText}
                                        onChange={(event) => setExternalWorkerForm((current) => ({ ...current, allowedSideEffectsText: event.target.value }))}
                                        placeholder="workspace_write, network_access"
                                    />
                                </div>
                                <div className="space-y-2 md:col-span-2">
                                    <Label>{locale === "en" ? "Result markers" : "结果标记"}</Label>
                                    <Input
                                        value={externalWorkerForm.resultMarkersText}
                                        onChange={(event) => setExternalWorkerForm((current) => ({ ...current, resultMarkersText: event.target.value }))}
                                        placeholder="<V8_WORKER_RESULT>, </V8_WORKER_RESULT>"
                                    />
                                </div>
                            </div>

                            <div className="grid gap-4 border-t border-slate-200 pt-4 md:grid-cols-2">
                                <div className="space-y-2">
                                    <HoverHelpLabel
                                        label={locale === "en" ? "Capability class" : "能力类型"}
                                        tooltip={locale === "en"
                                            ? "Free-form label used for routing. Examples: researcher, writer, operator, analyst, creative."
                                            : "自由路由标签。示例：researcher、writer、operator、analyst、creative。"}
                                    />
                                    <Input
                                        value={externalWorkerForm.agentClass}
                                        onChange={(event) => setExternalWorkerForm((current) => ({ ...current, agentClass: event.target.value }))}
                                        placeholder="external_worker"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>{locale === "en" ? "Tool exposure policy" : "工具暴露策略"}</Label>
                                    <Input
                                        value={externalWorkerForm.toolExposurePolicy}
                                        onChange={(event) => setExternalWorkerForm((current) => ({ ...current, toolExposurePolicy: event.target.value }))}
                                        placeholder="task_brief_driven"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>{locale === "en" ? "Domain tags" : "领域标签"}</Label>
                                    <Input
                                        value={externalWorkerForm.domainTagsText}
                                        onChange={(event) => setExternalWorkerForm((current) => ({ ...current, domainTagsText: event.target.value }))}
                                        placeholder="research, writing"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>{locale === "en" ? "Operations" : "操作能力"}</Label>
                                    <Input
                                        value={externalWorkerForm.operationCapabilitiesText}
                                        onChange={(event) => setExternalWorkerForm((current) => ({ ...current, operationCapabilitiesText: event.target.value }))}
                                        placeholder="research, synthesize, write"
                                    />
                                </div>
                                <div className="space-y-2 md:col-span-2">
                                    <Label>{locale === "en" ? "Runtime affinities" : "Runtime 偏好"}</Label>
                                    <Input
                                        value={externalWorkerForm.runtimeAffinitiesText}
                                        onChange={(event) => setExternalWorkerForm((current) => ({ ...current, runtimeAffinitiesText: event.target.value }))}
                                        placeholder="chat, command_session"
                                    />
                                </div>
                            </div>

                            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 pt-4">
                                <label className="flex items-center gap-3 text-sm font-medium text-slate-900">
                                    <Checkbox
                                        checked={externalWorkerForm.enabled}
                                        onCheckedChange={(next) => setExternalWorkerForm((current) => ({ ...current, enabled: Boolean(next) }))}
                                    />
                                    {locale === "en" ? "Enable this worker" : "启用这个 worker"}
                                </label>
                                <div className="flex gap-2">
                                    {editingExternalWorkerId ? (
                                        <Button type="button" variant="ghost" className="text-rose-600" onClick={() => handleDeleteExternalWorker(editingExternalWorkerId)}>
                                            <Trash2 className="mr-2 h-4 w-4" />
                                            {locale === "en" ? "Delete" : "删除"}
                                        </Button>
                                    ) : null}
                                    <Button type="button" variant="outline" onClick={handleApplyExternalWorkerForm}>
                                        {locale === "en" ? "Apply to list" : "应用到列表"}
                                    </Button>
                                </div>
                            </div>
                        </div>
                    </div>

                    <details className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4" open={showExternalWorkersJson} onToggle={(event) => setShowExternalWorkersJson(event.currentTarget.open)}>
                        <summary className="cursor-pointer text-sm font-medium text-slate-900">
                            {locale === "en" ? "Advanced JSON compatibility" : "高级 JSON 兼容入口"}
                        </summary>
                        <Textarea
                            value={externalWorkersJson}
                            onChange={(event) => setExternalWorkersJson(event.target.value)}
                            className="mt-3 min-h-[220px] font-mono text-xs"
                            placeholder='[{"id":"coding-cli-worker","enabled":false}]'
                        />
                        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                            <p className="text-xs leading-5 text-slate-500">
                                {t("app.admin.dashboard.subagents.page.externalWorkers.hintPrefix")} <code>launchProfile.commandTemplate</code> {t("app.admin.dashboard.subagents.page.externalWorkers.hintMiddle")} <code>resultSchema.markers</code>.
                            </p>
                            <Button type="button" variant="outline" size="sm" onClick={handleApplyExternalWorkersJson}>
                                {locale === "en" ? "Apply JSON to form" : "用 JSON 更新表单"}
                            </Button>
                        </div>
                    </details>

                    <div className="flex items-center justify-end">
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

                        <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(200px,240px)] md:items-start">
                            <div className="space-y-2">
                                <Label>{locale === "en" ? "Specialist family" : "专家族"}</Label>
                                <Input
                                    className="h-10"
                                    value={form.specialistFamily}
                                    onChange={(event) => setForm((current) => ({ ...current, specialistFamily: event.target.value }))}
                                    placeholder="engineering"
                                />
                                <p className="min-h-10 text-xs leading-5 text-slate-500">
                                    {locale === "en"
                                        ? "The supervisor exposes matched families each turn. Defaults: engineering / writing."
                                        : "Supervisor 每轮只暴露命中的专家族；默认演示族为 engineering / writing。"}
                                </p>
                            </div>
                            <div className="space-y-2">
                                <HoverHelpLabel
                                    label={locale === "en" ? "Agent class" : "能力类型"}
                                    tooltip={(
                                        <div>
                                            <div>{locale === "en" ? "Free-form routing label." : "自由填写的路由标签，不是封闭枚举。"}</div>
                                            <div className="mt-1 text-xs text-slate-300">researcher, writer, operator, coach, analyst, creative, skill_runtime_curator</div>
                                            <div className="mt-1 text-xs text-slate-300">
                                                {locale === "en"
                                                    ? "skill_runtime_curator means skill routing and quality governance; it does not guarantee strict SKILL.md execution by itself."
                                                    : "skill_runtime_curator 表示管理/审查 skill 使用与路由质量，不代表自动完全按 SKILL.md 规范执行。"}
                                            </div>
                                        </div>
                                    )}
                                />
                                <Input
                                    className="h-10"
                                    value={form.agentClass}
                                    onChange={(event) => setForm((current) => ({ ...current, agentClass: event.target.value }))}
                                    placeholder="researcher / writer / operator"
                                />
                                <p className="min-h-10 text-xs leading-5 text-slate-500">
                                    {locale === "en" ? "Saved as capabilitySnapshot.agentClass exactly as entered." : "原样保存到 capabilitySnapshot.agentClass，用于路由匹配。"}
                                </p>
                            </div>
                            <div className="space-y-2">
                                <Label>{locale === "en" ? "Prompt exposure" : "Prompt 可见性"}</Label>
                                <label className="flex h-10 items-center gap-3 rounded-2xl border border-slate-200 bg-slate-50/70 px-4 text-sm">
                                    <Checkbox
                                        checked={form.globalExposure}
                                        onCheckedChange={(next) => setForm((current) => ({ ...current, globalExposure: Boolean(next) }))}
                                    />
                                    <span className="font-medium text-slate-900">globalExposure</span>
                                </label>
                                <p className="min-h-10 text-xs leading-5 text-slate-500">
                                    {locale === "en" ? "Always visible in compact registry; does not grant tools." : "始终进入 compact 注册表；不代表额外工具授权。"}
                                </p>
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

                        <div className="space-y-4 rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                            <div className="grid gap-4 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label>{locale === "en" ? "Domain tags" : "领域标签"}</Label>
                                    <Input
                                        value={form.domainTagsText}
                                        onChange={(event) => setForm((current) => ({ ...current, domainTagsText: event.target.value }))}
                                        placeholder="software_engineering, frontend"
                                    />
                                    <p className="text-xs leading-5 text-slate-500">
                                        {locale === "en" ? "Custom values are supported. Separate with commas." : "支持自定义值，用逗号分隔。"}
                                    </p>
                                </div>
                                <div className="space-y-2">
                                    <Label>{locale === "en" ? "Operations" : "操作能力"}</Label>
                                    <Input
                                        value={form.operationCapabilitiesText}
                                        onChange={(event) => setForm((current) => ({ ...current, operationCapabilitiesText: event.target.value }))}
                                        placeholder="implement, review, test"
                                    />
                                    <p className="text-xs leading-5 text-slate-500">
                                        {locale === "en" ? "Custom verbs are supported for matching." : "支持自定义操作词，供路由匹配使用。"}
                                    </p>
                                </div>
                                <div className="space-y-2">
                                    <Label>{locale === "en" ? "Runtime affinities" : "Runtime 偏好"}</Label>
                                    <Input
                                        value={form.runtimeAffinitiesText}
                                        onChange={(event) => setForm((current) => ({ ...current, runtimeAffinitiesText: event.target.value }))}
                                        placeholder="engine, admin, web"
                                    />
                                    <p className="text-xs leading-5 text-slate-500">
                                        {locale === "en" ? "Custom runtime labels are supported." : "支持自定义 runtime 标签。"}
                                    </p>
                                </div>
                                <div className="space-y-2">
                                    <Label>{locale === "en" ? "Tool exposure policy" : "工具暴露策略"}</Label>
                                    <Select value={form.toolExposurePolicy} onValueChange={(value) => setForm((current) => ({ ...current, toolExposurePolicy: value }))}>
                                        <SelectTrigger>
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="contextual_auto">contextual_auto</SelectItem>
                                            <SelectItem value="explicit_only">explicit_only</SelectItem>
                                            <SelectItem value="none">none</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>
                            </div>
                            <details className="rounded-xl border border-slate-200 bg-white/80 p-3">
                                <summary className="cursor-pointer text-sm font-medium text-slate-900">
                                    {locale === "en" ? "Advanced raw capabilitySnapshot JSON" : "高级原始 capabilitySnapshot JSON"}
                                </summary>
                                <Textarea
                                    value={form.capabilitySnapshotJson}
                                    onChange={(event) => setForm((current) => ({ ...current, capabilitySnapshotJson: event.target.value }))}
                                    className="mt-3 min-h-[140px] font-mono text-xs"
                                    placeholder='{"agentClass":"executor","domainTags":["software_engineering"]}'
                                />
                                <p className="mt-2 text-xs leading-5 text-slate-500">
                                    {locale === "en"
                                        ? "Optional escape hatch for existing metadata. Guided fields above win when saving."
                                        : "仅作为兼容旧元数据的高级入口；保存时以上方引导字段为准。"}
                                </p>
                            </details>
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
