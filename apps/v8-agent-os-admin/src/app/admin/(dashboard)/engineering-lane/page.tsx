"use client";

import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Code2, Loader2, Play, RefreshCw, Save } from "lucide-react";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { SourceMetaRow } from "@/components/admin-shell/SourceMetaRow";
import { TechnicalReferenceDetails } from "@/components/common/TechnicalReferenceDetails";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { SettingToggleCard } from "@/components/admin-shell/SettingToggleCard";
import { Textarea } from "@/components/ui/textarea";
import { useT } from "@/components/providers/LocaleProvider";
import { fetchAdminJson } from "@/lib/admin-client-cache";
import { fetchConfigDomain, saveConfigDomain, type ConfigRegistryEnvelope } from "@/lib/config-registry";
import { tg } from "@/i18n/admin-legacy";

type DiagnosticsProviders = {
  git: boolean;
  command: boolean;
  lspBestEffort: boolean;
};

type EngineeringLaneConfig = {
  enabled: boolean;
  triggerMode: "auto" | "force" | "off";
  contextPackBudget: number;
  evidenceGraphEnabled: boolean;
  evidenceGraphBudget: number;
  codingExecutionContractEnabled: boolean;
  worksetGovernanceMode: "observe_auto_block" | "soft_gate" | "read_only" | "off";
  worksetObservationEnabled: boolean;
  workbenchDryRunMatrixEnabled: boolean;
  maxCriticalFiles: number;
  proofLedgerEnabled: boolean;
  autoProofCollectionEnabled: boolean;
  proofCollectionScope: "engineering_active" | "force_only" | "off";
  diagnosticsProviders: DiagnosticsProviders;
  worksetRiskMode: "observe_auto_block" | "soft_gate" | "read_only" | "off";
  suppressDailyMemory: boolean;
  suppressMemoryMap: boolean;
  rankedWorkflowPathCount: number;
};

type DiagnosticItem = {
  source?: string;
  kind?: string;
  command?: string;
  tool?: string;
  returnCode?: number | null;
  summary?: string;
  severity?: string;
  rawPreview?: string;
  truncated?: boolean;
};

type ProofEntry = {
  id: string;
  sessionId?: string;
  runId?: string;
  taskBriefId?: string | null;
  delegationId?: string | null;
  mode?: string;
  patchIntent?: string;
  verificationStatus?: string;
  readSet?: string[];
  writeSet?: string[];
  changedFiles?: string[];
  commands?: Array<Record<string, unknown>>;
  diagnostics?: {
    items?: DiagnosticItem[];
    gitSummary?: Record<string, unknown>;
    lspProvider?: Record<string, unknown>;
    worksetRisk?: Record<string, unknown>;
    worksetDispatchDecision?: Record<string, unknown>;
    worksetObservation?: Record<string, unknown>;
    worksetCorrelation?: Record<string, unknown>;
    outsideWriteSetFiles?: string[];
    manualOverride?: Record<string, unknown>;
    contextPackDigest?: Record<string, unknown>;
  };
  residualRisks?: string[];
  metadata?: Record<string, unknown>;
  worksetObservation?: Record<string, unknown>;
  worksetCorrelation?: Record<string, unknown>;
  outsideWriteSetFiles?: string[];
  manualOverride?: Record<string, unknown>;
  createdAt?: string;
};

type WorksetObservationEntry = {
  id: string;
  sessionId?: string | null;
  runId?: string | null;
  taskBriefId?: string | null;
  delegationId?: string | null;
  decisionSource?: string | null;
  phase?: string | null;
  decision?: Record<string, unknown>;
  warningOrBlockReason?: string | null;
  manualOverride?: boolean;
  outsideWriteSetFiles?: string[];
  correlationStatus?: string | null;
  metadata?: Record<string, unknown>;
  createdAt?: string | null;
  updatedAt?: string | null;
};

type CrossLinkMatrixScenario = {
  id?: string;
  group?: string;
  label?: string;
  status?: "pass" | "warning" | "fail" | string;
  summary?: string;
  checks?: Array<{id?: string;status?: string;message?: string;evidence?: unknown;}>;
  learningEligibility?: Record<string, unknown>;
  deepLinks?: Record<string, string>;
};

type CrossLinkMatrix = {
  enabled?: boolean;
  summary?: {
    total?: number;
    pass?: number;
    warning?: number;
    fail?: number;
    groups?: Record<string, {total?: number;pass?: number;warning?: number;fail?: number;}>;
  };
  scenarios?: CrossLinkMatrixScenario[];
};

type EngineeringWorkflowCandidate = {
  id: string;
  task_family?: string;
  taskFamily?: string;
  status?: string;
  proofBacked?: boolean;
  verificationBacked?: boolean;
  lastVerificationStatus?: string;
  worksetRisk?: string;
  outsideWriteSetCount?: number;
  manualOverrideCount?: number;
  proofEntryIds?: string[];
  updated_at?: string;
  updatedAt?: string;
};

const DEFAULT_CONFIG: EngineeringLaneConfig = {
  enabled: true,
  triggerMode: "auto",
  contextPackBudget: 48000,
  evidenceGraphEnabled: true,
  evidenceGraphBudget: 16000,
  codingExecutionContractEnabled: true,
  worksetGovernanceMode: "observe_auto_block",
  worksetObservationEnabled: true,
  workbenchDryRunMatrixEnabled: true,
  maxCriticalFiles: 24,
  proofLedgerEnabled: true,
  autoProofCollectionEnabled: true,
  proofCollectionScope: "engineering_active",
  diagnosticsProviders: { git: true, command: true, lspBestEffort: true },
  worksetRiskMode: "read_only",
  suppressDailyMemory: true,
  suppressMemoryMap: true,
  rankedWorkflowPathCount: 3
};

const TRIGGER_MODE_OPTIONS = [
{ value: "auto", labelKey: "app.admin.dashboard.engineeringLane.triggerMode.auto" },
{ value: "force", labelKey: "app.admin.dashboard.engineeringLane.triggerMode.force" },
{ value: "off", labelKey: "app.admin.dashboard.engineeringLane.triggerMode.off" }] as
const;

const WORKSET_GOVERNANCE_OPTIONS = [
{ value: "observe_auto_block", labelKey: "app.admin.dashboard.engineeringLane.worksetGovernance.observeAutoBlock" },
{ value: "soft_gate", labelKey: "app.admin.dashboard.engineeringLane.worksetGovernance.softGate" },
{ value: "read_only", labelKey: "app.admin.dashboard.engineeringLane.worksetGovernance.readOnly" },
{ value: "off", labelKey: "app.admin.dashboard.engineeringLane.worksetGovernance.off" }] as
const;

function asConfig(value: unknown): EngineeringLaneConfig {
  const raw = (value && typeof value === "object" ? value : {}) as Partial<EngineeringLaneConfig>;
  const providers = (raw.diagnosticsProviders || {}) as Partial<DiagnosticsProviders>;
  return {
    ...DEFAULT_CONFIG,
    ...raw,
    triggerMode: raw.triggerMode === "force" || raw.triggerMode === "off" ? raw.triggerMode : "auto",
    proofCollectionScope:
    raw.proofCollectionScope === "force_only" || raw.proofCollectionScope === "off" ?
    raw.proofCollectionScope :
    "engineering_active",
    worksetRiskMode:
    raw.worksetRiskMode === "off" || raw.worksetRiskMode === "soft_gate" || raw.worksetRiskMode === "observe_auto_block" ?
    raw.worksetRiskMode :
    "read_only",
    worksetGovernanceMode:
    raw.worksetGovernanceMode === "off" || raw.worksetGovernanceMode === "read_only" || raw.worksetGovernanceMode === "soft_gate" ?
    raw.worksetGovernanceMode :
    "observe_auto_block",
    diagnosticsProviders: {
      git: providers.git ?? true,
      command: providers.command ?? true,
      lspBestEffort: providers.lspBestEffort ?? true
    },
    contextPackBudget: Number(!raw.contextPackBudget || Number(raw.contextPackBudget) === 2400 ? DEFAULT_CONFIG.contextPackBudget : raw.contextPackBudget),
    evidenceGraphBudget: Number(!raw.evidenceGraphBudget || Number(raw.evidenceGraphBudget) === 1800 ? DEFAULT_CONFIG.evidenceGraphBudget : raw.evidenceGraphBudget),
    maxCriticalFiles: Number(raw.maxCriticalFiles || DEFAULT_CONFIG.maxCriticalFiles),
    rankedWorkflowPathCount: Number(raw.rankedWorkflowPathCount || DEFAULT_CONFIG.rankedWorkflowPathCount)
  };
}

function getStatusLabel(value: string, t: (key: string) => string): string {
  const map: Record<string, string> = {
    verified: "app.admin.dashboard.engineeringLane.statusVerified",
    unverified: "app.admin.dashboard.engineeringLane.statusUnverified",
    failed_verification: "app.admin.dashboard.engineeringLane.statusFailedVerification",
    failed_due_to_dispatch_error: "app.admin.dashboard.engineeringLane.statusFailedDueToDispatchError",
    failed: "app.admin.dashboard.engineeringLane.statusFail",
    planned: "app.admin.dashboard.engineeringLane.statusPlanned",
    observed_no_change: "app.admin.dashboard.engineeringLane.statusObservedNoChange",
    pass: "app.admin.dashboard.engineeringLane.statusPass",
    warning: "app.admin.dashboard.engineeringLane.statusWarning",
    fail: "app.admin.dashboard.engineeringLane.statusFail",
  };
  const labelKey = map[value];
  if (labelKey) return t(labelKey);
  return value.replace(/[_-]+/g, " ").trim() || "—";
}

function getRiskLabel(value: string, t: (key: string) => string): string {
  const map: Record<string, string> = {
    within_write_set: "app.admin.dashboard.engineeringLane.riskWithinScope",
    outside_write_set: "app.admin.dashboard.engineeringLane.riskOutsideScope",
    missing_write_set: "app.admin.dashboard.engineeringLane.riskMissingScope",
    unknown_write_set: "app.admin.dashboard.engineeringLane.riskNeedsReview",
    read_only_safe: "app.admin.dashboard.engineeringLane.riskReadOnly",
    not_evaluated: "app.admin.dashboard.engineeringLane.riskNotChecked"
  };
  const labelKey = map[value];
  if (labelKey) return t(labelKey);
  return value.replace(/[_-]+/g, " ").trim() || "—";
}

function StatusPill({ value }: {value?: string;}) {
  const t = useT();
  const normalized = String(value || "planned");
  const palette =
  normalized === "verified" || normalized === "pass" ?
  "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-200" :
  normalized === "failed_verification" || normalized.includes("fail") ?
  "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-200" :
  normalized === "unverified" || normalized === "warning" ?
  "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-200" :
  "bg-muted text-muted-foreground dark:bg-muted dark:text-muted-foreground";
  return <span className={`rounded-full px-3 py-1 text-xs font-medium ${palette}`}>{getStatusLabel(normalized, t)}</span>;
}

function MatrixStatusPill({ value }: {value?: string;}) {
  const t = useT();
  const normalized = String(value || "warning");
  const palette =
  normalized === "pass" ?
  "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-200" :
  normalized === "fail" ?
  "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-200" :
  "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-200";
  return <span className={`rounded-full px-3 py-1 text-xs font-medium ${palette}`}>{getStatusLabel(normalized, t)}</span>;
}

function FieldList({ items, empty }: {items?: string[];empty: string;}) {
  if (!items?.length) {
    return <p className="text-sm text-muted-foreground dark:text-muted-foreground">{empty}</p>;
  }
  return (
    <div className="flex flex-wrap gap-2">
            {items.slice(0, 24).map((item) =>
      <span key={item} className="rounded-full bg-muted px-3 py-1 text-xs text-foreground dark:bg-muted dark:text-muted-foreground">
                    {item}
                </span>
      )}
        </div>);

}

function SummaryCard({ label, value, hint, tone = "slate" }: {label: string;value: string;hint?: string;tone?: "slate" | "emerald" | "amber" | "rose";}) {
  const toneClass =
  tone === "emerald" ?
  "border-emerald-200 bg-emerald-50/70 text-emerald-900 dark:border-emerald-500/30 dark:bg-card dark:text-emerald-200" :
  tone === "amber" ?
  "border-amber-200 bg-amber-50/70 text-amber-900 dark:border-amber-500/30 dark:bg-card dark:text-amber-200" :
  tone === "rose" ?
  "border-rose-200 bg-rose-50/70 text-rose-900 dark:border-rose-500/30 dark:bg-card dark:text-rose-200" :
  "border-border bg-card text-foreground dark:border-border dark:bg-card dark:text-slate-100";
  return (
    <div className={`rounded-2xl border p-4 ${toneClass}`}>
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground dark:text-muted-foreground">{label}</div>
            <div className="mt-2 text-2xl font-semibold">{value}</div>
            {hint ? <p className="mt-2 text-sm text-muted-foreground dark:text-muted-foreground">{hint}</p> : null}
        </div>);

}

function AdvancedPanel({ title, children, defaultOpen = false }: {title: string;children: ReactNode;defaultOpen?: boolean;}) {
  return (
    <details open={defaultOpen} className="rounded-2xl border border-border bg-card p-4 shadow-sm dark:border-border dark:bg-card">
            <summary className="cursor-pointer list-none">
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                        <h3 className="text-base font-semibold text-foreground dark:text-slate-100">{title}</h3>
                    </div>
                </div>
            </summary>
            <div className="mt-4 border-t border-border/60 pt-4 dark:border-border">{children}</div>
        </details>);

}

function normalizeWorksetRisk(value: unknown): string {
  const raw = String(value || "").trim().toLowerCase();
  if (raw === "ready" || raw === "within_write_set" || raw === "none") return "within_write_set";
  if (raw === "write_set_conflict" || raw === "outside_write_set") return "outside_write_set";
  if (raw === "missing_write_set" || raw === "unknown_write_set" || raw === "read_only_safe" || raw === "not_evaluated") return raw;
  if (raw === "not_engineering") return "not_evaluated";
  return "not_evaluated";
}

function resolveWorksetRisk(proof: ProofEntry | null | undefined): string {
  if (!proof) return "not_evaluated";
  return normalizeWorksetRisk(
    (proof.diagnostics?.worksetRisk as Record<string, unknown> | undefined)?.risk ||
    (proof.worksetCorrelation as Record<string, unknown> | undefined)?.risk
  );
}

function resolveObservationRisk(entry: WorksetObservationEntry | null | undefined): string {
  if (!entry) return "not_evaluated";
  return normalizeWorksetRisk(entry.correlationStatus || (entry.decision || {}).risk);
}

export default function EngineeringLanePage() {
  const t = useT();
  const [envelope, setEnvelope] = useState<ConfigRegistryEnvelope<EngineeringLaneConfig> | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [proofLoading, setProofLoading] = useState(true);
  const [refreshingProof, setRefreshingProof] = useState(false);
  const [dryRunText, setDryRunText] = useState(tg(t, "2a585054"));
  const [dryRunMode, setDryRunMode] = useState<"auto" | "force" | "off">("auto");
  const [dryRunResult, setDryRunResult] = useState<Record<string, unknown> | null>(null);
  const [proofEntries, setProofEntries] = useState<ProofEntry[]>([]);
  const [worksetObservations, setWorksetObservations] = useState<WorksetObservationEntry[]>([]);
  const [selectedProofId, setSelectedProofId] = useState<string>("");
  const [proofStatusFilter, setProofStatusFilter] = useState("all");
  const [proofSessionFilter, setProofSessionFilter] = useState("");
  const [proofRunFilter, setProofRunFilter] = useState("");
  const [proofTaskBriefFilter, setProofTaskBriefFilter] = useState("");
  const [worksetRiskFilter, setWorksetRiskFilter] = useState("all");
  const [outsideFilter, setOutsideFilter] = useState("all");
  const [decisionSourceFilter, setDecisionSourceFilter] = useState("all");
  const [observationStateFilter, setObservationStateFilter] = useState("all");
  const [engineeringWorkflowCandidates, setEngineeringWorkflowCandidates] = useState<EngineeringWorkflowCandidate[]>([]);

  const config = useMemo(() => asConfig(envelope?.data), [envelope]);
  const visibleProofEntries = useMemo(() => proofEntries.filter((entry) => {
    const risk = resolveWorksetRisk(entry);
    const outsideCount = Array.isArray(entry.outsideWriteSetFiles) ? entry.outsideWriteSetFiles.length : 0;
    if (proofStatusFilter !== "all" && String(entry.verificationStatus || "") !== proofStatusFilter) return false;
    if (proofSessionFilter.trim() && String(entry.sessionId || "") !== proofSessionFilter.trim()) return false;
    if (proofRunFilter.trim() && String(entry.runId || "") !== proofRunFilter.trim()) return false;
    if (proofTaskBriefFilter.trim() && String(entry.taskBriefId || "") !== proofTaskBriefFilter.trim()) return false;
    if (worksetRiskFilter !== "all" && risk !== worksetRiskFilter) return false;
    if (outsideFilter === "outside_only" && outsideCount <= 0) return false;
    if (outsideFilter === "clean_only" && outsideCount > 0) return false;
    return true;
  }), [proofEntries, proofStatusFilter, proofSessionFilter, proofRunFilter, proofTaskBriefFilter, worksetRiskFilter, outsideFilter]);
  const selectedProof = visibleProofEntries.find((entry) => entry.id === selectedProofId) || visibleProofEntries[0] || proofEntries[0] || null;
  const visibleWorksetObservations = useMemo(() => worksetObservations.filter((entry) => {
    if (proofSessionFilter.trim() && String(entry.sessionId || "") !== proofSessionFilter.trim()) return false;
    if (proofRunFilter.trim() && String(entry.runId || "") !== proofRunFilter.trim()) return false;
    if (proofTaskBriefFilter.trim() && String(entry.taskBriefId || "") !== proofTaskBriefFilter.trim()) return false;
    if (decisionSourceFilter !== "all" && String(entry.decisionSource || "") !== decisionSourceFilter) return false;
    if (observationStateFilter === "blocked_only" && !Boolean((entry.decision || {}).blocked)) return false;
    if (observationStateFilter === "warning_only" && !Boolean((entry.decision || {}).warning || (entry.decision || {}).blocked)) return false;
    if (observationStateFilter === "clean_only" && Boolean((entry.decision || {}).warning || (entry.decision || {}).blocked)) return false;
    if (outsideFilter === "outside_only" && !(entry.outsideWriteSetFiles || []).length) return false;
    if (outsideFilter === "clean_only" && (entry.outsideWriteSetFiles || []).length) return false;
    if (worksetRiskFilter !== "all") {
      const risk = resolveObservationRisk(entry);
      if (risk !== worksetRiskFilter) return false;
    }
    return true;
  }), [worksetObservations, proofSessionFilter, proofRunFilter, proofTaskBriefFilter, decisionSourceFilter, observationStateFilter, outsideFilter, worksetRiskFilter]);

  const load = async (force = false) => {
    setLoading(true);
    try {
      const next = await fetchConfigDomain<EngineeringLaneConfig>("engineering-lane", { force });
      setEnvelope({ ...next, data: asConfig(next.data) });
    } finally {
      setLoading(false);
    }
  };

  const loadProof = async (force = false) => {
    setProofLoading(true);
    try {
      const params = new URLSearchParams();
      params.set("limit", "30");
      if (proofStatusFilter !== "all") params.set("status", proofStatusFilter);
      if (proofSessionFilter.trim()) params.set("sessionId", proofSessionFilter.trim());
      if (proofRunFilter.trim()) params.set("runId", proofRunFilter.trim());
      const data = await fetchAdminJson<{ items?: ProofEntry[] }>(`/api/engineering-lane/proof-ledger?${params.toString()}`, { force, ttlMs: 10_000 });
      const items = Array.isArray(data.items) ? data.items : [];
      setProofEntries(items);
      setSelectedProofId((current) => items.some((item: ProofEntry) => item.id === current) ? current : items[0]?.id || "");
    } finally {
      setProofLoading(false);
    }
  };

  const loadWorksetObservations = async (force = false) => {
    const params = new URLSearchParams();
    params.set("limit", "40");
    if (proofSessionFilter.trim()) params.set("sessionId", proofSessionFilter.trim());
    if (proofRunFilter.trim()) params.set("runId", proofRunFilter.trim());
    if (proofTaskBriefFilter.trim()) params.set("taskBriefId", proofTaskBriefFilter.trim());
    if (decisionSourceFilter !== "all") params.set("decisionSource", decisionSourceFilter);
    const data = await fetchAdminJson<{ items?: WorksetObservationEntry[] }>(`/api/engineering-lane/workset-observations?${params.toString()}`, { force, ttlMs: 10_000 });
    setWorksetObservations(Array.isArray(data.items) ? data.items : []);
  };

  const loadEngineeringWorkflowCandidates = async (force = false) => {
    const params = new URLSearchParams({ class: "engineering", limit: "8" });
    const data = await fetchAdminJson<{ items?: EngineeringWorkflowCandidate[] }>(`/api/memory/workflows?${params.toString()}`, { force, ttlMs: 10_000 });
    setEngineeringWorkflowCandidates(Array.isArray(data.items) ? data.items : []);
  };

  useEffect(() => {
    void Promise.all([load(), loadProof(), loadWorksetObservations(), loadEngineeringWorkflowCandidates()]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const patchConfig = (patch: Partial<EngineeringLaneConfig>) => {
    if (!envelope) return;
    setEnvelope({ ...envelope, data: { ...config, ...patch } });
  };

  const save = async () => {
    if (!envelope) return;
    setSaving(true);
    try {
      const next = await saveConfigDomain<EngineeringLaneConfig>("engineering-lane", { data: config });
      setEnvelope({ ...next, data: asConfig(next.data) });
    } finally {
      setSaving(false);
    }
  };

  const runDryRun = async () => {
    setRunning(true);
    try {
      const response = await fetch("/api/engineering-lane/dry-run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ userQuery: dryRunText, engineeringMode: dryRunMode })
      });
      setDryRunResult(await response.json().catch(() => ({})));
    } finally {
      setRunning(false);
    }
  };

  const refreshSelectedProof = async () => {
    if (!selectedProof?.sessionId || !selectedProof.runId) return;
    setRefreshingProof(true);
    try {
      await fetch("/api/engineering-lane/proof-ledger/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sessionId: selectedProof.sessionId, runId: selectedProof.runId })
      });
      await Promise.all([loadProof(true), loadWorksetObservations(true)]);
    } finally {
      setRefreshingProof(false);
    }
  };

  const triggerDecision = (dryRunResult?.triggerDecision || {}) as Record<string, unknown>;
  const contextPack = (dryRunResult?.contextPack || {}) as Record<string, unknown>;
  const dryRunSoftGate = (dryRunResult?.worksetSoftGateDecision || contextPack.worksetSoftGateDecision || {}) as Record<string, unknown>;
  const brokerDispatch = (dryRunResult?.brokerDispatchSimulation || contextPack.brokerDispatchSimulation || {}) as Record<string, unknown>;
  const crossLinkMatrix = (dryRunResult?.crossLinkDryRunMatrix || {}) as CrossLinkMatrix;
  const crossLinkScenarios = Array.isArray(crossLinkMatrix.scenarios) ? crossLinkMatrix.scenarios : [];
  const repoBrief = (contextPack.repoBrief || {}) as Record<string, unknown>;
  const memorySuppression = (contextPack.memorySuppression || {}) as Record<string, unknown>;
  const diagnostics = selectedProof?.diagnostics?.items || [];
  const worksetRisk = selectedProof?.diagnostics?.worksetRisk || {};
  const worksetObservation = selectedProof?.worksetObservation || selectedProof?.diagnostics?.worksetObservation || {};
  const worksetCorrelation = selectedProof?.worksetCorrelation || selectedProof?.diagnostics?.worksetCorrelation || {};
  const selectedRisk = resolveWorksetRisk(selectedProof);
  const latestObservation = visibleWorksetObservations[0] || worksetObservations[0] || null;
  const latestObservationRisk = resolveObservationRisk(latestObservation);
  const matrixSummary = crossLinkMatrix.summary || {};
  const matrixTotal = Number(matrixSummary.total ?? crossLinkScenarios.length ?? 0);
  const matrixPass = Number(matrixSummary.pass ?? crossLinkScenarios.filter((item) => item.status === "pass").length);
  const matrixWarning = Number(matrixSummary.warning ?? crossLinkScenarios.filter((item) => item.status === "warning").length);
  const matrixFail = Number(matrixSummary.fail ?? crossLinkScenarios.filter((item) => item.status === "fail").length);
  const matrixTone = matrixFail > 0 ? "rose" : matrixWarning > 0 ? "amber" : matrixTotal > 0 ? "emerald" : "slate";
  const topMatrixIssues = crossLinkScenarios.
  map((item) => {
    const issue = (item.checks || []).find((check) => check.status === "fail" || check.status === "warning");
    if (issue) return `${String(item.group || "matrix")}/${String(item.label || item.id || "-")}: ${String(issue.message || item.summary || "")}`;
    if (item.status === "fail" || item.status === "warning") return `${String(item.group || "matrix")}/${String(item.label || item.id || "-")}: ${String(item.summary || item.status || "")}`;
    return "";
  }).
  filter(Boolean).
  slice(0, 5);
  const dryRunProofDraft = (dryRunResult?.proofDraft || {}) as Record<string, unknown>;
  const learningEligibility = (dryRunResult?.learningEligibility || {}) as Record<string, unknown>;
  const dryRunBrokerRisk = normalizeWorksetRisk(dryRunSoftGate.risk || (brokerDispatch as Record<string, unknown>).risk || "not_evaluated");
  const healthTone = !config.enabled ? "slate" : selectedRisk === "outside_write_set" || selectedRisk === "missing_write_set" ? "amber" : "emerald";
  const triggerModeLabel = t(TRIGGER_MODE_OPTIONS.find((option) => option.value === config.triggerMode)?.labelKey || "app.admin.dashboard.engineeringLane.triggerMode.auto");

  return (
    <AdminPageShell className="max-w-none">
            <AdminPageHeader
        title="app.admin.dashboard.engineeringLane.title"
        description="app.admin.dashboard.engineeringLane.description"
        actions={
        <Button onClick={save} disabled={saving || loading}>
                        {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                        {t("app.admin.dashboard.engineeringLane.save")}
                    </Button>
        } />


            <div className="space-y-6">
                <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                    <SummaryCard
            label={t("app.admin.dashboard.engineeringLane.healthOverview")}
            value={config.enabled ? t("app.admin.dashboard.engineeringLane.valueEnabled") : t("app.admin.dashboard.engineeringLane.valueOff")}
            hint={`${t("app.admin.dashboard.engineeringLane.triggerMode")}: ${triggerModeLabel}`}
            tone={healthTone} />

                    <SummaryCard
            label={t("app.admin.dashboard.engineeringLane.recentProofStatus")}
            value={selectedProof?.verificationStatus ? getStatusLabel(selectedProof.verificationStatus, t) : t("app.admin.dashboard.engineeringLane.valueNone")}
            hint={`${t("app.admin.dashboard.engineeringLane.residualRisks")}: ${String(selectedProof?.residualRisks?.length || 0)}`}
            tone={selectedProof?.verificationStatus === "verified" ? "emerald" : selectedProof?.verificationStatus === "failed_verification" ? "rose" : "amber"} />

                    <SummaryCard
            label={t("app.admin.dashboard.engineeringLane.recentWorksetRisk")}
            value={getRiskLabel(latestObservationRisk !== "not_evaluated" ? latestObservationRisk : selectedRisk, t)}
            hint={t("app.admin.dashboard.engineeringLane.outsideFileCount", { count: String((selectedProof?.outsideWriteSetFiles || latestObservation?.outsideWriteSetFiles || []).length) })}
            tone={selectedRisk === "outside_write_set" || latestObservationRisk === "outside_write_set" ? "amber" : "slate"} />

                    <SummaryCard
            label={t("app.admin.dashboard.engineeringLane.matrixSummaryTitle")}
            value={matrixTotal ? `${matrixPass}/${matrixTotal}` : t("app.admin.dashboard.engineeringLane.valueNotRun")}
            hint={matrixTotal ? t("app.admin.dashboard.engineeringLane.matrixIssueSummary", { warn: String(matrixWarning), fail: String(matrixFail) }) : t("app.admin.dashboard.engineeringLane.matrixDiagnosticNote")}
            tone={matrixTone} />

                </div>

                <div className="grid gap-6 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.4fr)]">
                    <ConfigCard
            title="app.admin.dashboard.engineeringLane.basicConfigTitle"
            description="app.admin.dashboard.engineeringLane.basicConfigDescription"
            bodyHeight="auto"
            footer={envelope ? <SourceMetaRow source={envelope.source} savePath={envelope.savePath} reloadRequired={Boolean(envelope.reloadRequired)} /> : null}>

                        {loading ?
            <div className="flex items-center gap-2 text-sm text-muted-foreground dark:text-muted-foreground">
                                <Loader2 className="h-4 w-4 animate-spin" />
                                {t("app.admin.dashboard.engineeringLane.loading")}
                            </div> :

            <div className="space-y-4">
                                <SettingToggleCard
                                    title={t("app.admin.dashboard.engineeringLane.enabled")}
                                    description={t("app.admin.dashboard.engineeringLane.enabledHint")}
                                    checked={config.enabled}
                                    onCheckedChange={(enabled) => patchConfig({ enabled })}
                                    className="border-border p-4 rounded-2xl"
                                />
                                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-1">
                                    <div className="space-y-2">
                                        <Label>{t("app.admin.dashboard.engineeringLane.triggerMode")}</Label>
                                        <Select value={config.triggerMode} onValueChange={(value) => patchConfig({ triggerMode: value as EngineeringLaneConfig["triggerMode"] })}>
                                            <SelectTrigger><SelectValue /></SelectTrigger>
                                            <SelectContent>
                                                {TRIGGER_MODE_OPTIONS.map((option) =>
                      <SelectItem key={option.value} value={option.value}>{t(option.labelKey)}</SelectItem>
                      )}
                                            </SelectContent>
                                        </Select>
                                    </div>
                                    <SettingToggleCard
                                        title={t("app.admin.dashboard.engineeringLane.autoProofCollection")}
                                        description={t("app.admin.dashboard.engineeringLane.autoProofCollectionHint")}
                                        checked={config.autoProofCollectionEnabled}
                                        onCheckedChange={(autoProofCollectionEnabled) => patchConfig({ autoProofCollectionEnabled })}
                                        className="border-border p-4 rounded-2xl"
                                    />
                                </div>
                                <details className="rounded-2xl border border-border bg-muted/50 p-4 dark:border-border dark:bg-card">
                                    <summary className="cursor-pointer text-sm font-semibold text-foreground dark:text-slate-100">
                                        {t("app.admin.dashboard.engineeringLane.advancedConfigTitle")}
                                    </summary>
                                    <div className="mt-4 grid gap-3 md:grid-cols-2">
                                        <div className="space-y-2">
                                            <Label>{t("app.admin.dashboard.engineeringLane.contextBudget")}</Label>
                                            <Input type="number" value={config.contextPackBudget} onChange={(event) => patchConfig({ contextPackBudget: Number(event.target.value) })} />
                                        </div>
                                        <div className="space-y-2">
                                            <Label>{t("app.admin.dashboard.engineeringLane.evidenceGraphBudget")}</Label>
                                            <Input type="number" value={config.evidenceGraphBudget} onChange={(event) => patchConfig({ evidenceGraphBudget: Number(event.target.value) })} />
                                        </div>
                                        <div className="space-y-2">
                                            <Label>{t("app.admin.dashboard.engineeringLane.worksetGovernanceMode")}</Label>
                                            <Select value={config.worksetGovernanceMode} onValueChange={(value) => patchConfig({ worksetGovernanceMode: value as EngineeringLaneConfig["worksetGovernanceMode"], worksetRiskMode: value as EngineeringLaneConfig["worksetRiskMode"] })}>
                                                <SelectTrigger><SelectValue /></SelectTrigger>
                                                <SelectContent>
                                                    {WORKSET_GOVERNANCE_OPTIONS.map((option) =>
                        <SelectItem key={option.value} value={option.value}>{t(option.labelKey)}</SelectItem>
                        )}
                                                </SelectContent>
                                            </Select>
                                        </div>
                                        <div className="space-y-2">
                                            <Label>{t("app.admin.dashboard.engineeringLane.rankedPathCount")}</Label>
                                            <Input type="number" min={1} max={5} value={config.rankedWorkflowPathCount} onChange={(event) => patchConfig({ rankedWorkflowPathCount: Number(event.target.value) })} />
                                        </div>
                                        {([
                  ["evidenceGraphEnabled", "evidenceGraphEnabled"],
                  ["codingExecutionContractEnabled", "codingExecutionContractEnabled"],
                  ["proofLedgerEnabled", "proofLedger"],
                  ["worksetObservationEnabled", "worksetObservation"],
                  ["workbenchDryRunMatrixEnabled", "dryRunMatrix"],
                  ["suppressDailyMemory", "suppressDaily"],
                  ["suppressMemoryMap", "suppressMap"]] as
                  const).map(([key, label]) =>
                  <SettingToggleCard
                      key={key}
                      title={t(`app.admin.dashboard.engineeringLane.${label}`)}
                      description={t(`app.admin.dashboard.engineeringLane.${label}Hint`)}
                      checked={Boolean(config[key])}
                      onCheckedChange={(value) => patchConfig({ [key]: value } as Partial<EngineeringLaneConfig>)}
                      className="border-border bg-card p-3 rounded-xl dark:border-border dark:bg-muted/40"
                  />
                  )}
                                    </div>
                                </details>
                            </div>
            }
                    </ConfigCard>

                    <div className="space-y-6">
                        <ConfigCard
              title="app.admin.dashboard.engineeringLane.dryRunDiagnosticTitle"
              description="app.admin.dashboard.engineeringLane.dryRunDiagnosticDescription"
              bodyHeight="auto">

                            <div className="space-y-4">
                                <div className="grid gap-3 lg:grid-cols-[1fr_160px]">
                                    <Textarea className="min-h-[96px]" value={dryRunText} onChange={(event) => setDryRunText(event.target.value)} />
                                    <div className="space-y-3">
                                        <Select value={dryRunMode} onValueChange={(value) => setDryRunMode(value as "auto" | "force" | "off")}>
                                            <SelectTrigger><SelectValue /></SelectTrigger>
                                            <SelectContent>
                                                {TRIGGER_MODE_OPTIONS.map((option) =>
                        <SelectItem key={option.value} value={option.value}>{t(option.labelKey)}</SelectItem>
                        )}
                                            </SelectContent>
                                        </Select>
                                        <Button className="w-full" onClick={runDryRun} disabled={running || !dryRunText.trim()}>
                                            {running ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Play className="mr-2 h-4 w-4" />}
                                            {t("app.admin.dashboard.engineeringLane.runDryRun")}
                                        </Button>
                                    </div>
                                </div>
                                {dryRunResult ?
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                                        <SummaryCard label={t("app.admin.dashboard.engineeringLane.summaryTrigger")} value={triggerDecision.active ? t("app.admin.dashboard.engineeringLane.valueYes") : t("app.admin.dashboard.engineeringLane.valueNo")} hint={String(triggerDecision.reason || "")} tone={triggerDecision.active ? "emerald" : "slate"} />
                                        <SummaryCard label={t("app.admin.dashboard.engineeringLane.summaryScope")} value={repoBrief.repoDetected ? t("app.admin.dashboard.engineeringLane.valueRepo") : t("app.admin.dashboard.engineeringLane.valueNoRepo")} hint={String(repoBrief.workspaceRoot || repoBrief.repoRoot || "-")} />
                                        <SummaryCard
                    label={t("app.admin.dashboard.engineeringLane.summaryMemory")}
                    value={memorySuppression.suppressDailyMemory || memorySuppression.suppressMemoryMap ? t("app.admin.dashboard.engineeringLane.memorySuppressed") : t("app.admin.dashboard.engineeringLane.memoryNormal")}
                    hint={t("app.admin.dashboard.engineeringLane.workflowHintKept")}
                    tone="amber" />

                                        <SummaryCard label={t("app.admin.dashboard.engineeringLane.summaryDispatchRisk")} value={getRiskLabel(dryRunBrokerRisk, t)} hint={String(dryRunSoftGate.suggestedAction || brokerDispatch.recommendedAction || "-")} tone={dryRunBrokerRisk === "outside_write_set" || dryRunBrokerRisk === "missing_write_set" ? "amber" : "slate"} />
                                        <SummaryCard label={t("app.admin.dashboard.engineeringLane.summaryProofDraft")} value={getStatusLabel(String(dryRunProofDraft.verificationStatus || "planned"), t)} hint={String(dryRunProofDraft.reason || "")} />
                                        <SummaryCard label={t("app.admin.dashboard.engineeringLane.summaryLearning")} value={learningEligibility.eligible === true ? t("app.admin.dashboard.engineeringLane.valueYes") : learningEligibility.eligible === false ? t("app.admin.dashboard.engineeringLane.valueNo") : t("app.admin.dashboard.engineeringLane.valueRouteTestOnly")} hint={String(learningEligibility.reason || t("app.admin.dashboard.engineeringLane.matrixDiagnosticNote"))} />
                                    </div> :

                <div className="rounded-2xl border border-dashed border-border p-6 text-sm text-muted-foreground dark:border-border dark:text-muted-foreground">
                                        <Code2 className="mb-3 h-5 w-5" />
                                        {t("app.admin.dashboard.engineeringLane.noDryRun")}
                                    </div>
                }
                                {dryRunResult && (matrixTotal > 0 || topMatrixIssues.length > 0) ?
                <div className="rounded-2xl border border-border bg-muted/50 p-4 dark:border-border dark:bg-card">
                                        <div className="flex flex-wrap items-start justify-between gap-3">
                                            <div>
                                                <h3 className="text-sm font-semibold text-foreground dark:text-slate-100">{t("app.admin.dashboard.engineeringLane.crossLinkMatrixTitle")}</h3>
                                                <p className="mt-1 text-xs text-muted-foreground dark:text-muted-foreground">{t("app.admin.dashboard.engineeringLane.matrixDiagnosticNote")}</p>
                                            </div>
                                            <MatrixStatusPill value={matrixFail > 0 ? "fail" : matrixWarning > 0 ? "warning" : "pass"} />
                                        </div>
                                        <div className="mt-3 grid gap-2 sm:grid-cols-4">
                                            <span className="rounded-xl bg-card px-3 py-2 text-sm text-foreground dark:bg-muted dark:text-slate-100">{t("app.admin.dashboard.engineeringLane.matrixTotal", { count: String(matrixTotal) })}</span>
                                            <span className="rounded-xl bg-card px-3 py-2 text-sm text-emerald-700 dark:bg-muted dark:text-emerald-200">{t("app.admin.dashboard.engineeringLane.matrixPass", { count: String(matrixPass) })}</span>
                                            <span className="rounded-xl bg-card px-3 py-2 text-sm text-amber-700 dark:bg-muted dark:text-amber-200">{t("app.admin.dashboard.engineeringLane.matrixWarning", { count: String(matrixWarning) })}</span>
                                            <span className="rounded-xl bg-card px-3 py-2 text-sm text-rose-700 dark:bg-muted dark:text-rose-200">{t("app.admin.dashboard.engineeringLane.matrixFail", { count: String(matrixFail) })}</span>
                                        </div>
                                        {topMatrixIssues.length ?
                  <div className="mt-3 space-y-2">
                                                {topMatrixIssues.map((issue) =>
                    <div key={issue} className="rounded-xl border border-amber-100 bg-card px-3 py-2 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
                                                        {issue}
                                                    </div>
                    )}
                                            </div> :
                  null}
                                    </div> :
                null}
                            </div>
                        </ConfigCard>

                        <ConfigCard title="app.admin.dashboard.engineeringLane.recentRiskTitle" description="app.admin.dashboard.engineeringLane.recentRiskDescription" bodyHeight="auto">
                            <div className="grid gap-3 md:grid-cols-3">
                                <SummaryCard label={t("app.admin.dashboard.engineeringLane.summaryVerification")} value={selectedProof?.verificationStatus ? getStatusLabel(selectedProof.verificationStatus, t) : t("app.admin.dashboard.engineeringLane.valueNone")} hint={selectedProof?.createdAt || t("app.admin.dashboard.engineeringLane.valueNone")} />
                                <SummaryCard label={t("app.admin.dashboard.engineeringLane.summaryWorkset")} value={getRiskLabel(selectedRisk, t)} hint={t("app.admin.dashboard.engineeringLane.changedFileCount", { count: String(selectedProof?.changedFiles?.length || 0) })} tone={selectedRisk === "outside_write_set" ? "amber" : "slate"} />
                                <SummaryCard label={t("app.admin.dashboard.engineeringLane.summaryWorkflowMemory")} value={String(engineeringWorkflowCandidates.length)} hint={t("app.admin.dashboard.engineeringLane.workflowMemoryReadOnly")} />
                            </div>
                        </ConfigCard>
                    </div>
                </div>
            </div>

            <AdvancedPanel title={t("app.admin.dashboard.engineeringLane.advancedDiagnosticsTitle")}>

            <div className="space-y-6">
                        <ConfigCard title="app.admin.dashboard.engineeringLane.proofTitle" description="app.admin.dashboard.engineeringLane.proofDescription" bodyScroll="auto" bodyHeight={520}>
                            <div className="space-y-4">
                                <div className="grid gap-2">
                                    <Select value={proofStatusFilter} onValueChange={setProofStatusFilter}>
                                        <SelectTrigger><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="all">{t("app.admin.dashboard.engineeringLane.filterAll")}</SelectItem>
                                            <SelectItem value="verified">{getStatusLabel("verified", t)}</SelectItem>
                                            <SelectItem value="unverified">{getStatusLabel("unverified", t)}</SelectItem>
                                            <SelectItem value="failed_verification">{getStatusLabel("failed_verification", t)}</SelectItem>
                                            <SelectItem value="planned">{getStatusLabel("planned", t)}</SelectItem>
                                            <SelectItem value="observed_no_change">{getStatusLabel("observed_no_change", t)}</SelectItem>
                                        </SelectContent>
                                    </Select>
                                    <Select value={worksetRiskFilter} onValueChange={setWorksetRiskFilter}>
                                        <SelectTrigger><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="all">{t("app.admin.dashboard.engineeringLane.filterAllChangeScopes")}</SelectItem>
                                            <SelectItem value="within_write_set">{getRiskLabel("within_write_set", t)}</SelectItem>
                                            <SelectItem value="outside_write_set">{getRiskLabel("outside_write_set", t)}</SelectItem>
                                            <SelectItem value="missing_write_set">{getRiskLabel("missing_write_set", t)}</SelectItem>
                                            <SelectItem value="unknown_write_set">{getRiskLabel("unknown_write_set", t)}</SelectItem>
                                            <SelectItem value="read_only_safe">{getRiskLabel("read_only_safe", t)}</SelectItem>
                                            <SelectItem value="not_evaluated">{getRiskLabel("not_evaluated", t)}</SelectItem>
                                        </SelectContent>
                                    </Select>
                                    <Select value={outsideFilter} onValueChange={setOutsideFilter}>
                                        <SelectTrigger><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="all">{t("app.admin.dashboard.engineeringLane.filterAllRanges")}</SelectItem>
                                            <SelectItem value="outside_only">{t("app.admin.dashboard.engineeringLane.filterOutsideOnly")}</SelectItem>
                                            <SelectItem value="clean_only">{t("app.admin.dashboard.engineeringLane.filterCleanOnly")}</SelectItem>
                                        </SelectContent>
                                    </Select>
                                    <Input placeholder={t("app.admin.dashboard.engineeringLane.sessionFilter")} value={proofSessionFilter} onChange={(event) => setProofSessionFilter(event.target.value)} />
                                    <Input placeholder={t("app.admin.dashboard.engineeringLane.runFilter")} value={proofRunFilter} onChange={(event) => setProofRunFilter(event.target.value)} />
                                    <Input placeholder={t("app.admin.dashboard.engineeringLane.taskFilter")} value={proofTaskBriefFilter} onChange={(event) => setProofTaskBriefFilter(event.target.value)} />
                                    <Select value={decisionSourceFilter} onValueChange={setDecisionSourceFilter}>
                                        <SelectTrigger><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="all">{t("app.admin.dashboard.engineeringLane.filterAllRoutes")}</SelectItem>
                                            <SelectItem value="supervisor_auto">{t("app.admin.dashboard.engineeringLane.filterAutoRoute")}</SelectItem>
                                            <SelectItem value="runtime_auto">{t("app.admin.dashboard.engineeringLane.filterRuntimeRoute")}</SelectItem>
                                            <SelectItem value="supervisor_manual">{t("app.admin.dashboard.engineeringLane.filterUserRoute")}</SelectItem>
                                            <SelectItem value="dry_run">{t("app.admin.dashboard.engineeringLane.filterRouteTest")}</SelectItem>
                                        </SelectContent>
                                    </Select>
                                    <Select value={observationStateFilter} onValueChange={setObservationStateFilter}>
                                        <SelectTrigger><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="all">{t("app.admin.dashboard.engineeringLane.filterAllStates")}</SelectItem>
                                            <SelectItem value="blocked_only">{t("app.admin.dashboard.engineeringLane.filterBlockedOnly")}</SelectItem>
                                            <SelectItem value="warning_only">{t("app.admin.dashboard.engineeringLane.filterNeedsAttention")}</SelectItem>
                                            <SelectItem value="clean_only">{t("app.admin.dashboard.engineeringLane.filterCleanOnly")}</SelectItem>
                                        </SelectContent>
                                    </Select>
                                    <Button variant="outline" onClick={() => void Promise.all([loadProof(true), loadWorksetObservations(true)])} disabled={proofLoading}>
                                        {proofLoading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                                        {t("app.admin.dashboard.engineeringLane.refreshList")}
                                    </Button>
                                </div>
                                {visibleProofEntries.length ?
                  <div className="grid gap-3">
                                        {visibleProofEntries.map((entry) =>
                    <button
                      key={entry.id}
                      type="button"
                      onClick={() => setSelectedProofId(entry.id)}
                      className={`rounded-xl border p-4 text-left transition ${selectedProof?.id === entry.id ? "border-slate-900 bg-muted/50" : "border-border hover:border-input"}`}>

                                                <div className="flex items-start justify-between gap-3">
                                                    <div className="min-w-0">
                                                        <div className="truncate text-sm font-medium text-foreground">{entry.patchIntent || t("app.admin.dashboard.engineeringLane.proofTitle")}</div>
                                                        <div className="mt-1 truncate text-xs text-muted-foreground">{entry.createdAt || ""}</div>
                                                    </div>
                                                    <StatusPill value={entry.verificationStatus} />
                                                </div>
                                                {entry.changedFiles?.length ? <p className="mt-2 truncate text-xs text-muted-foreground">{entry.changedFiles.slice(0, 4).join(", ")}</p> : null}
                                            </button>
                    )}
                                    </div> :

                  <div className="rounded-xl border border-dashed border-border p-6 text-sm text-muted-foreground">
                                        {t("app.admin.dashboard.engineeringLane.noProof")}
                                    </div>
                  }
                            </div>
                        </ConfigCard>

                        <div className="grid gap-6">
                            <ConfigCard title="app.admin.dashboard.engineeringLane.proofDetailTitle" description="app.admin.dashboard.engineeringLane.proofDetailDescription" bodyScroll="auto" bodyHeight={360}>
                                {selectedProof ?
                  <div className="space-y-4">
                                        <div className="flex flex-wrap items-center justify-between gap-3">
                                            <div>
                                                <h3 className="text-base font-semibold text-foreground">{selectedProof.patchIntent || t("app.admin.dashboard.engineeringLane.proofTitle")}</h3>
                                                {selectedProof.createdAt ? <p className="mt-1 text-xs text-muted-foreground">{selectedProof.createdAt}</p> : null}
                                            </div>
                                            <div className="flex items-center gap-2">
                                                <StatusPill value={selectedProof.verificationStatus} />
                                                <Button variant="outline" size="sm" onClick={refreshSelectedProof} disabled={refreshingProof || !selectedProof.sessionId || !selectedProof.runId}>
                                                    {refreshingProof ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                                                    {t("app.admin.dashboard.engineeringLane.refreshProof")}
                                                </Button>
                                            </div>
                                        </div>
                                        <TechnicalReferenceDetails
                                            items={[
                                                { label: t("components.common.sessionReference"), value: selectedProof.sessionId },
                                                { label: t("components.common.runReference"), value: selectedProof.runId },
                                                { label: t("components.common.rawReference"), value: selectedProof.id },
                                            ]}
                                        />
                                        <div className="grid gap-4">
                                            <div>
                                                <h4 className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground/80">{t("app.admin.dashboard.engineeringLane.changedFiles")}</h4>
                                                <FieldList items={selectedProof.changedFiles} empty={t("app.admin.dashboard.engineeringLane.none")} />
                                            </div>
                                            <div>
                                                <h4 className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground/80">{t("app.admin.dashboard.engineeringLane.residualRisks")}</h4>
                                                {selectedProof.residualRisks?.length ?
                        <div className="space-y-2">
                                                        {selectedProof.residualRisks.map((risk) =>
                          <div key={risk} className="flex gap-2 rounded-lg bg-amber-50 p-2 text-xs text-amber-800">
                                                                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                                                                <span>{risk}</span>
                                                            </div>
                          )}
                                                    </div> :

                        <p className="text-sm text-muted-foreground">{t("app.admin.dashboard.engineeringLane.none")}</p>
                        }
                                            </div>
                                        </div>
                                    </div> :

                  <div className="rounded-xl border border-dashed border-border p-6 text-sm text-muted-foreground">{t("app.admin.dashboard.engineeringLane.noProof")}</div>
                  }
                            </ConfigCard>

                            <ConfigCard title="app.admin.dashboard.engineeringLane.worksetObservationTitle" description="app.admin.dashboard.engineeringLane.worksetObservationDescription" bodyScroll="auto" bodyHeight={360}>
                                {visibleWorksetObservations.length ?
                  <div className="space-y-3">
                                        {visibleWorksetObservations.slice(0, 16).map((entry) => {
                      const risk = resolveObservationRisk(entry);
                      return (
                        <div key={entry.id} className="rounded-xl border border-border p-3">
                                                    <div className="flex flex-wrap items-center justify-between gap-2">
                                                        <div className="min-w-0">
                                                            <div className="truncate text-sm font-medium text-foreground">{entry.taskBriefId || entry.delegationId || entry.id}</div>
                                                        </div>
                                                        <div className="text-right text-xs text-muted-foreground">
                                                            <div>{t("app.admin.dashboard.engineeringLane.labelRisk")}: {getRiskLabel(risk, t)}</div>
                                                            <div>{t("app.admin.dashboard.engineeringLane.labelOutside")}: {(entry.outsideWriteSetFiles || []).length}</div>
                                                        </div>
                                                    </div>
                                                    {entry.warningOrBlockReason ? <p className="mt-2 text-xs text-muted-foreground">{entry.warningOrBlockReason}</p> : null}
                                                </div>);

                    })}
                                    </div> :

                  <div className="rounded-xl border border-dashed border-border p-6 text-sm text-muted-foreground">
                                        {tg(t, "06796e5a")}
                                    </div>
                  }
                            </ConfigCard>

                            <div className="space-y-6">
                                <ConfigCard title="app.admin.dashboard.engineeringLane.diagnosticsTitle" description="app.admin.dashboard.engineeringLane.diagnosticsDescription" bodyScroll="auto" bodyHeight={360}>
                                    {diagnostics.length ?
                    <div className="space-y-3">
                                            {diagnostics.map((item, index) =>
                      <div key={`${item.source}-${item.kind}-${index}`} className="rounded-xl border border-border p-3">
                                                    <div className="flex flex-wrap items-center justify-between gap-2">
                                                        <span className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground/80">{item.source} · {item.kind}</span>
                                                    </div>
                                                    <p className="mt-2 text-sm text-foreground">{item.summary}</p>
                                                </div>
                      )}
                                        </div> :

                    <div className="rounded-xl border border-dashed border-border p-6 text-sm text-muted-foreground">{t("app.admin.dashboard.engineeringLane.noDiagnostics")}</div>
                    }
                                </ConfigCard>

                                <ConfigCard title="app.admin.dashboard.engineeringLane.worksetTitle" description="app.admin.dashboard.engineeringLane.worksetDescription" bodyScroll="auto" bodyHeight={360}>
                                    {selectedProof ?
                    <div className="space-y-4">
                                            <div className="grid gap-3">
                                                <div className="rounded-xl border border-border p-4">
                                                    <div className="text-xs uppercase tracking-[0.18em] text-muted-foreground/80">{t("app.admin.dashboard.engineeringLane.labelModifyRisk")}</div>
                                                    <div className="mt-1 text-lg font-semibold text-foreground">{getRiskLabel(resolveWorksetRisk(selectedProof), t)}</div>
                                                    {worksetRisk.note ? <p className="mt-1 text-xs text-muted-foreground">{String(worksetRisk.note)}</p> : null}
                                                </div>
                                                <div className="rounded-xl border border-border p-4">
                                                    <div className="text-xs uppercase tracking-[0.18em] text-muted-foreground/80">{t("app.admin.dashboard.engineeringLane.labelObservations")}</div>
                                                    <div className="mt-1 text-lg font-semibold text-foreground">{String(worksetObservation.observationCount ?? 0)}</div>
                                                    <p className="mt-1 text-xs text-muted-foreground">{t("app.admin.dashboard.engineeringLane.labelWarnings")} {String(worksetCorrelation.warningCount ?? 0)}</p>
                                                </div>
                                                <div className="rounded-xl border border-border p-4">
                                                    <div className="text-xs uppercase tracking-[0.18em] text-muted-foreground/80">{t("app.admin.dashboard.engineeringLane.labelOutsideFiles")}</div>
                                                    <div className="mt-1 text-lg font-semibold text-foreground">{String((selectedProof.outsideWriteSetFiles || []).length)}</div>
                                                </div>
                                            </div>
                                            <div>
                                                <h4 className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground/80">{t("app.admin.dashboard.engineeringLane.outsideFilesTitle")}</h4>
                                                <FieldList items={selectedProof.outsideWriteSetFiles || []} empty={t("app.admin.dashboard.engineeringLane.none")} />
                                            </div>
                                            <div>
                                                <h4 className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground/80">{t("app.admin.dashboard.engineeringLane.readSetTitle")}</h4>
                                                <FieldList items={selectedProof.readSet} empty={t("app.admin.dashboard.engineeringLane.none")} />
                                            </div>
                                            <div>
                                                <h4 className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground/80">{t("app.admin.dashboard.engineeringLane.writeSetTitle")}</h4>
                                                <FieldList items={selectedProof.writeSet} empty={t("app.admin.dashboard.engineeringLane.none")} />
                                            </div>
                                </div> :

                    <div className="rounded-xl border border-dashed border-border p-6 text-sm text-muted-foreground">{t("app.admin.dashboard.engineeringLane.noProof")}</div>
                    }
                        </ConfigCard>

                        <ConfigCard title="app.admin.dashboard.engineeringLane.workflowMemoryTitle" description="app.admin.dashboard.engineeringLane.workflowMemoryDescription" bodyScroll="auto" bodyHeight={360}>
                            <div className="space-y-3">
                                <div className="flex items-center justify-between gap-2">
                                    <span className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground/80">
                                        {t("app.admin.dashboard.engineeringLane.workflowMemoryReadOnly")}
                                    </span>
                                    <a className="text-xs font-medium text-foreground underline-offset-4 hover:underline" href="/admin/memory?tab=workflows">
                                        {t("app.admin.dashboard.engineeringLane.openMemoryWorkflows")}
                                    </a>
                                </div>
                                {engineeringWorkflowCandidates.length ?
                      <div className="space-y-2">
                                        {engineeringWorkflowCandidates.map((item) => {
                          return (
                            <div key={item.id} className="rounded-xl border border-border p-3">
                                                    <div className="flex flex-wrap items-start justify-between gap-2">
                                                        <div className="min-w-0">
                                                            <div className="truncate text-sm font-medium text-foreground">{item.task_family || item.taskFamily || item.id}</div>
                                                        </div>
                                                        <StatusPill value={item.lastVerificationStatus || item.status} />
                                                    </div>
                                                </div>);

                        })}
                                    </div> :

                      <div className="rounded-xl border border-dashed border-border p-6 text-sm text-muted-foreground">
                                        {t("app.admin.dashboard.engineeringLane.noEngineeringWorkflows")}
                                    </div>
                      }
                            </div>
                        </ConfigCard>
                    </div>
                </div>
            </div>
            </AdvancedPanel>
        </AdminPageShell>);

}
