"use client";

import { useEffect, useMemo, useState } from "react";
import { Loader2, ShieldCheck } from "lucide-react";
import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { AdvancedSection } from "@/components/admin-shell/AdvancedSection";
import { DomainSummaryStrip } from "@/components/admin-shell/DomainSummaryStrip";
import { InlineSaveState } from "@/components/admin-shell/InlineSaveState";
import { SourceMetaRow } from "@/components/admin-shell/SourceMetaRow";
import { StatusNotice } from "@/components/admin-shell/StatusNotice";
import { ModelSelect } from "@/components/models/ModelSelect";
import { SafetyGuardianPanel } from "@/components/runtime/SafetyGuardianPanel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useT } from "@/components/providers/LocaleProvider";
import { fetchConfigDomain, saveConfigDomain, type ConfigRegistryEnvelope } from "@/lib/config-registry";
import { ti } from "@/i18n/admin-legacy";
type MachinePosture = "dedicated_runtime_host" | "developer_mixed_host";
type ModelOption = {
  id?: string;
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
type SafetyApproval = {
  id: string;
  session_id?: string;
  run_id?: string;
  approval_kind?: string;
  status?: string;
  created_at?: string;
  question?: string;
  riskCode?: string;
  verdict?: string;
  reason?: string;
  allowlistCandidate?: Record<string, unknown> | null;
};
type SkillSafetyReview = {
  id: string;
  skill_name?: string;
  skill_id?: string;
  skill_path?: string;
  static_verdict?: string;
  effective_verdict?: string;
  user_override?: string | null;
  disabled?: boolean;
  reasons?: string[];
  flaggedFiles?: Array<Record<string, unknown>>;
  updated_at?: string;
};
type SafetyAllowlistEntry = {
  id: string;
  normalized_target_label?: string;
  path_plane?: string;
  runtime_source?: string;
  action?: string;
  risk_code?: string;
  enabled?: boolean;
  updated_at?: string;
};
type SafetyDecisionEvent = {
  id?: string;
  timestamp?: string;
  action?: string;
  status?: string;
  verdict?: string;
  riskCode?: string;
  runtimeSource?: string;
  subject?: string;
  reason?: string;
  decodedPreview?: unknown;
  downloadHosts?: string[];
};
type ActiveDefenseIncident = {
  id: string;
  riskCode?: string;
  severity?: string;
  summary?: string;
  status?: string;
  firstSeenAt?: number;
  lastSeenAt?: number;
  seenCount?: number;
  networkToolKey?: string;
  process?: {
    pid?: number;
    name?: string;
    cpuPercent?: number;
    memoryPercent?: number;
    rssMb?: number;
  };
};
type ActiveDefenseConfig = {
  enabled?: boolean;
  sampleIntervalSeconds?: number;
  injectHostAlerts?: boolean;
  maxInjectedProcesses?: number;
  highCpuPercent?: number;
  highMemoryPercent?: number;
  highMemoryRssMb?: number;
  networkTunnelPolicy?: string;
  knownNetworkTools?: string[];
  knownListeningPorts?: string[];
};
type ActiveDefenseDashboard = {
  enabled?: boolean;
  config?: ActiveDefenseConfig;
  status?: string;
  lastSampleAt?: number | null;
  lastError?: string | null;
  incidents?: ActiveDefenseIncident[];
  knownNetworkTools?: string[];
  knownListeningPorts?: string[];
  summary?: {
    activeIncidents?: number;
    highLoad?: number;
    networkTunnels?: number;
    unknownListeningPorts?: number;
  };
};
type SafetyDashboard = {
  pendingSafetyApprovals?: SafetyApproval[];
  skillSafetyReviews?: SkillSafetyReview[];
  allowlistEntries?: SafetyAllowlistEntry[];
  recentDecisions?: SafetyDecisionEvent[];
  activeDefense?: ActiveDefenseDashboard;
  summary?: {
    pendingSafetyApprovals?: number;
    skillReviews?: number;
    activeAllowlist?: number;
    recentDecisions?: number;
    verdictCounts?: Record<string, number>;
    riskCounts?: Record<string, number>;
    activeDefenseIncidents?: number;
  };
};
type SafetyData = {
  enabled: boolean;
  machinePosture: MachinePosture;
  skillRules?: {
    declarationVerdict?: string;
    localSecretReadVerdict?: string;
    browserProfileAccessVerdict?: Record<string, string>;
    binaryPayloadVerdict?: string;
    llmReviewEnabledFor?: string[];
  };
  networkMutationRules?: {
    defaultExternalMutationVerdict?: Record<string, string>;
    sensitivePayloadVerdict?: string;
    credentialExfiltrationVerdict?: string;
  };
  computerUseRules?: {
    defaultMutationVerdict?: Record<string, string>;
    hotkeyLifecycleVerdict?: string;
    destructiveKeywordVerdict?: string;
  };
  systemIntegrityRules?: {
    packageInstallVerdict?: Record<string, string>;
    destructiveCommandVerdict?: string;
  };
  v8IntegrityRules?: {
    protectedConfigWriteVerdict?: string;
    protectedRuntimeProcessVerdict?: string;
  };
  channelGroupGuard?: {
    enabled?: boolean;
    allowlistOnly?: boolean;
    requireMention?: boolean;
    auditOnly?: boolean;
  };
  modelBindings?: {
    safetyReviewModel?: string;
  };
  governancePolicies?: {
    machinePosture?: string;
    governanceTargets?: string[];
    skillStrategy?: string;
  };
  runtimeSummary?: {
    machinePosture?: string;
    safetyReviewModel?: string | null;
    llmBound?: boolean;
    auditCount?: number;
    reviewCount?: number;
    blockCount?: number;
    verdictDistribution?: Record<string, number>;
  };
  skillScanSummary?: {
    enabled?: boolean;
    verdictDistribution?: Record<string, number>;
    recentSkillScans?: Array<{
      skillName?: string;
      verdict?: string;
      confidence?: number;
      auditId?: string;
      timestamp?: string;
      reasons?: string[];
    }>;
  };
  activeDefense?: ActiveDefenseConfig;
};
const PRESET_OPTIONS = [{
  key: "dedicated_runtime_host",
  title: "app.admin.dashboard.safety.control.page.preset.dedicated.title",
  description: "app.admin.dashboard.safety.control.page.k4b26c5fc"
}, {
  key: "developer_mixed_host",
  title: "app.admin.dashboard.safety.control.page.preset.developer.title",
  description: "app.admin.dashboard.safety.control.page.k6a2115f2"
}, {
  key: "locked_down_sensitive",
  title: "app.admin.dashboard.safety.control.page.preset.locked.title",
  description: "app.admin.dashboard.safety.control.page.k24c3f9e9"
}] as const;
const SAFETY_LABEL_KEYS = {
  posture: {
    dedicated_runtime_host: "app.admin.dashboard.safety.control.page.label.posture.dedicated",
    developer_mixed_host: "app.admin.dashboard.safety.control.page.label.posture.developer"
  },
  verdict: {
    allow: "app.admin.dashboard.safety.control.page.label.verdict.allow",
    audit: "app.admin.dashboard.safety.control.page.label.verdict.audit",
    review: "app.admin.dashboard.safety.control.page.label.verdict.review",
    block: "app.admin.dashboard.safety.control.page.label.verdict.block",
    disabled: "app.admin.dashboard.safety.control.page.label.status.disabled"
  },
  status: {
    active: "app.admin.dashboard.safety.control.page.label.status.active",
    approved: "app.admin.dashboard.safety.control.page.label.status.approved",
    confirmed: "app.admin.dashboard.safety.control.page.label.status.confirmed",
    disabled: "app.admin.dashboard.safety.control.page.label.status.disabled",
    ignored: "app.admin.dashboard.safety.control.page.label.status.ignored",
    pending: "app.admin.dashboard.safety.control.page.label.status.pending",
    rejected: "app.admin.dashboard.safety.control.page.label.status.rejected",
    revoked: "app.admin.dashboard.safety.control.page.label.status.revoked",
    running: "app.admin.dashboard.safety.control.page.label.status.running"
  },
  risk: {
    active_defense: "app.admin.dashboard.safety.control.page.label.risk.activeDefense",
    binary_payload: "app.admin.dashboard.safety.control.page.label.risk.binaryPayload",
    browser_profile_access: "app.admin.dashboard.safety.control.page.label.risk.browserProfileAccess",
    credential_exfiltration: "app.admin.dashboard.safety.control.page.label.risk.credentialExfiltration",
    destructive_command: "app.admin.dashboard.safety.control.page.label.risk.destructiveCommand",
    external_mutation: "app.admin.dashboard.safety.control.page.label.risk.externalMutation",
    high_resource_process: "app.admin.dashboard.safety.control.page.label.risk.highResourceProcess",
    local_secret_read: "app.admin.dashboard.safety.control.page.label.risk.localSecretRead",
    network_tunnel_first_seen: "app.admin.dashboard.safety.control.page.label.risk.networkTunnel",
    protected_config_write: "app.admin.dashboard.safety.control.page.label.risk.protectedConfigWrite",
    protected_runtime_process: "app.admin.dashboard.safety.control.page.label.risk.protectedRuntimeProcess",
    sensitive_payload: "app.admin.dashboard.safety.control.page.label.risk.sensitivePayload",
    unknown_listening_port: "app.admin.dashboard.safety.control.page.label.risk.unknownPort",
    windows_profile_acl_mutation: "app.admin.dashboard.safety.control.page.label.risk.windowsProfileAcl",
    windows_profile_destructive_copy: "app.admin.dashboard.safety.control.page.label.risk.windowsProfileCopy",
    windows_profile_hive_mutation: "app.admin.dashboard.safety.control.page.label.risk.windowsProfileHive",
    windows_profile_registry_mutation: "app.admin.dashboard.safety.control.page.label.risk.windowsProfileRegistry",
    windows_profile_reparse_mutation: "app.admin.dashboard.safety.control.page.label.risk.windowsProfileReparse",
    windows_profile_sensitive_read: "app.admin.dashboard.safety.control.page.label.risk.windowsProfileRead"
  },
  approvalKind: {
    safety_review: "app.admin.dashboard.safety.control.page.label.approvalKind.safetyReview",
    command_safety_review: "app.admin.dashboard.safety.control.page.label.approvalKind.commandReview",
    external_tool_local_system_review: "app.admin.dashboard.safety.control.page.label.approvalKind.externalToolReview"
  },
  runtimeSource: {
    automation: "app.admin.dashboard.safety.control.page.label.runtime.automation",
    chat: "app.admin.dashboard.safety.control.page.label.runtime.chat",
    command: "app.admin.dashboard.safety.control.page.label.runtime.command",
    computer_use: "app.admin.dashboard.safety.control.page.label.runtime.computerUse",
    creative_media: "app.admin.dashboard.safety.control.page.label.runtime.creativeMedia",
    engineering: "app.admin.dashboard.safety.control.page.label.runtime.engineering",
    memory: "app.admin.dashboard.safety.control.page.label.runtime.memory",
    network_supervisor: "app.admin.dashboard.safety.control.page.label.runtime.networkSupervisor",
    plugin_host: "app.admin.dashboard.safety.control.page.label.runtime.pluginHost",
    rpa: "app.admin.dashboard.safety.control.page.label.runtime.rpa",
    safety: "app.admin.dashboard.safety.control.page.label.runtime.safety"
  },
  pathPlane: {
    browser_profile: "app.admin.dashboard.safety.control.page.label.path.browserProfile",
    config: "app.admin.dashboard.safety.control.page.label.path.config",
    network: "app.admin.dashboard.safety.control.page.label.path.network",
    profile: "app.admin.dashboard.safety.control.page.label.path.profile",
    system: "app.admin.dashboard.safety.control.page.label.path.system",
    v8_internal: "app.admin.dashboard.safety.control.page.label.path.v8Internal",
    workspace: "app.admin.dashboard.safety.control.page.label.path.workspace"
  },
  action: {
    delete: "app.admin.dashboard.safety.control.page.label.action.delete",
    download: "app.admin.dashboard.safety.control.page.label.action.download",
    execute: "app.admin.dashboard.safety.control.page.label.action.execute",
    install: "app.admin.dashboard.safety.control.page.label.action.install",
    network_request: "app.admin.dashboard.safety.control.page.label.action.networkRequest",
    read: "app.admin.dashboard.safety.control.page.label.action.read",
    revoke: "app.admin.dashboard.safety.control.page.label.action.revoke",
    scan: "app.admin.dashboard.safety.control.page.label.action.scan",
    write: "app.admin.dashboard.safety.control.page.label.action.write"
  },
  severity: {
    high: "app.admin.dashboard.safety.control.page.label.severity.high",
    low: "app.admin.dashboard.safety.control.page.label.severity.low",
    medium: "app.admin.dashboard.safety.control.page.label.severity.medium"
  }
} as const;
type SafetyLabelGroup = keyof typeof SAFETY_LABEL_KEYS;
type Translator = ReturnType<typeof useT>;
function normalizeToken(value?: string | null) {
  return String(value || "").trim().toLowerCase();
}
function humanizeToken(value?: string | null) {
  const token = String(value || "").trim();
  if (!token) return "";
  return token.replace(/[_-]+/g, " ").replace(/\s+/g, " ").replace(/\b\w/g, char => char.toUpperCase());
}
function safetyLabel(t: Translator, group: SafetyLabelGroup, value?: string | null, fallbackKey = "app.admin.dashboard.safety.control.page.label.unknown") {
  const normalized = normalizeToken(value);
  const key = normalized ? (SAFETY_LABEL_KEYS[group] as Record<string, string>)[normalized] : "";
  if (key) return t(key);
  return normalized ? humanizeToken(normalized) : t(fallbackKey);
}
function verdictLabel(t: Translator, value?: string | null) {
  return safetyLabel(t, "verdict", value);
}
function normalizeSafetyData(input: SafetyData): SafetyData {
  return {
    ...input,
    machinePosture: input.machinePosture === "developer_mixed_host" ? "developer_mixed_host" : "dedicated_runtime_host",
    skillRules: {
      declarationVerdict: input.skillRules?.declarationVerdict || "audit",
      localSecretReadVerdict: input.skillRules?.localSecretReadVerdict || "review",
      browserProfileAccessVerdict: {
        dedicated_runtime_host: input.skillRules?.browserProfileAccessVerdict?.dedicated_runtime_host || "review",
        developer_mixed_host: input.skillRules?.browserProfileAccessVerdict?.developer_mixed_host || "block"
      },
      binaryPayloadVerdict: input.skillRules?.binaryPayloadVerdict || "review",
      llmReviewEnabledFor: Array.isArray(input.skillRules?.llmReviewEnabledFor) ? input.skillRules?.llmReviewEnabledFor : ["review"]
    },
    networkMutationRules: {
      defaultExternalMutationVerdict: {
        dedicated_runtime_host: input.networkMutationRules?.defaultExternalMutationVerdict?.dedicated_runtime_host || "audit",
        developer_mixed_host: input.networkMutationRules?.defaultExternalMutationVerdict?.developer_mixed_host || "review"
      },
      sensitivePayloadVerdict: input.networkMutationRules?.sensitivePayloadVerdict || "review",
      credentialExfiltrationVerdict: input.networkMutationRules?.credentialExfiltrationVerdict || "block"
    },
    computerUseRules: {
      defaultMutationVerdict: {
        dedicated_runtime_host: input.computerUseRules?.defaultMutationVerdict?.dedicated_runtime_host || "audit",
        developer_mixed_host: input.computerUseRules?.defaultMutationVerdict?.developer_mixed_host || "review"
      },
      hotkeyLifecycleVerdict: input.computerUseRules?.hotkeyLifecycleVerdict || "review",
      destructiveKeywordVerdict: input.computerUseRules?.destructiveKeywordVerdict || "block"
    },
    systemIntegrityRules: {
      packageInstallVerdict: {
        dedicated_runtime_host: input.systemIntegrityRules?.packageInstallVerdict?.dedicated_runtime_host || "audit",
        developer_mixed_host: input.systemIntegrityRules?.packageInstallVerdict?.developer_mixed_host || "review"
      },
      destructiveCommandVerdict: input.systemIntegrityRules?.destructiveCommandVerdict || "block"
    },
    v8IntegrityRules: {
      protectedConfigWriteVerdict: input.v8IntegrityRules?.protectedConfigWriteVerdict || "review",
      protectedRuntimeProcessVerdict: input.v8IntegrityRules?.protectedRuntimeProcessVerdict || "block"
    },
    channelGroupGuard: {
      enabled: Boolean(input.channelGroupGuard?.enabled),
      allowlistOnly: Boolean(input.channelGroupGuard?.allowlistOnly),
      requireMention: Boolean(input.channelGroupGuard?.requireMention),
      auditOnly: Boolean(input.channelGroupGuard?.auditOnly)
    },
    runtimeSummary: {
      machinePosture: input.runtimeSummary?.machinePosture || input.machinePosture,
      safetyReviewModel: input.runtimeSummary?.safetyReviewModel || null,
      llmBound: Boolean(input.runtimeSummary?.llmBound),
      auditCount: Number(input.runtimeSummary?.auditCount || 0),
      reviewCount: Number(input.runtimeSummary?.reviewCount || 0),
      blockCount: Number(input.runtimeSummary?.blockCount || 0),
      verdictDistribution: input.runtimeSummary?.verdictDistribution || {}
    },
    skillScanSummary: {
      enabled: Boolean(input.skillScanSummary?.enabled),
      verdictDistribution: input.skillScanSummary?.verdictDistribution || {},
      recentSkillScans: Array.isArray(input.skillScanSummary?.recentSkillScans) ? input.skillScanSummary?.recentSkillScans : []
    },
    activeDefense: {
      enabled: Boolean(input.activeDefense?.enabled),
      sampleIntervalSeconds: Number(input.activeDefense?.sampleIntervalSeconds || 20),
      injectHostAlerts: input.activeDefense?.injectHostAlerts !== false,
      maxInjectedProcesses: Number(input.activeDefense?.maxInjectedProcesses || 3),
      highCpuPercent: Number(input.activeDefense?.highCpuPercent || 85),
      highMemoryPercent: Number(input.activeDefense?.highMemoryPercent || 25),
      highMemoryRssMb: Number(input.activeDefense?.highMemoryRssMb || 2048),
      networkTunnelPolicy: input.activeDefense?.networkTunnelPolicy || "confirm_first",
      knownNetworkTools: Array.isArray(input.activeDefense?.knownNetworkTools) ? input.activeDefense?.knownNetworkTools : [],
      knownListeningPorts: Array.isArray(input.activeDefense?.knownListeningPorts) ? input.activeDefense?.knownListeningPorts : ["tcp:9527", "tcp:9528", "tcp:9530"]
    }
  };
}
function applyPreset(config: SafetyData, preset: (typeof PRESET_OPTIONS)[number]["key"]): SafetyData {
  const next = normalizeSafetyData(structuredClone(config));
  next.enabled = true;
  if (preset === "dedicated_runtime_host") {
    next.machinePosture = "dedicated_runtime_host";
    next.skillRules!.declarationVerdict = "audit";
    next.skillRules!.localSecretReadVerdict = "review";
    next.networkMutationRules!.defaultExternalMutationVerdict!.dedicated_runtime_host = "audit";
    next.computerUseRules!.defaultMutationVerdict!.dedicated_runtime_host = "audit";
    next.networkMutationRules!.sensitivePayloadVerdict = "review";
    next.v8IntegrityRules!.protectedConfigWriteVerdict = "review";
    return next;
  }
  next.machinePosture = "developer_mixed_host";
  next.networkMutationRules!.defaultExternalMutationVerdict!.developer_mixed_host = "review";
  next.computerUseRules!.defaultMutationVerdict!.developer_mixed_host = "review";
  next.skillRules!.browserProfileAccessVerdict!.developer_mixed_host = "block";
  next.systemIntegrityRules!.packageInstallVerdict!.developer_mixed_host = "review";
  if (preset === "locked_down_sensitive") {
    next.networkMutationRules!.sensitivePayloadVerdict = "block";
    next.computerUseRules!.hotkeyLifecycleVerdict = "block";
    next.v8IntegrityRules!.protectedConfigWriteVerdict = "block";
  }
  return next;
}
function detectPreset(config: SafetyData) {
  if (config.machinePosture === "developer_mixed_host" && config.networkMutationRules?.sensitivePayloadVerdict === "block" && config.computerUseRules?.hotkeyLifecycleVerdict === "block" && config.v8IntegrityRules?.protectedConfigWriteVerdict === "block") {
    return "locked_down_sensitive";
  }
  return config.machinePosture;
}
export default function SafetyControlPage() {
  const t = useT();
  const [envelope, setEnvelope] = useState<ConfigRegistryEnvelope<SafetyData> | null>(null);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [preset, setPreset] = useState<(typeof PRESET_OPTIONS)[number]["key"]>("dedicated_runtime_host");
  const [dashboard, setDashboard] = useState<SafetyDashboard | null>(null);
  const [governanceBusy, setGovernanceBusy] = useState<string | null>(null);
  const [rememberAllowlist, setRememberAllowlist] = useState<Record<string, boolean>>({});
  const loadConfig = async () => {
    setLoading(true);
    try {
      const [next, modelList, safetyDashboard] = await Promise.all([fetchConfigDomain<SafetyData>("safety"), fetch("/api/models", {
        cache: "no-store"
      }).then(response => response.json().catch(() => [])), fetch("/api/safety/dashboard?limit=80", {
        cache: "no-store"
      }).then(response => response.json().catch(() => ({})))]);
      const normalized = normalizeSafetyData(next.data);
      setEnvelope({
        ...next,
        data: normalized
      });
      setModels(Array.isArray(modelList) ? modelList : []);
      setDashboard(safetyDashboard && typeof safetyDashboard === "object" ? safetyDashboard : {});
      setPreset(detectPreset(normalized));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void loadConfig();
  }, []);
  const llmModels = useMemo(() => models.filter(model => ["TEXT", "MULTIMODAL", "CHAT", "LLM"].includes((model.type || "").toUpperCase())), [models]);
  const summary = useMemo(() => {
    const data = envelope?.data;
    const runtimeSummary = data?.runtimeSummary || {};
    const skillScanSummary = data?.skillScanSummary || {};
    const reviewModel = String(data?.modelBindings?.safetyReviewModel || ti(t, "k3bf179d8d0"));
    return {
      posture: safetyLabel(t, "posture", data?.machinePosture || "dedicated_runtime_host"),
      reviewModel: reviewModel.length > 28 ? `${reviewModel.slice(0, 26)}…` : reviewModel,
      auditCount: Number(runtimeSummary.auditCount || 0),
      reviewCount: Number(runtimeSummary.reviewCount || 0),
      blockCount: Number(runtimeSummary.blockCount || 0),
      skillDistribution: skillScanSummary.verdictDistribution || {},
      recentSkillScans: skillScanSummary.recentSkillScans || []
    };
  }, [envelope, t]);
  const saveData = async (nextData: SafetyData) => {
    if (!envelope) return;
    setSaving(true);
    try {
      const next = await saveConfigDomain<SafetyData>("safety", {
        data: nextData
      });
      const normalized = normalizeSafetyData(next.data);
      setEnvelope({
        ...next,
        data: normalized
      });
      setPreset(detectPreset(normalized));
      setSaved(true);
      window.setTimeout(() => setSaved(false), 1800);
    } finally {
      setSaving(false);
    }
  };
  const handleApprovalAction = async (approvalId: string, approve: boolean) => {
    const busyKey = `approval:${approve ? "approve" : "reject"}:${approvalId}`;
    setGovernanceBusy(busyKey);
    try {
      const response = await fetch(`/api/approvals/${encodeURIComponent(approvalId)}/${approve ? "approve" : "reject"}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          response: {
            approved: approve,
            answer: approve ? "Approved from SafetyRuntime governance." : "Rejected from SafetyRuntime governance.",
            persistSafetyAllowlist: approve ? Boolean(rememberAllowlist[approvalId]) : false
          }
        })
      });
      if (!response.ok) {
        throw new Error(`Approval action failed: ${response.status}`);
      }
      await loadConfig();
    } finally {
      setGovernanceBusy(null);
    }
  };
  const handleSkillSafetyAction = async (reviewId: string, action: "approve" | "disable" | "revoke" | "rescan") => {
    const busyKey = `skill:${action}:${reviewId}`;
    setGovernanceBusy(busyKey);
    try {
      const response = await fetch(`/api/skills/safety/reviews/${encodeURIComponent(reviewId)}/${action}`, {
        method: "POST"
      });
      if (!response.ok) {
        throw new Error(`Skill safety action failed: ${response.status}`);
      }
      await loadConfig();
    } finally {
      setGovernanceBusy(null);
    }
  };
  const handleAllowlistRevoke = async (entryId: string) => {
    const busyKey = `allowlist:revoke:${entryId}`;
    setGovernanceBusy(busyKey);
    try {
      const response = await fetch(`/api/safety/allowlist/${encodeURIComponent(entryId)}/revoke`, {
        method: "POST"
      });
      if (!response.ok) {
        throw new Error(`Allowlist revoke failed: ${response.status}`);
      }
      await loadConfig();
    } finally {
      setGovernanceBusy(null);
    }
  };
  const handleActiveDefenseIncidentAction = async (incidentId: string, action: "confirm" | "ignore") => {
    const busyKey = `active-defense:${action}:${incidentId}`;
    setGovernanceBusy(busyKey);
    try {
      const response = await fetch(`/api/safety/active-defense/incidents/${encodeURIComponent(incidentId)}/${action}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          note: `Admin ${action}`
        })
      });
      if (!response.ok) {
        throw new Error(`Active defense ${action} failed: ${response.status}`);
      }
      await loadConfig();
    } finally {
      setGovernanceBusy(null);
    }
  };
  if (loading || !envelope) {
    return <div className="flex min-h-[320px] items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
            </div>;
  }
  const data = envelope.data;
  const activePreset = PRESET_OPTIONS.find(item => item.key === preset) || PRESET_OPTIONS[0];
  return <AdminPageShell>
            <AdminPageHeader title="app.admin.dashboard.safety.control.page.k8f467cf5" description="app.admin.dashboard.safety.control.page.k65868ff2" actions={<div className="flex items-center gap-3">
                        <InlineSaveState saving={saving} saved={saved} />
                        <Button onClick={() => void saveData(data)} disabled={saving}>
                            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <ShieldCheck className="mr-2 h-4 w-4" />}
                            {ti(t, "k5e4644a2c8")}
                        </Button>
                    </div>} />


            <div className="space-y-6">
                <DomainSummaryStrip items={[{
        label: "app.admin.dashboard.safety.control.page.summary.machinePosture",
        value: summary.posture
      }, {
        label: "app.admin.dashboard.safety.control.page.summary.verdictMix",
        value: `${summary.auditCount} / ${summary.reviewCount} / ${summary.blockCount}`
      }, {
        label: "app.admin.dashboard.safety.control.page.k87b50116",
        value: `${Number(summary.skillDistribution.audit || 0)} / ${Number(summary.skillDistribution.review || 0)} / ${Number(summary.skillDistribution.block || 0)}`
      }, {
        label: "app.admin.dashboard.safety.control.page.summary.reviewModel",
        value: summary.reviewModel
      }]} />


                <StatusNotice tone={data.machinePosture === "developer_mixed_host" ? "warning" : "success"} title={data.machinePosture === "developer_mixed_host" ? ti(t, "k0a54999adc") : ti(t, "k2de6238fbe")} description={data.machinePosture === "developer_mixed_host" ? ti(t, "kfea74a25fa") : ti(t, "kc22f677125")} />


                <Card className="rounded-2xl border-slate-200 shadow-sm">
                    <CardHeader>
                        <CardTitle className="text-base">{ti(t, "kac3aa53ae3")}</CardTitle>
                        <CardDescription>{ti(t, "ked7bf764bd")}</CardDescription>
                    </CardHeader>
                    <CardContent className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                        <div className="space-y-2">
                            <Label>{t("app.admin.dashboard.safety.control.page.field.machinePosture")}</Label>
                            <Select value={data.machinePosture} onValueChange={next => setEnvelope(previous => previous ? {
              ...previous,
              data: normalizeSafetyData({
                ...previous.data,
                machinePosture: next as MachinePosture
              })
            } : previous)}>

                                <SelectTrigger>
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="dedicated_runtime_host">{safetyLabel(t, "posture", "dedicated_runtime_host")}</SelectItem>
                                    <SelectItem value="developer_mixed_host">{safetyLabel(t, "posture", "developer_mixed_host")}</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="space-y-2">
                            <Label>{t("app.admin.dashboard.safety.control.page.field.preset")}</Label>
                            <Select value={preset} onValueChange={next => {
              const presetKey = next as (typeof PRESET_OPTIONS)[number]["key"];
              setPreset(presetKey);
              setEnvelope(previous => previous ? {
                ...previous,
                data: normalizeSafetyData(applyPreset(previous.data, presetKey))
              } : previous);
            }}>

                                <SelectTrigger>
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    {PRESET_OPTIONS.map(item => <SelectItem key={item.key} value={item.key}>
                                            {t(item.title)}
                                        </SelectItem>)}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="space-y-2">
                            <Label>{t("app.admin.dashboard.safety.control.page.field.reviewModel")}</Label>
                            <ModelSelect models={llmModels} value={String(data.modelBindings?.safetyReviewModel || "__none__")} emptyValue="__none__" emptyLabel={t("app.admin.dashboard.safety.control.page.label.notBound")} placeholder={t("app.admin.dashboard.safety.control.page.label.notBound")} onValueChange={next => setEnvelope(previous => previous ? {
              ...previous,
              data: {
                ...previous.data,
                modelBindings: {
                  ...(previous.data.modelBindings || {}),
                  safetyReviewModel: next
                }
              }
            } : previous)} />

                        </div>
                        <div className="flex items-center justify-between rounded-2xl border border-slate-200 px-4 py-3">
                            <div className="space-y-1">
                                <div className="text-sm font-medium text-slate-900">{ti(t, "k4d4a2abe6e")}</div>
                                <div className="text-xs leading-5 text-slate-500">{ti(t, "ke57511582a")}</div>
                            </div>
                            <Switch checked={Boolean(data.enabled)} onCheckedChange={checked => setEnvelope(previous => previous ? {
              ...previous,
              data: {
                ...previous.data,
                enabled: checked
              }
            } : previous)} />

                        </div>
                    </CardContent>
                </Card>

                <Card className="rounded-2xl border-slate-200 shadow-sm">
                    <CardHeader>
                        <CardTitle className="text-base">{ti(t, "k8f7932ff92")}</CardTitle>
                        <CardDescription>{t(activePreset.description)}</CardDescription>
                    </CardHeader>
                    <CardContent className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                        <div className="rounded-2xl border border-slate-200 p-4">
                            <div className="text-sm font-medium text-slate-900">{ti(t, "k0fad073470")}</div>
                            <div className="mt-2 text-sm leading-6 text-slate-600">
                                {ti(t, "kda13a9cff5")}<span className="font-medium text-slate-900">{verdictLabel(t, data.skillRules?.declarationVerdict)}</span>
                                <br />
                                {ti(t, "k8595ba0815")}<span className="font-medium text-slate-900">{verdictLabel(t, data.skillRules?.localSecretReadVerdict)}</span>
                                <br />
                                {ti(t, "kdda26d8b82")}<span className="font-medium text-slate-900">{verdictLabel(t, data.skillRules?.browserProfileAccessVerdict?.developer_mixed_host)}</span>
                            </div>
                        </div>
                        <div className="rounded-2xl border border-slate-200 p-4">
                            <div className="text-sm font-medium text-slate-900">{ti(t, "k3991c727ed")}</div>
                            <div className="mt-2 text-sm leading-6 text-slate-600">
                                {ti(t, "k8b9f7fc0ed")}<span className="font-medium text-slate-900">{verdictLabel(t, data.networkMutationRules?.defaultExternalMutationVerdict?.dedicated_runtime_host)}</span>
                                <br />
                                {ti(t, "k546f1f657f")}<span className="font-medium text-slate-900">{verdictLabel(t, data.networkMutationRules?.defaultExternalMutationVerdict?.developer_mixed_host)}</span>
                                <br />
                                {ti(t, "kd9abd67eaa")}<span className="font-medium text-slate-900">{verdictLabel(t, data.networkMutationRules?.sensitivePayloadVerdict)}</span>
                            </div>
                        </div>
                        <div className="rounded-2xl border border-slate-200 p-4">
                            <div className="text-sm font-medium text-slate-900">{t("app.admin.dashboard.safety.control.page.domain.computerUse")}</div>
                            <div className="mt-2 text-sm leading-6 text-slate-600">
                                {ti(t, "k9e4884053a")}<span className="font-medium text-slate-900">{verdictLabel(t, data.computerUseRules?.defaultMutationVerdict?.dedicated_runtime_host)}</span>
                                <br />
                                {ti(t, "k426e3712e7")}<span className="font-medium text-slate-900">{verdictLabel(t, data.computerUseRules?.defaultMutationVerdict?.developer_mixed_host)}</span>
                                <br />
                                {ti(t, "k5b4d128734")}<span className="font-medium text-slate-900">{verdictLabel(t, data.computerUseRules?.hotkeyLifecycleVerdict)}</span>
                            </div>
                        </div>
                        <div className="rounded-2xl border border-slate-200 p-4">
                            <div className="text-sm font-medium text-slate-900">{ti(t, "k94ba148b3e")}</div>
                            <div className="mt-2 text-sm leading-6 text-slate-600">
                                {ti(t, "ke64e2476ba")}<span className="font-medium text-slate-900">{verdictLabel(t, data.systemIntegrityRules?.packageInstallVerdict?.dedicated_runtime_host)}</span>
                                <br />
                                {ti(t, "k1c9f944a8e")}<span className="font-medium text-slate-900">{verdictLabel(t, data.v8IntegrityRules?.protectedConfigWriteVerdict)}</span>
                                <br />
                                {ti(t, "kf5ed0ffb9c")}<span className="font-medium text-slate-900">{verdictLabel(t, data.v8IntegrityRules?.protectedRuntimeProcessVerdict)}</span>
                            </div>
                        </div>
                    </CardContent>
                </Card>

                <Card className="rounded-2xl border-slate-200 shadow-sm">
                    <CardHeader>
                        <CardTitle className="text-base">{t("app.admin.dashboard.safety.control.page.section.recentSkillScans")}</CardTitle>
                        <CardDescription>{ti(t, "k3b8428667f")}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        {summary.recentSkillScans.length === 0 ? <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-6 text-sm text-slate-500">{ti(t, "k906ff562fc")}</div> : summary.recentSkillScans.map((item, index) => <div key={`${item.auditId || item.skillName || "skill"}-${index}`} className="rounded-2xl border border-slate-200 p-4">
                                    <div className="flex flex-wrap items-center gap-3">
                                        <div className="text-sm font-medium text-slate-900">{item.skillName || ti(t, "k233cfa7db1")}</div>
                                        <div className="text-xs uppercase tracking-[0.18em] text-slate-500">{verdictLabel(t, item.verdict)}</div>
                                        {item.confidence != null ? <div className="text-xs text-slate-500">{t("app.admin.dashboard.safety.control.page.label.confidence")} {item.confidence}</div> : null}
                                    </div>
                                    {Array.isArray(item.reasons) && item.reasons.length > 0 ? <ul className="mt-3 space-y-1 text-sm leading-6 text-slate-600">
                                            {item.reasons.map(reason => <li key={reason}>- {reason}</li>)}
                                        </ul> : null}
                                </div>)}
                    </CardContent>
                </Card>

                <AdvancedSection title="app.admin.dashboard.safety.control.page.section.observability" description="app.admin.dashboard.safety.control.page.section.observability.description" defaultOpen={false}>
                    <div className="space-y-4">
                        <div className="grid gap-3 md:grid-cols-4">
                            <div className="rounded-2xl border border-slate-200 bg-white p-4">
                                <div className="text-xs uppercase tracking-[0.18em] text-slate-400">{t("app.admin.dashboard.safety.control.page.summary.pending")}</div>
                                <div className="mt-2 text-2xl font-semibold text-slate-950">{dashboard?.summary?.pendingSafetyApprovals ?? 0}</div>
                                <div className="text-xs text-slate-500">{t("app.admin.dashboard.safety.control.page.summary.pendingApprovals")}</div>
                            </div>
                            <div className="rounded-2xl border border-slate-200 bg-white p-4">
                                <div className="text-xs uppercase tracking-[0.18em] text-slate-400">{t("app.admin.dashboard.safety.control.page.summary.ledger")}</div>
                                <div className="mt-2 text-2xl font-semibold text-slate-950">{dashboard?.summary?.skillReviews ?? 0}</div>
                                <div className="text-xs text-slate-500">{t("app.admin.dashboard.safety.control.page.summary.skillReviews")}</div>
                            </div>
                            <div className="rounded-2xl border border-slate-200 bg-white p-4">
                                <div className="text-xs uppercase tracking-[0.18em] text-slate-400">{t("app.admin.dashboard.safety.control.page.summary.allowlist")}</div>
                                <div className="mt-2 text-2xl font-semibold text-slate-950">{dashboard?.summary?.activeAllowlist ?? 0}</div>
                                <div className="text-xs text-slate-500">{t("app.admin.dashboard.safety.control.page.summary.activeEntries")}</div>
                            </div>
                            <div className="rounded-2xl border border-slate-200 bg-white p-4">
                                <div className="text-xs uppercase tracking-[0.18em] text-slate-400">{t("app.admin.dashboard.safety.control.page.summary.events")}</div>
                                <div className="mt-2 text-2xl font-semibold text-slate-950">{dashboard?.summary?.recentDecisions ?? 0}</div>
                                <div className="text-xs text-slate-500">{t("app.admin.dashboard.safety.control.page.summary.recentDecisions")}</div>
                            </div>
                        </div>

                        <Card className="rounded-2xl border-slate-200 shadow-sm">
                            <CardHeader>
                                <CardTitle className="text-base">{ti(t, "k0bd4eb9e51")}</CardTitle>
                                <CardDescription>{ti(t, "k53b0056fae")}</CardDescription>
                            </CardHeader>
                            <CardContent className="space-y-4">
                                <div className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-slate-200 p-4">
                                    <div className="space-y-1">
                                        <div className="text-sm font-medium text-slate-900">
                                            {data.activeDefense?.enabled ? ti(t, "k938cd2b63d") : ti(t, "kb3eb9f1698")}
                                        </div>
                                        <div className="text-xs leading-5 text-slate-500">
                                            {ti(t, "k01f122f48b")} {data.activeDefense?.sampleIntervalSeconds || 20}{ti(t, "k4d7902113a")} {data.activeDefense?.maxInjectedProcesses || 3} {ti(t, "k4b584a4a03")}
                                        </div>
                                    </div>
                                    <Switch checked={Boolean(data.activeDefense?.enabled)} onCheckedChange={checked => setEnvelope(previous => previous ? {
                  ...previous,
                  data: normalizeSafetyData({
                    ...previous.data,
                    activeDefense: {
                      ...(previous.data.activeDefense || {}),
                      enabled: checked
                    }
                  })
                } : previous)} />

                                </div>

                                <div className="grid gap-3 md:grid-cols-3">
                                    <div className="rounded-2xl border border-slate-200 p-4">
                                        <div className="text-xs uppercase tracking-[0.18em] text-slate-400">{t("app.admin.dashboard.safety.control.page.activeDefense.status")}</div>
                                        <div className="mt-2 text-sm font-medium text-slate-900">{safetyLabel(t, "status", dashboard?.activeDefense?.status || "disabled")}</div>
                                        <div className="mt-1 text-xs text-slate-500">
                                            {t("app.admin.dashboard.safety.control.page.activeDefense.lastSample")} {dashboard?.activeDefense?.lastSampleAt ? new Date(Number(dashboard.activeDefense.lastSampleAt) * 1000).toLocaleString() : "-"}
                                        </div>
                                    </div>
                                    <div className="rounded-2xl border border-slate-200 p-4">
                                        <div className="text-xs uppercase tracking-[0.18em] text-slate-400">{t("app.admin.dashboard.safety.control.page.activeDefense.incidents")}</div>
                                        <div className="mt-2 text-sm font-medium text-slate-900">{dashboard?.activeDefense?.summary?.activeIncidents ?? 0} {t("app.admin.dashboard.safety.control.page.label.status.active")}</div>
                                        <div className="mt-1 text-xs text-slate-500">
                                            {t("app.admin.dashboard.safety.control.page.activeDefense.highLoad")} {dashboard?.activeDefense?.summary?.highLoad ?? 0} · {t("app.admin.dashboard.safety.control.page.activeDefense.tunnels")} {dashboard?.activeDefense?.summary?.networkTunnels ?? 0} · {t("app.admin.dashboard.safety.control.page.activeDefense.ports")} {dashboard?.activeDefense?.summary?.unknownListeningPorts ?? 0}
                                        </div>
                                    </div>
                                    <div className="rounded-2xl border border-slate-200 p-4">
                                        <div className="text-xs uppercase tracking-[0.18em] text-slate-400">{t("app.admin.dashboard.safety.control.page.activeDefense.knownTunnels")}</div>
                                        <div className="mt-2 text-sm font-medium text-slate-900">{(dashboard?.activeDefense?.knownNetworkTools || []).length}</div>
                                        <div className="mt-1 line-clamp-2 text-xs text-slate-500">
                                            {(dashboard?.activeDefense?.knownNetworkTools || []).join(", ") || ti(t, "k37be479791")}
                                        </div>
                                    </div>
                                </div>

                                {dashboard?.activeDefense?.lastError ? <StatusNotice tone="warning" title={t("app.admin.dashboard.safety.control.page.activeDefense.samplingError")} description={dashboard.activeDefense.lastError} /> : null}

                                {(dashboard?.activeDefense?.incidents || []).length === 0 ? <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-6 text-sm text-slate-500">
                                        {ti(t, "k8e279a292f")}
                                    </div> : <div className="space-y-3">
                                        {(dashboard?.activeDefense?.incidents || []).slice(0, 8).map(incident => <div key={incident.id} className="rounded-2xl border border-slate-200 p-4">
                                                <div className="flex flex-wrap items-center gap-2">
                                                    <Badge variant={incident.riskCode === "high_resource_process" ? "secondary" : "outline"}>
                                                        {safetyLabel(t, "risk", incident.riskCode || "active_defense")}
                                                    </Badge>
                                                    {incident.severity ? <Badge variant="outline">{safetyLabel(t, "severity", incident.severity)}</Badge> : null}
                                                    <span className="text-xs text-slate-500">{t("app.admin.dashboard.safety.control.page.activeDefense.seen")} {incident.seenCount || 1}</span>
                                                </div>
                                                <div className="mt-2 text-sm text-slate-700">{incident.summary || incident.id}</div>
                                                {incident.process ? <div className="mt-2 text-xs text-slate-500">
                                                        {incident.process.name || t("app.admin.dashboard.safety.control.page.activeDefense.process")}({incident.process.pid || "-"}) CPU {incident.process.cpuPercent ?? "-"}% · {t("app.admin.dashboard.safety.control.page.activeDefense.memory")} {incident.process.rssMb ?? "-"}MB
                                                    </div> : null}
                                                <div className="mt-3 flex justify-end gap-2">
                                                    {incident.riskCode === "network_tunnel_first_seen" || incident.riskCode === "unknown_listening_port" ? <Button size="sm" variant="outline" disabled={governanceBusy === `active-defense:confirm:${incident.id}`} onClick={() => void handleActiveDefenseIncidentAction(incident.id, "confirm")}>
                                                            {ti(t, "kaba11b75f9")}
                                                        
                      </Button> : null}
                                                    <Button size="sm" variant="ghost" disabled={governanceBusy === `active-defense:ignore:${incident.id}`} onClick={() => void handleActiveDefenseIncidentAction(incident.id, "ignore")}>
                                                        {ti(t, "kd84129b8be")}
                                                    
                      </Button>
                                                </div>
                                            </div>)}
                                    </div>}
                            </CardContent>
                        </Card>

                        <Card className="rounded-2xl border-slate-200 shadow-sm">
                            <CardHeader>
                                <CardTitle className="text-base">{ti(t, "k207d54bdcb")}</CardTitle>
                                <CardDescription>{ti(t, "k31e671581a")}</CardDescription>
                            </CardHeader>
                            <CardContent className="space-y-3">
                                    {(dashboard?.pendingSafetyApprovals || []).length === 0 ? <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-6 text-sm text-slate-500">{ti(t, "k8943074568")}</div> : (dashboard?.pendingSafetyApprovals || []).slice(0, 8).map(approval => <div key={approval.id} className="rounded-2xl border border-slate-200 p-4">
                                            <div className="flex flex-wrap items-center gap-2">
                                                <Badge variant="outline">{safetyLabel(t, "approvalKind", approval.approval_kind || "safety_review")}</Badge>
                                                <Badge variant={approval.verdict === "block" ? "destructive" : "secondary"}>{safetyLabel(t, "risk", approval.riskCode)}</Badge>
                                                <span className="text-xs text-slate-500">Run {approval.run_id || "-"}</span>
                                            </div>
                                            <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-slate-700">{approval.question || approval.reason || ti(t, "k82b3e41793")}</p>
                                            {approval.allowlistCandidate ? <label className="mt-3 flex items-center gap-2 text-sm text-slate-600">
                                                    <input type="checkbox" checked={Boolean(rememberAllowlist[approval.id])} onChange={event => setRememberAllowlist(previous => ({
                    ...previous,
                    [approval.id]: event.target.checked
                  }))} />
                                                    {ti(t, "kfabda685a2")}
                                                
                  </label> : null}
                                            <div className="mt-3 flex justify-end gap-2">
                                                <Button type="button" variant="outline" disabled={governanceBusy === `approval:reject:${approval.id}` || governanceBusy === `approval:approve:${approval.id}`} onClick={() => void handleApprovalAction(approval.id, false)}>
                                                    {ti(t, "k03e210a66d")}
                                                
                    </Button>
                                                <Button type="button" disabled={governanceBusy === `approval:approve:${approval.id}` || governanceBusy === `approval:reject:${approval.id}`} onClick={() => void handleApprovalAction(approval.id, true)}>
                                                    {ti(t, "kdcc4233255")}
                                                
                    </Button>
                                            </div>
                                        </div>)}
                            </CardContent>
                        </Card>

                        <div className="grid gap-4 xl:grid-cols-2">
                            <Card className="rounded-2xl border-slate-200 shadow-sm">
                                <CardHeader>
                                    <CardTitle className="text-base">{t("app.admin.dashboard.safety.control.page.section.skillLedger")}</CardTitle>
                                    <CardDescription>{ti(t, "k0e783e6a82")}</CardDescription>
                                </CardHeader>
                                <CardContent>
                                    {(dashboard?.skillSafetyReviews || []).length === 0 ? <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-6 text-sm text-slate-500">{ti(t, "k4fd29dc28e")}</div> : <div className="max-h-[34rem] space-y-3 overflow-y-auto pr-2">
                                        {(dashboard?.skillSafetyReviews || []).map(review => <div key={review.id} className="rounded-2xl border border-slate-200 p-4">
                                                <div className="flex flex-wrap items-center gap-2">
                                                    <span className="font-medium text-slate-950">{review.skill_name || review.skill_id || ti(t, "k233cfa7db1")}</span>
                                                    <Badge variant={review.disabled ? "destructive" : "outline"}>{review.disabled ? safetyLabel(t, "status", "disabled") : verdictLabel(t, review.effective_verdict)}</Badge>
                                                    {review.user_override ? <Badge variant="secondary">{verdictLabel(t, review.user_override)}</Badge> : null}
                                                </div>
                                                <div className="mt-2 line-clamp-1 text-xs text-slate-500">{review.skill_path || "-"}</div>
                                                {Array.isArray(review.reasons) && review.reasons.length ? <div className="mt-2 text-sm text-slate-600">{review.reasons.slice(0, 2).join(" / ")}</div> : null}
                                                <div className="mt-3 flex flex-wrap justify-end gap-2">
                                                    <Button size="sm" variant="outline" disabled={Boolean(governanceBusy)} onClick={() => void handleSkillSafetyAction(review.id, "approve")}>{ti(t, "k0a6f0a30a8")}</Button>
                                                    <Button size="sm" variant="outline" disabled={Boolean(governanceBusy)} onClick={() => void handleSkillSafetyAction(review.id, "disable")}>{ti(t, "kbe70be5a2e")}</Button>
                                                    <Button size="sm" variant="ghost" disabled={Boolean(governanceBusy)} onClick={() => void handleSkillSafetyAction(review.id, "revoke")}>{ti(t, "k9fcefd8dc8")}</Button>
                                                    <Button size="sm" variant="ghost" disabled={Boolean(governanceBusy)} onClick={() => void handleSkillSafetyAction(review.id, "rescan")}>{ti(t, "kd5847a438e")}</Button>
                                                </div>
                                            </div>)}
                                    </div>}
                                </CardContent>
                            </Card>

                            <Card className="rounded-2xl border-slate-200 shadow-sm">
                                <CardHeader>
                                    <CardTitle className="text-base">{t("app.admin.dashboard.safety.control.page.section.allowlist")}</CardTitle>
                                    <CardDescription>{ti(t, "k24d5d908de")}</CardDescription>
                                </CardHeader>
                                <CardContent className="space-y-3">
                                    {(dashboard?.allowlistEntries || []).length === 0 ? <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-6 text-sm text-slate-500">{ti(t, "kc83fd439cd")}</div> : (dashboard?.allowlistEntries || []).slice(0, 8).map(entry => <div key={entry.id} className="rounded-2xl border border-slate-200 p-4">
                                                <div className="flex flex-wrap items-center gap-2">
                                                    <Badge variant={entry.enabled ? "secondary" : "outline"}>{safetyLabel(t, "status", entry.enabled ? "active" : "revoked")}</Badge>
                                                    <Badge variant="outline">{safetyLabel(t, "risk", entry.risk_code)}</Badge>
                                                    <span className="text-xs text-slate-500">{safetyLabel(t, "runtimeSource", entry.runtime_source)} / {safetyLabel(t, "pathPlane", entry.path_plane)} / {safetyLabel(t, "action", entry.action)}</span>
                                                </div>
                                                <div className="mt-2 break-all text-sm text-slate-700">{entry.normalized_target_label || entry.id}</div>
                                                {entry.enabled ? <div className="mt-3 flex justify-end">
                                                        <Button size="sm" variant="outline" disabled={governanceBusy === `allowlist:revoke:${entry.id}`} onClick={() => void handleAllowlistRevoke(entry.id)}>
                                                            {ti(t, "kb31883bd20")}
                                                        </Button>
                                                    </div> : null}
                                            </div>)}
                                </CardContent>
                            </Card>
                        </div>

                        <Card className="rounded-2xl border-slate-200 shadow-sm">
                            <CardHeader>
                                <CardTitle className="text-base">{ti(t, "k82e119eb54")}</CardTitle>
                                <CardDescription>{ti(t, "kf3c79e2420")}</CardDescription>
                            </CardHeader>
                            <CardContent className="space-y-3">
                                {(dashboard?.recentDecisions || []).length === 0 ? <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-6 text-sm text-slate-500">{ti(t, "k08fd2c0902")}</div> : (dashboard?.recentDecisions || []).slice(0, 10).map((event, index) => <div key={event.id || `${event.timestamp}-${index}`} className="rounded-2xl border border-slate-200 p-4">
                                            <div className="flex flex-wrap items-center gap-2">
                                                <Badge variant={event.verdict === "block" ? "destructive" : "outline"}>{event.verdict ? verdictLabel(t, event.verdict) : safetyLabel(t, "status", event.status)}</Badge>
                                                <Badge variant="secondary">{safetyLabel(t, "risk", event.riskCode)}</Badge>
                                                <span className="text-xs text-slate-500">{safetyLabel(t, "action", event.action || "scan")} · {safetyLabel(t, "runtimeSource", event.runtimeSource)} · {event.timestamp || "-"}</span>
                                            </div>
                                            {event.subject ? <div className="mt-2 break-all text-sm text-slate-700">{event.subject}</div> : null}
                                            {event.reason ? <div className="mt-2 text-sm text-slate-600">{event.reason}</div> : null}
                                            {Array.isArray(event.downloadHosts) && event.downloadHosts.length ? <div className="mt-2 text-xs text-slate-500">{t("app.admin.dashboard.safety.control.page.label.downloadHosts")}: {event.downloadHosts.join(", ")}</div> : null}
                                        </div>)}
                            </CardContent>
                        </Card>
                    </div>
                </AdvancedSection>

                <AdvancedSection title="app.admin.dashboard.safety.control.page.k4f8c7149" defaultOpen={false}>
                    <SafetyGuardianPanel />
                </AdvancedSection>

                <SourceMetaRow source={envelope.source} savePath={envelope.savePath} reloadRequired={envelope.reloadRequired} warnings={envelope.warnings} />
            </div>
        </AdminPageShell>;
}
