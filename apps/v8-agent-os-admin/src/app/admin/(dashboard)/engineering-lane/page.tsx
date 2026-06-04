"use client";

import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Code2, Loader2, Play, RefreshCw, Save } from "lucide-react";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { SourceMetaRow } from "@/components/admin-shell/SourceMetaRow";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { SettingToggleCard } from "@/components/admin-shell/SettingToggleCard";
import { Textarea } from "@/components/ui/textarea";
import { useT } from "@/components/providers/LocaleProvider";
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
  codingPlannerContractEnabled: boolean;
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
  codingPlannerContractEnabled: true,
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

const DIAGNOSTICS_PROVIDER_LABEL_KEYS: Record<keyof DiagnosticsProviders, string> = {
  git: "app.admin.dashboard.engineeringLane.diagnosticsProvider.git",
  command: "app.admin.dashboard.engineeringLane.diagnosticsProvider.command",
  lspBestEffort: "app.admin.dashboard.engineeringLane.diagnosticsProvider.lspBestEffort"
};

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

function JsonDebug({ value }: {value: unknown;}) {
  const t = useT();
  return (
    <details className="rounded-xl border border-slate-200 bg-slate-50 p-3">
            <summary className="cursor-pointer text-xs font-medium uppercase tracking-[0.18em] text-slate-500">{t("app.admin.dashboard.engineeringLane.rawDiagnostics")}</summary>
            <pre className="mt-3 max-h-[360px] overflow-auto rounded-xl bg-slate-950 p-4 text-xs leading-5 text-slate-100">
                {JSON.stringify(value ?? {}, null, 2)}
            </pre>
        </details>);

}

function StatusPill({ value }: {value?: string;}) {
  const normalized = String(value || "planned");
  const palette =
  normalized === "verified" ?
  "bg-emerald-100 text-emerald-700" :
  normalized === "failed_verification" ?
  "bg-rose-100 text-rose-700" :
  normalized === "unverified" ?
  "bg-amber-100 text-amber-700" :
  "bg-slate-100 text-slate-600";
  return <span className={`rounded-full px-3 py-1 text-xs font-medium ${palette}`}>{normalized}</span>;
}

function MatrixStatusPill({ value }: {value?: string;}) {
  const normalized = String(value || "warning");
  const palette =
  normalized === "pass" ?
  "bg-emerald-100 text-emerald-700" :
  normalized === "fail" ?
  "bg-rose-100 text-rose-700" :
  "bg-amber-100 text-amber-700";
  return <span className={`rounded-full px-3 py-1 text-xs font-medium ${palette}`}>{normalized}</span>;
}

function FieldList({ items, empty }: {items?: string[];empty: string;}) {
  if (!items?.length) {
    return <p className="text-sm text-slate-500">{empty}</p>;
  }
  return (
    <div className="flex flex-wrap gap-2">
            {items.slice(0, 24).map((item) =>
      <span key={item} className="rounded-full bg-slate-100 px-3 py-1 text-xs text-slate-700">
                    {item}
                </span>
      )}
        </div>);

}

function SummaryCard({ label, value, hint, tone = "slate" }: {label: string;value: string;hint?: string;tone?: "slate" | "emerald" | "amber" | "rose";}) {
  const toneClass =
  tone === "emerald" ?
  "border-emerald-200 bg-emerald-50/70 text-emerald-900" :
  tone === "amber" ?
  "border-amber-200 bg-amber-50/70 text-amber-900" :
  tone === "rose" ?
  "border-rose-200 bg-rose-50/70 text-rose-900" :
  "border-slate-200 bg-white text-slate-900";
  return (
    <div className={`rounded-2xl border p-4 ${toneClass}`}>
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">{label}</div>
            <div className="mt-2 text-2xl font-semibold">{value}</div>
            {hint ? <p className="mt-2 text-sm text-slate-600">{hint}</p> : null}
        </div>);

}

function AdvancedPanel({ title, description, children, defaultOpen = false }: {title: string;description?: string;children: ReactNode;defaultOpen?: boolean;}) {
  const t = useT();
  return (
    <details open={defaultOpen} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <summary className="cursor-pointer list-none">
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                        <h3 className="text-base font-semibold text-slate-950">{title}</h3>
                        {description ? <p className="mt-1 text-sm text-slate-500">{description}</p> : null}
                    </div>
                    <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-600">{t("app.admin.dashboard.engineeringLane.diagnosticBadge")}</span>
                </div>
            </summary>
            <div className="mt-4 border-t border-slate-100 pt-4">{children}</div>
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

  const load = async () => {
    setLoading(true);
    try {
      const next = await fetchConfigDomain<EngineeringLaneConfig>("engineering-lane");
      setEnvelope({ ...next, data: asConfig(next.data) });
    } finally {
      setLoading(false);
    }
  };

  const loadProof = async () => {
    setProofLoading(true);
    try {
      const params = new URLSearchParams();
      params.set("limit", "30");
      if (proofStatusFilter !== "all") params.set("status", proofStatusFilter);
      if (proofSessionFilter.trim()) params.set("sessionId", proofSessionFilter.trim());
      if (proofRunFilter.trim()) params.set("runId", proofRunFilter.trim());
      const response = await fetch(`/api/engineering-lane/proof-ledger?${params.toString()}`, { cache: "no-store" });
      const data = await response.json().catch(() => ({}));
      const items = Array.isArray(data.items) ? data.items : [];
      setProofEntries(items);
      setSelectedProofId((current) => items.some((item: ProofEntry) => item.id === current) ? current : items[0]?.id || "");
    } finally {
      setProofLoading(false);
    }
  };

  const loadWorksetObservations = async () => {
    const params = new URLSearchParams();
    params.set("limit", "40");
    if (proofSessionFilter.trim()) params.set("sessionId", proofSessionFilter.trim());
    if (proofRunFilter.trim()) params.set("runId", proofRunFilter.trim());
    if (proofTaskBriefFilter.trim()) params.set("taskBriefId", proofTaskBriefFilter.trim());
    if (decisionSourceFilter !== "all") params.set("decisionSource", decisionSourceFilter);
    const response = await fetch(`/api/engineering-lane/workset-observations?${params.toString()}`, { cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    setWorksetObservations(Array.isArray(data.items) ? data.items : []);
  };

  const loadEngineeringWorkflowCandidates = async () => {
    const params = new URLSearchParams({ class: "engineering", limit: "8" });
    const response = await fetch(`/api/memory/workflows?${params.toString()}`, { cache: "no-store" });
    const data = await response.json().catch(() => ({}));
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
      await Promise.all([loadProof(), loadWorksetObservations()]);
    } finally {
      setRefreshingProof(false);
    }
  };

  const triggerDecision = (dryRunResult?.triggerDecision || {}) as Record<string, unknown>;
  const contextPack = (dryRunResult?.contextPack || {}) as Record<string, unknown>;
  const evidenceGraph = (dryRunResult?.evidenceGraphDigest || contextPack.evidenceGraphDigest || {}) as Record<string, unknown>;
  const codingPlanner = (dryRunResult?.codingPlannerContractPreview || contextPack.codingPlannerContractPreview || {}) as Record<string, unknown>;
  const dryRunSoftGate = (dryRunResult?.worksetSoftGateDecision || contextPack.worksetSoftGateDecision || {}) as Record<string, unknown>;
  const brokerDispatch = (dryRunResult?.brokerDispatchSimulation || contextPack.brokerDispatchSimulation || {}) as Record<string, unknown>;
  const dryRunMatrix = (dryRunResult?.dryRunMatrix || contextPack.dryRunMatrix || {}) as Record<string, unknown>;
  const crossLinkMatrix = (dryRunResult?.crossLinkDryRunMatrix || {}) as CrossLinkMatrix;
  const crossLinkScenarios = Array.isArray(crossLinkMatrix.scenarios) ? crossLinkMatrix.scenarios : [];
  const crossLinkGroups = crossLinkScenarios.reduce<Record<string, CrossLinkMatrixScenario[]>>((acc, item) => {
    const group = String(item.group || "other");
    acc[group] = acc[group] || [];
    acc[group].push(item);
    return acc;
  }, {});
  const repoBrief = (contextPack.repoBrief || {}) as Record<string, unknown>;
  const gitSummary = (contextPack.gitSummary || {}) as Record<string, unknown>;
  const memorySuppression = (contextPack.memorySuppression || {}) as Record<string, unknown>;
  const rankedPaths = Array.isArray((contextPack as {workflowRankedPaths?: unknown[];}).workflowRankedPaths) ?
  (contextPack as {workflowRankedPaths?: unknown[];}).workflowRankedPaths || [] :
  [];
  const diagnostics = selectedProof?.diagnostics?.items || [];
  const worksetRisk = selectedProof?.diagnostics?.worksetRisk || {};
  const worksetDispatchDecision = selectedProof?.diagnostics?.worksetDispatchDecision || {};
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

  return (
    <AdminPageShell>
            <AdminPageHeader
        title="app.admin.dashboard.engineeringLane.title"
        description="app.admin.dashboard.engineeringLane.description"
        badges={["app.admin.dashboard.engineeringLane.hybridOverlay"]}
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
            value={config.enabled ? "Enabled" : "Off"}
            hint={`${t("app.admin.dashboard.engineeringLane.triggerMode")}: ${config.triggerMode}`}
            tone={healthTone} />

                    <SummaryCard
            label={t("app.admin.dashboard.engineeringLane.recentProofStatus")}
            value={selectedProof?.verificationStatus || "none"}
            hint={`${t("app.admin.dashboard.engineeringLane.residualRisks")}: ${String(selectedProof?.residualRisks?.length || 0)}`}
            tone={selectedProof?.verificationStatus === "verified" ? "emerald" : selectedProof?.verificationStatus === "failed_verification" ? "rose" : "amber"} />

                    <SummaryCard
            label={t("app.admin.dashboard.engineeringLane.recentWorksetRisk")}
            value={latestObservationRisk !== "not_evaluated" ? latestObservationRisk : selectedRisk}
            hint={`outside: ${String((selectedProof?.outsideWriteSetFiles || latestObservation?.outsideWriteSetFiles || []).length)}`}
            tone={selectedRisk === "outside_write_set" || latestObservationRisk === "outside_write_set" ? "amber" : "slate"} />

                    <SummaryCard
            label={t("app.admin.dashboard.engineeringLane.matrixSummaryTitle")}
            value={matrixTotal ? `${matrixPass}/${matrixTotal}` : "not run"}
            hint={matrixTotal ? `warn ${matrixWarning} · fail ${matrixFail}` : t("app.admin.dashboard.engineeringLane.matrixDiagnosticNote")}
            tone={matrixTone} />

                </div>

                <div className="grid gap-6 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.4fr)]">
                    <ConfigCard
            title="app.admin.dashboard.engineeringLane.basicConfigTitle"
            description="app.admin.dashboard.engineeringLane.basicConfigDescription"
            bodyHeight="auto"
            footer={envelope ? <SourceMetaRow source={envelope.source} savePath={envelope.savePath} reloadRequired={Boolean(envelope.reloadRequired)} /> : null}>

                        {loading ?
            <div className="flex items-center gap-2 text-sm text-slate-500">
                                <Loader2 className="h-4 w-4 animate-spin" />
                                {t("app.admin.dashboard.engineeringLane.loading")}
                            </div> :

            <div className="space-y-4">
                                <SettingToggleCard
                                    title={t("app.admin.dashboard.engineeringLane.enabled")}
                                    description={t("app.admin.dashboard.engineeringLane.enabledHint")}
                                    checked={config.enabled}
                                    onCheckedChange={(enabled) => patchConfig({ enabled })}
                                    className="border-slate-200 p-4 rounded-2xl"
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
                                        className="border-slate-200 p-4 rounded-2xl"
                                    />
                                </div>
                                <details className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                                    <summary className="cursor-pointer text-sm font-semibold text-slate-800">
                                        {t("app.admin.dashboard.engineeringLane.advancedConfigTitle")}
                                    </summary>
                                    <p className="mt-2 text-xs text-slate-500">{t("app.admin.dashboard.engineeringLane.advancedConfigDescription")}</p>
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
                  ["codingPlannerContractEnabled", "codingPlannerContractEnabled"],
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
                      className="border-slate-200 bg-white p-3 rounded-xl"
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
                                        <SummaryCard label={t("app.admin.dashboard.engineeringLane.summaryTrigger")} value={String(triggerDecision.active ?? false)} hint={String(triggerDecision.reason || "")} tone={triggerDecision.active ? "emerald" : "slate"} />
                                        <SummaryCard label={t("app.admin.dashboard.engineeringLane.summaryScope")} value={repoBrief.repoDetected ? "repo" : "no repo"} hint={String(repoBrief.workspaceRoot || repoBrief.repoRoot || "-")} />
                                        <SummaryCard
                    label={t("app.admin.dashboard.engineeringLane.summaryMemory")}
                    value={memorySuppression.suppressDailyMemory || memorySuppression.suppressMemoryMap ? t("app.admin.dashboard.engineeringLane.memorySuppressed") : t("app.admin.dashboard.engineeringLane.memoryNormal")}
                    hint={`workflow=${String(memorySuppression.workflowHintsRetained ?? true)}`}
                    tone="amber" />

                                        <SummaryCard label={t("app.admin.dashboard.engineeringLane.summaryDispatchRisk")} value={dryRunBrokerRisk} hint={String(dryRunSoftGate.suggestedAction || brokerDispatch.recommendedAction || "-")} tone={dryRunBrokerRisk === "outside_write_set" || dryRunBrokerRisk === "missing_write_set" ? "amber" : "slate"} />
                                        <SummaryCard label={t("app.admin.dashboard.engineeringLane.summaryProofDraft")} value={String(dryRunProofDraft.verificationStatus || "planned")} hint={String(dryRunProofDraft.reason || "")} />
                                        <SummaryCard label={t("app.admin.dashboard.engineeringLane.summaryLearning")} value={String(learningEligibility.status || learningEligibility.eligible || "dry-run only")} hint={String(learningEligibility.reason || t("app.admin.dashboard.engineeringLane.matrixDiagnosticNote"))} />
                                    </div> :

                <div className="rounded-2xl border border-dashed border-slate-200 p-6 text-sm text-slate-500">
                                        <Code2 className="mb-3 h-5 w-5" />
                                        {t("app.admin.dashboard.engineeringLane.noDryRun")}
                                    </div>
                }
                                {dryRunResult && (matrixTotal > 0 || topMatrixIssues.length > 0) ?
                <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                                        <div className="flex flex-wrap items-start justify-between gap-3">
                                            <div>
                                                <h3 className="text-sm font-semibold text-slate-900">{t("app.admin.dashboard.engineeringLane.crossLinkMatrixTitle")}</h3>
                                                <p className="mt-1 text-xs text-slate-500">{t("app.admin.dashboard.engineeringLane.matrixDiagnosticNote")}</p>
                                            </div>
                                            <MatrixStatusPill value={matrixFail > 0 ? "fail" : matrixWarning > 0 ? "warning" : "pass"} />
                                        </div>
                                        <div className="mt-3 grid gap-2 sm:grid-cols-4">
                                            <span className="rounded-xl bg-white px-3 py-2 text-sm text-slate-700">total {matrixTotal}</span>
                                            <span className="rounded-xl bg-white px-3 py-2 text-sm text-emerald-700">pass {matrixPass}</span>
                                            <span className="rounded-xl bg-white px-3 py-2 text-sm text-amber-700">warn {matrixWarning}</span>
                                            <span className="rounded-xl bg-white px-3 py-2 text-sm text-rose-700">fail {matrixFail}</span>
                                        </div>
                                        {topMatrixIssues.length ?
                  <div className="mt-3 space-y-2">
                                                {topMatrixIssues.map((issue) =>
                    <div key={issue} className="rounded-xl border border-amber-100 bg-white px-3 py-2 text-xs text-amber-800">
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
                                <SummaryCard label={t("app.admin.dashboard.engineeringLane.summaryVerification")} value={selectedProof?.verificationStatus || "none"} hint={selectedProof?.runId || selectedProof?.createdAt || "-"} />
                                <SummaryCard label={t("app.admin.dashboard.engineeringLane.summaryWorkset")} value={selectedRisk} hint={`changed ${String(selectedProof?.changedFiles?.length || 0)}`} tone={selectedRisk === "outside_write_set" ? "amber" : "slate"} />
                                <SummaryCard label={t("app.admin.dashboard.engineeringLane.summaryWorkflowMemory")} value={String(engineeringWorkflowCandidates.length)} hint={t("app.admin.dashboard.engineeringLane.workflowMemoryReadOnly")} />
                            </div>
                        </ConfigCard>
                    </div>
                </div>
            </div>

            <AdvancedPanel
        title={t("app.admin.dashboard.engineeringLane.advancedDiagnosticsTitle")}
        description={t("app.admin.dashboard.engineeringLane.advancedDiagnosticsDescription")}>

            <div className="grid gap-6 xl:grid-cols-[360px_1fr]">
                <ConfigCard
            title="app.admin.dashboard.engineeringLane.diagnosticScopeTitle"
            description="app.admin.dashboard.engineeringLane.diagnosticScopeDescription"
            bodyHeight="auto"
            footer={envelope ? <SourceMetaRow source={envelope.source} savePath={envelope.savePath} reloadRequired={Boolean(envelope.reloadRequired)} /> : null}>

                    {loading ?
            <div className="flex items-center gap-2 text-sm text-slate-500">
                            <Loader2 className="h-4 w-4 animate-spin" />
                            {t("app.admin.dashboard.engineeringLane.loading")}
                        </div> :

            <div className="space-y-4">
                            <div className="grid gap-3">
                                <SummaryCard label={t("app.admin.dashboard.engineeringLane.contextBudget")} value={String(config.contextPackBudget)} hint={t("app.admin.dashboard.engineeringLane.diagnosticScope.readOnly")} />
                                <SummaryCard label={t("app.admin.dashboard.engineeringLane.evidenceGraphBudget")} value={String(config.evidenceGraphBudget)} hint={t("app.admin.dashboard.engineeringLane.diagnosticScope.readOnly")} />
                                <SummaryCard
                  label={t("app.admin.dashboard.engineeringLane.worksetGovernanceMode")}
                  value={t(WORKSET_GOVERNANCE_OPTIONS.find((option) => option.value === config.worksetGovernanceMode)?.labelKey || "app.admin.dashboard.engineeringLane.worksetGovernance.observeAutoBlock")}
                  hint={t("app.admin.dashboard.engineeringLane.diagnosticScope.configLivesAbove")} />

                            </div>
                            <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs leading-5 text-slate-600">
                                <div className="font-semibold text-slate-900">{t("app.admin.dashboard.engineeringLane.diagnosticsProviders")}</div>
                                <div className="mt-2 grid gap-2">
                                    {(["git", "command", "lspBestEffort"] as const).map((key) =>
                  <div key={key} className="flex items-center justify-between gap-3">
                                            <span>{t(DIAGNOSTICS_PROVIDER_LABEL_KEYS[key])}</span>
                                            <span className={config.diagnosticsProviders[key] ? "text-emerald-700" : "text-slate-400"}>
                                                {config.diagnosticsProviders[key] ? t("app.admin.dashboard.engineeringLane.enabledState") : t("app.admin.dashboard.engineeringLane.disabledState")}
                                            </span>
                                        </div>
                  )}
                                </div>
                            </div>
                        </div>
            }
                </ConfigCard>

                <div className="space-y-6">
                    <ConfigCard title="app.admin.dashboard.engineeringLane.contextPackTitle" description="app.admin.dashboard.engineeringLane.contextPackDescription" bodyScroll="auto" bodyHeight={520}>
                        <div className="space-y-4">
                            <div className="grid gap-3 md:grid-cols-[1fr_160px]">
                                <Textarea className="min-h-[112px]" value={dryRunText} onChange={(event) => setDryRunText(event.target.value)} />
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
                <div className="space-y-4">
                                    <div className="grid gap-3 md:grid-cols-4">
                                        <div className="rounded-xl border border-slate-200 p-3">
                                            <div className="text-xs uppercase tracking-[0.18em] text-slate-400">trigger</div>
                                            <div className="mt-1 text-lg font-semibold text-slate-900">{String(triggerDecision.active ?? false)}</div>
                                            <p className="mt-1 text-xs text-slate-500">{String(triggerDecision.reason || "")}</p>
                                        </div>
                                        <div className="rounded-xl border border-slate-200 p-3">
                                            <div className="text-xs uppercase tracking-[0.18em] text-slate-400">repo</div>
                                            <div className="mt-1 text-lg font-semibold text-slate-900">{repoBrief.repoDetected ? "yes" : "no"}</div>
                                            <p className="mt-1 truncate text-xs text-slate-500">{String(repoBrief.repoRoot || repoBrief.workspaceRoot || "")}</p>
                                        </div>
                                        <div className="rounded-xl border border-slate-200 p-3">
                                            <div className="text-xs uppercase tracking-[0.18em] text-slate-400">tokens</div>
                                            <div className="mt-1 text-lg font-semibold text-slate-900">{String(dryRunResult.contextPackEstimatedTokens || 0)}</div>
                                            <p className="mt-1 text-xs text-slate-500">budget {String(dryRunResult.contextPackBudget || "-")}</p>
                                        </div>
                                        <div className="rounded-xl border border-slate-200 p-3">
                                            <div className="text-xs uppercase tracking-[0.18em] text-slate-400">workflow paths</div>
                                            <div className="mt-1 text-lg font-semibold text-slate-900">{rankedPaths.length}</div>
                                            <p className="mt-1 text-xs text-slate-500">{t("app.admin.dashboard.engineeringLane.workflowHintKept")}</p>
                                        </div>
                                    </div>
                                    <div className="grid gap-4 lg:grid-cols-2">
                                        <div className="rounded-xl border border-slate-200 p-4">
                                            <h3 className="text-sm font-semibold text-slate-900">{t("app.admin.dashboard.engineeringLane.evidenceGraphTitle")}</h3>
                                            <div className="mt-3 grid gap-2 text-sm text-slate-600">
                                                <span>repo: {String(evidenceGraph.repoDetected ?? false)}</span>
                                                <span>branch: {String(evidenceGraph.branch || "-")}</span>
                                                <span>files: {String((evidenceGraph.fileInventoryDigest as Record<string, unknown> | undefined)?.totalFiles || 0)}</span>
                                                <span>changed: {String((evidenceGraph.dirtyState as Record<string, unknown> | undefined)?.changedFileCount || 0)}</span>
                                            </div>
                                            <FieldList
                        items={(Array.isArray(evidenceGraph.criticalFileCandidates) ? evidenceGraph.criticalFileCandidates : []).
                        map((item) => item && typeof item === "object" ? String((item as Record<string, unknown>).path || "") : "").
                        filter(Boolean).
                        slice(0, 10)}
                        empty={t("app.admin.dashboard.engineeringLane.none")} />

                                        </div>
                                        <div className="rounded-xl border border-slate-200 p-4">
                                            <h3 className="text-sm font-semibold text-slate-900">{t("app.admin.dashboard.engineeringLane.codingPlannerTitle")}</h3>
                                            <div className="mt-3 space-y-3 text-sm text-slate-600">
                                                <div>
                                                    <div className="mb-1 text-xs uppercase tracking-[0.18em] text-slate-400">writeSet</div>
                                                    <FieldList items={(Array.isArray(codingPlanner.writeSet) ? codingPlanner.writeSet : []).map(String)} empty={t("app.admin.dashboard.engineeringLane.none")} />
                                                </div>
                                                <div>
                                                    <div className="mb-1 text-xs uppercase tracking-[0.18em] text-slate-400">verification</div>
                                                    <FieldList
                            items={(Array.isArray(codingPlanner.verificationMatrix) ? codingPlanner.verificationMatrix : []).
                            map((item) => item && typeof item === "object" ? String((item as Record<string, unknown>).command || (item as Record<string, unknown>).kind || "") : String(item)).
                            filter(Boolean)}
                            empty={t("app.admin.dashboard.engineeringLane.none")} />

                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                    <div className="grid gap-4 lg:grid-cols-2">
                                        <div className="rounded-xl border border-slate-200 p-4">
                                            <h3 className="text-sm font-semibold text-slate-900">{t("app.admin.dashboard.engineeringLane.gitEvidence")}</h3>
                                            <pre className="mt-3 max-h-[180px] overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
                                                {String(gitSummary.statusShort || gitSummary.diffStat || gitSummary.stagedDiffStat || t("app.admin.dashboard.engineeringLane.noGitEvidence"))}
                                            </pre>
                                        </div>
                                        <div className="rounded-xl border border-slate-200 p-4">
                                            <h3 className="text-sm font-semibold text-slate-900">{t("app.admin.dashboard.engineeringLane.memorySuppression")}</h3>
                                            <div className="mt-3 grid gap-2 text-sm text-slate-600">
                                                <span>daily: {String(memorySuppression.suppressDailyMemory ?? false)}</span>
                                                <span>map: {String(memorySuppression.suppressMemoryMap ?? false)}</span>
                                                <span>workflow: {String(memorySuppression.workflowHintsRetained ?? true)}</span>
                                            </div>
                                        </div>
                                        <div className="rounded-xl border border-slate-200 p-4">
                                            <h3 className="text-sm font-semibold text-slate-900">{t("app.admin.dashboard.engineeringLane.softGateTitle")}</h3>
                                            <div className="mt-3 grid gap-2 text-sm text-slate-600">
                                                <span>risk: {normalizeWorksetRisk(dryRunSoftGate.risk || "not_evaluated")}</span>
                                                <span>warning: {String(dryRunSoftGate.warning ?? false)}</span>
                                                <span>{String(dryRunSoftGate.suggestedAction || "")}</span>
                                            </div>
                                            <FieldList items={(Array.isArray(dryRunSoftGate.outsideWriteSet) ? dryRunSoftGate.outsideWriteSet : []).map(String)} empty={t("app.admin.dashboard.engineeringLane.none")} />
                                        </div>
                                        <div className="rounded-xl border border-slate-200 p-4">
                                            <h3 className="text-sm font-semibold text-slate-900">{t("app.admin.dashboard.engineeringLane.brokerDispatchTitle")}</h3>
                                            <div className="mt-3 grid gap-2 text-sm text-slate-600">
                                                <span>enabled: {String(brokerDispatch.enabled ?? false)}</span>
                                                <span>auto blocked: {String(brokerDispatch.autoDispatchBlocked ?? false)}</span>
                                                <span>action: {String(brokerDispatch.recommendedAction || "-")}</span>
                                            </div>
                                            <FieldList
                        items={(Array.isArray(brokerDispatch.autoDecisions) ? brokerDispatch.autoDecisions : []).
                        map((item) => item && typeof item === "object" ? `${String((item as Record<string, unknown>).taskBriefId || "-")}: ${String((item as Record<string, unknown>).risk || "-")}` : "").
                        filter(Boolean)}
                        empty={t("app.admin.dashboard.engineeringLane.none")} />

                                        </div>
                                        <div className="rounded-xl border border-slate-200 p-4 lg:col-span-2">
                                            <h3 className="text-sm font-semibold text-slate-900">{t("app.admin.dashboard.engineeringLane.dryRunMatrixTitle")}</h3>
                                            <div className="mt-3 grid gap-2 md:grid-cols-3">
                                                <span className="rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-600">enabled: {String(dryRunMatrix.enabled ?? false)}</span>
                                                <span className="rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-600">blocked: {String(dryRunMatrix.blockedScenarioCount ?? 0)}</span>
                                                <span className="rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-600">warnings: {String(dryRunMatrix.warningScenarioCount ?? 0)}</span>
                                            </div>
                                            <div className="mt-3 grid gap-2">
                                                {(Array.isArray(dryRunMatrix.scenarios) ? dryRunMatrix.scenarios : []).slice(0, 8).map((scenario) => {
                          const item = (scenario || {}) as Record<string, unknown>;
                          return (
                            <div key={String(item.id || item.label)} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-100 px-3 py-2 text-sm">
                                                            <span className="font-medium text-slate-700">{String(item.label || item.id || "-")}</span>
                                                            <span className="text-xs text-slate-500">
                                                                autoBlocked={String(item.autoBlocked ?? false)} · manualWarning={String(item.manualWarning ?? false)} · verification={String(item.simulatedVerificationStatus || "planned")} · {String(item.recommendedAction || "-")}
                                                            </span>
                                                        </div>);

                        })}
                                            </div>
                                        </div>
                                        <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-4 lg:col-span-2">
                                            <div className="flex flex-wrap items-start justify-between gap-3">
                                                <div>
                                                    <h3 className="text-sm font-semibold text-slate-900">{t("app.admin.dashboard.engineeringLane.crossLinkMatrixTitle")}</h3>
                                                    <p className="mt-1 text-xs text-slate-500">{t("app.admin.dashboard.engineeringLane.crossLinkMatrixDescription")}</p>
                                                </div>
                                                <MatrixStatusPill value={Number(crossLinkMatrix.summary?.fail || 0) > 0 ? "fail" : Number(crossLinkMatrix.summary?.warning || 0) > 0 ? "warning" : "pass"} />
                                            </div>
                                            <div className="mt-4 grid gap-2 sm:grid-cols-4">
                                                {[
                        ["total", crossLinkMatrix.summary?.total ?? crossLinkScenarios.length],
                        ["pass", crossLinkMatrix.summary?.pass ?? 0],
                        ["warning", crossLinkMatrix.summary?.warning ?? 0],
                        ["fail", crossLinkMatrix.summary?.fail ?? 0]].
                        map(([label, value]) =>
                        <div key={String(label)} className="rounded-xl border border-white bg-white px-3 py-2">
                                                        <div className="text-xs uppercase tracking-[0.18em] text-slate-400">{String(label)}</div>
                                                        <div className="mt-1 text-lg font-semibold text-slate-900">{String(value)}</div>
                                                    </div>
                        )}
                                            </div>
                                            <div className="mt-4 grid gap-3 lg:grid-cols-2">
                                                {Object.entries(crossLinkGroups).map(([group, items]) =>
                        <div key={group} className="rounded-xl border border-slate-200 bg-white p-3">
                                                        <div className="mb-2 flex items-center justify-between gap-2">
                                                            <span className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">{group}</span>
                                                            <span className="text-xs text-slate-500">{items.length}</span>
                                                        </div>
                                                        <div className="space-y-2">
                                                            {items.map((item) => {
                              const firstIssue = (item.checks || []).find((check) => check.status === "fail" || check.status === "warning");
                              return (
                                <div key={String(item.id || item.label)} className="rounded-lg border border-slate-100 px-3 py-2">
                                                                        <div className="flex items-center justify-between gap-2">
                                                                            <span className="text-sm font-medium text-slate-800">{String(item.label || item.id || "-")}</span>
                                                                            <MatrixStatusPill value={item.status} />
                                                                        </div>
                                                                        <p className="mt-1 text-xs text-slate-500">{String(firstIssue?.message || item.summary || "")}</p>
                                                                        {item.deepLinks?.workflows ?
                                  <a className="mt-2 inline-flex text-xs font-medium text-slate-900 underline-offset-4 hover:underline" href={item.deepLinks.workflows}>
                                                                                {t("app.admin.dashboard.engineeringLane.openMemoryWorkflows")}
                                                                            </a> :
                                  null}
                                                                    </div>);

                            })}
                                                        </div>
                                                    </div>
                        )}
                                            </div>
                                            {crossLinkScenarios.length ? <JsonDebug value={crossLinkMatrix} /> : null}
                                        </div>
                                    </div>
                                    <JsonDebug value={dryRunResult} />
                                </div> :

                <div className="rounded-xl border border-dashed border-slate-200 p-6 text-sm text-slate-500">
                                    <Code2 className="mb-3 h-5 w-5" />
                                    {t("app.admin.dashboard.engineeringLane.noDryRun")}
                                </div>
                }
                        </div>
                    </ConfigCard>

                    <div className="grid gap-6 xl:grid-cols-[360px_1fr]">
                        <ConfigCard title="app.admin.dashboard.engineeringLane.proofTitle" description="app.admin.dashboard.engineeringLane.proofDescription" bodyScroll="auto" bodyHeight={520}>
                            <div className="space-y-4">
                                <div className="grid gap-2">
                                    <Select value={proofStatusFilter} onValueChange={setProofStatusFilter}>
                                        <SelectTrigger><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="all">all</SelectItem>
                                            <SelectItem value="verified">verified</SelectItem>
                                            <SelectItem value="unverified">unverified</SelectItem>
                                            <SelectItem value="failed_verification">failed_verification</SelectItem>
                                            <SelectItem value="planned">planned</SelectItem>
                                            <SelectItem value="observed_no_change">observed_no_change</SelectItem>
                                        </SelectContent>
                                    </Select>
                                    <Select value={worksetRiskFilter} onValueChange={setWorksetRiskFilter}>
                                        <SelectTrigger><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="all">all workset risks</SelectItem>
                                            <SelectItem value="within_write_set">within_write_set</SelectItem>
                                            <SelectItem value="outside_write_set">outside_write_set</SelectItem>
                                            <SelectItem value="missing_write_set">missing_write_set</SelectItem>
                                            <SelectItem value="unknown_write_set">unknown_write_set</SelectItem>
                                            <SelectItem value="read_only_safe">read_only_safe</SelectItem>
                                            <SelectItem value="not_evaluated">not_evaluated</SelectItem>
                                        </SelectContent>
                                    </Select>
                                    <Select value={outsideFilter} onValueChange={setOutsideFilter}>
                                        <SelectTrigger><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="all">all drift states</SelectItem>
                                            <SelectItem value="outside_only">outside write-set only</SelectItem>
                                            <SelectItem value="clean_only">clean only</SelectItem>
                                        </SelectContent>
                                    </Select>
                                    <Input placeholder={t("app.admin.dashboard.engineeringLane.sessionFilter")} value={proofSessionFilter} onChange={(event) => setProofSessionFilter(event.target.value)} />
                                    <Input placeholder={t("app.admin.dashboard.engineeringLane.runFilter")} value={proofRunFilter} onChange={(event) => setProofRunFilter(event.target.value)} />
                                    <Input placeholder="taskBriefId" value={proofTaskBriefFilter} onChange={(event) => setProofTaskBriefFilter(event.target.value)} />
                                    <Select value={decisionSourceFilter} onValueChange={setDecisionSourceFilter}>
                                        <SelectTrigger><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="all">all decision sources</SelectItem>
                                            <SelectItem value="planner_auto">planner_auto</SelectItem>
                                            <SelectItem value="supervisor_manual">supervisor_manual</SelectItem>
                                            <SelectItem value="dry_run">dry_run</SelectItem>
                                        </SelectContent>
                                    </Select>
                                    <Select value={observationStateFilter} onValueChange={setObservationStateFilter}>
                                        <SelectTrigger><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="all">all observation states</SelectItem>
                                            <SelectItem value="blocked_only">blocked only</SelectItem>
                                            <SelectItem value="warning_only">warning only</SelectItem>
                                            <SelectItem value="clean_only">clean only</SelectItem>
                                        </SelectContent>
                                    </Select>
                                    <Button variant="outline" onClick={() => void Promise.all([loadProof(), loadWorksetObservations()])} disabled={proofLoading}>
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
                      className={`rounded-xl border p-4 text-left transition ${selectedProof?.id === entry.id ? "border-slate-900 bg-slate-50" : "border-slate-200 hover:border-slate-300"}`}>

                                                <div className="flex items-start justify-between gap-3">
                                                    <div className="min-w-0">
                                                        <div className="truncate text-sm font-medium text-slate-900">{entry.patchIntent || entry.id}</div>
                                                        <div className="mt-1 truncate text-xs text-slate-500">{entry.runId || entry.createdAt || ""}</div>
                                                    </div>
                                                    <StatusPill value={entry.verificationStatus} />
                                                </div>
                                                {entry.changedFiles?.length ? <p className="mt-2 truncate text-xs text-slate-500">{entry.changedFiles.slice(0, 4).join(", ")}</p> : null}
                                            </button>
                    )}
                                    </div> :

                  <div className="rounded-xl border border-dashed border-slate-200 p-6 text-sm text-slate-500">
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
                                                <h3 className="text-base font-semibold text-slate-900">{selectedProof.patchIntent || selectedProof.id}</h3>
                                                <p className="mt-1 text-xs text-slate-500">{selectedProof.sessionId || "-"} · {selectedProof.runId || "-"}</p>
                                            </div>
                                            <div className="flex items-center gap-2">
                                                <StatusPill value={selectedProof.verificationStatus} />
                                                <Button variant="outline" size="sm" onClick={refreshSelectedProof} disabled={refreshingProof || !selectedProof.sessionId || !selectedProof.runId}>
                                                    {refreshingProof ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                                                    {t("app.admin.dashboard.engineeringLane.refreshProof")}
                                                </Button>
                                            </div>
                                        </div>
                                        <div className="grid gap-4 lg:grid-cols-2">
                                            <div>
                                                <h4 className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">{t("app.admin.dashboard.engineeringLane.changedFiles")}</h4>
                                                <FieldList items={selectedProof.changedFiles} empty={t("app.admin.dashboard.engineeringLane.none")} />
                                            </div>
                                            <div>
                                                <h4 className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">{t("app.admin.dashboard.engineeringLane.residualRisks")}</h4>
                                                {selectedProof.residualRisks?.length ?
                        <div className="space-y-2">
                                                        {selectedProof.residualRisks.map((risk) =>
                          <div key={risk} className="flex gap-2 rounded-lg bg-amber-50 p-2 text-xs text-amber-800">
                                                                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                                                                <span>{risk}</span>
                                                            </div>
                          )}
                                                    </div> :

                        <p className="text-sm text-slate-500">{t("app.admin.dashboard.engineeringLane.none")}</p>
                        }
                                            </div>
                                        </div>
                                        <JsonDebug value={selectedProof} />
                                    </div> :

                  <div className="rounded-xl border border-dashed border-slate-200 p-6 text-sm text-slate-500">{t("app.admin.dashboard.engineeringLane.noProof")}</div>
                  }
                            </ConfigCard>

                            <ConfigCard title="app.admin.dashboard.engineeringLane.worksetObservationTitle" description="app.admin.dashboard.engineeringLane.worksetObservationDescription" bodyScroll="auto" bodyHeight={360}>
                                {visibleWorksetObservations.length ?
                  <div className="space-y-3">
                                        {visibleWorksetObservations.slice(0, 16).map((entry) => {
                      const decision = entry.decision || {};
                      const risk = resolveObservationRisk(entry);
                      return (
                        <div key={entry.id} className="rounded-xl border border-slate-200 p-3">
                                                    <div className="flex flex-wrap items-center justify-between gap-2">
                                                        <div className="min-w-0">
                                                            <div className="truncate text-sm font-medium text-slate-900">{entry.taskBriefId || entry.delegationId || entry.id}</div>
                                                            <div className="mt-1 text-xs text-slate-500">{String(entry.phase || "dispatch")} · {String(entry.decisionSource || "planner_auto")}</div>
                                                        </div>
                                                        <div className="text-right text-xs text-slate-500">
                                                            <div>{t("app.admin.dashboard.engineeringLane.labelRisk")}: {risk}</div>
                                                            <div>{t("app.admin.dashboard.engineeringLane.labelOutside")}: {(entry.outsideWriteSetFiles || []).length}</div>
                                                        </div>
                                                    </div>
                                                    {entry.warningOrBlockReason ? <p className="mt-2 text-xs text-slate-600">{entry.warningOrBlockReason}</p> : null}
                                                    {typeof decision["reason"] === "string" && decision["reason"] && decision["reason"] !== entry.warningOrBlockReason ?
                          <p className="mt-1 text-[11px] text-slate-500">{t("app.admin.dashboard.engineeringLane.labelReason")}: {String(decision["reason"])}</p> :
                          null}
                                                    <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-slate-500">
                                                        <span>{t("app.admin.dashboard.engineeringLane.labelManualOverride")}: {String(Boolean(entry.manualOverride))}</span>
                                                        {entry.correlationStatus ? <span>{t("app.admin.dashboard.engineeringLane.labelCorrelation")}: {String(entry.correlationStatus)}</span> : null}
                                                        {entry.metadata?.scenarioId ? <span>{t("app.admin.dashboard.engineeringLane.labelScenario")}: {String(entry.metadata.scenarioId)}</span> : null}
                                                        {entry.metadata?.proofEntryId ? <span>{t("app.admin.dashboard.engineeringLane.labelProof")}: {String(entry.metadata.proofEntryId)}</span> : null}
                                                    </div>
                                                </div>);

                    })}
                                    </div> :

                  <div className="rounded-xl border border-dashed border-slate-200 p-6 text-sm text-slate-500">
                                        {tg(t, "06796e5a")}
                                    </div>
                  }
                            </ConfigCard>

                            <div className="grid gap-6 xl:grid-cols-2">
                                <ConfigCard title="app.admin.dashboard.engineeringLane.diagnosticsTitle" description="app.admin.dashboard.engineeringLane.diagnosticsDescription" bodyScroll="auto" bodyHeight={360}>
                                    {diagnostics.length ?
                    <div className="space-y-3">
                                            {diagnostics.map((item, index) =>
                      <div key={`${item.source}-${item.kind}-${index}`} className="rounded-xl border border-slate-200 p-3">
                                                    <div className="flex flex-wrap items-center justify-between gap-2">
                                                        <span className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">{item.source} · {item.kind}</span>
                                                        {item.returnCode !== undefined ? <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">rc={String(item.returnCode)}</span> : null}
                                                    </div>
                                                    <p className="mt-2 text-sm text-slate-700">{item.summary}</p>
                                                    {item.command ? <p className="mt-1 truncate text-xs text-slate-500">{item.command}</p> : null}
                                                </div>
                      )}
                                        </div> :

                    <div className="rounded-xl border border-dashed border-slate-200 p-6 text-sm text-slate-500">{t("app.admin.dashboard.engineeringLane.noDiagnostics")}</div>
                    }
                                </ConfigCard>

                                <ConfigCard title="app.admin.dashboard.engineeringLane.worksetTitle" description="app.admin.dashboard.engineeringLane.worksetDescription" bodyScroll="auto" bodyHeight={360}>
                                    {selectedProof ?
                    <div className="space-y-4">
                                            <div className="grid gap-3 md:grid-cols-3">
                                                <div className="rounded-xl border border-slate-200 p-4">
                                                    <div className="text-xs uppercase tracking-[0.18em] text-slate-400">{t("app.admin.dashboard.engineeringLane.labelModifyRisk")}</div>
                                                    <div className="mt-1 text-lg font-semibold text-slate-900">{resolveWorksetRisk(selectedProof)}</div>
                                                    {worksetRisk.note ? <p className="mt-1 text-xs text-slate-500">{String(worksetRisk.note)}</p> : null}
                                                </div>
                                                <div className="rounded-xl border border-slate-200 p-4">
                                                    <div className="text-xs uppercase tracking-[0.18em] text-slate-400">{t("app.admin.dashboard.engineeringLane.labelObservations")}</div>
                                                    <div className="mt-1 text-lg font-semibold text-slate-900">{String(worksetObservation.observationCount ?? 0)}</div>
                                                    <p className="mt-1 text-xs text-slate-500">{t("app.admin.dashboard.engineeringLane.labelWarnings")} {String(worksetCorrelation.warningCount ?? 0)}</p>
                                                </div>
                                                <div className="rounded-xl border border-slate-200 p-4">
                                                    <div className="text-xs uppercase tracking-[0.18em] text-slate-400">{t("app.admin.dashboard.engineeringLane.labelOutsideFiles")}</div>
                                                    <div className="mt-1 text-lg font-semibold text-slate-900">{String((selectedProof.outsideWriteSetFiles || []).length)}</div>
                                                    <p className="mt-1 text-xs text-slate-500">{t("app.admin.dashboard.engineeringLane.labelManualOverride")} {String((selectedProof.manualOverride || {}).present ?? false)}</p>
                                                </div>
                                            </div>
                                            <div className="grid gap-4 lg:grid-cols-2">
                                                <div>
                                                    <h4 className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">{t("app.admin.dashboard.engineeringLane.preflightCheckTitle")}</h4>
                                                    <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
                                                        <div>{t("app.admin.dashboard.engineeringLane.labelSource")}: {String(worksetDispatchDecision.worksetDecisionSource || "—")}</div>
                                                        <div>{t("app.admin.dashboard.engineeringLane.labelRisk")}: {normalizeWorksetRisk(worksetDispatchDecision.risk || "not_evaluated")}</div>
                                                        <div>{t("app.admin.dashboard.engineeringLane.labelBlocked")}: {String(worksetDispatchDecision.blocked ?? false)}</div>
                                                        <div>{t("app.admin.dashboard.engineeringLane.labelWarning")}: {String(worksetDispatchDecision.warning ?? false)}</div>
                                                        {worksetDispatchDecision.rawRisk ? <div>rawRisk: {String(worksetDispatchDecision.rawRisk)}</div> : null}
                                                        {worksetDispatchDecision.reason ? <div className="mt-2">{String(worksetDispatchDecision.reason)}</div> : null}
                                                        {worksetDispatchDecision.repairSuggestion ? <div className="mt-2 text-amber-700">{String(worksetDispatchDecision.repairSuggestion)}</div> : null}
                                                    </div>
                                                </div>
                                                <div>
                                                    <h4 className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">{t("app.admin.dashboard.engineeringLane.proofCorrelationTitle")}</h4>
                                                    <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
                                                        <div>{t("app.admin.dashboard.engineeringLane.labelRisk")}: {normalizeWorksetRisk(worksetCorrelation.risk || "not_evaluated")}</div>
                                                        <div>{t("app.admin.dashboard.engineeringLane.labelWarnings")}: {String(worksetCorrelation.warningCount ?? 0)}</div>
                                                        <div>{t("app.admin.dashboard.engineeringLane.labelBlocked")}: {String(worksetCorrelation.blockedCount ?? 0)}</div>
                                                        <div>{t("app.admin.dashboard.engineeringLane.labelMatched")}: {String(Array.isArray(worksetCorrelation.matchedWriteSetFiles) ? worksetCorrelation.matchedWriteSetFiles.length : 0)}</div>
                                                        {worksetCorrelation.suggestedAction ? <div className="mt-2">{String(worksetCorrelation.suggestedAction)}</div> : null}
                                                    </div>
                                                </div>
                                            </div>
                                            <div>
                                                <h4 className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">{t("app.admin.dashboard.engineeringLane.outsideFilesTitle")}</h4>
                                                <FieldList items={selectedProof.outsideWriteSetFiles || []} empty={t("app.admin.dashboard.engineeringLane.none")} />
                                            </div>
                                            <div>
                                                <h4 className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">{t("app.admin.dashboard.engineeringLane.readSetTitle")}</h4>
                                                <FieldList items={selectedProof.readSet} empty={t("app.admin.dashboard.engineeringLane.none")} />
                                            </div>
                                            <div>
                                                <h4 className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">{t("app.admin.dashboard.engineeringLane.writeSetTitle")}</h4>
                                                <FieldList items={selectedProof.writeSet} empty={t("app.admin.dashboard.engineeringLane.none")} />
                                            </div>
                                    <JsonDebug value={{ worksetRisk, worksetObservation, worksetCorrelation }} />
                                </div> :

                    <div className="rounded-xl border border-dashed border-slate-200 p-6 text-sm text-slate-500">{t("app.admin.dashboard.engineeringLane.noProof")}</div>
                    }
                        </ConfigCard>

                        <ConfigCard title="app.admin.dashboard.engineeringLane.workflowMemoryTitle" description="app.admin.dashboard.engineeringLane.workflowMemoryDescription" bodyScroll="auto" bodyHeight={360}>
                            <div className="space-y-3">
                                <div className="flex items-center justify-between gap-2">
                                    <span className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">
                                        {t("app.admin.dashboard.engineeringLane.workflowMemoryReadOnly")}
                                    </span>
                                    <a className="text-xs font-medium text-slate-900 underline-offset-4 hover:underline" href="/admin/memory?tab=workflows">
                                        {t("app.admin.dashboard.engineeringLane.openMemoryWorkflows")}
                                    </a>
                                </div>
                                {engineeringWorkflowCandidates.length ?
                      <div className="space-y-2">
                                        {engineeringWorkflowCandidates.map((item) => {
                          const eligible = item.status === "active_hint" || item.status === "approved";
                          const blockedReason = !item.proofBacked ?
                          "not proof-backed" :
                          !item.verificationBacked ?
                          "not verification-backed" :
                          item.worksetRisk && ["outside_write_set", "missing_write_set", "unknown_write_set"].includes(item.worksetRisk) ?
                          `workset risk: ${item.worksetRisk}` :
                          eligible ?
                          "eligible" :
                          `status: ${item.status || "candidate"}`;
                          return (
                            <div key={item.id} className="rounded-xl border border-slate-200 p-3">
                                                    <div className="flex flex-wrap items-start justify-between gap-2">
                                                        <div className="min-w-0">
                                                            <div className="truncate text-sm font-medium text-slate-900">{item.task_family || item.taskFamily || item.id}</div>
                                                            <div className="mt-1 font-mono text-[11px] text-slate-500">{item.id}</div>
                                                        </div>
                                                        <StatusPill value={item.lastVerificationStatus || item.status} />
                                                    </div>
                                                    <div className="mt-2 grid gap-1 text-xs text-slate-500 sm:grid-cols-2">
                                                        <span>proofBacked={String(Boolean(item.proofBacked))}</span>
                                                        <span>verificationBacked={String(Boolean(item.verificationBacked))}</span>
                                                        <span>worksetRisk={item.worksetRisk || "n/a"}</span>
                                                        <span>proofRefs={(item.proofEntryIds || []).length}</span>
                                                    </div>
                                                    <p className={`mt-2 text-xs ${eligible ? "text-emerald-700" : "text-amber-700"}`}>
                                                        {t("app.admin.dashboard.engineeringLane.learningEligibility")}: {blockedReason}
                                                    </p>
                                                </div>);

                        })}
                                    </div> :

                      <div className="rounded-xl border border-dashed border-slate-200 p-6 text-sm text-slate-500">
                                        {t("app.admin.dashboard.engineeringLane.noEngineeringWorkflows")}
                                    </div>
                      }
                            </div>
                        </ConfigCard>
                    </div>
                </div>
            </div>
                </div>
            </div>
            </AdvancedPanel>
        </AdminPageShell>);

}
