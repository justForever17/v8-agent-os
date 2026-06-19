"use client";

import Link from "next/link";
import { type CSSProperties, type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { AdminHoverInfo } from "@/components/admin-shell/AdminHoverInfo";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ModelSelect, modelOptionLabel, modelOptionValue } from "@/components/models/ModelSelect";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { SettingToggleCard } from "@/components/admin-shell/SettingToggleCard";
import { useToast } from "@/components/ui/use-toast";
import { useT } from "@/components/providers/LocaleProvider";
import { ArrowLeft, BrainCircuit, Cable, ChevronDown, Loader2, Plus, RefreshCw, Save, SearchCheck, ShieldCheck, Sparkles, Trash2, Wrench } from "lucide-react";
import { ir, tg, ti } from "@/i18n/admin-legacy";
import { getAdminOptions, resolveAdminLabel } from "@/lib/admin-labels";
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
    provider?: {
      name?: string | null;
    } | null;
  } | null;
};
type BaselineSystemTool = {
  name: string;
  description?: string;
};
type AIModel = {
  id: string;
  modelRef?: string;
  providerId?: string;
  modelId?: string;
  name: string;
  provider?: {
    id?: string;
    name?: string | null;
  } | null;
  providerName?: string | null;
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
      recursive?: {
        enabled?: boolean;
        maxDelegationDepth?: number;
        maxChildrenPerDelegation?: number;
        maxTotalDelegationNodes?: number;
        maxConcurrentDelegations?: number;
      };
    } | null;
    specialistRegistry?: {
      familyModeEnabled?: boolean;
      maxMembersPerFamily?: number;
      families?: SubagentFamilySummary[];
    } | null;
    research?: {
      enabled?: boolean;
      defaultShardCount?: number;
      maxShardCount?: number;
      maxRounds?: number;
      evidenceTtlSeconds?: number;
    } | null;
  } | null;
};
type SubagentFamilySummary = {
  familyId?: string;
  displayName?: string;
  aliases?: string[];
  description?: string;
  memberCount?: number;
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
      tools?: Array<{
        name?: string;
        description?: string;
      }>;
    }>;
  };
};
type AgentToolSurfacePayload = {
  baselineSystemTools?: BaselineSystemTool[];
  toolModes?: {
    recommended?: string;
    modes?: Record<string, {
      status?: string;
      selectorPolicy?: string;
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
    renderer?: string;
    commandProfile?: string;
    permissionMode?: string;
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
  maxReflections: 3
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
  capabilitySnapshotJson: "{}"
};
const CLAUDE_CODE_COMMAND_TEMPLATE = 'claude -p --permission-mode acceptEdits --output-format text "V8 external worker task. Read task brief JSON from file: .v8-agent-os/external-workers/{task_brief_id}/task_brief.json. Obey writeSet, behaviorScope, requiredCapabilities, and acceptanceContract. Work only in the current workspace. When finished, print exactly one <V8_WORKER_RESULT> JSON object with keys status, summary, changedFiles, commandsRun, verification, and notes </V8_WORKER_RESULT> block. The result JSON must be compact one-line JSON with short string values and no Markdown fence."';
const TEMPERATURE_PRESET = 0.7;
const MIN_CONFIG_TEMPERATURE = 0.05;
const MAX_SPECIALIST_FAMILY_MEMBERS = 50;
const DEFAULT_RECURSIVE_DELEGATION = {
  enabled: true,
  maxDelegationDepth: 10,
  maxChildrenPerDelegation: 10,
  maxTotalDelegationNodes: 100,
  maxConcurrentDelegations: 10
};
const clampInt = (value: unknown, fallback: number, min: number, max: number) => {
  const parsed = Math.round(Number(value));
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, parsed));
};
const DEFAULT_SPECIALIST_FAMILIES: SubagentFamilySummary[] = [{
  familyId: "engineering",
  displayName: "Engineering",
  aliases: [ir("ke42503f328"), "coding", "project_coding"],
  description: "Code, architecture, tests, migration, debugging, and repository implementation work."
}, {
  familyId: "creative_media",
  displayName: "Creative Media",
  aliases: [ir("kfbd01caa81"), "media", "multimedia"],
  description: "Image, video, voice, music brief, recipe, asset, and post-production specialist work."
}, {
  familyId: "writing",
  displayName: "Writing",
  aliases: [ir("k2e33ad4230"), "docs", "documentation"],
  description: "Documentation, research synthesis, handoff, proposals, and narrative delivery."
}, {
  familyId: "research",
  displayName: "Research",
  aliases: [ir("kf04090805c"), ir("k6d53e9d515"), "web_research", "source_quality"],
  description: "Web research planning, source ranking, evidence bundles, confidence, and citation synthesis."
}];
const FAMILY_AVATAR_COLORS = [{
  backgroundColor: "#E0F2FE",
  borderColor: "#38BDF8",
  color: "#075985"
}, {
  backgroundColor: "#DCFCE7",
  borderColor: "#4ADE80",
  color: "#166534"
}, {
  backgroundColor: "#FEF3C7",
  borderColor: "#FBBF24",
  color: "#92400E"
}, {
  backgroundColor: "#FCE7F3",
  borderColor: "#F472B6",
  color: "#9D174D"
}, {
  backgroundColor: "#EDE9FE",
  borderColor: "#A78BFA",
  color: "#5B21B6"
}, {
  backgroundColor: "#CCFBF1",
  borderColor: "#2DD4BF",
  color: "#115E59"
}, {
  backgroundColor: "#FFE4E6",
  borderColor: "#FB7185",
  color: "#9F1239"
}, {
  backgroundColor: "#DBEAFE",
  borderColor: "#60A5FA",
  color: "#1E3A8A"
}];
function normalizeFamilyId(value: unknown) {
  const normalized = String(value || "").trim().toLowerCase().replace(/\s+/g, "_").replace(/[^\p{L}\p{N}_.+-]+/gu, "_").replace(/_+/g, "_").replace(/^[._-]+|[._-]+$/g, "");
  return normalized || "engineering";
}
function normalizeFamilyEntry(value: unknown): SubagentFamilySummary | null {
  if (typeof value === "string") {
    const familyId = normalizeFamilyId(value);
    return {
      familyId,
      displayName: value.trim() || familyId,
      aliases: [],
      description: ""
    };
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const raw = value as Record<string, unknown>;
  const familyId = normalizeFamilyId(raw.familyId || raw.id || raw.name || raw.displayName);
  const displayName = String(raw.displayName || raw.name || raw.familyId || familyId).trim() || familyId;
  const aliases = Array.isArray(raw.aliases) ? raw.aliases.map(item => String(item || "").trim()).filter(Boolean) : [];
  return {
    familyId,
    displayName,
    aliases: Array.from(new Set(aliases)),
    description: String(raw.description || "").trim(),
    memberCount: Number(raw.memberCount || 0) || 0
  };
}
const GLOBAL_AVATAR_STYLE: CSSProperties = {
  backgroundColor: "#059669",
  borderColor: "#047857",
  color: "#FFFFFF"
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
function temperatureStatusText(t: ReturnType<typeof useT>, value: string) {
  if (String(value || "").trim()) {
    return `${ti(t, "k7d84e1319b")}${formatDecimal(temperatureSliderValue(value))}`;
  }
  return `${ti(t, "k630e24cd54")}${formatDecimal(TEMPERATURE_PRESET)}${ti(t, "kc10e7c65e4")}`;
}
function temperatureDefaultText(t: ReturnType<typeof useT>) {
  return ti(t, "k833e316858");
}
function splitListText(value: string) {
  return String(value || "").split(/[,，\n]/).map(item => item.trim()).filter(Boolean);
}
function stringListFromSnapshot(value: unknown) {
  return Array.isArray(value) ? value.map(item => String(item || "").trim()).filter(Boolean) : [];
}
function normalizeExternalWorkerDescriptor(value: unknown): ExternalWorkerDescriptor {
  const payload = value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
  const launchProfile = payload.launchProfile && typeof payload.launchProfile === "object" && !Array.isArray(payload.launchProfile) ? payload.launchProfile as Record<string, unknown> : {};
  const resultSchema = payload.resultSchema && typeof payload.resultSchema === "object" && !Array.isArray(payload.resultSchema) ? payload.resultSchema as Record<string, unknown> : {};
  const capabilitySnapshot = payload.capabilitySnapshot && typeof payload.capabilitySnapshot === "object" && !Array.isArray(payload.capabilitySnapshot) ? payload.capabilitySnapshot as Record<string, unknown> : {};
  const workerType = String(payload.workerType || "").trim() || "custom";
  const isClaudeWorker = workerType === "claude_code";
  return {
    id: String(payload.id || "").trim(),
    name: String(payload.name || "").trim(),
    description: String(payload.description || "").trim(),
    enabled: Boolean(payload.enabled),
    workerType,
    capabilitySnapshot,
    launchProfile: {
      commandTemplate: String(launchProfile.commandTemplate || "").trim(),
      renderer: String(launchProfile.renderer || (isClaudeWorker ? "claude_code" : "")).trim() || undefined,
      commandProfile: String(launchProfile.commandProfile || (isClaudeWorker ? "chat_cli" : "auto")).trim() || "auto",
      permissionMode: String(launchProfile.permissionMode || (isClaudeWorker ? "acceptEdits" : "")).trim() || undefined,
      cwdPolicy: String(launchProfile.cwdPolicy || "inherit_workspace").trim() || "inherit_workspace",
      envPassThrough: stringListFromSnapshot(launchProfile.envPassThrough),
      startupTimeoutSeconds: Math.max(3, Math.min(Number(launchProfile.startupTimeoutSeconds || 10) || 10, 120))
    },
    sessionMode: String(payload.sessionMode || (isClaudeWorker ? "print" : "interactive")).trim() || (isClaudeWorker ? "print" : "interactive"),
    allowedSideEffects: stringListFromSnapshot(payload.allowedSideEffects),
    resultSchema: {
      type: String(resultSchema.type || "v8_worker_result_v1").trim() || "v8_worker_result_v1",
      markers: stringListFromSnapshot(resultSchema.markers).length > 0 ? stringListFromSnapshot(resultSchema.markers) : ["<V8_WORKER_RESULT>", "</V8_WORKER_RESULT>"]
    }
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
  if (!worker) return {
    ...DEFAULT_EXTERNAL_WORKER_FORM
  };
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
    toolExposurePolicy: typeof snapshot.toolExposurePolicy === "string" && snapshot.toolExposurePolicy.trim() ? snapshot.toolExposurePolicy.trim() : "task_brief_driven",
    capabilitySnapshotJson: JSON.stringify(snapshot, null, 2)
  };
}
function externalWorkerFormToDescriptor(form: ExternalWorkerFormState): ExternalWorkerDescriptor {
  let capabilitySnapshot: Record<string, unknown> = {};
  try {
    const parsed = JSON.parse(form.capabilitySnapshotJson || "{}");
    capabilitySnapshot = parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    capabilitySnapshot = {};
  }
  capabilitySnapshot = {
    ...capabilitySnapshot,
    agentClass: form.agentClass.trim() || "external_worker",
    domainTags: splitListText(form.domainTagsText),
    operationCapabilities: splitListText(form.operationCapabilitiesText),
    runtimeAffinities: splitListText(form.runtimeAffinitiesText),
    toolExposurePolicy: form.toolExposurePolicy.trim() || "task_brief_driven"
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
      renderer: form.workerType === "claude_code" ? "claude_code" : undefined,
      commandProfile: form.workerType === "claude_code" ? "chat_cli" : "auto",
      permissionMode: form.workerType === "claude_code" ? "acceptEdits" : undefined,
      cwdPolicy: form.cwdPolicy,
      envPassThrough: splitListText(form.envPassThroughText),
      startupTimeoutSeconds: Number(form.startupTimeoutSeconds || 10) || 10
    },
    sessionMode: form.sessionMode,
    allowedSideEffects: splitListText(form.allowedSideEffectsText),
    resultSchema: {
      type: "v8_worker_result_v1",
      markers: splitListText(form.resultMarkersText)
    }
  });
}
function uniqueWorkerId(baseId: string, workers: ExternalWorkerDescriptor[]) {
  const base = String(baseId || "external-worker").trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "") || "external-worker";
  const existing = new Set(workers.map(worker => worker.id));
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
  tooltip
}: {
  icon: ReactNode;
  title: string;
  tooltip: ReactNode;
}) {
  return <AdminHoverInfo content={tooltip} panelClassName="text-sm leading-7">
            <CardTitle className="flex max-w-full items-center gap-2 truncate text-sm font-bold text-slate-950">
                {icon}
                <span className="truncate">{title}</span>
            </CardTitle>
        </AdminHoverInfo>;
}
function HoverHelpLabel({
  label,
  tooltip
}: {
  label: ReactNode;
  tooltip: ReactNode;
}) {
  return <AdminHoverInfo content={tooltip} panelClassName="text-sm leading-7">
            <Label className="font-medium text-slate-950">{label}</Label>
        </AdminHoverInfo>;
}
function WorkerConfigLabel({
  label,
  tooltip
}: {
  label: ReactNode;
  tooltip: ReactNode;
}) {
  return <HoverHelpLabel label={label} tooltip={tooltip} />;
}
function classifySelector(selector: string, skillNames: Set<string>, mcpNames: Set<string>, pluginHostNames: Set<string>) {
  if (skillNames.has(selector)) return "skill";
  if (mcpNames.has(selector)) return "mcp";
  if (pluginHostNames.has(selector)) return "plugin_host";
  return "other";
}
export default function SubagentsPage() {
  const t = useT();
  const {
    toast
  } = useToast();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [models, setModels] = useState<AIModel[]>([]);
  const [mcpTools, setMcpTools] = useState<MCPTool[]>([]);
  const [skills, setSkills] = useState<SkillEntry[]>([]);
  const [pluginHostTools, setPluginHostTools] = useState<PluginHostTool[]>([]);
  const [extensionsSummary, setExtensionsSummary] = useState<{
    mcpServerCount: number;
    connectedMcpServerCount: number;
    mcpToolCount: number;
  }>({
    mcpServerCount: 0,
    connectedMcpServerCount: 0,
    mcpToolCount: 0
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
  const [researchEnabled, setResearchEnabled] = useState(true);
  const [researchDefaultShards, setResearchDefaultShards] = useState(10);
  const [researchMaxShards, setResearchMaxShards] = useState(30);
  const [researchMaxRounds, setResearchMaxRounds] = useState(5);
  const [recursiveDelegationEnabled, setRecursiveDelegationEnabled] = useState(DEFAULT_RECURSIVE_DELEGATION.enabled);
  const [recursiveMaxDepth, setRecursiveMaxDepth] = useState(DEFAULT_RECURSIVE_DELEGATION.maxDelegationDepth);
  const [recursiveMaxChildren, setRecursiveMaxChildren] = useState(DEFAULT_RECURSIVE_DELEGATION.maxChildrenPerDelegation);
  const [recursiveMaxTotalNodes, setRecursiveMaxTotalNodes] = useState(DEFAULT_RECURSIVE_DELEGATION.maxTotalDelegationNodes);
  const [recursiveMaxConcurrent, setRecursiveMaxConcurrent] = useState(DEFAULT_RECURSIVE_DELEGATION.maxConcurrentDelegations);
  const [isLoading, setIsLoading] = useState(false);
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isSavingExternalWorkers, setIsSavingExternalWorkers] = useState(false);
  const [isSavingSubagentTemperature, setIsSavingSubagentTemperature] = useState(false);
  const [isSavingSpecialistRegistry, setIsSavingSpecialistRegistry] = useState(false);
  const [isSavingResearch, setIsSavingResearch] = useState(false);
  const [isSavingRecursiveDelegation, setIsSavingRecursiveDelegation] = useState(false);
  const [editingAgent, setEditingAgent] = useState<Agent | null>(null);
  const [form, setForm] = useState<AgentFormState>(DEFAULT_FORM_STATE);
  const [toolPanels, setToolPanels] = useState<Record<ToolPanelKey, boolean>>({
    baseline: false,
    skills: true,
    mcp: false,
    plugin_host: false
  });
  const skillNames = useMemo(() => new Set(skills.map(item => item.name)), [skills]);
  const mcpNames = useMemo(() => new Set(mcpTools.map(item => item.name)), [mcpTools]);
  const pluginHostNames = useMemo(() => new Set(pluginHostTools.map(item => String(item.canonicalName || item.toolName || "").trim()).filter(Boolean)), [pluginHostTools]);
  const baselineToolNames = useMemo(() => baselineSystemTools.map(item => String(item.name || "").trim()).filter(Boolean), [baselineSystemTools]);
  const familyOptions = useMemo(() => {
    const merged = new Map<string, SubagentFamilySummary>();
    const addFamily = (entry: unknown, memberCountDelta = 0) => {
      const normalized = normalizeFamilyEntry(entry);
      if (!normalized?.familyId) return;
      const existing = merged.get(normalized.familyId) || {};
      merged.set(normalized.familyId, {
        ...existing,
        ...normalized,
        aliases: Array.from(new Set([...(existing.aliases || []).map(item => String(item || "").trim()).filter(Boolean), ...(normalized.aliases || []).map(item => String(item || "").trim()).filter(Boolean)])),
        memberCount: Math.max(0, Number(existing.memberCount || 0)) + Math.max(0, Number(normalized.memberCount || 0)) + memberCountDelta
      });
    };
    DEFAULT_SPECIALIST_FAMILIES.forEach(family => addFamily(family));
    (supervisorDomainData?.data?.specialistRegistry?.families || []).forEach(family => addFamily(family));
    agents.forEach(agent => {
      const snapshot = agent.capabilitySnapshot && typeof agent.capabilitySnapshot === "object" && !Array.isArray(agent.capabilitySnapshot) ? agent.capabilitySnapshot : {};
      const familyId = normalizeFamilyId(snapshot.specialistFamily || "engineering");
      addFamily({
        familyId,
        displayName: String(snapshot.specialistFamily || familyId)
      }, 1);
    });
    return Array.from(merged.values()).sort((left, right) => String(left.displayName || left.familyId).localeCompare(String(right.displayName || right.familyId)));
  }, [agents, supervisorDomainData]);
  const familyColorMap = useMemo(() => {
    const families = familyOptions.map(family => normalizeFamilyId(family.familyId || family.displayName || "engineering"));
    return families.reduce<Record<string, CSSProperties>>((acc, family, index) => {
      const base = FAMILY_AVATAR_COLORS[index % FAMILY_AVATAR_COLORS.length];
      const cycle = Math.floor(index / FAMILY_AVATAR_COLORS.length);
      acc[family] = cycle === 0 ? base : {
        backgroundColor: `hsl(${index * 47 % 360} 82% 92%)`,
        borderColor: `hsl(${index * 47 % 360} 72% 48%)`,
        color: `hsl(${index * 47 % 360} 82% 24%)`
      };
      return acc;
    }, {});
  }, [familyOptions]);
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
  const resolveAgentModelDisplay = useCallback((agent: Agent) => {
    const explicitModelRef = String(agent.modelId || "").trim();
    const effectiveModelRef = explicitModelRef || String(defaultModelId || "").trim();
    if (!effectiveModelRef) {
      return t("app.admin.dashboard.subagents.page.kb1fcabf9");
    }
    const exact = models.find(model => modelOptionValue(model) === effectiveModelRef || String(model.id || "").trim() === effectiveModelRef);
    const legacyMatches = exact ? [] : models.filter(model => String(model.modelId || model.name || "").trim() === effectiveModelRef);
    const resolvedModel = exact || (legacyMatches.length === 1 ? legacyMatches[0] : null);
    const label = resolvedModel ? modelOptionLabel(resolvedModel) : agent.model?.name || effectiveModelRef;
    return explicitModelRef ? label : t("app.admin.dashboard.subagents.page.defaultInheritedModel", {
      model: label
    });
  }, [defaultModelId, models, t]);
  const resolveToolModeLabel = useCallback((value?: string | null) => {
    return resolveAdminLabel(t, "toolMode", value, {
      fallbackKey: "app.admin.dashboard.subagents.page.toolMode.unknown"
    });
  }, [t]);
  const mcpServiceCount = extensionsSummary.mcpServerCount;
  const connectedMcpServiceCount = extensionsSummary.connectedMcpServerCount;
  const availableMcpToolCount = extensionsSummary.mcpToolCount;
  const enabledSubagentCount = agents.filter(agent => agent.isEnabled !== false).length;
  const externalWorkerDescriptors = externalWorkers;
  const enabledExternalWorkerCount = externalWorkerDescriptors.filter(item => Boolean(item.enabled) && Boolean(item.launchProfile.commandTemplate.trim())).length;
  const externalWorkerTemplateCount = externalWorkerDescriptors.length;
  const syncExternalWorkers = useCallback((values: unknown) => {
    const normalized = normalizeExternalWorkers(values);
    setExternalWorkers(normalized);
    setExternalWorkersJson(JSON.stringify(normalized, null, 2));
    if (normalized.length > 0) {
      setEditingExternalWorkerId(current => current && normalized.some(item => item.id === current) ? current : normalized[0].id);
      setExternalWorkerForm(current => {
        const target = normalized.find(item => item.id === current.id) || normalized[0];
        return externalWorkerToForm(target);
      });
    } else {
      setEditingExternalWorkerId("");
      setExternalWorkerForm({
        ...DEFAULT_EXTERNAL_WORKER_FORM
      });
    }
  }, []);
  const resetForm = useCallback((agent?: Agent | null) => {
    if (!agent) {
      setForm({
        ...DEFAULT_FORM_STATE,
        modelId: defaultModelId || ""
      });
      return;
    }
    const capabilitySnapshot = agent.capabilitySnapshot && typeof agent.capabilitySnapshot === "object" && !Array.isArray(agent.capabilitySnapshot) ? agent.capabilitySnapshot : {};
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
      specialistFamily: typeof capabilitySnapshot.specialistFamily === "string" && capabilitySnapshot.specialistFamily.trim() ? capabilitySnapshot.specialistFamily.trim() : "engineering",
      globalExposure: Boolean(agent.globalExposure),
      agentClass: typeof capabilitySnapshot.agentClass === "string" && capabilitySnapshot.agentClass.trim() ? capabilitySnapshot.agentClass.trim() : "specialist",
      domainTagsText: stringListFromSnapshot(capabilitySnapshot.domainTags).join(", "),
      operationCapabilitiesText: stringListFromSnapshot(capabilitySnapshot.operationCapabilities).join(", "),
      runtimeAffinitiesText: stringListFromSnapshot(capabilitySnapshot.runtimeAffinities).join(", "),
      toolExposurePolicy: typeof capabilitySnapshot.toolExposurePolicy === "string" && capabilitySnapshot.toolExposurePolicy.trim() ? capabilitySnapshot.toolExposurePolicy.trim() : "contextual_auto",
      capabilitySnapshotJson: JSON.stringify(capabilitySnapshot, null, 2),
      reflectionEnabled: Boolean(agent.reflection_enabled),
      maxReflections: agent.max_reflections || 3
    });
  }, [defaultModelId]);
  const toggleSelector = useCallback((selector: string, checked: boolean) => {
    setForm(current => ({
      ...current,
      tools: checked ? Array.from(new Set([...current.tools, selector])) : current.tools.filter(item => item !== selector)
    }));
  }, []);
  const toggleToolPanel = useCallback((panel: ToolPanelKey) => {
    setToolPanels(current => ({
      ...current,
      [panel]: !current[panel]
    }));
  }, []);
  const renderToolBadgeSummary = useCallback((selectors: string[]) => {
    const counts = selectors.reduce((acc, selector) => {
      const kind = classifySelector(selector, skillNames, mcpNames, pluginHostNames);
      acc[kind] += 1;
      return acc;
    }, {
      skill: 0,
      mcp: 0,
      plugin_host: 0,
      other: 0
    });
    return <div className="flex flex-wrap gap-2">
                    <Badge variant="secondary">{counts.skill} {t("app.admin.dashboard.subagents.page.k6440d98e")}</Badge>
                    <Badge variant="secondary">{counts.mcp} MCP</Badge>
                    <Badge variant="secondary">{counts.plugin_host} PluginHost</Badge>
                    {counts.other > 0 ? <Badge variant="outline">{counts.other} {t("app.admin.dashboard.subagents.page.kd7875daf")}</Badge> : null}
                </div>;
  }, [mcpNames, pluginHostNames, skillNames, t]);
  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [agentsRes, modelsRes, defaultModelRes, extensionsRes, bridgeRes, supervisorRes, toolSurfaceRes] = await Promise.all([fetch("/api/agents", {
        cache: "no-store"
      }), fetch("/api/models", {
        cache: "no-store"
      }), fetch("/api/settings/default-agent-model", {
        cache: "no-store"
      }), fetch("/api/extensions/catalog", {
        cache: "no-store"
      }), fetch("/api/plugin-host/bridge/tools?limit=24", {
        cache: "no-store"
      }), fetch("/api/config-registry/supervisor", {
        cache: "no-store"
      }), fetch("/api/agents/tool-surface", {
        cache: "no-store"
      })]);
      if (agentsRes.ok) setAgents(await agentsRes.json());
      if (modelsRes.ok) setModels(await modelsRes.json());
      if (defaultModelRes.ok) {
        const data = await defaultModelRes.json();
        setDefaultModelId(String(data.modelId || "").trim());
      }
      if (extensionsRes.ok) {
        const data: ExtensionsCatalogPayload = await extensionsRes.json();
        setSkills(Array.isArray(data.skills?.items) ? data.skills!.items! : []);
        const flattenedMcpTools = Array.isArray(data.mcp?.servers) ? data.mcp!.servers!.flatMap(server => Array.isArray(server.tools) ? server.tools.map(tool => ({
          name: String(tool.name || "").trim(),
          description: String(tool.description || "").trim(),
          serverName: String(server.name || "MCP").trim() || "MCP"
        })) : []).filter(tool => tool.name) : [];
        setMcpTools(flattenedMcpTools);
        setExtensionsSummary({
          mcpServerCount: Number(data.summary?.mcpServerCount || 0) || 0,
          connectedMcpServerCount: Number(data.summary?.connectedMcpServerCount || 0) || 0,
          mcpToolCount: Number(data.summary?.mcpToolCount || 0) || 0
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
        const research = data?.data?.research || {};
        setResearchEnabled(research.enabled !== false);
        setResearchDefaultShards(Math.max(1, Math.min(30, Number(research.defaultShardCount || 10) || 10)));
        setResearchMaxShards(Math.max(1, Math.min(30, Number(research.maxShardCount || 30) || 30)));
        setResearchMaxRounds(Math.max(1, Math.min(5, Number(research.maxRounds || 5) || 5)));
        const recursive = data?.data?.delegation?.recursive || {};
        setRecursiveDelegationEnabled(recursive.enabled !== false);
        setRecursiveMaxDepth(clampInt(recursive.maxDelegationDepth, DEFAULT_RECURSIVE_DELEGATION.maxDelegationDepth, 1, 100));
        setRecursiveMaxChildren(clampInt(recursive.maxChildrenPerDelegation, DEFAULT_RECURSIVE_DELEGATION.maxChildrenPerDelegation, 1, 50));
        setRecursiveMaxTotalNodes(clampInt(recursive.maxTotalDelegationNodes, DEFAULT_RECURSIVE_DELEGATION.maxTotalDelegationNodes, 1, 1000));
        setRecursiveMaxConcurrent(clampInt(recursive.maxConcurrentDelegations, DEFAULT_RECURSIVE_DELEGATION.maxConcurrentDelegations, 1, 50));
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
        variant: "destructive"
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
  const ensureSpecialistFamilyRegistered = useCallback(async (familyId: string, displayName?: string) => {
    const normalizedFamilyId = normalizeFamilyId(familyId);
    const registry = (supervisorDomainData?.data?.specialistRegistry || {}) as Record<string, unknown>;
    const existingFamilies = Array.isArray(registry.families) ? registry.families.map(item => normalizeFamilyEntry(item)).filter((item): item is SubagentFamilySummary => Boolean(item?.familyId)) : [];
    if (existingFamilies.some(family => normalizeFamilyId(family.familyId || family.displayName) === normalizedFamilyId)) {
      return;
    }
    const nextFamilies = [...existingFamilies, {
      familyId: normalizedFamilyId,
      displayName: String(displayName || familyId || normalizedFamilyId).trim() || normalizedFamilyId,
      aliases: [],
      description: ""
    }];
    const response = await fetch("/api/config-registry/supervisor", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        data: {
          ...(supervisorDomainData?.data || {}),
          specialistRegistry: {
            ...registry,
            families: nextFamilies
          }
        }
      })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(String(data?.detail || data?.error || response.status));
    }
    setSupervisorDomainData(data);
  }, [supervisorDomainData]);
  const handleSave = useCallback(async () => {
    if (!form.name.trim()) {
      toast({
        title: t("app.admin.dashboard.subagents.page.k2ba9f8cf"),
        description: t("app.admin.dashboard.subagents.page.kda9e4fc0"),
        variant: "destructive"
      });
      return;
    }
    if (!form.modelId.trim()) {
      toast({
        title: t("app.admin.dashboard.subagents.page.k24a5ad1b"),
        description: t("app.admin.dashboard.subagents.page.ka092e243"),
        variant: "destructive"
      });
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
        variant: "destructive"
      });
      return;
    }
    const specialistFamilyLabel = form.specialistFamily.trim() || "engineering";
    const specialistFamily = normalizeFamilyId(specialistFamilyLabel);
    capabilitySnapshot = {
      ...capabilitySnapshot,
      specialistFamily,
      agentClass: form.agentClass.trim() || "specialist",
      domainTags: splitListText(form.domainTagsText),
      operationCapabilities: splitListText(form.operationCapabilitiesText),
      runtimeAffinities: splitListText(form.runtimeAffinitiesText),
      toolExposurePolicy: form.toolExposurePolicy.trim() || "contextual_auto"
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
        createdBy: editingAgent?.createdBy || "human"
      };
      const url = editingAgent ? `/api/agents/${editingAgent.id}` : "/api/agents";
      const method = editingAgent ? "PUT" : "POST";
      const response = await fetch(url, {
        method,
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(payload)
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(String(data?.detail || data?.error || response.status));
      }
      await ensureSpecialistFamilyRegistered(specialistFamily, specialistFamilyLabel);
      toast({
        title: editingAgent ? t("app.admin.dashboard.subagents.page.kfeb7fab7") : t("app.admin.dashboard.subagents.page.kbd2c49ab"),
        description: form.toolMode === "contextual_auto" ? t("app.admin.dashboard.subagents.page.k6693a150") : t("app.admin.dashboard.subagents.page.kd4ec2786")
      });
      setIsDialogOpen(false);
      setEditingAgent(null);
      await fetchData();
    } catch (error) {
      console.error("Failed to save subagent", error);
      toast({
        title: t("app.admin.dashboard.subagents.page.k12769ce1"),
        description: error instanceof Error ? error.message : t("app.admin.dashboard.subagents.page.ke0d2c647"),
        variant: "destructive"
      });
    } finally {
      setIsSaving(false);
    }
  }, [editingAgent, ensureSpecialistFamilyRegistered, fetchData, form, t, toast]);
  const handleSelectExternalWorker = useCallback((workerId: string) => {
    const worker = externalWorkers.find(item => item.id === workerId);
    if (!worker) return;
    setEditingExternalWorkerId(worker.id);
    setExternalWorkerForm(externalWorkerToForm(worker));
  }, [externalWorkers]);
  const handleStartExternalWorkerTemplate = useCallback((template: "custom" | "claude_code") => {
    const templateId = template === "claude_code" ? "claude-code-worker" : "external-worker";
    const existing = externalWorkers.find(item => item.id === templateId);
    if (existing) {
      handleSelectExternalWorker(existing.id);
      return;
    }
    const baseForm: ExternalWorkerFormState = {
      ...DEFAULT_EXTERNAL_WORKER_FORM,
      id: uniqueWorkerId(templateId, externalWorkers),
      name: template === "claude_code" ? "Claude Code Worker" : "Custom External Worker",
      description: template === "claude_code" ? "Real Claude Code CLI worker for bounded implementation, debugging, review, or verification tasks." : "",
      workerType: template,
      commandTemplate: template === "claude_code" ? CLAUDE_CODE_COMMAND_TEMPLATE : "",
      sessionMode: template === "claude_code" ? "print" : DEFAULT_EXTERNAL_WORKER_FORM.sessionMode,
      domainTagsText: template === "claude_code" ? "software_engineering, implementation, debugging, code_review" : "",
      operationCapabilitiesText: template === "claude_code" ? "implement, debug, review, verify" : "",
      runtimeAffinitiesText: template === "claude_code" ? "chat, command_session, claude_code" : DEFAULT_EXTERNAL_WORKER_FORM.runtimeAffinitiesText,
      allowedSideEffectsText: template === "claude_code" ? "workspace_write, tool_use, long_running_cli" : ""
    };
    setEditingExternalWorkerId("");
    setExternalWorkerForm(baseForm);
  }, [externalWorkers, handleSelectExternalWorker]);
  const handleApplyExternalWorkerForm = useCallback(() => {
    const descriptor = externalWorkerFormToDescriptor(externalWorkerForm);
    if (!descriptor.id) {
      toast({
        title: tg(t, "229f534d"),
        description: tg(t, "938eda35"),
        variant: "destructive"
      });
      return;
    }
    const nextWorkers = externalWorkers.some(item => item.id === editingExternalWorkerId || item.id === descriptor.id) ? externalWorkers.map(item => item.id === editingExternalWorkerId || item.id === descriptor.id ? descriptor : item) : [...externalWorkers, descriptor];
    syncExternalWorkers(nextWorkers);
    setEditingExternalWorkerId(descriptor.id);
  }, [editingExternalWorkerId, externalWorkerForm, externalWorkers, syncExternalWorkers, t, toast]);
  const handleDeleteExternalWorker = useCallback((workerId: string) => {
    syncExternalWorkers(externalWorkers.filter(item => item.id !== workerId));
  }, [externalWorkers, syncExternalWorkers]);
  const handleApplyExternalWorkersJson = useCallback(() => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(externalWorkersJson || "[]");
    } catch (error) {
      toast({
        title: t("app.admin.dashboard.subagents.page.externalWorkers.invalidJsonTitle"),
        description: error instanceof Error ? error.message : t("app.admin.dashboard.subagents.page.externalWorkers.invalidJsonDescription"),
        variant: "destructive"
      });
      return;
    }
    if (!Array.isArray(parsed)) {
      toast({
        title: t("app.admin.dashboard.subagents.page.externalWorkers.arrayJsonTitle"),
        description: t("app.admin.dashboard.subagents.page.externalWorkers.arrayJsonDescription"),
        variant: "destructive"
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
          variant: "destructive"
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
            externalWorkers: workersToSave
          }
        }
      };
      const response = await fetch("/api/config-registry/supervisor", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(nextPayload)
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(String(data?.detail || data?.error || response.status));
      }
      setSupervisorDomainData(data);
      syncExternalWorkers(data?.data?.delegation?.externalWorkers);
      toast({
        title: t("app.admin.dashboard.subagents.page.externalWorkers.savedTitle"),
        description: t("app.admin.dashboard.subagents.page.externalWorkers.savedDescription")
      });
    } catch (error) {
      console.error("Failed to save external workers", error);
      toast({
        title: t("app.admin.dashboard.subagents.page.externalWorkers.saveFailedTitle"),
        description: error instanceof Error ? error.message : t("app.admin.dashboard.subagents.page.externalWorkers.unknownError"),
        variant: "destructive"
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
            maxMembersPerFamily: Math.max(1, Math.min(MAX_SPECIALIST_FAMILY_MEMBERS, maxMembersPerFamily))
          }
        }
      };
      const response = await fetch("/api/config-registry/supervisor", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(nextPayload)
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
        title: tg(t, "cfa3e507"),
        description: registry.familyModeEnabled === false ? tg(t, "c64065eb") : tg(t, "1660f7ec")
      });
    } catch (error) {
      console.error("Failed to save specialist registry config", error);
      toast({
        title: tg(t, "7982f619"),
        description: error instanceof Error ? error.message : t("app.admin.dashboard.subagents.page.externalWorkers.unknownError"),
        variant: "destructive"
      });
    } finally {
      setIsSavingSpecialistRegistry(false);
    }
  }, [familyModeEnabled, maxMembersPerFamily, supervisorDomainData, t, toast]);
  const handleSaveResearchConfig = useCallback(async () => {
    setIsSavingResearch(true);
    const nextDefault = Math.max(1, Math.min(30, Math.round(researchDefaultShards)));
    const nextMax = Math.max(nextDefault, Math.min(30, Math.round(researchMaxShards)));
    const nextRounds = Math.max(1, Math.min(5, Math.round(researchMaxRounds)));
    try {
      const nextPayload = {
        data: {
          ...(supervisorDomainData?.data || {}),
          research: {
            ...((supervisorDomainData?.data?.research || {}) as Record<string, unknown>),
            enabled: researchEnabled,
            defaultShardCount: nextDefault,
            maxShardCount: nextMax,
            maxRounds: nextRounds
          }
        }
      };
      const response = await fetch("/api/config-registry/supervisor", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(nextPayload)
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(String(data?.detail || data?.error || response.status));
      }
      setSupervisorDomainData(data);
      const research = data?.data?.research || {};
      setResearchEnabled(research.enabled !== false);
      setResearchDefaultShards(Math.max(1, Math.min(30, Number(research.defaultShardCount || nextDefault) || nextDefault)));
      setResearchMaxShards(Math.max(1, Math.min(30, Number(research.maxShardCount || nextMax) || nextMax)));
      setResearchMaxRounds(Math.max(1, Math.min(5, Number(research.maxRounds || nextRounds) || nextRounds)));
      toast({
        title: tg(t, "a331007b"),
        description: tg(t, "f2bbe54c")
      });
    } catch (error) {
      console.error("Failed to save research config", error);
      toast({
        title: tg(t, "d6d079f9"),
        description: error instanceof Error ? error.message : t("app.admin.dashboard.subagents.page.externalWorkers.unknownError"),
        variant: "destructive"
      });
    } finally {
      setIsSavingResearch(false);
    }
  }, [researchDefaultShards, researchEnabled, researchMaxRounds, researchMaxShards, supervisorDomainData, t, toast]);
  const handleSaveRecursiveDelegationConfig = useCallback(async () => {
    setIsSavingRecursiveDelegation(true);
    const nextDepth = clampInt(recursiveMaxDepth, DEFAULT_RECURSIVE_DELEGATION.maxDelegationDepth, 1, 100);
    const nextChildren = clampInt(recursiveMaxChildren, DEFAULT_RECURSIVE_DELEGATION.maxChildrenPerDelegation, 1, 50);
    const nextTotal = clampInt(recursiveMaxTotalNodes, DEFAULT_RECURSIVE_DELEGATION.maxTotalDelegationNodes, 1, 1000);
    const nextConcurrent = clampInt(recursiveMaxConcurrent, DEFAULT_RECURSIVE_DELEGATION.maxConcurrentDelegations, 1, 50);
    try {
      const nextPayload = {
        data: {
          ...(supervisorDomainData?.data || {}),
          delegation: {
            ...((supervisorDomainData?.data?.delegation || {}) as Record<string, unknown>),
            recursive: {
              ...((supervisorDomainData?.data?.delegation?.recursive || {}) as Record<string, unknown>),
              enabled: recursiveDelegationEnabled,
              maxDelegationDepth: nextDepth,
              maxChildrenPerDelegation: nextChildren,
              maxTotalDelegationNodes: nextTotal,
              maxConcurrentDelegations: nextConcurrent
            }
          }
        }
      };
      const response = await fetch("/api/config-registry/supervisor", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(nextPayload)
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(String(data?.detail || data?.error || response.status));
      }
      setSupervisorDomainData(data);
      const recursive = data?.data?.delegation?.recursive || {};
      setRecursiveDelegationEnabled(recursive.enabled !== false);
      setRecursiveMaxDepth(clampInt(recursive.maxDelegationDepth, nextDepth, 1, 100));
      setRecursiveMaxChildren(clampInt(recursive.maxChildrenPerDelegation, nextChildren, 1, 50));
      setRecursiveMaxTotalNodes(clampInt(recursive.maxTotalDelegationNodes, nextTotal, 1, 1000));
      setRecursiveMaxConcurrent(clampInt(recursive.maxConcurrentDelegations, nextConcurrent, 1, 50));
      toast({
        title: t("admin.pages.subagents.recursive.savedTitle"),
        description: t("admin.pages.subagents.recursive.savedDescription")
      });
    } catch (error) {
      console.error("Failed to save recursive delegation config", error);
      toast({
        title: t("admin.pages.subagents.recursive.saveFailedTitle"),
        description: error instanceof Error ? error.message : t("app.admin.dashboard.subagents.page.externalWorkers.unknownError"),
        variant: "destructive"
      });
    } finally {
      setIsSavingRecursiveDelegation(false);
    }
  }, [recursiveDelegationEnabled, recursiveMaxChildren, recursiveMaxConcurrent, recursiveMaxDepth, recursiveMaxTotalNodes, supervisorDomainData, t, toast]);
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
              ...((supervisorDomainData?.data?.modelParameters?.subagent || {}) as Record<string, unknown>),
              temperature: parsedTemperature
            }
          }
        }
      };
      const response = await fetch("/api/config-registry/supervisor", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(nextPayload)
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(String(data?.detail || data?.error || response.status));
      }
      setSupervisorDomainData(data);
      const temperature = data?.data?.modelParameters?.subagent?.temperature;
      setSubagentTemperature(temperature === null || temperature === undefined ? "" : String(temperature));
      toast({
        title: tg(t, "15a67709"),
        description: tg(t, "0a7919e7")
      });
    } catch (error) {
      console.error("Failed to save subagent temperature", error);
      toast({
        title: tg(t, "e315020e"),
        description: error instanceof Error ? error.message : t("app.admin.dashboard.subagents.page.externalWorkers.unknownError"),
        variant: "destructive"
      });
    } finally {
      setIsSavingSubagentTemperature(false);
    }
  }, [subagentTemperature, supervisorDomainData, t, toast]);
  const handleDelete = useCallback(async (id: string) => {
    if (!confirm(t("app.admin.dashboard.subagents.page.ka7d365b9"))) return;
    try {
      const response = await fetch(`/api/agents/${id}`, {
        method: "DELETE"
      });
      if (!response.ok) {
        throw new Error(String(response.status));
      }
      toast({
        title: t("app.admin.dashboard.subagents.page.k1b2c89e7")
      });
      await fetchData();
    } catch (error) {
      console.error("Failed to delete subagent", error);
      toast({
        title: t("app.admin.dashboard.subagents.page.k0915ccdf"),
        description: t("app.admin.dashboard.subagents.page.k5d01859a"),
        variant: "destructive"
      });
    }
  }, [fetchData, t, toast]);
  return <div className="mx-auto max-w-[1600px] w-full space-y-8 p-6 lg:p-8">
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
                    <Button onClick={() => {
                        setEditingAgent(null);
                        setIsDialogOpen(true);
                        resetForm(null);
                    }}>
                        <Plus className="mr-2 h-4 w-4" />
                        {t("app.admin.dashboard.subagents.page.k5ae562aa")}
                    </Button>
                </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-[1fr_400px] xl:grid-cols-[1fr_450px] gap-6 items-start">
                {/* 左栏：指标看板、Specialists 网格与 External Workers 面板 */}
                <div className="space-y-6">
                    {/* 数据统计看板 */}
                    <div className="grid gap-4 grid-cols-2 xl:grid-cols-4">
                        <Card className="h-28 overflow-visible rounded-2xl border-slate-200 bg-white/95 shadow-sm">
                            <CardHeader className="space-y-1 p-3 pb-1">
                                <StatusCardTitle icon={<ShieldCheck className="h-4 w-4 shrink-0 text-sky-600" />} title={t("app.admin.dashboard.subagents.page.k00bf2013")} tooltip={<div>
                                            <div>{tg(t, "47f887c9")}: {baselineToolNames.length}</div>
                                            <div className="mt-1 break-words font-mono text-xs text-slate-200">
                                                {baselineToolNames.slice(0, 12).join(", ") || "none"}
                                            </div>
                                        </div>} />
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
                                <StatusCardTitle icon={<Sparkles className="h-4 w-4 shrink-0 text-violet-600" />} title={t("app.admin.dashboard.subagents.page.k9764402c")} tooltip={<div>
                                            <div>Skills: {skills.length}</div>
                                            <div>MCP servers: {connectedMcpServiceCount}/{mcpServiceCount}</div>
                                            <div>MCP tools: {availableMcpToolCount}</div>
                                            <div>PluginHost tools: {pluginHostTools.length}</div>
                                        </div>} />
                                <CardDescription className="truncate text-xs">{t("app.admin.dashboard.subagents.page.k90999eb9")}</CardDescription>
                            </CardHeader>
                            <CardContent className="grid grid-cols-2 gap-x-3 gap-y-1 px-3 pb-3 text-xs text-slate-500">
                                <div>Skills <span className="font-medium text-slate-900">{skills.length}</span></div>
                                <div>MCP <span className="font-medium text-slate-900">{connectedMcpServiceCount}/{mcpServiceCount}</span></div>
                            </CardContent>
                        </Card>
                        <Card className="h-28 overflow-visible rounded-2xl border-slate-200 bg-white/95 shadow-sm">
                            <CardHeader className="space-y-1 p-3 pb-1">
                                <StatusCardTitle icon={<Cable className="h-4 w-4 shrink-0 text-emerald-600" />} title={t("app.admin.dashboard.subagents.page.k11cd990c")} tooltip={<div>
                                            <div>Broker: fan-out / join</div>
                                            <div>{tg(t, "b109c831")}</div>
                                            <div>{tg(t, "495fdf53")}: {enabledSubagentCount}</div>
                                            <div>{tg(t, "fb1073a6")}: {enabledExternalWorkerCount} / {externalWorkerTemplateCount}</div>
                                            <div className="mt-1 text-xs text-slate-300">
                                                {tg(t, "0159e367")}
                                            </div>
                                        </div>} />
                                <CardDescription className="truncate text-xs">
                                    {tg(t, "1940e65b")}
                                </CardDescription>
                            </CardHeader>
                            <CardContent className="space-y-1 px-3 pb-3 text-xs text-slate-500">
                                <div><span className="font-medium text-slate-900">fan-out / join</span></div>
                                <div>{t("components.runtime.RecentRunsPanel.kbad18df9")} <span className="font-medium text-slate-900">{enabledSubagentCount} local / {enabledExternalWorkerCount} remote</span></div>
                            </CardContent>
                        </Card>
                        <Card className="h-28 overflow-visible rounded-2xl border-slate-200 bg-white/95 shadow-sm">
                            <CardHeader className="space-y-1 p-3 pb-1">
                                <StatusCardTitle icon={<BrainCircuit className="h-4 w-4 shrink-0 text-indigo-600" />} title={tg(t, "520dbe37")} tooltip={<div>
                                            <div>{tg(t, "8c3999bf")}: {formatDecimal(TEMPERATURE_PRESET)}</div>
                                            <div>{tg(t, "a9f873a8")}: {subagentTemperature.trim() ? formatDecimal(temperatureSliderValue(subagentTemperature)) : temperatureDefaultText(t)}</div>
                                            <div className="mt-1 text-xs text-slate-300">
                                                {tg(t, "00f4e417")}
                                            </div>
                                        </div>} />
                                <CardDescription className="truncate text-xs">{temperatureStatusText(t, subagentTemperature)}</CardDescription>
                            </CardHeader>
                            <CardContent className="space-y-2 px-3 pb-3">
                                <Slider value={[temperatureSliderValue(subagentTemperature)]} min={MIN_CONFIG_TEMPERATURE} max={2} step={0.05} onValueChange={([value]) => setSubagentTemperature(formatDecimal(value))} />
                                <div className="flex items-center justify-between gap-2">
                                    <Button size="sm" variant="outline" className="h-7 px-2 text-xs" onClick={() => setSubagentTemperature("")}>
                                        {t("components.memory.MemoryConfigPanel.k5e4b837d")}
                                    </Button>
                                    <Button size="sm" className="h-7 px-2 text-xs" onClick={() => void handleSaveSubagentTemperature()} disabled={isSavingSubagentTemperature}>
                                        {isSavingSubagentTemperature ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Save className="mr-1 h-3 w-3" />}
                                        {t("components.memory.MemoryWorkflowsPanel.save")}
                                    </Button>
                                </div>
                            </CardContent>
                        </Card>
                    </div>

                    {/* Specialists 列表网格 */}
                    <div className="grid gap-6 md:grid-cols-1 xl:grid-cols-2">
                        {agents.map(agent => {
                            const selectors = Array.isArray(agent.tools) ? agent.tools : [];
                            const toolMode = agent.tool_mode === "explicit" ? "explicit" : "contextual_auto";
                            const capabilitySnapshot = agent.capabilitySnapshot && typeof agent.capabilitySnapshot === "object" && !Array.isArray(agent.capabilitySnapshot) ? agent.capabilitySnapshot : {};
                            const agentClass = typeof capabilitySnapshot.agentClass === "string" ? capabilitySnapshot.agentClass : "";
                            const specialistFamily = typeof capabilitySnapshot.specialistFamily === "string" ? capabilitySnapshot.specialistFamily : "";
                            const domainTags = Array.isArray(capabilitySnapshot.domainTags) ? capabilitySnapshot.domainTags.filter((item): item is string => typeof item === "string").slice(0, 3) : [];
                            const familyKey = normalizeFamilyId(specialistFamily || "engineering");
                            const avatarStyle = agent.globalExposure ? GLOBAL_AVATAR_STYLE : familyColorMap[familyKey] || FAMILY_AVATAR_COLORS[0];
                            const avatarLabel = agent.globalExposure ? "G" : firstGrapheme(specialistFamily || "engineering", "E");
                            return <Card key={agent.id} className="rounded-3xl border-slate-200 bg-white/95 shadow-sm">
                                        <CardHeader className="space-y-4">
                                            <div className="flex items-start justify-between gap-3">
                                                <div className="flex min-w-0 items-center gap-3">
                                                    <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border text-lg font-bold" style={avatarStyle} title={agent.globalExposure ? "globalExposure" : `family:${familyKey}`}>
                                                        {avatarLabel}
                                                    </div>
                                                    <div className="min-w-0">
                                                        <CardTitle className="truncate text-lg">{agent.name}</CardTitle>
                                                        <CardDescription className="truncate">{resolveAgentModelDisplay(agent)}</CardDescription>
                                                    </div>
                                                </div>
                                                <div className="flex gap-1">
                                                    <Button type="button" variant="ghost" size="sm" onClick={() => {
                                                        setEditingAgent(agent);
                                                        setIsDialogOpen(true);
                                                    }}>
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
                                                {domainTags.map(tag => <Badge key={tag} variant="outline">{tag}</Badge>)}
                                                {agent.createdBy === "supervisor" ? <Badge className="bg-indigo-600 hover:bg-indigo-600">{t("app.admin.dashboard.subagents.page.kcec0f2f4")}</Badge> : null}
                                                {agent.reflection_enabled ? <Badge variant="outline">{t("app.admin.dashboard.subagents.page.k1599cdff")} × {agent.max_reflections || 3}</Badge> : null}
                                            </div>
                                        </CardHeader>
                                        <CardContent className="space-y-4">
                                            <p className="min-h-[3rem] text-sm leading-6 text-slate-500">{agent.description || t("app.admin.dashboard.subagents.page.k70eaab39")}</p>
                                            {toolMode === "explicit" ? <div className="space-y-2">
                                                    <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{t("app.admin.dashboard.subagents.page.k1dc6b253")}</div>
                                                    {renderToolBadgeSummary(selectors)}
                                                </div> : <div className="space-y-2 rounded-2xl border border-slate-200 bg-slate-50/70 p-3 text-xs leading-5 text-slate-500">
                                                    <div className="font-medium text-slate-900">{resolveToolModeLabel("contextual_auto")}</div>
                                                    <div>{t("app.admin.dashboard.subagents.page.kf913a2e6")}</div>
                                                </div>}
                                        </CardContent>
                                    </Card>;
                        })}
                        {agents.length === 0 ? <div className="col-span-full rounded-3xl border border-dashed border-slate-200 bg-slate-50/80 py-12 text-center text-sm text-slate-500">
                                    {t("app.admin.dashboard.subagents.page.kc6380706")}
                                </div> : null}
                    </div>

                    {/* External Workers 工人配置 Card */}
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
                                        {t("app.admin.dashboard.model.hub.catalog.customSuffix")}
                                    </Button>
                                </div>
                                <Badge variant="secondary">
                                    {tg(t, "ac5c1f76")} {enabledExternalWorkerCount}/{externalWorkerTemplateCount}
                                </Badge>
                            </div>

                            <div className="grid gap-5 lg:grid-cols-[minmax(260px,0.9fr)_minmax(0,1.35fr)]">
                                <div className="space-y-3">
                                    {externalWorkers.length === 0 ? <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/80 p-5 text-sm text-slate-500">
                                            {tg(t, "bbb79e84")}
                                        </div> : null}
                                    {externalWorkers.map(worker => {
                                        const isActive = worker.id === editingExternalWorkerId;
                                        const isEnabledTarget = Boolean(worker.enabled && worker.launchProfile.commandTemplate.trim());
                                        return <button key={worker.id} type="button" className={`w-full rounded-2xl border p-4 text-left transition ${isActive ? "border-emerald-400 bg-emerald-50/70" : "border-slate-200 bg-slate-50/70 hover:border-slate-300"}`} onClick={() => handleSelectExternalWorker(worker.id)}>
                                                <div className="flex items-start justify-between gap-3">
                                                    <div className="min-w-0">
                                                        <div className="truncate text-sm font-semibold text-slate-950">{worker.name || worker.id}</div>
                                                        <div className="mt-1 truncate font-mono text-xs text-slate-500">{worker.id}</div>
                                                    </div>
                                                    <Badge variant={isEnabledTarget ? "default" : "secondary"}>
                                                        {isEnabledTarget ? t("app.admin.dashboard.engineeringLane.enabledState") : tg(t, "06d0f38d")}
                                                    </Badge>
                                                </div>
                                                <div className="mt-3 text-xs leading-5 text-slate-500">
                                                    {resolveAdminLabel(t, "workerType", worker.workerType || "custom")} · {resolveAdminLabel(t, "workerCwdPolicy", worker.launchProfile.cwdPolicy || "inherit_workspace")} · {resolveAdminLabel(t, "workerSessionMode", worker.sessionMode || "interactive")}
                                                </div>
                                            </button>;
                                    })}
                                </div>

                                <div className="space-y-4 rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                                    <div className="grid gap-3 md:grid-cols-4">
                                        <div className="rounded-2xl border border-slate-200 bg-white/80 p-3">
                                            <StatusCardTitle icon={<Cable className="h-4 w-4 shrink-0 text-emerald-600" />} title={tg(t, "8263f1ae")} tooltip={tg(t, "b70ead00")} />
                                            <div className="mt-2 truncate text-xs text-slate-500">{resolveAdminLabel(t, "workerType", externalWorkerForm.workerType || "custom")}</div>
                                        </div>
                                        <div className="rounded-2xl border border-slate-200 bg-white/80 p-3">
                                            <StatusCardTitle icon={<BrainCircuit className="h-4 w-4 shrink-0 text-indigo-600" />} title={tg(t, "f17bef17")} tooltip={tg(t, "4a77fff3")} />
                                            <div className="mt-2 truncate text-xs text-slate-500">{externalWorkerForm.agentClass || "external_worker"}</div>
                                        </div>
                                        <div className="rounded-2xl border border-slate-200 bg-white/80 p-3">
                                            <StatusCardTitle icon={<ShieldCheck className="h-4 w-4 shrink-0 text-sky-600" />} title={t("app.admin.dashboard.automation.cron.page.k3936c4f6")} tooltip={tg(t, "77b8a673")} />
                                            <div className="mt-2 truncate text-xs text-slate-500">{externalWorkerForm.enabled ? t("app.admin.dashboard.engineeringLane.enabledState") : tg(t, "cfb6d117")}</div>
                                        </div>
                                        <div className="rounded-2xl border border-slate-200 bg-white/80 p-3">
                                            <StatusCardTitle icon={<Wrench className="h-4 w-4 shrink-0 text-slate-600" />} title={tg(t, "6dac0d10")} tooltip={tg(t, "e1efa4b4")} />
                                            <div className="mt-2 truncate text-xs text-slate-500">V8_WORKER_RESULT</div>
                                        </div>
                                    </div>

                                    <div className="grid gap-4 md:grid-cols-2">
                                        <div className="space-y-2">
                                            <WorkerConfigLabel label={t("app.admin.dashboard.creativeMedia.tableName")} tooltip={tg(t, "c94b6fbd")} />
                                            <Input value={externalWorkerForm.name} onChange={event => setExternalWorkerForm(current => ({
                                                ...current,
                                                name: event.target.value
                                            }))} placeholder="Claude Code Worker" />
                                        </div>
                                        <div className="space-y-2">
                                            <WorkerConfigLabel label={tg(t, "93b2f9c3")} tooltip={tg(t, "be1620ca")} />
                                            <Input value={externalWorkerForm.agentClass} onChange={event => setExternalWorkerForm(current => ({
                                                ...current,
                                                agentClass: event.target.value
                                            }))} placeholder="coder / reviewer / writer" />
                                        </div>
                                        <div className="space-y-2 md:col-span-2">
                                            <WorkerConfigLabel label={tg(t, "16536cdf")} tooltip={tg(t, "4cc8846a")} />
                                            <Input value={externalWorkerForm.description} onChange={event => setExternalWorkerForm(current => ({
                                                ...current,
                                                description: event.target.value
                                            }))} placeholder={tg(t, "052ddc5f")} />
                                        </div>
                                        <div className="space-y-2">
                                            <WorkerConfigLabel label={tg(t, "49aa0a72")} tooltip={tg(t, "20fb46f5")} />
                                            <Input value={externalWorkerForm.domainTagsText} onChange={event => setExternalWorkerForm(current => ({
                                                ...current,
                                                domainTagsText: event.target.value
                                            }))} placeholder="software_engineering, code_review" />
                                        </div>
                                        <div className="space-y-2">
                                            <WorkerConfigLabel label={tg(t, "396f27fd")} tooltip={tg(t, "cf125faf")} />
                                            <Input value={externalWorkerForm.operationCapabilitiesText} onChange={event => setExternalWorkerForm(current => ({
                                                ...current,
                                                operationCapabilitiesText: event.target.value
                                            }))} placeholder="implement, debug, review, verify" />
                                        </div>
                                    </div>

                                    <details className="rounded-2xl border border-slate-200 bg-white/80 p-3">
                                        <summary className="cursor-pointer text-sm font-medium text-slate-900">
                                            {tg(t, "976a39ef")}
                                        </summary>
                                        <div className="mt-4 grid gap-4 md:grid-cols-2">
                                            <div className="space-y-2">
                                                <WorkerConfigLabel label="Worker ID" tooltip={tg(t, "02087023")} />
                                                <Input value={externalWorkerForm.id} onChange={event => setExternalWorkerForm(current => ({
                                                    ...current,
                                                    id: event.target.value
                                                }))} placeholder="claude-code-worker" />
                                            </div>
                                            <div className="space-y-2">
                                                <WorkerConfigLabel label={tg(t, "c7209605")} tooltip={tg(t, "db37b9b6")} />
                                                <Select value={externalWorkerForm.workerType} onValueChange={value => setExternalWorkerForm(current => ({
                                                    ...current,
                                                    workerType: value
                                                }))}>
                                                    <SelectTrigger><SelectValue /></SelectTrigger>
                                                    <SelectContent>
                                                        {!getAdminOptions("workerType").some((option) => option.value === externalWorkerForm.workerType) && externalWorkerForm.workerType ? <SelectItem value={externalWorkerForm.workerType}>{resolveAdminLabel(t, "workerType", externalWorkerForm.workerType)}</SelectItem> : null}
                                                        {getAdminOptions("workerType").map((option) => <SelectItem key={option.value} value={option.value}>{t(option.labelKey)}</SelectItem>)}
                                                    </SelectContent>
                                                </Select>
                                            </div>
                                            <div className="space-y-2 md:col-span-2">
                                                <WorkerConfigLabel label={tg(t, "3ce5c87e")} tooltip={tg(t, "734fe4f1", { task_brief_b64: "{task_brief_b64}" })} />
                                                <Textarea value={externalWorkerForm.commandTemplate} onChange={event => setExternalWorkerForm(current => ({
                                                    ...current,
                                                    commandTemplate: event.target.value
                                                }))} className="min-h-[84px] font-mono text-xs" placeholder='claude -p "... {task_brief_b64} ..."' />
                                            </div>
                                            <div className="space-y-2">
                                                <WorkerConfigLabel label={tg(t, "5a843790")} tooltip={tg(t, "51924d82")} />
                                                <Select value={externalWorkerForm.cwdPolicy} onValueChange={value => setExternalWorkerForm(current => ({
                                                    ...current,
                                                    cwdPolicy: value
                                                }))}>
                                                    <SelectTrigger><SelectValue /></SelectTrigger>
                                                    <SelectContent>
                                                        {!getAdminOptions("workerCwdPolicy").some((option) => option.value === externalWorkerForm.cwdPolicy) && externalWorkerForm.cwdPolicy ? <SelectItem value={externalWorkerForm.cwdPolicy}>{resolveAdminLabel(t, "workerCwdPolicy", externalWorkerForm.cwdPolicy)}</SelectItem> : null}
                                                        {getAdminOptions("workerCwdPolicy").map((option) => <SelectItem key={option.value} value={option.value}>{t(option.labelKey)}</SelectItem>)}
                                                    </SelectContent>
                                                </Select>
                                            </div>
                                            <div className="space-y-2">
                                                <WorkerConfigLabel label={tg(t, "7764f97e")} tooltip={tg(t, "ccd23c9d")} />
                                                <Input type="number" min={3} max={120} value={externalWorkerForm.startupTimeoutSeconds} onChange={event => setExternalWorkerForm(current => ({
                                                    ...current,
                                                    startupTimeoutSeconds: event.target.value
                                                }))} />
                                            </div>
                                            <div className="space-y-2">
                                                <WorkerConfigLabel label={tg(t, "9633e050")} tooltip={tg(t, "7dc5d6fa")} />
                                                <Select value={externalWorkerForm.sessionMode} onValueChange={value => setExternalWorkerForm(current => ({
                                                    ...current,
                                                    sessionMode: value
                                                }))}>
                                                    <SelectTrigger><SelectValue /></SelectTrigger>
                                                    <SelectContent>
                                                        {!getAdminOptions("workerSessionMode").some((option) => option.value === externalWorkerForm.sessionMode) && externalWorkerForm.sessionMode ? <SelectItem value={externalWorkerForm.sessionMode}>{resolveAdminLabel(t, "workerSessionMode", externalWorkerForm.sessionMode)}</SelectItem> : null}
                                                        {getAdminOptions("workerSessionMode").map((option) => <SelectItem key={option.value} value={option.value}>{t(option.labelKey)}</SelectItem>)}
                                                    </SelectContent>
                                                </Select>
                                            </div>
                                            <div className="space-y-2">
                                                <WorkerConfigLabel label={tg(t, "5f90878e")} tooltip={tg(t, "fc1f4a2f")} />
                                                <Input value={externalWorkerForm.envPassThroughText} onChange={event => setExternalWorkerForm(current => ({
                                                    ...current,
                                                    envPassThroughText: event.target.value
                                                }))} placeholder="PATH, HOME" />
                                            </div>
                                            <div className="space-y-2">
                                                <WorkerConfigLabel
                                                    label={tg(t, "7b11bd2b")}
                                                    tooltip={
                                                        <div className="space-y-1">
                                                            <div>{tg(t, "761aca19")}</div>
                                                            <div className="mt-2 font-semibold">Supported side effects / 支持的副作用:</div>
                                                            <ul className="list-disc pl-4 text-xs">
                                                                <li>{t("app.admin.dashboard.subagents.page.externalWorkers.sideEffects.workspace_write")}</li>
                                                                <li>{t("app.admin.dashboard.subagents.page.externalWorkers.sideEffects.tool_use")}</li>
                                                                <li>{t("app.admin.dashboard.subagents.page.externalWorkers.sideEffects.long_running_cli")}</li>
                                                                <li>{t("app.admin.dashboard.subagents.page.externalWorkers.sideEffects.shell_command")}</li>
                                                                <li>{t("app.admin.dashboard.subagents.page.externalWorkers.sideEffects.network_request")}</li>
                                                            </ul>
                                                        </div>
                                                    }
                                                />
                                                <Input value={externalWorkerForm.allowedSideEffectsText} onChange={event => setExternalWorkerForm(current => ({
                                                    ...current,
                                                    allowedSideEffectsText: event.target.value
                                                }))} placeholder="workspace_write, tool_use" />
                                            </div>
                                            <div className="space-y-2">
                                                <WorkerConfigLabel label={tg(t, "9d1f7d68")} tooltip={tg(t, "9b2ec4bf")} />
                                                <Select value={externalWorkerForm.toolExposurePolicy} onValueChange={value => setExternalWorkerForm(current => ({
                                                    ...current,
                                                    toolExposurePolicy: value
                                                }))}>
                                                    <SelectTrigger><SelectValue /></SelectTrigger>
                                                    <SelectContent>
                                                        {!getAdminOptions("toolExposurePolicy").some((option) => option.value === externalWorkerForm.toolExposurePolicy) && externalWorkerForm.toolExposurePolicy ? <SelectItem value={externalWorkerForm.toolExposurePolicy}>{resolveAdminLabel(t, "toolExposurePolicy", externalWorkerForm.toolExposurePolicy)}</SelectItem> : null}
                                                        {getAdminOptions("toolExposurePolicy").map((option) => <SelectItem key={option.value} value={option.value}>{t(option.labelKey)}</SelectItem>)}
                                                    </SelectContent>
                                                </Select>
                                            </div>
                                            <div className="space-y-2">
                                                <WorkerConfigLabel label={tg(t, "3a3a388f")} tooltip={tg(t, "abc3953d")} />
                                                <Input value={externalWorkerForm.runtimeAffinitiesText} onChange={event => setExternalWorkerForm(current => ({
                                                    ...current,
                                                    runtimeAffinitiesText: event.target.value
                                                }))} placeholder="chat, command_session" />
                                            </div>
                                            <div className="space-y-2 md:col-span-2">
                                                <WorkerConfigLabel label={tg(t, "8f472ae1")} tooltip={tg(t, "63d27c83")} />
                                                <Input value={externalWorkerForm.resultMarkersText} onChange={event => setExternalWorkerForm(current => ({
                                                    ...current,
                                                    resultMarkersText: event.target.value
                                                }))} placeholder="<V8_WORKER_RESULT>, </V8_WORKER_RESULT>" />
                                            </div>
                                        </div>
                                    </details>

                                    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 pt-4">
                                        <label className="flex items-center gap-3 text-sm font-medium text-slate-900">
                                            <Checkbox checked={externalWorkerForm.enabled} onCheckedChange={next => setExternalWorkerForm(current => ({
                                                ...current,
                                                enabled: Boolean(next)
                                            }))} />
                                            {tg(t, "dd0b58a6")}
                                        </label>
                                        <div className="flex gap-2">
                                            {editingExternalWorkerId ? <Button type="button" variant="ghost" className="text-rose-600" onClick={() => handleDeleteExternalWorker(editingExternalWorkerId)}>
                                                    <Trash2 className="mr-2 h-4 w-4" />
                                                    {t("components.memory.MemoryWorkflowsPanel.delete")}
                                                </Button> : null}
                                            <Button type="button" variant="outline" onClick={handleApplyExternalWorkerForm}>
                                                {tg(t, "2fb9af69")}
                                            </Button>
                                        </div>
                                    </div>
                                </div>
                            </div>

                            <details className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4" open={showExternalWorkersJson} onToggle={event => setShowExternalWorkersJson(event.currentTarget.open)}>
                                <summary className="cursor-pointer text-sm font-medium text-slate-900">
                                    {tg(t, "30b77df9")}
                                </summary>
                                <Textarea value={externalWorkersJson} onChange={event => setExternalWorkersJson(event.target.value)} className="mt-3 min-h-[220px] font-mono text-xs" placeholder='[{"id":"coding-cli-worker","enabled":false}]' />
                                <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                                    <p className="text-xs leading-5 text-slate-500">
                                        {t("app.admin.dashboard.subagents.page.externalWorkers.hintPrefix")} <code>launchProfile.commandTemplate</code> {t("app.admin.dashboard.subagents.page.externalWorkers.hintMiddle")} <code>resultSchema.markers</code>.
                                    </p>
                                    <Button type="button" variant="outline" size="sm" onClick={handleApplyExternalWorkersJson}>
                                        {tg(t, "7780539e")}
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
                </div>

                {/* 右栏：全局委派与研究参数配置 */}
                <div className="space-y-6">
                    {/* 1. Specialist Registry 家族配置 Card */}
                    <Card className="rounded-2xl border-slate-200 bg-white/95 shadow-sm">
                        <CardHeader className="space-y-1 p-4 pb-2 border-b">
                            <CardTitle className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                                <BrainCircuit className="h-4 w-4 text-indigo-600" />
                                {tg(t, "cd5d78a6")}
                                <Badge variant={familyModeEnabled ? "secondary" : "destructive"}>{familyModeEnabled ? "compact" : "full"}</Badge>
                            </CardTitle>
                            <p className="text-[11px] leading-relaxed text-slate-500">
                                {familyModeEnabled ? tg(t, "d096101e") : tg(t, "571f4a11")}
                            </p>
                        </CardHeader>
                        <CardContent className="p-4 space-y-4">
                            <div className="space-y-2">
                                <SettingToggleCard
                                    title={<span>{tg(t, "7074eefa")}：{maxMembersPerFamily}</span>}
                                    checked={familyModeEnabled}
                                    onCheckedChange={setFamilyModeEnabled}
                                    className="border-none bg-transparent hover:bg-transparent p-0 shadow-none gap-3 items-center"
                                    titleClassName="text-xs font-normal"
                                />
                                <Slider value={[maxMembersPerFamily]} min={1} max={MAX_SPECIALIST_FAMILY_MEMBERS} step={1} disabled={!familyModeEnabled} onValueChange={([value]) => setMaxMembersPerFamily(Math.max(1, Math.min(MAX_SPECIALIST_FAMILY_MEMBERS, Math.round(value))))} />
                            </div>
                            <Button onClick={() => void handleSaveSpecialistRegistry()} disabled={isSavingSpecialistRegistry} className="w-full">
                                {isSavingSpecialistRegistry ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                                {tg(t, "a518dc17")}
                            </Button>
                        </CardContent>
                    </Card>

                    {/* 2. Search/Research 搜寻配置 Card */}
                    <Card className="rounded-2xl border-slate-200 bg-white/95 shadow-sm">
                        <CardHeader className="space-y-1 p-4 pb-2 border-b">
                            <div className="flex flex-wrap items-center gap-2 text-sm font-semibold text-slate-900">
                                <SearchCheck className="h-4 w-4 text-cyan-600" />
                                {tg(t, "ed0fa816")}
                                <Badge variant={researchEnabled ? "secondary" : "destructive"}>{researchEnabled ? "research.core" : "off"}</Badge>
                            </div>
                            <p className="text-[11px] leading-relaxed text-slate-500">
                                {tg(t, "a1c3fdb1")}
                            </p>
                            <div className="flex flex-wrap gap-1.5 pt-1">
                                <Badge variant="outline" className="text-[10px] px-1.5 py-0">{tg(t, "6f6a45fc")}</Badge>
                                <Badge variant="outline" className="text-[10px] px-1.5 py-0">{tg(t, "cfd045a5")}</Badge>
                                <Badge variant="outline" className="text-[10px] px-1.5 py-0">{t("admin.pages.subagents.research.evidenceLedgerTtl")} 6h</Badge>
                            </div>
                        </CardHeader>
                        <CardContent className="p-4 space-y-4">
                            <SettingToggleCard
                                title={<span>{tg(t, "2a5c9f81")}</span>}
                                checked={researchEnabled}
                                onCheckedChange={setResearchEnabled}
                                className="border-none bg-transparent hover:bg-transparent p-0 shadow-none gap-3 items-center"
                                titleClassName="text-xs font-normal"
                            />
                            <div className="space-y-3">
                                <div className="space-y-1">
                                    <div className="flex items-center justify-between gap-3">
                                        <Label className="text-xs">{tg(t, "d6c520d8")}：{researchDefaultShards}</Label>
                                        <span className="text-xs text-slate-500">1-30</span>
                                    </div>
                                    <Slider value={[researchDefaultShards]} min={1} max={30} step={1} disabled={!researchEnabled} onValueChange={([value]) => {
                                        const nextDefault = Math.max(1, Math.min(30, Math.round(value)));
                                        setResearchDefaultShards(nextDefault);
                                        setResearchMaxShards(current => Math.max(nextDefault, current));
                                    }} />
                                </div>
                                <div className="space-y-1">
                                    <div className="flex items-center justify-between gap-3">
                                        <Label className="text-xs">{tg(t, "03514d16")}：{researchMaxShards}</Label>
                                        <span className="text-xs text-slate-500">max 30</span>
                                    </div>
                                    <Slider value={[researchMaxShards]} min={researchDefaultShards} max={30} step={1} disabled={!researchEnabled} onValueChange={([value]) => setResearchMaxShards(Math.max(researchDefaultShards, Math.min(30, Math.round(value))))} />
                                </div>
                                <div className="space-y-1">
                                    <div className="flex items-center justify-between gap-3">
                                        <Label className="text-xs">{tg(t, "d28b7ea4")}：{researchMaxRounds}</Label>
                                        <span className="text-xs text-slate-500">1-5</span>
                                    </div>
                                    <Slider value={[researchMaxRounds]} min={1} max={5} step={1} disabled={!researchEnabled} onValueChange={([value]) => setResearchMaxRounds(Math.max(1, Math.min(5, Math.round(value))))} />
                                </div>
                            </div>
                            <Button onClick={() => void handleSaveResearchConfig()} disabled={isSavingResearch} className="w-full">
                                {isSavingResearch ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                                {tg(t, "cdd9d125")}
                            </Button>
                        </CardContent>
                    </Card>

                    {/* 3. Recursive 递归委派配置 Card */}
                    <Card className="rounded-2xl border-slate-200 bg-white/95 shadow-sm">
                        <CardHeader className="space-y-1 p-4 pb-2 border-b">
                            <div className="flex flex-wrap items-center gap-2 text-sm font-semibold text-slate-900">
                                <Cable className="h-4 w-4 text-emerald-600" />
                                {t("admin.pages.subagents.recursive.title")}
                                <Badge variant={recursiveDelegationEnabled ? "secondary" : "destructive"}>
                                    {recursiveDelegationEnabled ? t("admin.pages.subagents.recursive.enabledBadge") : t("admin.pages.subagents.recursive.disabledBadge")}
                                </Badge>
                            </div>
                            <p className="text-[11px] leading-relaxed text-slate-500">
                                {t("admin.pages.subagents.recursive.description")}
                            </p>
                            <div className="flex flex-wrap gap-1.5 pt-1">
                                <Badge variant="outline" className="text-[10px] px-1.5 py-0">{t("admin.pages.subagents.recursive.depthBadge", { value: recursiveMaxDepth })}</Badge>
                                <Badge variant="outline" className="text-[10px] px-1.5 py-0">{t("admin.pages.subagents.recursive.childrenBadge", { value: recursiveMaxChildren })}</Badge>
                                <Badge variant="outline" className="text-[10px] px-1.5 py-0">{t("admin.pages.subagents.recursive.totalBadge", { value: recursiveMaxTotalNodes })}</Badge>
                            </div>
                        </CardHeader>
                        <CardContent className="p-4 space-y-4">
                            <SettingToggleCard
                                title={<HoverHelpLabel label={t("admin.pages.subagents.recursive.enableLabel")} tooltip={t("admin.pages.subagents.recursive.enableTooltip")} />}
                                checked={recursiveDelegationEnabled}
                                onCheckedChange={setRecursiveDelegationEnabled}
                                className="border-none bg-transparent hover:bg-transparent p-0 shadow-none gap-3 items-center"
                            />
                            <div className="space-y-3">
                                <div className="space-y-1">
                                    <div className="flex items-center justify-between gap-3">
                                        <HoverHelpLabel label={t("admin.pages.subagents.recursive.maxDepthLabel", { value: recursiveMaxDepth })} tooltip={t("admin.pages.subagents.recursive.maxDepthTooltip")} />
                                        <span className="text-xs text-slate-500">1-100</span>
                                    </div>
                                    <Slider value={[recursiveMaxDepth]} min={1} max={100} step={1} disabled={!recursiveDelegationEnabled} onValueChange={([value]) => setRecursiveMaxDepth(clampInt(value, DEFAULT_RECURSIVE_DELEGATION.maxDelegationDepth, 1, 100))} />
                                </div>
                                <div className="space-y-1">
                                    <div className="flex items-center justify-between gap-3">
                                        <HoverHelpLabel label={t("admin.pages.subagents.recursive.maxChildrenLabel", { value: recursiveMaxChildren })} tooltip={t("admin.pages.subagents.recursive.maxChildrenTooltip", { value: recursiveMaxChildren })} />
                                        <span className="text-xs text-slate-500">1-50</span>
                                    </div>
                                    <Slider value={[recursiveMaxChildren]} min={1} max={50} step={1} disabled={!recursiveDelegationEnabled} onValueChange={([value]) => setRecursiveMaxChildren(clampInt(value, DEFAULT_RECURSIVE_DELEGATION.maxChildrenPerDelegation, 1, 50))} />
                                </div>
                                <div className="space-y-1">
                                    <div className="flex items-center justify-between gap-3">
                                        <HoverHelpLabel label={t("admin.pages.subagents.recursive.maxTotalLabel", { value: recursiveMaxTotalNodes })} tooltip={t("admin.pages.subagents.recursive.maxTotalTooltip")} />
                                        <span className="text-xs text-slate-500">1-1000</span>
                                    </div>
                                    <Slider value={[recursiveMaxTotalNodes]} min={1} max={1000} step={10} disabled={!recursiveDelegationEnabled} onValueChange={([value]) => setRecursiveMaxTotalNodes(clampInt(value, DEFAULT_RECURSIVE_DELEGATION.maxTotalDelegationNodes, 1, 1000))} />
                                </div>
                                <div className="space-y-1">
                                    <div className="flex items-center justify-between gap-3">
                                        <HoverHelpLabel label={t("admin.pages.subagents.recursive.maxConcurrentLabel", { value: recursiveMaxConcurrent })} tooltip={t("admin.pages.subagents.recursive.maxConcurrentTooltip")} />
                                        <span className="text-xs text-slate-500">1-50</span>
                                    </div>
                                    <Slider value={[recursiveMaxConcurrent]} min={1} max={50} step={1} disabled={!recursiveDelegationEnabled} onValueChange={([value]) => setRecursiveMaxConcurrent(clampInt(value, DEFAULT_RECURSIVE_DELEGATION.maxConcurrentDelegations, 1, 50))} />
                                </div>
                            </div>
                            <Button onClick={() => void handleSaveRecursiveDelegationConfig()} disabled={isSavingRecursiveDelegation} className="w-full">
                                {isSavingRecursiveDelegation ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                                {t("admin.pages.subagents.recursive.save")}
                            </Button>
                        </CardContent>
                    </Card>
                </div>

        {/* 右栏：全局委派与研究参数配置 */}
        <div className="space-y-6">
            {/* 1. Specialist Registry 家族配置 Card */}
            <Card className="rounded-2xl border-slate-200 bg-white/95 shadow-sm">
                <CardHeader className="space-y-1 p-4 pb-2 border-b">
                    <CardTitle className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                        <BrainCircuit className="h-4 w-4 text-indigo-600" />
                        {tg(t, "cd5d78a6")}
                        <Badge variant={familyModeEnabled ? "secondary" : "destructive"}>{familyModeEnabled ? "compact" : "full"}</Badge>
                    </CardTitle>
                    <p className="text-[11px] leading-relaxed text-slate-500">
                        {familyModeEnabled ? tg(t, "d096101e") : tg(t, "571f4a11")}
                    </p>
                </CardHeader>
                <CardContent className="p-4 space-y-4">
                    <div className="space-y-2">
                        <SettingToggleCard
                            title={<span>{tg(t, "7074eefa")}：{maxMembersPerFamily}</span>}
                            checked={familyModeEnabled}
                            onCheckedChange={setFamilyModeEnabled}
                            className="border-none bg-transparent hover:bg-transparent p-0 shadow-none gap-3 items-center"
                            titleClassName="text-xs font-normal"
                        />
                        <Slider value={[maxMembersPerFamily]} min={1} max={MAX_SPECIALIST_FAMILY_MEMBERS} step={1} disabled={!familyModeEnabled} onValueChange={([value]) => setMaxMembersPerFamily(Math.max(1, Math.min(MAX_SPECIALIST_FAMILY_MEMBERS, Math.round(value))))} />
                    </div>
                    <Button onClick={() => void handleSaveSpecialistRegistry()} disabled={isSavingSpecialistRegistry} className="w-full">
                        {isSavingSpecialistRegistry ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                        {tg(t, "a518dc17")}
                    </Button>
                </CardContent>
            </Card>

            {/* 2. Search/Research 搜寻配置 Card */}
            <Card className="rounded-2xl border-slate-200 bg-white/95 shadow-sm">
                <CardHeader className="space-y-1 p-4 pb-2 border-b">
                    <div className="flex flex-wrap items-center gap-2 text-sm font-semibold text-slate-900">
                        <SearchCheck className="h-4 w-4 text-cyan-600" />
                        {tg(t, "ed0fa816")}
                        <Badge variant={researchEnabled ? "secondary" : "destructive"}>{researchEnabled ? "research.core" : "off"}</Badge>
                    </div>
                    <p className="text-[11px] leading-relaxed text-slate-500">
                        {tg(t, "a1c3fdb1")}
                    </p>
                    <div className="flex flex-wrap gap-1.5 pt-1">
                        <Badge variant="outline" className="text-[10px] px-1.5 py-0">{tg(t, "6f6a45fc")}</Badge>
                        <Badge variant="outline" className="text-[10px] px-1.5 py-0">{tg(t, "cfd045a5")}</Badge>
                        <Badge variant="outline" className="text-[10px] px-1.5 py-0">{t("admin.pages.subagents.research.evidenceLedgerTtl")} 6h</Badge>
                    </div>
                </CardHeader>
                <CardContent className="p-4 space-y-4">
                    <SettingToggleCard
                        title={<span>{tg(t, "2a5c9f81")}</span>}
                        checked={researchEnabled}
                        onCheckedChange={setResearchEnabled}
                        className="border-none bg-transparent hover:bg-transparent p-0 shadow-none gap-3 items-center"
                        titleClassName="text-xs font-normal"
                    />
                    <div className="space-y-3">
                        <div className="space-y-1">
                            <div className="flex items-center justify-between gap-3">
                                <Label className="text-xs">{tg(t, "d6c520d8")}：{researchDefaultShards}</Label>
                                <span className="text-xs text-slate-500">1-30</span>
                            </div>
                            <Slider value={[researchDefaultShards]} min={1} max={30} step={1} disabled={!researchEnabled} onValueChange={([value]) => {
                                const nextDefault = Math.max(1, Math.min(30, Math.round(value)));
                                setResearchDefaultShards(nextDefault);
                                setResearchMaxShards(current => Math.max(nextDefault, current));
                            }} />
                        </div>
                        <div className="space-y-1">
                            <div className="flex items-center justify-between gap-3">
                                <Label className="text-xs">{tg(t, "03514d16")}：{researchMaxShards}</Label>
                                <span className="text-xs text-slate-500">max 30</span>
                            </div>
                            <Slider value={[researchMaxShards]} min={researchDefaultShards} max={30} step={1} disabled={!researchEnabled} onValueChange={([value]) => setResearchMaxShards(Math.max(researchDefaultShards, Math.min(30, Math.round(value))))} />
                        </div>
                        <div className="space-y-1">
                            <div className="flex items-center justify-between gap-3">
                                <Label className="text-xs">{tg(t, "d28b7ea4")}：{researchMaxRounds}</Label>
                                <span className="text-xs text-slate-500">1-5</span>
                            </div>
                            <Slider value={[researchMaxRounds]} min={1} max={5} step={1} disabled={!researchEnabled} onValueChange={([value]) => setResearchMaxRounds(Math.max(1, Math.min(5, Math.round(value))))} />
                        </div>
                    </div>
                    <Button onClick={() => void handleSaveResearchConfig()} disabled={isSavingResearch} className="w-full">
                        {isSavingResearch ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                        {tg(t, "cdd9d125")}
                    </Button>
                </CardContent>
            </Card>

            {/* 3. Recursive 递归委派配置 Card */}
            <Card className="rounded-2xl border-slate-200 bg-white/95 shadow-sm">
                <CardHeader className="space-y-1 p-4 pb-2 border-b">
                    <div className="flex flex-wrap items-center gap-2 text-sm font-semibold text-slate-900">
                        <Cable className="h-4 w-4 text-emerald-600" />
                        {t("admin.pages.subagents.recursive.title")}
                        <Badge variant={recursiveDelegationEnabled ? "secondary" : "destructive"}>
                            {recursiveDelegationEnabled ? t("admin.pages.subagents.recursive.enabledBadge") : t("admin.pages.subagents.recursive.disabledBadge")}
                        </Badge>
                    </div>
                    <p className="text-[11px] leading-relaxed text-slate-500">
                        {t("admin.pages.subagents.recursive.description")}
                    </p>
                    <div className="flex flex-wrap gap-1.5 pt-1">
                        <Badge variant="outline" className="text-[10px] px-1.5 py-0">{t("admin.pages.subagents.recursive.depthBadge", { value: recursiveMaxDepth })}</Badge>
                        <Badge variant="outline" className="text-[10px] px-1.5 py-0">{t("admin.pages.subagents.recursive.childrenBadge", { value: recursiveMaxChildren })}</Badge>
                        <Badge variant="outline" className="text-[10px] px-1.5 py-0">{t("admin.pages.subagents.recursive.totalBadge", { value: recursiveMaxTotalNodes })}</Badge>
                    </div>
                </CardHeader>
                <CardContent className="p-4 space-y-4">
                    <SettingToggleCard
                        title={<HoverHelpLabel label={t("admin.pages.subagents.recursive.enableLabel")} tooltip={t("admin.pages.subagents.recursive.enableTooltip")} />}
                        checked={recursiveDelegationEnabled}
                        onCheckedChange={setRecursiveDelegationEnabled}
                        className="border-none bg-transparent hover:bg-transparent p-0 shadow-none gap-3 items-center"
                    />
                    <div className="space-y-3">
                        <div className="space-y-1">
                            <div className="flex items-center justify-between gap-3">
                                <HoverHelpLabel label={t("admin.pages.subagents.recursive.maxDepthLabel", { value: recursiveMaxDepth })} tooltip={t("admin.pages.subagents.recursive.maxDepthTooltip")} />
                                <span className="text-xs text-slate-500">1-100</span>
                            </div>
                            <Slider value={[recursiveMaxDepth]} min={1} max={100} step={1} disabled={!recursiveDelegationEnabled} onValueChange={([value]) => setRecursiveMaxDepth(clampInt(value, DEFAULT_RECURSIVE_DELEGATION.maxDelegationDepth, 1, 100))} />
                        </div>
                        <div className="space-y-1">
                            <div className="flex items-center justify-between gap-3">
                                <HoverHelpLabel label={t("admin.pages.subagents.recursive.maxChildrenLabel", { value: recursiveMaxChildren })} tooltip={t("admin.pages.subagents.recursive.maxChildrenTooltip", { value: recursiveMaxChildren })} />
                                <span className="text-xs text-slate-500">1-50</span>
                            </div>
                            <Slider value={[recursiveMaxChildren]} min={1} max={50} step={1} disabled={!recursiveDelegationEnabled} onValueChange={([value]) => setRecursiveMaxChildren(clampInt(value, DEFAULT_RECURSIVE_DELEGATION.maxChildrenPerDelegation, 1, 50))} />
                        </div>
                        <div className="space-y-1">
                            <div className="flex items-center justify-between gap-3">
                                <HoverHelpLabel label={t("admin.pages.subagents.recursive.maxTotalLabel", { value: recursiveMaxTotalNodes })} tooltip={t("admin.pages.subagents.recursive.maxTotalTooltip")} />
                                <span className="text-xs text-slate-500">1-1000</span>
                            </div>
                            <Slider value={[recursiveMaxTotalNodes]} min={1} max={1000} step={10} disabled={!recursiveDelegationEnabled} onValueChange={([value]) => setRecursiveMaxTotalNodes(clampInt(value, DEFAULT_RECURSIVE_DELEGATION.maxTotalDelegationNodes, 1, 1000))} />
                        </div>
                        <div className="space-y-1">
                            <div className="flex items-center justify-between gap-3">
                                <HoverHelpLabel label={t("admin.pages.subagents.recursive.maxConcurrentLabel", { value: recursiveMaxConcurrent })} tooltip={t("admin.pages.subagents.recursive.maxConcurrentTooltip")} />
                                <span className="text-xs text-slate-500">1-50</span>
                            </div>
                            <Slider value={[recursiveMaxConcurrent]} min={1} max={50} step={1} disabled={!recursiveDelegationEnabled} onValueChange={([value]) => setRecursiveMaxConcurrent(clampInt(value, DEFAULT_RECURSIVE_DELEGATION.maxConcurrentDelegations, 1, 50))} />
                        </div>
                    </div>
                    <Button onClick={() => void handleSaveRecursiveDelegationConfig()} disabled={isSavingRecursiveDelegation} className="w-full">
                        {isSavingRecursiveDelegation ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                        {t("admin.pages.subagents.recursive.save")}
                    </Button>
                </CardContent>
            </Card>
        </div>
    </div>

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
                                <Input value={form.name} onChange={event => setForm(current => ({
                  ...current,
                  name: event.target.value
                }))} placeholder={t("app.admin.dashboard.subagents.page.kffd7236f")} />
                            </div>
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.subagents.page.kc4e7d695")}</Label>
                                <Input value={form.roleLabel} onChange={event => setForm(current => ({
                  ...current,
                  roleLabel: event.target.value
                }))} placeholder={t("app.admin.dashboard.subagents.page.k49570350")} />
                            </div>
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.subagents.page.k5888282d")}</Label>
                                <Input value={form.icon} onChange={event => setForm(current => ({
                  ...current,
                  icon: event.target.value
                }))} placeholder="🤖" />
                            </div>
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.subagents.page.kc2c1e310")}</Label>
                                <Input value={form.avatar} onChange={event => setForm(current => ({
                  ...current,
                  avatar: event.target.value
                }))} placeholder={t("app.admin.dashboard.subagents.page.kf714bed3")} />
                            </div>
                        </div>

                        <div className="space-y-2">
                            <Label>{t("app.admin.dashboard.subagents.page.k4218ea5a")}</Label>
                            <Textarea value={form.description} onChange={event => setForm(current => ({
                ...current,
                description: event.target.value
              }))} placeholder={t("app.admin.dashboard.subagents.page.kdd73d22c")} />
                        </div>

                        <div className="grid gap-4 md:grid-cols-2">
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.subagents.page.kca695f8f")}</Label>
                                <ModelSelect models={models} value={form.modelId} placeholder={t("app.admin.dashboard.subagents.page.k9e6fdf0a")} onValueChange={value => setForm(current => ({
                  ...current,
                  modelId: value
                }))} />

                            </div>
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.subagents.page.k963f9424")}</Label>
                                <Select value={form.toolMode} onValueChange={(value: "explicit" | "contextual_auto") => setForm(current => ({
                  ...current,
                  toolMode: value
                }))}>
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {getAdminOptions("toolMode").map((option) => <SelectItem key={option.value} value={option.value}>{t(option.labelKey)}</SelectItem>)}
                                    </SelectContent>
                                </Select>
                            </div>
                        </div>

                        <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(200px,240px)] md:items-start">
                            <div className="space-y-2">
                                <HoverHelpLabel label={tg(t, "9512faae")} tooltip={tg(t, "5c02fc01")} />

                                <Input list="subagent-family-options" className="h-10" value={form.specialistFamily} onChange={event => setForm(current => ({
                  ...current,
                  specialistFamily: event.target.value
                }))} placeholder="engineering" />

                                <datalist id="subagent-family-options">
                                    {familyOptions.map(family => <option key={family.familyId || family.displayName} value={family.familyId || family.displayName || ""} label={`${family.displayName || family.familyId || ""}${family.memberCount ? ` (${family.memberCount})` : ""}`} />)}
                                </datalist>
                            </div>
                            <div className="space-y-2">
                                <HoverHelpLabel label={tg(t, "93b2f9c3")} tooltip={<div>
                                            <div>{tg(t, "d51acc60")}</div>
                                            <div className="mt-1 text-xs text-slate-300">researcher, writer, operator, coach, analyst, creative, skill_runtime_curator</div>
                                            <div className="mt-1 text-xs text-slate-300">
                                                {tg(t, "ca63bded")}
                                            </div>
                                        </div>} />

                                <Input className="h-10" value={form.agentClass} onChange={event => setForm(current => ({
                  ...current,
                  agentClass: event.target.value
                }))} placeholder="researcher / writer / operator" />

                                <p className="min-h-10 text-xs leading-5 text-slate-500">
                                    {tg(t, "bdfeb614")}
                                </p>
                            </div>
                            <div className="space-y-2">
                                <Label>{tg(t, "ec8c5cb6")}</Label>
                                <label className="flex h-10 items-center gap-3 rounded-2xl border border-slate-200 bg-slate-50/70 px-4 text-sm">
                                    <Checkbox checked={form.globalExposure} onCheckedChange={next => setForm(current => ({
                    ...current,
                    globalExposure: Boolean(next)
                  }))} />

                                    <span className="font-medium text-slate-900">globalExposure</span>
                                </label>
                                <p className="min-h-10 text-xs leading-5 text-slate-500">
                                    {tg(t, "8b369719")}
                                </p>
                            </div>
                        </div>

                        <div className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4 text-sm leading-6 text-slate-500">
                            {form.toolMode === "contextual_auto" ? <>
                                    <div className="font-medium text-slate-900">{t("app.admin.dashboard.subagents.page.k9431e8c4")}</div>
                                    <div>{t("app.admin.dashboard.subagents.page.k3b6c2a75")}</div>
                                </> : <>
                                    <div className="font-medium text-slate-900">{t("app.admin.dashboard.subagents.page.kaaa3ff24")}</div>
                                    <div>{t("app.admin.dashboard.subagents.page.k502a06d7")}</div>
                                </>}
                        </div>

                        <div className="space-y-4 rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                            <div className="grid gap-4 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label>{tg(t, "49aa0a72")}</Label>
                                    <Input value={form.domainTagsText} onChange={event => setForm(current => ({
                    ...current,
                    domainTagsText: event.target.value
                  }))} placeholder="software_engineering, frontend" />

                                    <p className="text-xs leading-5 text-slate-500">
                                        {tg(t, "aa3e8d8f")}
                                    </p>
                                </div>
                                <div className="space-y-2">
                                    <Label>{tg(t, "396f27fd")}</Label>
                                    <Input value={form.operationCapabilitiesText} onChange={event => setForm(current => ({
                    ...current,
                    operationCapabilitiesText: event.target.value
                  }))} placeholder="implement, review, test" />

                                    <p className="text-xs leading-5 text-slate-500">
                                        {tg(t, "172cd73c")}
                                    </p>
                                </div>
                                <div className="space-y-2">
                                    <Label>{tg(t, "3a3a388f")}</Label>
                                    <Input value={form.runtimeAffinitiesText} onChange={event => setForm(current => ({
                    ...current,
                    runtimeAffinitiesText: event.target.value
                  }))} placeholder="engine, admin, web" />

                                    <p className="text-xs leading-5 text-slate-500">
                                        {tg(t, "0afbbc87")}
                                    </p>
                                </div>
                                <div className="space-y-2">
                                    <Label>{tg(t, "9d1f7d68")}</Label>
                                    <Select value={form.toolExposurePolicy} onValueChange={value => setForm(current => ({
                    ...current,
                    toolExposurePolicy: value
                  }))}>
                                        <SelectTrigger>
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {getAdminOptions("toolExposurePolicy").map((option) => <SelectItem key={option.value} value={option.value}>{t(option.labelKey)}</SelectItem>)}
                                        </SelectContent>
                                    </Select>
                                </div>
                            </div>
                            <details className="rounded-xl border border-slate-200 bg-white/80 p-3">
                                <summary className="cursor-pointer text-sm font-medium text-slate-900">
                                    {tg(t, "58deedf1")}
                                </summary>
                                <Textarea value={form.capabilitySnapshotJson} onChange={event => setForm(current => ({
                  ...current,
                  capabilitySnapshotJson: event.target.value
                }))} className="mt-3 min-h-[140px] font-mono text-xs" placeholder='{"agentClass":"executor","domainTags":["software_engineering"]}' />

                                <p className="mt-2 text-xs leading-5 text-slate-500">
                                    {tg(t, "049582b6")}
                                </p>
                            </details>
                        </div>

                        {form.toolMode === "explicit" ? <div className="grid gap-4 xl:grid-cols-2 xl:items-start">
                                <Card className="rounded-3xl border-slate-200 xl:col-span-2">
                                    <CardHeader className="space-y-0">
                                        <button type="button" className="flex w-full items-start justify-between gap-3 text-left" onClick={() => toggleToolPanel("baseline")}>

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
                                    {toolPanels.baseline ? <CardContent className="max-h-[148px] space-y-3 overflow-y-auto overscroll-contain pr-2">
                                            {baselineSystemTools.length > 0 ? <div className="grid gap-2">
                                                    {baselineSystemTools.map(tool => <div key={tool.name} className="rounded-2xl border border-slate-200 bg-slate-50/70 px-3 py-2">
                                                            <div className="font-mono text-[11px] font-medium text-slate-900">{tool.name}</div>
                                                            <div className="mt-1 text-xs leading-5 text-slate-500">
                                                                {tool.description || t("app.admin.dashboard.subagents.page.k86e9a787")}
                                                            </div>
                                                        </div>)}
                                                </div> : <div className="text-xs text-slate-500">{t("app.admin.dashboard.subagents.page.kca19dcd0")}</div>}
                                            <p className="text-xs leading-5 text-slate-500">
                                                {t("app.admin.dashboard.subagents.page.k76e62da1")}
                                            </p>
                                        </CardContent> : null}
                                </Card>
                                <Card className="rounded-3xl border-slate-200">
                                    <CardHeader className="space-y-0">
                                        <button type="button" className="flex w-full items-start justify-between gap-3 text-left" onClick={() => toggleToolPanel("skills")}>

                                            <div className="space-y-1">
                                                <CardTitle className="flex items-center gap-2 text-base"><Sparkles className="h-4 w-4 text-violet-600" />{t("app.admin.dashboard.subagents.page.ke431abc9")}<Badge variant="outline">{skills.length}</Badge></CardTitle>
                                                <CardDescription>{t("app.admin.dashboard.subagents.page.k62807e10")}</CardDescription>
                                            </div>
                                            <ChevronDown className={`mt-1 h-4 w-4 shrink-0 text-slate-400 transition-transform ${toolPanels.skills ? "rotate-180" : ""}`} />
                                        </button>
                                    </CardHeader>
                                    {toolPanels.skills ? <CardContent className="max-h-[224px] space-y-3 overflow-y-auto overscroll-contain pr-2">
                                        {skills.length === 0 ? <div className="text-xs text-slate-500">{t("app.admin.dashboard.subagents.page.k2b7b1954")}</div> : null}
                                        {skills.map(skill => {
                    const checked = form.tools.includes(skill.name);
                    return <label key={skill.path} className="flex items-start gap-3">
                                                    <Checkbox checked={checked} onCheckedChange={next => toggleSelector(skill.name, Boolean(next))} className="mt-1" />
                                                    <div className="min-w-0">
                                                        <div className="text-sm font-medium text-slate-900">{skill.name}</div>
                                                        <div className="text-xs leading-5 text-slate-500">{skill.description || skill.path}</div>
                                                    </div>
                                                </label>;
                  })}
                                    </CardContent> : null}
                                </Card>
                                <Card className="rounded-3xl border-slate-200">
                                    <CardHeader className="space-y-0">
                                        <button type="button" className="flex w-full items-start justify-between gap-3 text-left" onClick={() => toggleToolPanel("mcp")}>

                                            <div className="space-y-1">
                                                <CardTitle className="flex items-center gap-2 text-base"><Wrench className="h-4 w-4 text-sky-600" />MCP<Badge variant="outline">{availableMcpToolCount}</Badge></CardTitle>
                                                <CardDescription>{t("app.admin.dashboard.subagents.page.k6490c720")}</CardDescription>
                                            </div>
                                            <ChevronDown className={`mt-1 h-4 w-4 shrink-0 text-slate-400 transition-transform ${toolPanels.mcp ? "rotate-180" : ""}`} />
                                        </button>
                                    </CardHeader>
                                    {toolPanels.mcp ? <CardContent className="max-h-[224px] space-y-4 overflow-y-auto overscroll-contain pr-2">
                                        {Object.keys(groupedMcpTools).length === 0 ? <div className="text-xs text-slate-500">{t("app.admin.dashboard.subagents.page.k57c2bf93")}</div> : null}
                                        {Object.entries(groupedMcpTools).map(([serverName, items]) => <div key={serverName} className="space-y-2">
                                                <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{serverName}</div>
                                                {items.map(tool => {
                      const checked = form.tools.includes(tool.name);
                      return <label key={tool.name} className="flex items-start gap-3">
                                                            <Checkbox checked={checked} onCheckedChange={next => toggleSelector(tool.name, Boolean(next))} className="mt-1" />
                                                            <div className="min-w-0">
                                                                <div className="break-all text-sm font-medium text-slate-900">{tool.name}</div>
                                                                <div className="text-xs leading-5 text-slate-500">{tool.description || t("app.admin.dashboard.subagents.page.k86e9a787")}</div>
                                                            </div>
                                                        </label>;
                    })}
                                            </div>)}
                                    </CardContent> : null}
                                </Card>
                                <Card className="rounded-3xl border-slate-200">
                                    <CardHeader className="space-y-0">
                                        <button type="button" className="flex w-full items-start justify-between gap-3 text-left" onClick={() => toggleToolPanel("plugin_host")}>

                                            <div className="space-y-1">
                                                <CardTitle className="flex items-center gap-2 text-base"><BrainCircuit className="h-4 w-4 text-emerald-600" />PluginHost<Badge variant="outline">{pluginHostTools.length}</Badge></CardTitle>
                                                <CardDescription>{t("app.admin.dashboard.subagents.page.k5f711f45")}</CardDescription>
                                            </div>
                                            <ChevronDown className={`mt-1 h-4 w-4 shrink-0 text-slate-400 transition-transform ${toolPanels.plugin_host ? "rotate-180" : ""}`} />
                                        </button>
                                    </CardHeader>
                                    {toolPanels.plugin_host ? <CardContent className="max-h-[224px] space-y-4 overflow-y-auto overscroll-contain pr-2">
                                        {bridgeError ? <div className="rounded-2xl border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-700">{bridgeError}</div> : null}
                                        {!bridgeError && Object.keys(groupedPluginHostTools).length === 0 ? <div className="text-xs text-slate-500">{t("app.admin.dashboard.subagents.page.kc9324cc5")}</div> : null}
                                        {Object.entries(groupedPluginHostTools).map(([pluginId, items]) => <div key={pluginId} className="space-y-2">
                                                <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{pluginId}</div>
                                                {items.map(tool => {
                      const selector = String(tool.canonicalName || tool.toolName || "").trim();
                      const checked = form.tools.includes(selector);
                      return <label key={selector} className="flex items-start gap-3">
                                                            <Checkbox checked={checked} onCheckedChange={next => toggleSelector(selector, Boolean(next))} className="mt-1" />
                                                            <div className="min-w-0">
                                                                <div className="break-all text-sm font-medium text-slate-900">{selector}</div>
                                                                <div className="text-xs leading-5 text-slate-500">{tool.description || tool.label || selector}</div>
                                                            </div>
                                                        </label>;
                    })}
                                            </div>)}
                                    </CardContent> : null}
                                </Card>
                            </div> : null}

                        <div className="space-y-2">
                            <Label>{t("app.admin.dashboard.subagents.page.kc2dd0474")}</Label>
                            <Textarea value={form.systemPrompt} onChange={event => setForm(current => ({
                ...current,
                systemPrompt: event.target.value
              }))} className="min-h-[180px] font-mono text-sm" placeholder={t("app.admin.dashboard.subagents.page.ke7295552")} />

                        </div>

                        <div className="space-y-4 rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                            <label className="flex items-center gap-3">
                                <Checkbox checked={form.reflectionEnabled} onCheckedChange={checked => setForm(current => ({
                  ...current,
                  reflectionEnabled: Boolean(checked)
                }))} />

                                <div>
                                    <div className="text-sm font-medium text-slate-900">{t("app.admin.dashboard.subagents.page.kdaaa0859")}</div>
                                    <div className="text-xs text-slate-500">{t("app.admin.dashboard.subagents.page.k45e15cc8")}</div>
                                </div>
                            </label>
                            {form.reflectionEnabled ? <div className="space-y-2">
                                    <Label>{t("app.admin.dashboard.subagents.page.k372414e4")}</Label>
                                    <Input type="number" min={1} max={10} value={form.maxReflections} onChange={event => setForm(current => ({
                  ...current,
                  maxReflections: Math.max(1, Math.min(10, Number(event.target.value) || 1))
                }))} className="w-32" />

                                </div> : null}
                        </div>

                        <Button className="w-full" onClick={() => void handleSave()} disabled={isSaving}>
                            {isSaving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                            {t("app.admin.dashboard.subagents.page.k7171a69c")}
                        </Button>
                        </div>
                    </div>
                </DialogContent>
            </Dialog>
        </div>;
}
