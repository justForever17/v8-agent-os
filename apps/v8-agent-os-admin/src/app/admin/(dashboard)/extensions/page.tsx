"use client";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { CheckCircle2, ExternalLink, Loader2, PackageCheck, Plus, RefreshCw, Save, Server, Terminal, Trash2, Upload, Wrench } from "lucide-react";
import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { DomainSummaryStrip } from "@/components/admin-shell/DomainSummaryStrip";
import { EmptyState } from "@/components/admin-shell/EmptyState";
import { AdminHoverInfo } from "@/components/admin-shell/AdminHoverInfo";
import { InlineSaveState } from "@/components/admin-shell/InlineSaveState";
import { SourceMetaRow } from "@/components/admin-shell/SourceMetaRow";
import { StatusNotice } from "@/components/admin-shell/StatusNotice";
import { ModelSelect } from "@/components/models/ModelSelect";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { SettingToggleCard } from "@/components/admin-shell/SettingToggleCard";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/use-toast";
import { useT } from "@/components/providers/LocaleProvider";
import { fetchConfigDomain, saveConfigDomain, type ConfigRegistryEnvelope } from "@/lib/config-registry";
import { tg } from "@/i18n/admin-legacy";
type ExtensionCatalogResponse = {
  startupState?: "cold" | "refreshing" | "ready" | "error";
  snapshotFreshness?: "cold" | "cached" | "live";
  lastRefreshAt?: string | null;
  lastRefreshError?: string | null;
  fingerprint?: string | null;
  visibleRootSignature?: string | null;
  catalogScope?: {
    mode?: string;
    workspacePath?: string;
    workspaceId?: string;
    projectId?: string;
  };
  changedAt?: string | null;
  lastSkillInventoryChange?: {
    reason?: string | null;
    changedAt?: string | null;
    fingerprint?: string | null;
    addedSkills?: string[];
    removedSkills?: string[];
    updatedSkills?: string[];
  } | null;
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
  summary: {
    skillCount: number;
    mcpServerCount: number;
    connectedMcpServerCount: number;
    mcpToolCount: number;
  };
  skillDependencyPolicy?: {
    mode?: string;
    pythonTarget?: string;
    systemWideInstallAllowed?: boolean;
    nodeGlobalInstallAllowed?: boolean;
  };
  skills?: {
    root: string;
    roots?: string[];
    rootDescriptors?: Array<{
      rootPath: string;
      sourceType?: "global" | "main_workspace" | "scoped_workspace" | string;
      visibility?: "global" | "scoped" | string;
      workspacePath?: string;
      workspaceId?: string | null;
      projectId?: string | null;
    }>;
    fingerprint?: string | null;
    changedAt?: string | null;
    visibleRootSignature?: string | null;
    discoveryRevision?: string | null;
    changedRoots?: string[];
    scopedRefreshMode?: string | null;
    items: Array<{
      skillId?: string;
      name: string;
      description: string;
      path: string;
      skillRoot?: string;
      instructionPath?: string;
      sourceType?: "global" | "main_workspace" | "scoped_workspace" | string;
      visibility?: "global" | "scoped" | string;
      workspacePath?: string;
      workspaceId?: string | null;
      projectId?: string | null;
      rootPath?: string;
    }>;
  };
  mcp: {
    servers: Array<{
      name: string;
      status: "connected" | "disabled" | "error";
      toolCount: number;
      tools: Array<{
        name: string;
        description: string;
      }>;
      transport: string;
      target: string;
      appsSupported?: boolean;
      appToolCount?: number;
      uiResourceCount?: number;
      lastAppsError?: string | null;
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
  skillDependencyPolicy?: ExtensionCatalogResponse["skillDependencyPolicy"];
  mcp: {
    statusBreakdown: Record<string, number>;
  };
  silk?: {
    available?: boolean;
    version?: string | null;
    toolRoot?: string;
  };
};
type ExtensionSkillItem = NonNullable<ExtensionCatalogResponse["skills"]>["items"][number];
type SkillInstallResult = {
  source: string;
  targetRoot: string;
  installed: Array<{
    name: string;
    path: string;
  }>;
  conflicts: Array<{
    name?: string;
    path?: string;
    reason?: string;
  }>;
  warnings: string[];
};
type SkillSafetyReview = {
  id: string;
  skill_id?: string | null;
  skill_name?: string | null;
  skill_path?: string | null;
  instruction_path?: string | null;
  content_hash?: string | null;
  static_verdict?: string | null;
  effective_verdict?: string | null;
  user_override?: string | null;
  disabled?: boolean;
  reasons?: string[];
  flaggedFiles?: Array<{
    path?: string;
    severity?: string;
    findings?: Array<{id?: string;label?: string;}>;
  }>;
  findingCategories?: string[];
  updated_at?: string | null;
  reviewed_at?: string | null;
};
type SysModel = {
  id: string;
  modelRef?: string;
  providerId?: string;
  modelId: string;
  name: string;
  type: string;
  provider?: {
    id?: string;
    name?: string;
  };
  providerName?: string;
};
type ProjectRecord = {
  id: string;
  name?: string;
  workspaceId?: string;
  workspacePath?: string;
};
type ExtensionsConfigData = {
  prefilterPolicy?: {
    enabled?: boolean;
    mode?: string;
    skills?: {
      stage1Enabled?: boolean;
      stage1TopK?: number;
      llmEnabled?: boolean;
      stage2TopK?: number;
      llmTimeoutSeconds?: number;
    };
    mcp?: {
      stage1Enabled?: boolean;
      stage1TopK?: number;
      llmEnabled?: boolean;
      stage2TopK?: number;
      llmTimeoutSeconds?: number;
    };
  };
  modelBindings?: {
    prefilterModel?: string;
  };
};
type ExtensionPreviewSkillEntry = {
  skillId?: string;
  skillName?: string;
  skillRoot?: string;
  instructionPath?: string;
  sourceType?: string;
  visibility?: string;
  workspacePath?: string;
  workspaceId?: string;
  projectId?: string;
  rootPath?: string;
  referencesDir?: string;
  scriptsDir?: string;
  assetsDir?: string;
  templatesDir?: string;
  availableFiles?: string[];
};
type ExtensionPreviewMcpServer = {
  serverKey?: string;
  familyKey: string;
  serverName: string;
  title: string;
  toolCount: number;
  toolNames: string[];
  tools?: Array<{
    name: string;
    description?: string;
  }>;
  descriptions?: string[];
};
type ExtensionPrefilterPreviewResponse = {
  queryPreview?: string;
  skillStage1Entries?: ExtensionPreviewSkillEntry[];
  skillEntries?: ExtensionPreviewSkillEntry[];
  skillRootDescriptors?: NonNullable<ExtensionCatalogResponse["skills"]>["rootDescriptors"];
  mcpStage1Servers?: ExtensionPreviewMcpServer[];
  mcpServers?: ExtensionPreviewMcpServer[];
  mcpFamilies?: ExtensionPreviewMcpServer[];
  counts?: {
    mode?: string;
    routingMode?: string;
    skillsRoutingMode?: string;
    mcpRoutingMode?: string;
    modelId?: string;
    role?: string;
    reason?: string | null;
    prefilterTimedOut?: boolean;
    prefilterCacheHit?: boolean;
    stage1Enabled?: {
      skills?: boolean;
      mcp?: boolean;
    };
    stage1TopK?: {
      skills?: number;
      mcp?: number;
    };
    stage2Enabled?: {
      skills?: boolean;
      mcp?: boolean;
    };
    stage2TopK?: {
      skills?: number;
      mcp?: number;
    };
    llmTimeoutSeconds?: {
      skills?: number;
      mcp?: number;
    };
    skillCandidates?: number;
    mcpCandidates?: number;
    mcpServerCandidates?: number;
    skillInventoryCount?: number;
    mcpInventoryCount?: number;
    skillPoolSize?: number;
    mcpPoolSize?: number;
    mcpServerPoolSize?: number;
    mcpFamilyPoolSize?: number;
    skillStage1HitCount?: number;
    skillStage1ShortlistCount?: number;
    skillFinalExposedCount?: number;
    mcpStage1HitCount?: number;
    mcpStage1ShortlistCount?: number;
    mcpFinalExposedCount?: number;
  };
  routing?: {
    mode?: string;
    routingMode?: string;
    skillsRoutingMode?: string;
    mcpRoutingMode?: string;
    modelId?: string;
    role?: string;
    reason?: string | null;
    prefilterTimedOut?: boolean;
    prefilterCacheHit?: boolean;
    stage1Enabled?: {
      skills?: boolean;
      mcp?: boolean;
    };
    stage1TopK?: {
      skills?: number;
      mcp?: number;
    };
    stage2Enabled?: {
      skills?: boolean;
      mcp?: boolean;
    };
    stage2TopK?: {
      skills?: number;
      mcp?: number;
    };
    llmTimeoutSeconds?: {
      skills?: number;
      mcp?: number;
    };
    selectedSkills?: string[];
    selectedSkillIds?: string[];
    selectedMcpServers?: string[];
    selectedMcpFamilies?: string[];
    selectedMcpTools?: string[];
    skillInventoryCount?: number;
    mcpInventoryCount?: number;
    skillPoolSize?: number;
    mcpPoolSize?: number;
    mcpServerPoolSize?: number;
    mcpFamilyPoolSize?: number;
    skillStage1HitCount?: number;
    skillStage1ShortlistCount?: number;
    skillFinalExposedCount?: number;
    mcpStage1HitCount?: number;
    mcpStage1ShortlistCount?: number;
    mcpFinalExposedCount?: number;
  };
};
type StructuredValidationPayload = {
  code?: string;
  message?: string;
  details?: Record<string, unknown>;
};
type TranslateFn = (value: string, params?: Record<string, string | number>) => string;
function statusLabel(status: string, t: TranslateFn) {
  if (status === "connected")
  return t("app.admin.dashboard.extensions.page.kf2ef9263");
  if (status === "disabled")
  return t("app.admin.dashboard.extensions.page.k369f3547");
  return t("app.admin.dashboard.extensions.page.k5797988b");
}
function StatPill({ label, value


}: {label: string;value: string | number;}) {
  return <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
            <div className="text-xs text-slate-500">{label}</div>
            <div className="mt-2 text-2xl font-semibold text-slate-900">{value}</div>
        </div>;
}
function PolicyToggleCard({ title, description, checked, onCheckedChange, children





}: {title: string;description: string;checked: boolean;onCheckedChange: (checked: boolean) => void;children?: ReactNode;}) {
  return <div className="space-y-4 rounded-2xl border border-slate-200 bg-slate-50/80 p-4">
            <SettingToggleCard
                title={title}
                description={description}
                checked={checked}
                onCheckedChange={onCheckedChange}
                className="border-none bg-transparent hover:bg-transparent p-0 shadow-none gap-4 items-start"
            />
            {children}
        </div>;
}
function SliderField({ label, value, min, max, step = 1, disabled, onValueChange, formatter, hint









}: {label: string;value: number;min: number;max: number;step?: number;disabled?: boolean;onValueChange: (value: number) => void;formatter?: (value: number) => string;hint?: string;}) {
  return <div className={`space-y-3 ${disabled ? "opacity-50" : ""}`}>
            <div className="flex items-center justify-between gap-3">
                <Label>{label}</Label>
                <span className="rounded-full border border-slate-200 bg-white px-2.5 py-1 text-xs font-semibold text-slate-700">
                    {formatter ? formatter(value) : value}
                </span>
            </div>
            <Slider value={[value]} min={min} max={max} step={step} disabled={disabled} onValueChange={([next]) => {
      if (typeof next === "number" && Number.isFinite(next)) {
        onValueChange(next);
      }
    }} />
            <div className="flex items-center justify-between text-[11px] text-slate-400">
                <span>{min}</span>
                <span>{max}</span>
            </div>
            {hint ? <div className="text-xs leading-5 text-slate-500">{hint}</div> : null}
        </div>;
}
function previewMcpServerTools(server: ExtensionPreviewMcpServer): Array<{
  name: string;
  description?: string;
}> {
  if (server.tools && server.tools.length > 0) {
    return server.tools;
  }
  return (server.toolNames || []).map((name) => ({ name, description: "" }));
}
function skillSourceBadgeLabel(sourceType: string | undefined, t: TranslateFn) {
  if (sourceType === "main_workspace")
  return t("app.admin.dashboard.extensions.page.k61ce835a");
  if (sourceType === "scoped_workspace")
  return t("app.admin.dashboard.extensions.page.kf0786585");
  return t("app.admin.dashboard.extensions.page.k2cdad9c0");
}
function extractValidationPayload(payload: unknown): StructuredValidationPayload | null {
  if (!payload || typeof payload !== "object")
  return null;
  const record = payload as Record<string, unknown>;
  const detail = record.detail;
  if (detail && typeof detail === "object") {
    return detail as StructuredValidationPayload;
  }
  if (typeof record.error === "string") {
    return { message: record.error };
  }
  if (typeof detail === "string") {
    return { message: detail };
  }
  return null;
}
function localizeSkillZipValidationPayload(payload: StructuredValidationPayload | null, t: TranslateFn): string | null {
  if (!payload)
  return null;
  const details = payload.details || {};
  switch (payload.code) {
    case "invalid_file_type":
      return t("app.admin.dashboard.extensions.page.k39d87bf6");
    case "empty_archive":
      return t("app.admin.dashboard.extensions.page.k2cb65945");
    case "invalid_root_structure":{
        const rootFiles = Array.isArray(details.rootFiles) ? details.rootFiles.filter((item): item is string => typeof item === "string") : [];
        return rootFiles.length > 0 ?
        t("app.admin.dashboard.extensions.page.k077d3c19", {
          rootFiles_join: rootFiles.join("、")
        }) : t("app.admin.dashboard.extensions.page.kc65ffcd3");
      }
    case "multiple_root_directories":{
        const rootEntries = Array.isArray(details.rootEntries) ? details.rootEntries.filter((item): item is string => typeof item === "string") : [];
        return rootEntries.length > 0 ?
        t("app.admin.dashboard.extensions.page.k9407c8ed", {
          rootEntries_join: rootEntries.join("、")
        }) : t("app.admin.dashboard.extensions.page.k3c857a6f");
      }
    case "missing_skill_manifest":
      return t("app.admin.dashboard.extensions.page.k7cdffc4a");
    case "invalid_zip":
      return t("app.admin.dashboard.extensions.page.k727a7d38");
    default:
      return typeof payload.message === "string" && payload.message.trim() ? payload.message : null;
  }
}
function localizeMcpValidationPayload(payload: StructuredValidationPayload | null, t: TranslateFn): string | null {
  if (!payload)
  return null;
  switch (payload.code) {
    case "invalid_payload":
      return t("app.admin.dashboard.extensions.page.kf9af03ce");
    case "invalid_server_map":
      return t("app.admin.dashboard.extensions.page.k669b692b");
    case "empty_server_map":
      return t("app.admin.dashboard.extensions.page.kaa32b9ff");
    case "empty_server_name":
      return t("app.admin.dashboard.extensions.page.k2f871867");
    case "invalid_server_payload":
      return t("app.admin.dashboard.extensions.page.kb30b8c40");
    case "invalid_command":
      return t("app.admin.dashboard.extensions.page.k0275c14d");
    case "invalid_url":
      return t("app.admin.dashboard.extensions.page.k0b9ee333");
    case "invalid_args":
      return t("app.admin.dashboard.extensions.page.k452944be");
    case "invalid_env":
      return t("app.admin.dashboard.extensions.page.k97645b49");
    case "invalid_headers":
      return t("app.admin.dashboard.extensions.page.k251f00b3");
    case "missing_target":
      return t("app.admin.dashboard.extensions.page.k8f18a1e4");
    default:
      return typeof payload.message === "string" && payload.message.trim() ? payload.message : null;
  }
}
function validateMcpJsonInput(raw: string, t: TranslateFn): {
  parsed: Record<string, unknown>;
  serverCount: number;
} {
  const parsed = JSON.parse(raw) as Record<string, unknown>;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(t("app.admin.dashboard.extensions.page.kf9af03ce"));
  }
  const serverMap = ("mcpServers" in parsed ? parsed.mcpServers : parsed) as Record<string, unknown>;
  if (!serverMap || typeof serverMap !== "object" || Array.isArray(serverMap)) {
    throw new Error(t("app.admin.dashboard.extensions.page.k669b692b"));
  }
  const entries = Object.entries(serverMap);
  if (entries.length === 0) {
    throw new Error(t("app.admin.dashboard.extensions.page.kaa32b9ff"));
  }
  for (const [name, rawServer] of entries) {
    if (!String(name || "").trim()) {
      throw new Error(t("app.admin.dashboard.extensions.page.k2f871867"));
    }
    if (!rawServer || typeof rawServer !== "object" || Array.isArray(rawServer)) {
      throw new Error(t("app.admin.dashboard.extensions.page.kaf580ce9", {
        name: name
      }));
    }
    const server = rawServer as Record<string, unknown>;
    const disabled = Boolean(server.disabled);
    const command = typeof server.command === "string" ? server.command.trim() : "";
    const url = typeof server.url === "string" ? server.url.trim() : "";
    if (!disabled && !command && !url) {
      throw new Error(t("app.admin.dashboard.extensions.page.ke81bbcc1", {
        name: name
      }));
    }
    if ("args" in server && !Array.isArray(server.args)) {
      throw new Error(t("app.admin.dashboard.extensions.page.kbaa986a0", {
        name: name
      }));
    }
    if ("env" in server && (!server.env || typeof server.env !== "object" || Array.isArray(server.env))) {
      throw new Error(t("app.admin.dashboard.extensions.page.kc1c54333", {
        name: name
      }));
    }
    if ("headers" in server && (!server.headers || typeof server.headers !== "object" || Array.isArray(server.headers))) {
      throw new Error(t("app.admin.dashboard.extensions.page.kf12b6e45", {
        name: name
      }));
    }
  }
  return { parsed, serverCount: entries.length };
}
export default function ExtensionsPage() {
  const t = useT();
  const [catalog, setCatalog] = useState<ExtensionCatalogResponse | null>(null);
  const [health, setHealth] = useState<ExtensionHealthResponse | null>(null);
  const [configEnvelope, setConfigEnvelope] = useState<ConfigRegistryEnvelope<ExtensionsConfigData> | null>(null);
  const [models, setModels] = useState<SysModel[]>([]);
  const [projects, setProjects] = useState<ProjectRecord[]>([]);
  const [skillSafetyReviews, setSkillSafetyReviews] = useState<SkillSafetyReview[]>([]);
  const [previewScope, setPreviewScope] = useState("default");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [reloading, setReloading] = useState(false);
  const [installingCommand, setInstallingCommand] = useState(false);
  const [uploadingZip, setUploadingZip] = useState(false);
  const [savingMcp, setSavingMcp] = useState(false);
  const [deletingMcpServer, setDeletingMcpServer] = useState("");
  const [deletingSkillId, setDeletingSkillId] = useState("");
  const [commandInput, setCommandInput] = useState("");
  const [mcpConfigInput, setMcpConfigInput] = useState("");
  const [installResult, setInstallResult] = useState<SkillInstallResult | null>(null);
  const [mcpDialogOpen, setMcpDialogOpen] = useState(false);
  const [zipFileLabel, setZipFileLabel] = useState("");
  const [zipValidationError, setZipValidationError] = useState("");
  const [mcpValidationError, setMcpValidationError] = useState("");
  const [mcpValidationSummary, setMcpValidationSummary] = useState("");
  const [previewQuery, setPreviewQuery] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [previewedQuery, setPreviewedQuery] = useState("");
  const [previewResult, setPreviewResult] = useState<ExtensionPrefilterPreviewResponse | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { toast } = useToast();
  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [healthResponse, config, modelList, projectsPayload, skillSafetyPayload] = await Promise.all([
      fetch("/api/extensions/health", { cache: "no-store" }),
      fetchConfigDomain<ExtensionsConfigData>("extensions"),
      fetch("/api/models", { cache: "no-store" }).then((response) => response.json().catch(() => [])),
      fetch("/api/projects", { cache: "no-store" }).then((response) => response.json().catch(() => ({}))),
      fetch("/api/skills/safety/reviews?limit=100", { cache: "no-store" }).then((response) => response.json().catch(() => ({ items: [] })))]
      );
      if (!healthResponse.ok) {
        throw new Error(t("app.admin.dashboard.extensions.page.kae795c9a"));
      }
      const healthPayload = await healthResponse.json();
      const projectItems = Array.isArray(projectsPayload?.projects) ? projectsPayload.projects : [];
      const catalogParams = new URLSearchParams();
      if (previewScope.startsWith("project:")) {
        const projectId = previewScope.slice("project:".length);
        const project = projectItems.find((item: ProjectRecord) => item.id === projectId);
        if (project?.workspacePath) {
          catalogParams.set("workspacePath", project.workspacePath);
          catalogParams.set("projectId", project.id);
          if (project.workspaceId) {
            catalogParams.set("workspaceId", project.workspaceId);
          }
        }
      }
      const catalogResponse = await fetch(
        `/api/extensions/catalog${catalogParams.toString() ? `?${catalogParams.toString()}` : ""}`,
        { cache: "no-store" }
      );
      if (!catalogResponse.ok) {
        throw new Error(t("app.admin.dashboard.extensions.page.kae795c9a"));
      }
      const catalogPayload = await catalogResponse.json();
      setCatalog(catalogPayload);
      setHealth(healthPayload);
      setConfigEnvelope(config);
      setModels(Array.isArray(modelList) ? modelList : []);
      setProjects(projectItems);
      setSkillSafetyReviews(Array.isArray(skillSafetyPayload?.items) ? skillSafetyPayload.items : []);
    } finally
    {
      setLoading(false);
    }
  }, [previewScope, t]);
  useEffect(() => {
    void loadData();
  }, [loadData]);
  const prefilterModels = useMemo(() => models.filter((model) => !["EMBEDDING", "RERANK", "RERANKER"].includes((model.type || "").toUpperCase())), [models]);
  const summaryItems = useMemo(() => [
  { label: "app.admin.dashboard.extensions.page.ke431abc9", value: catalog?.summary.skillCount ?? 0, description: "app.admin.dashboard.extensions.page.kfe05ff1c" },
  { label: "app.admin.dashboard.extensions.page.k1b083815", value: catalog?.summary.mcpServerCount ?? 0, description: "app.admin.dashboard.extensions.page.k8f8a9a70" },
  { label: "app.admin.dashboard.extensions.page.k80047162", value: catalog?.summary.connectedMcpServerCount ?? 0, description: "app.admin.dashboard.extensions.page.kc0f82f02" },
  { label: "app.admin.dashboard.extensions.page.k1521f304", value: catalog?.summary.mcpToolCount ?? 0, description: "app.admin.dashboard.extensions.page.k0e799947" }],
  [catalog]);
  const previewSkillStage1Entries = previewResult?.skillStage1Entries || [];
  const previewSkillFinalEntries = previewResult?.skillEntries || [];
  const previewMcpStage1Servers = previewResult?.mcpStage1Servers || [];
  const previewMcpFinalServers = previewResult?.mcpServers || previewResult?.mcpFamilies || [];
  const defaultWorkspacePath = useMemo(() => {
    const descriptors = catalog?.skills?.rootDescriptors || [];
    return String(descriptors.find((item) => item.sourceType === "main_workspace")?.workspacePath || "").trim();
  }, [catalog?.skills?.rootDescriptors]);
  const selectedPreviewProject = useMemo(() => {
    if (!previewScope.startsWith("project:")) return null;
    const id = previewScope.slice("project:".length);
    return projects.find((project) => project.id === id) || null;
  }, [previewScope, projects]);
  const prefilterPolicy = (configEnvelope?.data?.prefilterPolicy || {}) as NonNullable<ExtensionsConfigData["prefilterPolicy"]>;
  const skillsPrefilter = (prefilterPolicy.skills || {}) as NonNullable<NonNullable<ExtensionsConfigData["prefilterPolicy"]>["skills"]>;
  const mcpPrefilter = (prefilterPolicy.mcp || {}) as NonNullable<NonNullable<ExtensionsConfigData["prefilterPolicy"]>["mcp"]>;
  const mergeStageConfig = (
  current: NonNullable<ExtensionsConfigData["prefilterPolicy"]>,
  stageKey: "skills" | "mcp",
  patch: NonNullable<NonNullable<ExtensionsConfigData["prefilterPolicy"]>[typeof stageKey]>) => (
  {
    ...current,
    [stageKey]: { ...(current[stageKey] || {}), ...patch }
  });
  const updateConfig = (patch: Partial<ExtensionsConfigData>) => {
    if (!configEnvelope)
    return;
    setConfigEnvelope({
      ...configEnvelope,
      data: {
        ...configEnvelope.data,
        ...patch,
        prefilterPolicy: { ...(configEnvelope.data?.prefilterPolicy || {}), ...(patch.prefilterPolicy || {}) },
        modelBindings: { ...(configEnvelope.data?.modelBindings || {}), ...(patch.modelBindings || {}) }
      }
    });
  };
  const handleSaveConfig = async () => {
    if (!configEnvelope)
    return;
    setSaving(true);
    try {
      const next = await saveConfigDomain<ExtensionsConfigData>("extensions", {
        data: {
          prefilterPolicy: {
            enabled: Boolean(configEnvelope.data?.prefilterPolicy?.enabled),
            mode: "two_stage",
            skills: {
              stage1Enabled: Boolean(configEnvelope.data?.prefilterPolicy?.skills?.stage1Enabled ?? true),
              stage1TopK: Number(configEnvelope.data?.prefilterPolicy?.skills?.stage1TopK || 20),
              llmEnabled: Boolean(configEnvelope.data?.prefilterPolicy?.skills?.llmEnabled ?? true),
              stage2TopK: Number(configEnvelope.data?.prefilterPolicy?.skills?.stage2TopK || 5),
              llmTimeoutSeconds: Number(configEnvelope.data?.prefilterPolicy?.skills?.llmTimeoutSeconds || 5)
            },
            mcp: {
              stage1Enabled: Boolean(configEnvelope.data?.prefilterPolicy?.mcp?.stage1Enabled ?? true),
              stage1TopK: Number(configEnvelope.data?.prefilterPolicy?.mcp?.stage1TopK || 20),
              llmEnabled: Boolean(configEnvelope.data?.prefilterPolicy?.mcp?.llmEnabled ?? true),
              stage2TopK: Number(configEnvelope.data?.prefilterPolicy?.mcp?.stage2TopK || 2),
              llmTimeoutSeconds: Number(configEnvelope.data?.prefilterPolicy?.mcp?.llmTimeoutSeconds || 5)
            }
          },
          modelBindings: { prefilterModel: String(configEnvelope.data?.modelBindings?.prefilterModel || "").trim() }
        }
      });
      setConfigEnvelope(next);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 1800);
      toast({ title: t("app.admin.dashboard.extensions.page.k0498cb65") });
    }
    catch (error) {
      toast({
        title: t("app.admin.dashboard.extensions.page.k12769ce1"),
        description: error instanceof Error ? error.message : t("app.admin.dashboard.extensions.page.ke0d2c647"),
        variant: "destructive"
      });
    } finally
    {
      setSaving(false);
    }
  };
  const handleReloadSystem = async () => {
    setReloading(true);
    try {
      const res = await fetch("/api/extensions/reload", { method: "POST" });
      if (!res.ok)
      throw new Error(t("app.admin.dashboard.extensions.page.k812655ea"));
      await res.json();
      await loadData();
      toast({ title: t("app.admin.dashboard.extensions.page.kcfa8ac90") });
    }
    catch {
      toast({ title: t("app.admin.dashboard.extensions.page.k22aa01cb"), description: t("app.admin.dashboard.extensions.page.ke0d2c647"), variant: "destructive" });
    } finally
    {
      setReloading(false);
    }
  };
  const previewExtensionsSelection = async () => {
    const normalizedQuery = String(previewQuery || "").trim();
    if (!normalizedQuery)
    return;
    setPreviewLoading(true);
    setPreviewError("");
    setPreviewedQuery(normalizedQuery);
    try {
      const params = new URLSearchParams({ query: normalizedQuery });
      if (selectedPreviewProject?.workspacePath) {
        params.set("workspacePath", selectedPreviewProject.workspacePath);
        params.set("projectId", selectedPreviewProject.id);
        if (selectedPreviewProject.workspaceId) {
          params.set("workspaceId", selectedPreviewProject.workspaceId);
        }
      } else if (defaultWorkspacePath) {
        params.set("workspacePath", defaultWorkspacePath);
      }
      const res = await fetch(`/api/extensions/preview?${params.toString()}`, { cache: "no-store" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(String(data?.detail || data?.error || t("app.admin.dashboard.extensions.page.kca3eef0d")));
      }
      setPreviewResult(data as ExtensionPrefilterPreviewResponse);
    }
    catch (error) {
      const message = error instanceof Error ? error.message : t("app.admin.dashboard.extensions.page.kca3eef0d");
      setPreviewResult(null);
      setPreviewError(message);
      toast({
        title: t("app.admin.dashboard.extensions.page.k69be1591"),
        description: message,
        variant: "destructive"
      });
    } finally
    {
      setPreviewLoading(false);
    }
  };
  const handleCommandInstall = async () => {
    if (!commandInput.trim())
    return;
    setInstallingCommand(true);
    setInstallResult(null);
    try {
      const res = await fetch("/api/skills/install/command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command: commandInput })
      });
      const data = await res.json();
      if (!res.ok)
      throw new Error(String(data?.detail || data?.error || t("app.admin.dashboard.extensions.page.k08260d4c")));
      setInstallResult(data);
      setCommandInput("");
      toast({ title: t("app.admin.dashboard.extensions.page.k4877c2e6"), description: t("app.admin.dashboard.extensions.page.kc33ca4fe", {
          data_installed_length_0: data.installed?.length ?? 0
        }) });
      await loadData();
    }
    catch (error) {
      toast({
        title: t("app.admin.dashboard.extensions.page.k77e8b0ea"),
        description: error instanceof Error ? error.message : t("app.admin.dashboard.extensions.page.k037fd762"),
        variant: "destructive"
      });
    } finally
    {
      setInstallingCommand(false);
    }
  };
  const handleZipUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file)
    return;
    setUploadingZip(true);
    setZipValidationError("");
    try {
      if (!String(file.name || "").toLowerCase().endsWith(".zip")) {
        throw new Error(t("app.admin.dashboard.extensions.page.k39d87bf6"));
      }
      if (file.size <= 0) {
        throw new Error(t("app.admin.dashboard.extensions.page.kefbf07b6"));
      }
      setZipFileLabel(`${file.name} · ${Math.max(1, Math.round(file.size / 1024))} KB`);
      const formData = new FormData();
      formData.append("file", file);
      const res = await fetch("/api/skills/install/zip", { method: "POST", body: formData });
      const data = await res.json();
      if (!res.ok) {
        const validation = extractValidationPayload(data);
        throw new Error(localizeSkillZipValidationPayload(validation, t) || String(data?.detail || data?.error || t("app.admin.dashboard.extensions.page.k28d7f856")));
      }
      setInstallResult(data);
      toast({ title: t("app.admin.dashboard.extensions.page.k98312139") });
      await loadData();
    }
    catch (error) {
      setZipValidationError(error instanceof Error ? error.message : t("app.admin.dashboard.extensions.page.k61c03dc2"));
      toast({
        title: t("app.admin.dashboard.extensions.page.k0dc966ec"),
        description: error instanceof Error ? error.message : t("app.admin.dashboard.extensions.page.k61c03dc2"),
        variant: "destructive"
      });
    } finally
    {
      setUploadingZip(false);
      if (fileInputRef.current)
      fileInputRef.current.value = "";
    }
  };
  const saveMcpConfig = async () => {
    if (!mcpConfigInput.trim())
    return;
    setSavingMcp(true);
    setMcpValidationError("");
    try {
      const validation = validateMcpJsonInput(mcpConfigInput, t);
      setMcpValidationSummary(t("app.admin.dashboard.extensions.page.k26d037a0", {
        validation_serverCount: validation.serverCount
      }));
      const res = await fetch("/api/mcp/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: mcpConfigInput
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const validationError = extractValidationPayload(data);
        throw new Error(localizeMcpValidationPayload(validationError, t) || t("app.admin.dashboard.extensions.page.k6e203323"));
      }
      setMcpDialogOpen(false);
      setMcpConfigInput("");
      setMcpValidationSummary("");
      toast({ title: t("app.admin.dashboard.extensions.page.kceb42548"), description: t("app.admin.dashboard.extensions.page.kcc0b918f") });
      await loadData();
    }
    catch (error) {
      setMcpValidationError(error instanceof Error ? error.message : t("app.admin.dashboard.extensions.page.k02db39a8"));
      toast({
        title: t("app.admin.dashboard.extensions.page.ka7539197"),
        description: error instanceof Error ? error.message : t("app.admin.dashboard.extensions.page.k02db39a8"),
        variant: "destructive"
      });
    } finally
    {
      setSavingMcp(false);
    }
  };
  const deleteMcpServer = async (serverName: string) => {
    const normalizedName = String(serverName || "").trim();
    if (!normalizedName)
    return;
    const confirmed = window.confirm(tg(t, "731f1b28", { value1:
      normalizedName }));
    if (!confirmed)
    return;
    setDeletingMcpServer(normalizedName);
    try {
      const res = await fetch(`/api/mcp/config/${encodeURIComponent(normalizedName)}`, { method: "DELETE" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const validationError = extractValidationPayload(data);
        throw new Error(localizeMcpValidationPayload(validationError, t) || String(data?.detail || data?.error || tg(t, "f6f40de6")));
      }
      setCatalog((previous) => previous ? {
        ...previous,
        mcp: {
          ...previous.mcp,
          servers: (previous.mcp?.servers || []).filter((server) => server.name !== normalizedName)
        }
      } : previous);
      toast({
        title: data?.alreadyRemovedFromConfig ? tg(t, "93607b46") : tg(t, "92b60366"),


        description: tg(t, "2ec10ec7")
      });
      await loadData();
    }
    catch (error) {
      toast({
        title: tg(t, "8cc73e26"),
        description: error instanceof Error ? error.message : tg(t, "2fb13dfc"),
        variant: "destructive"
      });
    } finally
    {
      setDeletingMcpServer("");
    }
  };
  const deleteSkill = async (skill: ExtensionSkillItem) => {
    const skillId = String(skill.skillId || "").trim();
    if (!skillId)
    return;
    const skillName = String(skill.name || skillId);
    const isGlobal = String(skill.visibility || "global") !== "scoped";
    const confirmed = window.confirm(tg(t, "1cea8488", { value1:
      skillName, value2: tg(t, "20494ad9") }));
    if (!confirmed)
    return;
    setDeletingSkillId(skillId);
    try {
      const params = new URLSearchParams();
      params.set("scope", isGlobal ? "global" : "workspace");
      if (skill.workspaceId)
      params.set("workspaceId", skill.workspaceId);
      if (skill.workspacePath)
      params.set("workspacePath", skill.workspacePath);
      if (skill.projectId)
      params.set("projectId", skill.projectId);
      const res = await fetch(`/api/extensions/skills/${encodeURIComponent(skillId)}?${params.toString()}`, { method: "DELETE" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(String(data?.detail || data?.error || tg(t, "e83e6c41")));
      }
      toast({
        title: tg(t, "e95e338d"),
        description: tg(t, "955629d4")
      });
      await loadData();
    }
    catch (error) {
      toast({
        title: tg(t, "a4e79e03"),
        description: error instanceof Error ? error.message : tg(t, "2fb13dfc"),
        variant: "destructive"
      });
    } finally
    {
      setDeletingSkillId("");
    }
  };
  if (loading || !catalog || !health || !configEnvelope) {
    return <div className="flex min-h-[320px] items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
            </div>;
  }
  const clampRange = (value: number, min: number, max: number) => Math.max(min, Math.min(value, max));
  const prefilterEnabled = Boolean(prefilterPolicy?.enabled);
  const prefilterModel = String(configEnvelope.data?.modelBindings?.prefilterModel || "").trim();
  const skillsStage1Enabled = Boolean(skillsPrefilter.stage1Enabled ?? true);
  const skillsStage1TopK = Number(skillsPrefilter.stage1TopK || 20);
  const skillsLlmEnabled = Boolean(skillsPrefilter.llmEnabled ?? true);
  const skillsStage2TopK = Number(skillsPrefilter.stage2TopK || 5);
  const skillsLlmTimeoutSeconds = Number(skillsPrefilter.llmTimeoutSeconds || 5);
  const mcpStage1Enabled = Boolean(mcpPrefilter.stage1Enabled ?? true);
  const mcpStage1TopK = Number(mcpPrefilter.stage1TopK || 20);
  const mcpLlmEnabled = Boolean(mcpPrefilter.llmEnabled ?? true);
  const mcpStage2TopK = Number(mcpPrefilter.stage2TopK || 2);
  const mcpLlmTimeoutSeconds = Number(mcpPrefilter.llmTimeoutSeconds || 5);
  const previewCounts = previewResult?.counts;
  const previewSkillsStage1Enabled = Boolean(previewCounts?.stage1Enabled?.skills ?? true);
  const previewMcpStage1Enabled = Boolean(previewCounts?.stage1Enabled?.mcp ?? true);
  const skillSafetyDisabledCount = skillSafetyReviews.filter((item) => item.disabled).length;
  const skillSafetyReviewCount = skillSafetyReviews.filter((item) => String(item.effective_verdict || "").toLowerCase() === "review" && !item.disabled).length;
  const skillSafetyApprovedCount = skillSafetyReviews.filter((item) => String(item.user_override || "").toLowerCase() === "approved" && !item.disabled).length;
  const runtimeStartupState = String(health.runtime?.startupState || catalog.startupState || "cold").trim().toLowerCase();
  const snapshotFreshness = String(health.runtime?.snapshotFreshness || catalog.snapshotFreshness || "cold").trim().toLowerCase();
  const silkAvailable = Boolean(health.silk?.available ?? health.runtime?.silk?.available);
  const silkVersion = String(health.silk?.version || health.runtime?.silk?.version || "").trim();
  const silkRoot = String(health.silk?.toolRoot || health.runtime?.silk?.toolRoot || "").trim();
  const dependencyPolicy = catalog.skillDependencyPolicy || health.skillDependencyPolicy || {};
  const skillsPolicyBadge = skillsStage1Enabled ?
  skillsLlmEnabled ? `${skillsStage1TopK} → ${skillsStage2TopK} / ${skillsLlmTimeoutSeconds}s` : `${skillsStage1TopK}` :
  skillsLlmEnabled ? `full → ${skillsStage2TopK} / ${skillsLlmTimeoutSeconds}s` : t("admin.pages.extensions.prefilter.fullInventory");
  const mcpPolicyBadge = mcpStage1Enabled ?
  mcpLlmEnabled ? `${mcpStage1TopK} → ${mcpStage2TopK} / ${mcpLlmTimeoutSeconds}s` : `${mcpStage1TopK}` :
  mcpLlmEnabled ? `full → ${mcpStage2TopK} / ${mcpLlmTimeoutSeconds}s` : t("admin.pages.extensions.prefilter.fullInventory");
  return <AdminPageShell>
            <AdminPageHeader title={"app.admin.dashboard.extensions.page.k5b035c36"} description={"app.admin.dashboard.extensions.page.k042a5a79"} actions={<div className="flex items-center gap-3">
                        <InlineSaveState saving={saving} saved={saved} label={t("app.admin.dashboard.extensions.page.kcc06e009")} />
                        <Button onClick={() => void handleSaveConfig()} disabled={saving}>
                            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                            {t("app.admin.dashboard.extensions.page.k6010e1ed")}
                        </Button>
                        <Button variant="outline" onClick={() => void loadData()} disabled={reloading || saving}>
                            <RefreshCw className="mr-2 h-4 w-4" />
                            {t("app.admin.dashboard.extensions.page.k286cb634")}
                        </Button>
                        <Button onClick={() => void handleReloadSystem()} disabled={reloading || saving}>
                            {reloading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                            {t("app.admin.dashboard.extensions.page.ke25fea31")}
                        </Button>
                    </div>} />

            <DomainSummaryStrip items={summaryItems} />
            {runtimeStartupState === "refreshing" ? <StatusNotice title={"app.admin.dashboard.extensions.page.ke3fbd37c"} description={t("app.admin.dashboard.extensions.page.k575262a6", {
      snapshotFreshness_live_live_snapshotFreshness_cached: snapshotFreshness === "live" ? "live" : t("components.plugin.host.PluginHostWorkbench.snapshotFreshnessCached")
    })} tone="info" /> : null}
            {runtimeStartupState === "error" ? <StatusNotice title={"app.admin.dashboard.extensions.page.kc3221dca"} description={health.lastRefreshError || catalog.lastRefreshError || t("app.admin.dashboard.extensions.page.ka1c8eb51")} tone="warning" /> : null}
            <StatusNotice title={silkAvailable ? "app.admin.dashboard.extensions.page.kdba6ed3d" : "app.admin.dashboard.extensions.page.kf4e67cdf"} description={silkAvailable ?
    t("app.admin.dashboard.extensions.page.kc090216b", {
      silkVersion_silkVersion: silkVersion ? tg(t, "be614a10", { value1: silkVersion }) : "",
      silkRoot_silkRoot: silkRoot ? tg(t, "a91318cc", { value1: silkRoot }) : ""
    }) : t("app.admin.dashboard.extensions.page.k8c0be031", {
      silkRoot_silkRoot: silkRoot ? tg(t, "3baf9380", { value1: silkRoot }) : ""
    })} tone={silkAvailable ? "success" : "warning"} />

            <ConfigCard title={"app.admin.dashboard.extensions.page.kcc06e009"} description={"app.admin.dashboard.extensions.page.k3605ab6b"}>
                <div className="space-y-5">
                    <div className="space-y-5">
                        <SettingToggleCard
                            title={t("app.admin.dashboard.extensions.page.k74dc7104")}
                            description={t("app.admin.dashboard.extensions.page.k758d3280")}
                            checked={prefilterEnabled}
                            onCheckedChange={(checked) => updateConfig({ prefilterPolicy: { enabled: checked, mode: "two_stage" } })}
                            className="border-slate-200 bg-slate-50/80 px-4 py-3 rounded-2xl"
                        />

                        <div className="space-y-2">
                            <Label>{t("app.admin.dashboard.extensions.page.k4c4359c1")}</Label>
                            <ModelSelect
              models={prefilterModels}
              value={prefilterModel || "__empty__"}
              emptyLabel={t("app.admin.dashboard.extensions.page.kccd8e176")}
              placeholder={t("app.admin.dashboard.extensions.page.kccd8e176")}
              onValueChange={(value) => updateConfig({ modelBindings: { prefilterModel: value } })} />

                            <p className="text-xs leading-5 text-slate-500">
                                {t("app.admin.dashboard.extensions.page.k2cebabf6")}
                            </p>
                        </div>

                        <div className="grid gap-4 xl:grid-cols-2">
                            <div className="space-y-4 rounded-2xl border border-slate-200 bg-white p-4">
                                <div className="flex items-center justify-between gap-3">
                                    <div className="text-sm font-semibold text-slate-900">{tg(t, "79736210")}</div>
                                    <Badge variant="outline">{skillsPolicyBadge}</Badge>
                                </div>
                                <PolicyToggleCard title={tg(t, "49bc8921")} description={tg(t, "20cdf19c")} checked={skillsStage1Enabled} onCheckedChange={(checked) => updateConfig({
                prefilterPolicy: mergeStageConfig(prefilterPolicy, "skills", { stage1Enabled: checked })
              })}>
                                    <SliderField label={tg(t, "ca97d660")} value={skillsStage1TopK} min={1} max={100} disabled={!skillsStage1Enabled} onValueChange={(value) => updateConfig({
                  prefilterPolicy: mergeStageConfig(prefilterPolicy, "skills", {
                    stage1TopK: clampRange(value, 1, 100)
                  })
                })} hint={tg(t, "2656f849")} />
                                </PolicyToggleCard>
                                <PolicyToggleCard title={tg(t, "ab537e9f")} description={tg(t, "ff8dca4e")} checked={skillsLlmEnabled} onCheckedChange={(checked) => updateConfig({
                prefilterPolicy: mergeStageConfig(prefilterPolicy, "skills", { llmEnabled: checked })
              })}>
                                    <div className="grid gap-4 md:grid-cols-2">
                                        <SliderField label={tg(t, "bbcb0c2a")} value={skillsStage2TopK} min={1} max={50} disabled={!skillsLlmEnabled} onValueChange={(value) => updateConfig({
                    prefilterPolicy: mergeStageConfig(prefilterPolicy, "skills", {
                      stage2TopK: clampRange(value, 1, 50)
                    })
                  })} />
                                        <SliderField label={tg(t, "8cb99dde")} value={skillsLlmTimeoutSeconds} min={5} max={10} disabled={!skillsLlmEnabled} onValueChange={(value) => updateConfig({
                    prefilterPolicy: mergeStageConfig(prefilterPolicy, "skills", {
                      llmTimeoutSeconds: clampRange(value, 5, 10)
                    })
                  })} />
                                    </div>
                                </PolicyToggleCard>
                            </div>

                            <div className="space-y-4 rounded-2xl border border-slate-200 bg-white p-4">
                                <div className="flex items-center justify-between gap-3">
                                    <div className="text-sm font-semibold text-slate-900">{tg(t, "48ba093e")}</div>
                                    <Badge variant="outline">{mcpPolicyBadge}</Badge>
                                </div>
                                <PolicyToggleCard title={tg(t, "49bc8921")} description={tg(t, "0abf4d17")} checked={mcpStage1Enabled} onCheckedChange={(checked) => updateConfig({
                prefilterPolicy: mergeStageConfig(prefilterPolicy, "mcp", { stage1Enabled: checked })
              })}>
                                    <SliderField label={tg(t, "ca97d660")} value={mcpStage1TopK} min={1} max={100} disabled={!mcpStage1Enabled} onValueChange={(value) => updateConfig({
                  prefilterPolicy: mergeStageConfig(prefilterPolicy, "mcp", {
                    stage1TopK: clampRange(value, 1, 100)
                  })
                })} hint={tg(t, "0e9110ba")} />
                                </PolicyToggleCard>
                                <PolicyToggleCard title={tg(t, "ab537e9f")} description={tg(t, "71de3a30")} checked={mcpLlmEnabled} onCheckedChange={(checked) => updateConfig({
                prefilterPolicy: mergeStageConfig(prefilterPolicy, "mcp", { llmEnabled: checked })
              })}>
                                    <div className="grid gap-4 md:grid-cols-2">
                                        <SliderField label={tg(t, "bbcb0c2a")} value={mcpStage2TopK} min={1} max={50} disabled={!mcpLlmEnabled} onValueChange={(value) => updateConfig({
                    prefilterPolicy: mergeStageConfig(prefilterPolicy, "mcp", {
                      stage2TopK: clampRange(value, 1, 50)
                    })
                  })} />
                                        <SliderField label={tg(t, "8cb99dde")} value={mcpLlmTimeoutSeconds} min={5} max={10} disabled={!mcpLlmEnabled} onValueChange={(value) => updateConfig({
                    prefilterPolicy: mergeStageConfig(prefilterPolicy, "mcp", {
                      llmTimeoutSeconds: clampRange(value, 5, 10)
                    })
                  })} />
                                    </div>
                                </PolicyToggleCard>
                            </div>
                        </div>
                    </div>

                    <div className="space-y-3 rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-4 text-sm text-slate-600">
                        <div className="text-sm font-semibold text-slate-900">{t("app.admin.dashboard.stability.strategy.page.k2837705a")}</div>
                        <div className="flex items-center justify-between gap-3"><span>{t("app.admin.dashboard.extensions.page.k626da329")}</span><Badge variant={prefilterEnabled ? "default" : "secondary"}>{prefilterEnabled ? t("app.admin.dashboard.extensions.page.kdb6c0cc1") : t("app.admin.dashboard.extensions.page.k12b31ba6")}</Badge></div>
                        <div className="flex items-center justify-between gap-3"><span>{t("app.admin.dashboard.extensions.page.k154a393b")}</span><Badge variant="outline">{prefilterModel || t("app.admin.dashboard.extensions.page.k54745147")}</Badge></div>
                        <div className="flex items-center justify-between gap-3"><span>{tg(t, "67306bc1")}</span><Badge variant="outline">{skillsPolicyBadge}</Badge></div>
                        <div className="flex items-center justify-between gap-3"><span>{tg(t, "3f37427c")}</span><Badge variant="outline">{mcpPolicyBadge}</Badge></div>
                        <div className="flex items-center justify-between gap-3"><span>{tg(t, "2d1ba0ff")}</span><Badge variant="outline">{tg(t, "e23e4778")}</Badge></div>
                        <div className="rounded-xl border border-slate-200 bg-white px-3 py-3 text-xs leading-6 text-slate-500">
                            {tg(t, "4b107a8b")

            }
                        </div>
                    </div>
                </div>
            </ConfigCard>

            <ConfigCard title={"app.admin.dashboard.extensions.page.kb4447c01"} description={"app.admin.dashboard.extensions.page.keda55a79"}>
                <div className="space-y-5">
                    <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3 text-sm leading-6 text-slate-600">
                        {t("app.admin.dashboard.extensions.page.k66034ec1")}
                    </div>

                    <div className="grid gap-3 lg:grid-cols-[280px_minmax(0,1fr)_120px]">
                        <Select value={previewScope} onValueChange={setPreviewScope}>
                            <SelectTrigger className="h-11">
                                <SelectValue placeholder={tg(t, "0a0abb6e")} />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="default">{t("app.admin.dashboard.projects.workspaces.page.defaultCard.title")}</SelectItem>
                                {projects.map((project) =>
              <SelectItem key={project.id} value={`project:${project.id}`}>
                                        {project.name || project.id}
                                    </SelectItem>
              )}
                            </SelectContent>
                        </Select>
                        <Input value={previewQuery} onChange={(event) => setPreviewQuery(event.target.value)} onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void previewExtensionsSelection();
            }
          }} placeholder={t("app.admin.dashboard.extensions.page.kff8e3438")} className="h-11 flex-1" />
                        <Button onClick={() => void previewExtensionsSelection()} disabled={previewLoading || !previewQuery.trim()} className="h-11 min-w-[120px]">
                            {previewLoading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                            {t("app.admin.dashboard.extensions.page.k76932896")}
                        </Button>
                    </div>
                    <div className="rounded-xl border border-slate-200 bg-slate-50/80 px-3 py-2 text-xs leading-5 text-slate-500">
                        {selectedPreviewProject ?
          tg(t, "f5181fbd", { value1:
            selectedPreviewProject.name || selectedPreviewProject.id, value2: selectedPreviewProject.workspacePath || "" }) :
          tg(t, "0b6e9949", { value1:
            defaultWorkspacePath || t("app.admin.dashboard.system.base.page.k6ed9c299") })}
                    </div>

                    {previewResult ? <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
                            <StatPill label={tg(t, "b9b6b6e7")} value={previewCounts?.skillInventoryCount ?? previewCounts?.skillPoolSize ?? 0} />
                            <StatPill label={tg(t, "2d31de55")} value={previewCounts?.skillStage1ShortlistCount ?? previewSkillStage1Entries.length} />
                            <StatPill label={tg(t, "00365fb5")} value={previewCounts?.skillFinalExposedCount ?? previewSkillFinalEntries.length} />
                            <StatPill label={tg(t, "c92c805c")} value={previewCounts?.mcpInventoryCount ?? previewCounts?.mcpServerPoolSize ?? previewCounts?.mcpFamilyPoolSize ?? 0} />
                            <StatPill label={tg(t, "fc4516cc")} value={previewCounts?.mcpStage1ShortlistCount ?? previewMcpStage1Servers.length} />
                            <StatPill label={tg(t, "3819c372")} value={previewCounts?.mcpFinalExposedCount ?? previewMcpFinalServers.length} />
                        </div> : null}

                    {previewResult ? <div className="grid gap-3 lg:grid-cols-3">
                            <div className="rounded-2xl border border-slate-200 bg-slate-50/70 px-4 py-3 text-sm text-slate-600">
                                <div className="text-xs uppercase tracking-wide text-slate-500">{tg(t, "15779211")}</div>
                                <div className="mt-2 font-medium text-slate-900">{previewCounts?.routingMode || previewCounts?.mode || "stage1_only"}</div>
                                <div className="mt-2 text-xs leading-6 text-slate-500">
                                    {`skills=${previewCounts?.skillsRoutingMode || "stage1_only"} · mcp=${previewCounts?.mcpRoutingMode || "stage1_only"}`}
                                </div>
                            </div>
                            <div className="rounded-2xl border border-slate-200 bg-slate-50/70 px-4 py-3 text-sm text-slate-600">
                                <div className="text-xs uppercase tracking-wide text-slate-500">{tg(t, "912c7155")}</div>
                                <div className="mt-2 text-slate-900">
                                    {`stage1=${previewSkillsStage1Enabled ? `on(${previewCounts?.stage1TopK?.skills ?? 0})` : "off"}, hits=${previewCounts?.skillStage1HitCount ?? 0}, shortlist=${previewCounts?.skillStage1ShortlistCount ?? previewSkillStage1Entries.length}, stage2=${previewCounts?.stage2Enabled?.skills ? previewCounts?.stage2TopK?.skills ?? 0 : "off"}, final=${previewCounts?.skillFinalExposedCount ?? previewSkillFinalEntries.length}, timeout=${previewCounts?.llmTimeoutSeconds?.skills ?? 0}s`}
                                </div>
                            </div>
                            <div className="rounded-2xl border border-slate-200 bg-slate-50/70 px-4 py-3 text-sm text-slate-600">
                                <div className="text-xs uppercase tracking-wide text-slate-500">{tg(t, "eaa43b49")}</div>
                                <div className="mt-2 text-slate-900">
                                    {`stage1=${previewMcpStage1Enabled ? `on(${previewCounts?.stage1TopK?.mcp ?? 0})` : "off"}, hits=${previewCounts?.mcpStage1HitCount ?? 0}, shortlist=${previewCounts?.mcpStage1ShortlistCount ?? previewMcpStage1Servers.length}, stage2=${previewCounts?.stage2Enabled?.mcp ? previewCounts?.stage2TopK?.mcp ?? 0 : "off"}, final=${previewCounts?.mcpFinalExposedCount ?? previewMcpFinalServers.length}, timeout=${previewCounts?.llmTimeoutSeconds?.mcp ?? 0}s`}
                                </div>
                            </div>
                        </div> : null}

                    {previewResult ? <div className="grid gap-6 xl:grid-cols-2">
                            <div className="space-y-3 rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                                <div className="flex items-center justify-between gap-3">
                                    <div className="text-sm font-semibold text-slate-900">{tg(t, "fbecfab2")}</div>
                                    <Badge variant="outline">{previewCounts?.skillFinalExposedCount ?? previewSkillFinalEntries.length}</Badge>
                                </div>
                                {previewSkillFinalEntries.length === 0 ? <div className="rounded-2xl border border-dashed border-slate-200 bg-white px-4 py-6 text-sm leading-6 text-slate-500">
                                        {tg(t, "34558308")}
                                    </div> : <div className="max-h-[42rem] space-y-3 overflow-y-auto pr-1">
                                        {previewSkillFinalEntries.map((skill) => <div key={`final:${skill.skillId || skill.instructionPath || skill.skillRoot || skill.skillName}`} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                                                <div className="flex flex-wrap items-center gap-2">
                                                    <PackageCheck className="h-4 w-4 text-emerald-600" />
                                                    <div className="text-sm font-semibold text-slate-900">{skill.skillName || "unknown"}</div>
                                                    <Badge variant="secondary">{skillSourceBadgeLabel(skill.sourceType, t)}</Badge>
                                                    {skill.visibility === "scoped" ? <Badge variant="outline">{t("app.admin.dashboard.extensions.page.k43e1d513")}</Badge> : null}
                                                </div>
                                                {skill.skillId ? <div className="mt-2 break-all rounded-xl bg-slate-50 px-3 py-2 text-[11px] text-slate-500">id: {skill.skillId}</div> : null}
                                                {skill.workspacePath ? <div className="mt-2 break-all rounded-xl bg-slate-50 px-3 py-2 text-[11px] text-slate-500">{t("app.admin.dashboard.extensions.page.kd723b49c")}{skill.workspacePath}</div> : null}
                                                {skill.projectId ? <div className="mt-2 rounded-xl bg-slate-50 px-3 py-2 text-[11px] text-slate-500">{t("app.admin.dashboard.extensions.page.k6c66fa4c")}{skill.projectId}</div> : null}
                                                {skill.instructionPath ? <div className="mt-2 break-all rounded-xl bg-slate-50 px-3 py-2 text-[11px] text-slate-500">{skill.instructionPath}</div> : null}
                                            </div>)}
                                    </div>}
                            </div>

                            <div className="space-y-3 rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                                <div className="flex items-center justify-between gap-3">
                                    <div className="text-sm font-semibold text-slate-900">{tg(t, "2c0b2ebf")}</div>
                                    <Badge variant="outline">{previewCounts?.mcpFinalExposedCount ?? previewMcpFinalServers.length}</Badge>
                                </div>
                                {previewMcpFinalServers.length === 0 ? <div className="rounded-2xl border border-dashed border-slate-200 bg-white px-4 py-6 text-sm leading-6 text-slate-500">
                                        {tg(t, "657e53a4")}
                                    </div> : <div className="max-h-[42rem] space-y-3 overflow-y-auto pr-1">
                                        {previewMcpFinalServers.map((server) => <div key={`final:${server.serverKey || server.familyKey || server.serverName}`} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                                                <div className="flex flex-wrap items-center gap-2">
                                                    <Server className="h-4 w-4 text-sky-600" />
                                                    <div className="text-sm font-semibold text-slate-900">{server.serverName || server.title}</div>
                                                    <Badge variant="secondary">server</Badge>
                                                    <Badge variant="outline">{t("app.admin.dashboard.extensions.page.kb1d8ed4b", {
                      server_toolCount: server.toolCount
                    })}</Badge>
                                                </div>
                                                {(server.descriptions || []).length > 0 ? <div className="mt-2 text-xs leading-6 text-slate-500">
                                                        {(server.descriptions || []).slice(0, 3).join(" / ")}
                                                    </div> : null}
                                                <div className="mt-3 space-y-2">
                                                    {previewMcpServerTools(server).map((tool) => <div key={`${server.serverKey || server.familyKey || server.serverName}:${tool.name}`} className="rounded-xl border border-slate-100 bg-slate-50 px-3 py-2">
                                                            <div className="font-mono text-xs font-semibold text-slate-800">{tool.name}</div>
                                                            {tool.description ? <div className="mt-1 text-xs leading-5 text-slate-500">{tool.description}</div> : null}
                                                        </div>)}
                                                </div>
                                            </div>)}
                                    </div>}
                            </div>
                        </div> : <div className="space-y-3">
                            <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/60 px-4 py-6 text-sm leading-6 text-slate-500">
                                {previewError ?
            previewError :
            t("app.admin.dashboard.extensions.page.k90f6d4f7")}
                            </div>
                        </div>}

                    <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3 text-xs leading-6 text-slate-500">
                        {previewedQuery ?
          t("app.admin.dashboard.extensions.page.kff12c04d", {
            previewedQuery: previewedQuery
          }) : t("app.admin.dashboard.extensions.page.k4113b1ce")}
                    </div>
                </div>
            </ConfigCard>

            <SourceMetaRow source={configEnvelope.source} savePath={configEnvelope.savePath} reloadRequired={configEnvelope.reloadRequired} />

            <ConfigCard title={tg(t, "75497cb2")} description={tg(t, "ee842ada")} variant="list">
                <div className="space-y-4">
                    <div className="grid gap-3 md:grid-cols-3">
                        <StatPill label={t("app.admin.dashboard.rpa.page.kc6ff9900")} value={skillSafetyDisabledCount} />
                        <StatPill label={tg(t, "eaae1132")} value={skillSafetyReviewCount} />
                        <StatPill label={tg(t, "65c814f7")} value={skillSafetyApprovedCount} />
                    </div>
                    <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/70 px-4 py-5 text-sm leading-6 text-slate-500">
                        {skillSafetyReviews.length === 0 ? tg(t, "c38e628d") : tg(t, "f2b2b3a6")

          }
                    </div>
                    <div className="flex justify-end">
                        <Button asChild variant="outline">
                            <a href="/admin/safety-control">{tg(t, "fc2a86a8")}</a>
                        </Button>
                    </div>
                </div>
            </ConfigCard>

            <div className="grid auto-rows-fr gap-4 xl:grid-cols-2">
                <ConfigCard title={"app.admin.dashboard.extensions.page.kec74feaf"} description={"app.admin.dashboard.extensions.page.kcc79174f"} variant="list" bodyHeight={420} bodyScroll="auto" className="h-full">
                    <div className="space-y-3">
                        <div className="space-y-3 rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3 text-sm text-slate-600">
                            <div>
                                {t("app.admin.dashboard.extensions.page.k99bf9749")}
                                <span className="font-medium break-all text-slate-900">{catalog.skills?.root || "—"}</span>
                            </div>
                            <div className="grid gap-2 md:grid-cols-2">
                                <div className="rounded-xl border border-slate-200 bg-white px-3 py-2">
                                    <div className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{tg(t, "7279ecd4")}</div>
                                    <div className="mt-1 text-xs text-slate-700">{String(catalog.catalogScope?.mode || "default")}</div>
                                    {catalog.catalogScope?.projectId ? <div className="mt-1 text-[11px] text-slate-500">{t("app.admin.dashboard.extensions.page.k6c66fa4c")}{catalog.catalogScope.projectId}</div> : null}
                                    {catalog.catalogScope?.workspacePath ? <div className="mt-1 break-all text-[11px] text-slate-500">{t("app.admin.dashboard.extensions.page.kd723b49c")}{catalog.catalogScope.workspacePath}</div> : null}
                                </div>
                                <div className="rounded-xl border border-slate-200 bg-white px-3 py-2">
                                    <div className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{tg(t, "161c82a4")}</div>
                                    <div className="mt-1 break-all text-xs text-slate-700">{catalog.skills?.visibleRootSignature || catalog.visibleRootSignature || "—"}</div>
                                    <div className="mt-1 text-[11px] text-slate-500">{tg(t, "b23c239c")}{String(catalog.skills?.discoveryRevision || "—")}</div>
                                    <div className="mt-1 text-[11px] text-slate-500">{tg(t, "1a5ed9ee")}{String(catalog.skills?.scopedRefreshMode || "base")}</div>
                                </div>
                            </div>
                            <div className="rounded-xl border border-slate-200 bg-white px-3 py-2">
                                <div className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{tg(t, "88063e8d")}</div>
                                <div className="mt-2">
                                    {(catalog.skills?.changedRoots || []).length ?
                <div className="space-y-1">
                                            {(catalog.skills?.changedRoots || []).map((root) =>
                  <div key={String(root)} className="break-all rounded-lg bg-slate-50 px-2 py-1 text-[11px] text-slate-600">
                                                    {String(root)}
                                                </div>
                  )}
                                        </div> :

                <div className="text-xs text-slate-500">{tg(t, "0c294602")}</div>
                }
                                </div>
                            </div>
                            <div className="space-y-2">
                                <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{t("app.admin.dashboard.extensions.page.kcc6ff432")}</div>
                                {(catalog.skills?.rootDescriptors || []).length === 0 ? <div className="text-xs text-slate-500">{t("app.admin.dashboard.extensions.page.k5a1c552a")}</div> : (catalog.skills?.rootDescriptors || []).map((root) => <div key={root.rootPath} className="rounded-xl border border-slate-200 bg-white px-3 py-3">
                                            <div className="flex flex-wrap items-center gap-2">
                                                <Badge variant="secondary">{skillSourceBadgeLabel(root.sourceType, t)}</Badge>
                                                {root.visibility === "scoped" ? <Badge variant="outline">{t("app.admin.dashboard.extensions.page.k3d11f197")}</Badge> : null}
                                            </div>
                                            <div className="mt-2 break-all text-xs text-slate-600">{root.rootPath}</div>
                                            {root.workspacePath ? <div className="mt-1 break-all text-[11px] text-slate-500">{t("app.admin.dashboard.extensions.page.kd723b49c")}{root.workspacePath}</div> : null}
                                            {root.projectId ? <div className="mt-1 text-[11px] text-slate-500">{t("app.admin.dashboard.extensions.page.k6c66fa4c")}{root.projectId}</div> : null}
                                        </div>)}
                            </div>
                        </div>
                        {(catalog.skills?.items || []).length === 0 ? <EmptyState title={t("app.admin.dashboard.extensions.page.k8f2a9946")} description={t("app.admin.dashboard.extensions.page.kd9677b98")} /> : (catalog.skills?.items || []).map((skill) => <div key={skill.skillId || `${skill.name}:${skill.path}`} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                                    <div className="flex items-start justify-between gap-3">
                                        <div className="min-w-0 space-y-2">
                                            <div className="flex items-center gap-2">
                                                <PackageCheck className="h-4 w-4 text-emerald-600" />
                                                <div className="text-sm font-semibold text-slate-900">{skill.name}</div>
                                                <Badge variant="secondary">{skillSourceBadgeLabel(skill.sourceType, t)}</Badge>
                                                {skill.visibility === "scoped" ? <Badge variant="outline">{t("app.admin.dashboard.extensions.page.k43e1d513")}</Badge> : null}
                                            </div>
                                            <div className="line-clamp-2 text-sm leading-6 text-slate-600">{skill.description}</div>
                                            {skill.skillId ? <div className="break-all rounded-xl bg-slate-50 px-3 py-2 text-[11px] text-slate-500">id: {skill.skillId}</div> : null}
                                            {skill.workspacePath ? <div className="break-all rounded-xl bg-slate-50 px-3 py-2 text-[11px] text-slate-500">{t("app.admin.dashboard.extensions.page.kd723b49c")}{skill.workspacePath}</div> : null}
                                            {skill.projectId ? <div className="rounded-xl bg-slate-50 px-3 py-2 text-[11px] text-slate-500">{t("app.admin.dashboard.extensions.page.k6c66fa4c")}{skill.projectId}</div> : null}
                                            <div className="break-all rounded-xl bg-slate-50 px-3 py-2 text-xs text-slate-500">{skill.path}</div>
                                        </div>
                                        <div className="flex shrink-0 items-center gap-2">
                                            <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 text-rose-600 hover:bg-rose-50 hover:text-rose-700"
                  title={tg(t, "8d0a9426")}
                  onClick={() => void deleteSkill(skill)}
                  disabled={!skill.skillId || deletingSkillId === skill.skillId}>

                                                {deletingSkillId === skill.skillId ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                                            </Button>
                                            <CheckCircle2 className="mt-0.5 h-5 w-5 text-emerald-500" />
                                        </div>
                                    </div>
                                </div>)}
                    </div>
                </ConfigCard>

                <ConfigCard title={"app.admin.dashboard.extensions.page.kdbd9cf57"} description={"app.admin.dashboard.extensions.page.kcc7340fc"} variant="list" bodyHeight={420} bodyScroll="auto" className="h-full">
                    <div className="space-y-3">
                        {catalog.mcp.servers.length === 0 ? <EmptyState title={t("app.admin.dashboard.extensions.page.kf3616847")} description={t("app.admin.dashboard.extensions.page.k9baa8ec6")} /> : catalog.mcp.servers.map((server) => <div key={server.name} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                                    <div className="flex items-start justify-between gap-3">
                                        <div className="min-w-0 space-y-2">
                                            <div className="flex flex-wrap items-center gap-2">
                                                <div className="text-sm font-semibold text-slate-900">{server.name}</div>
                                                <Badge variant={server.status === "connected" ? "default" : server.status === "disabled" ? "secondary" : "destructive"}>{statusLabel(server.status, t)}</Badge>
                                                <Badge variant="outline">{server.transport}</Badge>
                                            </div>
                                            <div className="break-all text-xs text-slate-500">{server.target || t("app.admin.dashboard.extensions.page.k2af0f4dc")}</div>
                                            <div className="text-xs text-slate-600">{t("app.admin.dashboard.extensions.page.k43da15a1")}{server.toolCount}</div>
                                            {server.appsSupported ? (
                                                <div className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
                                                    <Badge variant="secondary">{t("app.admin.dashboard.extensions.page.mcpAppsSupported")}</Badge>
                                                    <span>{t("app.admin.dashboard.extensions.page.mcpAppTools")}{server.appToolCount ?? 0}</span>
                                                    <span>{t("app.admin.dashboard.extensions.page.mcpUiResources")}{server.uiResourceCount ?? 0}</span>
                                                </div>
                                            ) : null}
                                            {server.lastAppsError ? (
                                                <div className="rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-700">
                                                    {t("app.admin.dashboard.extensions.page.mcpAppsError")}{server.lastAppsError}
                                                </div>
                                            ) : null}
                                            <div className="flex flex-wrap gap-2">
                                                {server.tools.slice(0, 6).map((tool) => <Badge key={tool.name} variant="secondary">{tool.name}</Badge>)}
                                                {server.tools.length > 6 ? <Badge variant="secondary">+{server.tools.length - 6}</Badge> : null}
                                            </div>
                                        </div>
                                        <div className="flex shrink-0 items-center gap-2">
                                            <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 text-rose-600 hover:bg-rose-50 hover:text-rose-700"
                  title={tg(t, "3d2ba7c9")}
                  onClick={() => void deleteMcpServer(server.name)}
                  disabled={deletingMcpServer === server.name}>

                                                {deletingMcpServer === server.name ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                                            </Button>
                                            <Server className="mt-0.5 h-5 w-5 text-sky-600" />
                                        </div>
                                    </div>
                                </div>)}
                    </div>
                </ConfigCard>
            </div>

            <div className="grid auto-rows-fr gap-4 xl:grid-cols-2">
                <ConfigCard title={"app.admin.dashboard.extensions.page.kf6bbc138"} description={"app.admin.dashboard.extensions.page.kde458108"} variant="editor" bodyHeight="clamp" bodyScroll="auto" className="h-full">
                    <div className="space-y-4">
                        <div className="space-y-2">
                            <AdminHoverInfo
              panelClassName="text-xs leading-6"
              content={
              <>
                                        {t("app.admin.dashboard.extensions.page.k8d26505b")}<span className="font-mono text-white">{"npx skills add <source> [--skill <name>] [--overwrite]"}</span>。
                                        {t("app.admin.dashboard.extensions.page.k684bddc4")}<span className="font-mono text-white">~/.agents/skills</span>。
                                        {t("app.admin.dashboard.extensions.page.k0cd07ab9")}<span className="font-mono text-white">workspace/.agents/skills</span>{t("app.admin.dashboard.extensions.page.k9181443f")}
                                    </>
              }>

                                <Label className="cursor-help">{t("app.admin.dashboard.extensions.page.k94e8c946")}</Label>
                            </AdminHoverInfo>
                            <Input value={commandInput} onChange={(event) => setCommandInput(event.target.value)} placeholder="npx skills add https://github.com/vercel-labs/skills --skill find-skills" />
                            <div className="rounded-2xl border border-amber-200 bg-amber-50/80 px-4 py-3 text-xs leading-6 text-amber-900">
                                <div className="font-medium">
                                    {t("app.admin.dashboard.extensions.page.k2aa71b7e")}
                                </div>
                                <div className="mt-1">
                                    {t("app.admin.dashboard.extensions.page.kf2884ea3")}<span className="font-mono">{dependencyPolicy.pythonTarget || "apps/v8-agent-os-engine/.venv"}</span>{t("app.admin.dashboard.extensions.page.k48288d81")}
                                </div>
                            </div>
                            <div className="text-xs leading-5 text-slate-500">
                                {t("app.admin.dashboard.extensions.page.k55339092")}{" "}
                                <a href="https://skills.sh/" target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-sky-600 underline">
                                    skills.sh
                                    <ExternalLink className="h-3 w-3" />
                                </a>
                                {t("app.admin.dashboard.extensions.page.k032a368a")}
                            </div>
                        </div>
                        {installResult ? <div className="space-y-3 rounded-2xl border border-slate-200 bg-slate-50/80 p-4 text-sm text-slate-700">
                                <div className="flex flex-wrap items-center gap-2"><Badge variant="outline">{t("app.admin.dashboard.extensions.page.ke7139376")}</Badge><span className="break-all">{installResult.source}</span></div>
                                <div className="flex flex-wrap items-center gap-2"><Badge variant="outline">{t("app.admin.dashboard.extensions.page.ke1a0bb35")}</Badge><span className="break-all">{installResult.targetRoot}</span></div>
                                <div className="grid gap-3 md:grid-cols-3">
                                    <StatPill label={t("app.admin.dashboard.extensions.page.kbea3beaa")} value={installResult.installed.length} />
                                    <StatPill label={t("app.admin.dashboard.extensions.page.k83af8057")} value={installResult.conflicts.length} />
                                    <StatPill label={t("app.admin.dashboard.extensions.page.k2cc2fe0c")} value={installResult.warnings.length} />
                                </div>
                            </div> : null}
                        <div className="flex flex-wrap gap-3">
                            <Button onClick={() => void handleCommandInstall()} disabled={installingCommand || !commandInput.trim()}>
                                <Terminal className="mr-2 h-4 w-4" />
                                {installingCommand ? t("app.admin.dashboard.extensions.page.kbdd8dbe7") : t("app.admin.dashboard.extensions.page.k4dcfc814")}
                            </Button>
                            <div className="flex min-w-0 flex-1 items-center gap-3">
                                <Input ref={fileInputRef} type="file" accept=".zip" onChange={handleZipUpload} disabled={uploadingZip} className="hidden" />
                                <Button type="button" variant="outline" onClick={() => fileInputRef.current?.click()} disabled={uploadingZip}>
                                    <Upload className="mr-2 h-4 w-4" />
                                    {t("app.admin.dashboard.extensions.page.k424fe082")}
                                </Button>
                                <div className="min-w-0 flex-1 rounded-2xl border border-dashed border-slate-200 bg-slate-50/80 px-3 py-2 text-xs text-slate-500">
                                    {zipFileLabel || t("app.admin.dashboard.extensions.page.k543b111a")}
                                </div>
                                {uploadingZip ? <Loader2 className="h-4 w-4 animate-spin text-slate-400" /> : null}
                            </div>
                        </div>
                        {zipValidationError ? <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
                                {zipValidationError}
                            </div> : null}
                        <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3 text-xs leading-6 text-slate-600">
                            <div className="font-medium text-slate-900">{t("app.admin.dashboard.extensions.page.k0122c8bd")}</div>
                            <ul className="mt-2 space-y-1">
                                <li>{t("app.admin.dashboard.extensions.page.k7b12f611")}</li>
                                <li>{t("app.admin.dashboard.extensions.page.k1db7e693")}</li>
                                <li>{t("app.admin.dashboard.extensions.page.k92a88923")}</li>
                            </ul>
                        </div>
                    </div>
                </ConfigCard>

                <ConfigCard title={"app.admin.dashboard.extensions.page.k8a16c8db"} description={"app.admin.dashboard.extensions.page.kf25b7ed0"} variant="editor" bodyHeight="clamp" bodyScroll="auto" className="h-full">
                    <div className="space-y-4">
                        <div className="grid gap-3 md:grid-cols-3">
                            <StatPill label={t("app.admin.dashboard.extensions.page.kb54e7c93")} value={health.mcp.statusBreakdown.connected || 0} />
                            <StatPill label={t("app.admin.dashboard.extensions.page.k68ea0239")} value={health.mcp.statusBreakdown.disabled || 0} />
                            <StatPill label={t("app.admin.dashboard.extensions.page.k51f11e87")} value={health.mcp.statusBreakdown.error || 0} />
                        </div>
                        <Dialog open={mcpDialogOpen} onOpenChange={setMcpDialogOpen}>
                            <DialogTrigger asChild>
                                <Button variant="outline">
                                    <Plus className="mr-2 h-4 w-4" />
                                    {t("app.admin.dashboard.extensions.page.k62d9d2e5")}
                                </Button>
                            </DialogTrigger>
                            <DialogContent className="max-w-2xl">
                                <DialogHeader>
                                    <DialogTitle>{t("app.admin.dashboard.extensions.page.k061b2335")}</DialogTitle>
                                    <DialogDescription>{t("app.admin.dashboard.extensions.page.ka0ebb4b7")}</DialogDescription>
                                </DialogHeader>
                                <div className="space-y-3 py-4">
                                    <Textarea className="h-[300px] bg-slate-50 font-mono text-sm" value={mcpConfigInput} onChange={(event) => {
                  setMcpConfigInput(event.target.value);
                  if (mcpValidationError)
                  setMcpValidationError("");
                  if (mcpValidationSummary)
                  setMcpValidationSummary("");
                }} placeholder={'{\n  "mcpServers": {\n    "example": {\n      "command": "npx",\n      "args": ["-y", "@example/server"]\n    }\n  }\n}'} />
                                    {mcpValidationSummary ? <div className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
                                            {mcpValidationSummary}
                                        </div> : null}
                                    {mcpValidationError ? <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
                                            {mcpValidationError}
                                        </div> : null}
                                </div>
                                <DialogFooter>
                                    <Button variant="outline" onClick={() => setMcpDialogOpen(false)}>{t("app.admin.dashboard.extensions.page.kb92cb20c")}</Button>
                                    <Button onClick={() => void saveMcpConfig()} disabled={savingMcp}>{savingMcp ? t("app.admin.dashboard.extensions.page.kfc8f3cfd") : t("app.admin.dashboard.extensions.page.k836f3c8b")}</Button>
                                </DialogFooter>
                            </DialogContent>
                        </Dialog>
                        <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3 text-sm text-slate-600">
                            <div className="flex items-start gap-2">
                                <Wrench className="mt-0.5 h-4 w-4 text-sky-600" />
                                <div>{t("app.admin.dashboard.extensions.page.kb69d2650")}</div>
                            </div>
                        </div>
                        <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3 text-xs leading-6 text-slate-600">
                            <div className="font-medium text-slate-900">{t("app.admin.dashboard.extensions.page.k499b6163")}</div>
                            <ul className="mt-2 space-y-1">
                                <li>{t("app.admin.dashboard.extensions.page.ka8ca160e")}</li>
                                <li>{t("app.admin.dashboard.extensions.page.k94876ec4")}</li>
                                <li>{t("app.admin.dashboard.extensions.page.keeaf797e")}</li>
                            </ul>
                        </div>
                    </div>
                </ConfigCard>
            </div>

        </AdminPageShell>;
}
