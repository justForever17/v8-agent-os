"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2, RefreshCw } from "lucide-react";
import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { AdvancedSection } from "@/components/admin-shell/AdvancedSection";
import { AdminHoverInfo } from "@/components/admin-shell/AdminHoverInfo";
import { DomainSummaryStrip } from "@/components/admin-shell/DomainSummaryStrip";
import { StatusNotice } from "@/components/admin-shell/StatusNotice";
import AuditLogsPanel from "@/components/memory/AuditLogsPanel";
import { PendingApprovalsPanel } from "@/components/runtime/PendingApprovalsPanel";
import { RecentRunsPanel } from "@/components/runtime/RecentRunsPanel";
import { useRuntimeOpsData } from "@/components/runtime/use-runtime-ops";
import { formatRuntimeKindLabel } from "@/components/runtime/use-runtime-ops";
import { TechnicalReferenceDetails } from "@/components/common/TechnicalReferenceDetails";
import { useT } from "@/components/providers/LocaleProvider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { tg, ti } from "@/i18n/admin-legacy";
type SummaryPayload = {
  pendingApprovals: number;
  recentRuns: number;
  runningCount: number;
  recoverableCount: number;
  health?: {
    status?: string;
    mcp_tools?: number;
    mcp?: {
      configured?: number;
      connected?: number;
      degraded?: number;
      executionImpacted?: boolean;
      backgroundReconnectOnly?: boolean;
      degradedServers?: Array<{
        name?: string;
        transport?: string;
        status?: string;
        impact?: string;
        lastError?: string | null;
      }>;
      streamableHttpIssues?: Array<{
        name?: string;
        status?: string;
        impact?: string;
        lastError?: string | null;
        executionImpacted?: boolean;
      }>;
    };
    memory?: {
      mode?: string;
      interpreterPath?: string;
      expectedInterpreterPath?: string;
      interpreterDrift?: boolean;
      fts5OnlyDegraded?: boolean;
      chromadb?: {
        available?: boolean;
        version?: string;
        error?: string | null;
      };
      vectorBackend?: {
        ready?: boolean;
      };
      warnings?: string[];
    };
  };
};
type StorageRetentionPayload = {
  config?: {
    budgets?: Record<string, {
      maxBytes?: number;
      retentionDays?: number;
      mode?: string;
    }>;
  };
  maxBytes?: number;
  totalGovernedBytes?: number;
  totalProductBytes?: number | null;
  registeredStorageBytes?: number | null;
  storageClassTotals?: Record<string, number>;
  physicalBytes?: number;
  logicalBytes?: number;
  reclaimableBytes?: number;
  overCapBytes?: number;
  backupState?: string;
  recoverability?: string;
  disk?: {
    freeBytes?: number;
    freeRatio?: number;
    emergencySafeMode?: boolean;
    watermark?: string;
  };
  retentionJournal?: {
    state?: string;
    backupState?: string;
    backupManifestPath?: string;
  };
  components?: Record<string, number>;
  budgetComponents?: Record<string, {
    label?: string;
    usedBytes?: number;
    maxBytes?: number;
    retentionDays?: number;
    mode?: string;
    autoPrune?: boolean;
  }>;
  budgetFindings?: Array<{
    key?: string;
    label?: string;
    severity?: string;
    usedBytes?: number;
    maxBytes?: number;
    usageRatio?: number;
  }>;
  recommendations?: Array<{
    key?: string;
    action?: string;
    message?: string;
  }>;
  recentRetentionEvents?: Array<{
    id?: string;
    status?: string;
    before_bytes?: number;
    after_bytes?: number;
    beforeBytes?: number;
    afterBytes?: number;
    created_at?: string;
    createdAt?: string;
    actions?: unknown[];
  }>;
  storageRegistry?: {
    generatedAt?: string | null;
    stale?: boolean;
    refreshScheduled?: boolean;
    registeredBytes?: number | null;
    classTotals?: Record<string, number>;
    entries?: Array<{
      id?: string;
      label?: string;
      classification?: string;
      autoDelete?: boolean;
      ttlDays?: number | null;
      maxBytes?: number | null;
      backupPolicy?: string;
      restoreStrategy?: string;
      cleanupMode?: string;
      managedBy?: string;
      bytes?: number | null;
      fileCount?: number | null;
      lastAccessAt?: string | null;
      expiredBytes?: number;
      expiredFileCount?: number;
      overCapacityBytes?: number;
      policyState?: string;
      scanState?: string;
    }>;
  };
};
const VALID_TABS = new Set(["overview", "approvals", "runs", "evidence", "advanced"]);
const OPERATION_LOG_SOURCES = ["all", "runtime", "audit", "cron", "hook", "safety", "storage"] as const;
type OperationLogSource = (typeof OPERATION_LOG_SOURCES)[number];
type OperationLogItem = {
  id: string;
  timestamp: string;
  source: string;
  status: string;
  action: string;
  runId?: string;
  sessionId?: string;
  summary: string;
  details?: string;
};
type RunLedgerPayload = {
  runId?: string;
  status?: string;
  runtimeKind?: string;
  nextAction?: string;
  refs?: Record<string, string[]>;
  timeline?: Array<{
    id?: string;
    type?: string;
    source?: string;
    runtimeKind?: string;
    ts?: string;
    summary?: string;
    refs?: Record<string, unknown>;
  }>;
};
type DoctorPayload = {
  id?: string;
  generatedAt?: string;
  summary?: {
    status?: string;
    counts?: Record<string, number>;
  };
  checks?: Array<{
    id?: string;
    status?: string;
    title?: string;
    summary?: string;
  }>;
  repairPlan?: {
    actions?: Array<{
      id?: string;
      title?: string;
      description?: string;
      requiresConfirmation?: boolean;
    }>;
  };
};
type ConfigMigrationPlan = {
  target?: string;
  status?: string;
  reason?: string;
  reversible?: boolean;
  runtimeImpact?: string[];
  changes?: Array<{
    path?: string;
    before?: unknown;
    after?: unknown;
  }>;
};
type RuntimeEpisodeOverviewPayload = {
  ok?: boolean;
  summary?: {
    episodeCount?: number;
    queueCount?: number;
    activeLeaseCount?: number;
    byState?: Record<string, number>;
    byKind?: Record<string, number>;
    byTargetKind?: Record<string, number>;
  };
  episodes?: Array<Record<string, unknown>>;
  queue?: Array<Record<string, unknown>>;
  leases?: Array<Record<string, unknown>>;
  handoffs?: Array<Record<string, unknown>>;
};
type JsonRecord = Record<string, unknown>;

function asRecord(value: unknown): JsonRecord {
  return value && typeof value === "object" ? value as JsonRecord : {};
}

function fieldText(value: unknown, fallback = "") {
  return String(value ?? fallback);
}

function fieldNumber(value: unknown) {
  const numberValue = Number(value || 0);
  return Number.isFinite(numberValue) ? numberValue : 0;
}

function apiErrorMessage(payload: unknown, status: number) {
  const record = asRecord(payload);
  const detail = record.detail;
  const error = record.error;
  const message = typeof detail === "string" ? detail : typeof error === "string" ? error : JSON.stringify(record || {});
  return `HTTP ${status}${message && message !== "{}" ? ` · ${message}` : ""}`;
}

function formatBytes(value?: number) {
  const bytes = Number(value || 0);
  if (bytes <= 0) return "0 MB";
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
function bytesToMb(value?: number) {
  return Math.round(Number(value || 0) / 1024 / 1024);
}
function mbToBytes(value: string) {
  const mb = Number(value || 0);
  if (!Number.isFinite(mb) || mb <= 0) return 0;
  return Math.round(mb * 1024 * 1024);
}
function StorageRetentionPanel() {
  const t = useT();
  const [stats, setStats] = useState<StorageRetentionPayload | null>(null);
  const [lastResult, setLastResult] = useState<JsonRecord | null>(null);
  const [budgetDraft, setBudgetDraft] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const load = async () => {
    setLoading(true);
    try {
      const response = await fetch("/api/storage-retention/stats", {
        cache: "no-store"
      });
      const payload = await response.json().catch(() => null);
      if (response.ok) {
        setStats(payload);
        const budgets = payload?.config?.budgets || payload?.budgetComponents || {};
        const nextDraft: Record<string, string> = {};
        for (const [key, value] of Object.entries(budgets)) {
          const maxBytes = Number(asRecord(value).maxBytes || 0);
          if (maxBytes > 0) nextDraft[key] = String(bytesToMb(maxBytes));
        }
        setBudgetDraft(nextDraft);
      }
    } finally {
      setLoading(false);
    }
  };
  const run = async (kind: "dry-run" | "prune" | "compact") => {
    if (kind === "prune" && !window.confirm(t("app.admin.dashboard.operations.storage.confirmPrune"))) return;
    if (kind === "compact" && !window.confirm(t("app.admin.dashboard.operations.storage.confirmCompact"))) return;
    setLoading(true);
    try {
      const response = await fetch(`/api/storage-retention/${kind}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          reason: `admin_${kind}`
        })
      });
      const payload = await response.json().catch(() => null);
      setLastResult(payload);
      await load();
    } finally {
      setLoading(false);
    }
  };
  const refreshRegistry = async () => {
    setLoading(true);
    try {
      const response = await fetch("/api/storage-retention/registry/refresh", {
        method: "POST",
        cache: "no-store",
      });
      const payload = await response.json().catch(() => null);
      setLastResult(payload);
      await load();
    } finally {
      setLoading(false);
    }
  };
  const saveBudgets = async () => {
    if (!stats?.config) return;
    if (!window.confirm(ti(t, "k595dde7c31"))) return;
    setLoading(true);
    try {
      const budgets: Record<string, unknown> = {
        ...(stats.config.budgets || {})
      };
      for (const [key, value] of Object.entries(budgetDraft)) {
        budgets[key] = {
          ...asRecord(budgets[key]),
          maxBytes: mbToBytes(value)
        };
      }
      const response = await fetch("/api/storage-retention/config", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          ...(stats.config || {}),
          budgets
        })
      });
      const payload = await response.json().catch(() => null);
      setLastResult(payload);
      await load();
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void load();
  }, []);
  const components = stats?.components || {};
  const budgetComponents = stats?.budgetComponents || {};
  const registry = stats?.storageRegistry;
  const registryEntries = registry?.entries || [];
  const actionCount = Array.isArray(lastResult?.actions) ? lastResult.actions.length : 0;
  const statusText = (value?: unknown) => {
    const status = String(value || "");
    return ({
      completed: t("app.admin.dashboard.operations.storage.status.completed"),
      dry_run: t("app.admin.dashboard.operations.storage.status.planned"),
      blocked: t("app.admin.dashboard.operations.storage.status.blocked"),
      failed: t("app.admin.dashboard.operations.storage.status.failed"),
      ready: t("app.admin.dashboard.operations.storage.status.ready"),
      not_started: t("app.admin.dashboard.operations.storage.status.notStarted"),
      not_required: t("app.admin.dashboard.operations.storage.status.notRequired"),
      protected: t("app.admin.dashboard.operations.storage.status.recoverable"),
      plan_only: t("app.admin.dashboard.operations.storage.status.planOnly")
    } as Record<string, string>)[status] || t("app.admin.dashboard.operations.storage.status.unknown");
  };
  const modeText = (value?: unknown) => {
    const mode = String(value || "warn_only");
    return ({
      hard_rolling: t("app.admin.dashboard.operations.storage.mode.automatic"),
      rolling: t("app.admin.dashboard.operations.storage.mode.rolling"),
      manual_prune: t("app.admin.dashboard.operations.storage.mode.manual"),
      warn_only: t("app.admin.dashboard.operations.storage.mode.warningOnly")
    } as Record<string, string>)[mode] || t("app.admin.dashboard.operations.storage.mode.warningOnly");
  };
  const budgetLabel = (key: string) => t(`app.admin.dashboard.operations.storage.budget.${key}`);
  const storageClassLabel = (value?: string) => t(`app.admin.dashboard.operations.storage.registry.class.${value || "derived"}`);
  const storageEntryLabel = (entry: NonNullable<NonNullable<StorageRetentionPayload["storageRegistry"]>["entries"]>[number]) => ({
    state_truth: t("app.admin.dashboard.operations.storage.registry.entry.stateTruth"),
    knowledge_truth: t("app.admin.dashboard.operations.storage.registry.entry.knowledgeTruth"),
    checkpoints: t("app.admin.dashboard.operations.storage.registry.entry.checkpoints"),
    observability: t("app.admin.dashboard.operations.storage.registry.entry.observability"),
    agent_browser_profile: t("app.admin.dashboard.operations.storage.registry.entry.agentBrowserProfile"),
    cache: t("app.admin.dashboard.operations.storage.registry.entry.cache"),
    longmemeval_reports: t("app.admin.dashboard.operations.storage.registry.entry.longMemEval"),
    other_reports: t("app.admin.dashboard.operations.storage.registry.entry.otherReports"),
    temporary_builds: t("app.admin.dashboard.operations.storage.registry.entry.temporaryBuilds"),
    backups: t("app.admin.dashboard.operations.storage.registry.entry.backups"),
    memory_daily: t("app.admin.dashboard.operations.storage.registry.entry.memoryDaily"),
    memory_workflow_exports: t("app.admin.dashboard.operations.storage.registry.entry.workflowExports"),
    memory_vectors: t("app.admin.dashboard.operations.storage.registry.entry.memoryVectors"),
    research_experience: t("app.admin.dashboard.operations.storage.registry.entry.researchExperience"),
    rpa_history: t("app.admin.dashboard.operations.storage.registry.entry.rpaHistory"),
    toolchains: t("app.admin.dashboard.operations.storage.registry.entry.toolchains"),
    plugins: t("app.admin.dashboard.operations.storage.registry.entry.plugins"),
    artifacts: t("app.admin.dashboard.operations.storage.registry.entry.artifacts"),
    configuration: t("app.admin.dashboard.operations.storage.registry.entry.configuration"),
    runtime_control: t("app.admin.dashboard.operations.storage.registry.entry.runtimeControl"),
    runtime_assets: t("app.admin.dashboard.operations.storage.registry.entry.runtimeAssets"),
    unclassified: t("app.admin.dashboard.operations.storage.registry.entry.unclassified")
  } as Record<string, string>)[String(entry.id || "")] || entry.label || t("app.admin.dashboard.operations.storage.registry.entry.unclassified");
  const backupPolicyLabel = (value?: string) => t(`app.admin.dashboard.operations.storage.registry.backup.${value || "subsystem_managed"}`);
  const restoreStrategyLabel = (value?: string) => t(`app.admin.dashboard.operations.storage.registry.restore.${value || "subsystem_reconcile"}`);
  const watermarkLabel = (value?: string) => t(`app.admin.dashboard.operations.storage.registry.watermark.${value || "healthy"}`);
  const storagePolicyLabel = (entry: NonNullable<NonNullable<StorageRetentionPayload["storageRegistry"]>["entries"]>[number]) => {
    if (entry.policyState === "cleanup_available") {
      return t("app.admin.dashboard.operations.storage.registry.policy.cleanup", { size: formatBytes(Math.max(Number(entry.expiredBytes || 0), Number(entry.overCapacityBytes || 0))) });
    }
    if (entry.policyState === "review_required") {
      return t("app.admin.dashboard.operations.storage.registry.policy.review", { size: formatBytes(Math.max(Number(entry.expiredBytes || 0), Number(entry.overCapacityBytes || 0))) });
    }
    return t("app.admin.dashboard.operations.storage.registry.policy.within");
  };
  const humanComponents = [
    { label: t("app.admin.dashboard.operations.storage.executionHistory"), value: Number(components.checkpointDbBytes || 0) },
    { label: t("app.admin.dashboard.operations.storage.diagnostics"), value: Number(components.observabilityDbBytes || 0) + Number(components.stateLogPayloadBytes || 0) + Number(components.pluginRuntimeLogBytes || 0) },
    { label: t("app.admin.dashboard.operations.storage.memoryTruth"), value: Number(components.knowledgeDbBytes || 0) },
    { label: t("app.admin.dashboard.operations.storage.memoryIndex"), value: Number(components.vectorDbBytes || 0) },
    { label: t("app.admin.dashboard.operations.storage.memoryRecords"), value: Number(components.memoryAuxiliaryBytes || 0) },
    { label: t("app.admin.dashboard.operations.storage.userArtifacts"), value: Number(components.artifactFileBytes || 0) + Number(components.screenshotFileBytes || 0) }
  ];
  return <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <div className="text-sm font-semibold text-foreground">{t("app.admin.dashboard.operations.center.advanced.storageRetention.title")}</div>
                    <div className="text-xs leading-5 text-muted-foreground">
                        {ti(t, "ka0d7a179bf")}
                    </div>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
                        {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                        {t("app.admin.dashboard.operations.center.advanced.refresh")}
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => void run("dry-run")} disabled={loading}>{t("app.admin.dashboard.operations.center.advanced.dryRun")}</Button>
                    <Button size="sm" onClick={() => void run("prune")} disabled={loading}>{t("app.admin.dashboard.operations.center.advanced.prune")}</Button>
                    <Button variant="outline" size="sm" onClick={() => void run("compact")} disabled={loading}>{t("app.admin.dashboard.operations.storage.compact")}</Button>
                    <Button variant="outline" size="sm" onClick={() => void saveBudgets()} disabled={loading}>{ti(t, "kf8d9ddff2f")}</Button>
                </div>
            </div>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-6">
                <div className="rounded-xl border border-border bg-muted/50 p-3 text-sm">
                    <div className="text-xs text-muted-foreground">{t("app.admin.dashboard.operations.storage.logical")}</div>
                    <div className="font-semibold text-foreground">{formatBytes(stats?.logicalBytes)}</div>
                </div>
                <div className="rounded-xl border border-border bg-muted/50 p-3 text-sm">
                    <div className="text-xs text-muted-foreground">{t("app.admin.dashboard.operations.storage.physical")}</div>
                    <div className="font-semibold text-foreground">{formatBytes(stats?.physicalBytes)}</div>
                </div>
                <div className="rounded-xl border border-border bg-muted/50 p-3 text-sm">
                    <div className="text-xs text-muted-foreground">{t("app.admin.dashboard.operations.storage.reclaimable")}</div>
                    <div className={Number(stats?.reclaimableBytes || 0) > 0 ? "font-semibold text-amber-700 dark:text-amber-300" : "font-semibold text-emerald-700 dark:text-emerald-300"}>{formatBytes(stats?.reclaimableBytes)}</div>
                </div>
                <div className="rounded-xl border border-border bg-muted/50 p-3 text-sm">
                    <div className="text-xs text-muted-foreground">{t("app.admin.dashboard.operations.storage.backup")}</div>
                    <div className="font-semibold text-foreground">{statusText(stats?.backupState)}</div>
                </div>
                <div className="rounded-xl border border-border bg-muted/50 p-3 text-sm">
                    <div className="text-xs text-muted-foreground">{t("app.admin.dashboard.operations.storage.recoverability")}</div>
                    <div className="font-semibold text-foreground">{statusText(stats?.recoverability)}</div>
                </div>
                <div className="rounded-xl border border-border bg-muted/50 p-3 text-sm">
                    <div className="text-xs text-muted-foreground">{t("app.admin.dashboard.operations.storage.registry.totalProduct")}</div>
                    <div className="font-semibold text-foreground">{stats?.totalProductBytes == null ? t("app.admin.dashboard.operations.storage.registry.scanning") : formatBytes(stats.totalProductBytes)}</div>
                    <div className="mt-1 text-xs text-muted-foreground">{t("app.admin.dashboard.operations.storage.registry.freeSpace", { free: formatBytes(stats?.disk?.freeBytes), state: watermarkLabel(stats?.disk?.watermark) })}</div>
                </div>
            </div>
            {stats?.disk?.emergencySafeMode ? <StatusNotice title={t("app.admin.dashboard.operations.storage.lowSpaceTitle")} description={t("app.admin.dashboard.operations.storage.lowSpaceDescription", { free: formatBytes(stats.disk.freeBytes) })} tone="warning" /> : null}
            <div className="rounded-xl border border-border bg-card p-3">
                <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                    <div>
                        <div className="text-sm font-semibold text-foreground">{t("app.admin.dashboard.operations.storage.registry.title")}</div>
                        <div className="text-xs leading-5 text-muted-foreground">
                            {registry?.generatedAt ? t("app.admin.dashboard.operations.storage.registry.updated", { time: registry.generatedAt }) : t("app.admin.dashboard.operations.storage.registry.pending")}
                        </div>
                    </div>
                    <Button variant="outline" size="sm" onClick={() => void refreshRegistry()} disabled={loading}>
                        {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                        {t("app.admin.dashboard.operations.storage.registry.rescan")}
                    </Button>
                </div>
                <div className="mb-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
                    {Object.entries(registry?.classTotals || {}).map(([classification, bytes]) => <div key={classification} className="rounded-lg bg-muted/50 px-3 py-2 text-xs">
                        <div className="text-muted-foreground">{storageClassLabel(classification)}</div>
                        <div className="font-semibold text-foreground">{formatBytes(bytes)}</div>
                    </div>)}
                </div>
                <div className="grid gap-2 md:grid-cols-2">
                    {registryEntries.slice().sort((left, right) => Number(right.bytes || 0) - Number(left.bytes || 0)).map(entry => <div key={entry.id} className="rounded-xl border border-border p-3 text-xs leading-5">
                        <div className="flex items-center justify-between gap-3">
                            <span className="font-semibold text-foreground">{storageEntryLabel(entry)}</span>
                            <span className="text-muted-foreground">{entry.bytes == null ? t("app.admin.dashboard.operations.storage.registry.scanning") : formatBytes(entry.bytes)}</span>
                        </div>
                        <div className="flex flex-wrap gap-x-3 text-muted-foreground">
                            <span>{storageClassLabel(entry.classification)}</span>
                            <span>{entry.autoDelete ? t("app.admin.dashboard.operations.storage.registry.auto") : t("app.admin.dashboard.operations.storage.registry.review")}</span>
                            <span>{entry.ttlDays ? t("app.admin.dashboard.operations.storage.retentionDays", { count: entry.ttlDays }) : t("app.admin.dashboard.operations.storage.registry.noTtl")}</span>
                            <span>{entry.maxBytes ? t("app.admin.dashboard.operations.storage.registry.capacity", { size: formatBytes(entry.maxBytes) }) : t("app.admin.dashboard.operations.storage.registry.noCapacity")}</span>
                            {entry.fileCount != null ? <span>{t("app.admin.dashboard.operations.storage.registry.fileCount", { count: entry.fileCount })}</span> : null}
                            {entry.lastAccessAt ? <span>{t("app.admin.dashboard.operations.storage.registry.lastAccess", { time: entry.lastAccessAt })}</span> : null}
                        </div>
                        <div className="mt-1 text-muted-foreground">
                            {t("app.admin.dashboard.operations.storage.registry.recovery", { backup: backupPolicyLabel(entry.backupPolicy), restore: restoreStrategyLabel(entry.restoreStrategy) })}
                        </div>
                        <div className={entry.policyState === "review_required" ? "mt-1 text-amber-700 dark:text-amber-300" : "mt-1 text-muted-foreground"}>{storagePolicyLabel(entry)}</div>
                    </div>)}
                </div>
            </div>
            <div className="rounded-xl border border-border bg-card p-3">
                <div className="mb-3 text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">{t("app.admin.dashboard.operations.center.advanced.budgets")}</div>
                <div className="grid gap-3 md:grid-cols-2">
                    {Object.entries(budgetComponents).map(([key, value]) => {
          const used = Number(value.usedBytes || 0);
          const max = Number(value.maxBytes || 0);
          const ratio = max > 0 ? used / max : 0;
          return <div key={key} className="rounded-xl border border-border p-3 text-xs">
                                <div className="flex items-center justify-between gap-3">
                                    <div>
                                        <div className="font-semibold text-foreground">{budgetLabel(key)}</div>
                                        <div className="text-muted-foreground">
                                            {t("app.admin.dashboard.operations.center.advanced.used")} {formatBytes(used)} · {modeText(value.mode)}{value.retentionDays ? ` · ${t("app.admin.dashboard.operations.storage.retentionDays", { count: value.retentionDays })}` : ""}
                                        </div>
                                    </div>
                                    <div className={ratio >= 1 ? "text-amber-700" : "text-muted-foreground"}>{Math.round(ratio * 100)}%</div>
                                </div>
                                <div className="mt-2 flex items-center gap-2">
                                    <Input className="h-8" type="number" min={1} value={budgetDraft[key] || ""} onChange={event => setBudgetDraft(current => ({
                ...current,
                [key]: event.target.value
              }))} />

                                    <span className="shrink-0 text-muted-foreground">MB</span>
                                </div>
                            </div>;
        })}
                </div>
            </div>
            {Array.isArray(stats?.recommendations) && stats.recommendations.length ? <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-800">
                    {stats.recommendations.map(item => <div key={`${item.key}-${item.action}`}>{t(`app.admin.dashboard.operations.storage.recommendation.${item.key || "logs"}`)}</div>)}
                </div> : null}
            <div className="grid gap-2 md:grid-cols-2">
                {humanComponents.map(item => <div key={item.label} className="flex items-center justify-between rounded-xl border border-border px-3 py-2 text-xs">
                        <span className="text-muted-foreground">{item.label}</span>
                        <span className="font-medium text-foreground">{formatBytes(item.value)}</span>
                    </div>)}
            </div>
            <TechnicalReferenceDetails items={Object.entries(components).map(([key, value]) => ({ label: key, value: formatBytes(Number(value || 0)) }))} />
            {lastResult ? <div className="rounded-xl border border-border bg-card p-3 text-xs leading-5 text-muted-foreground">
                    {t("app.admin.dashboard.operations.center.advanced.resultSummary", {
            status: statusText(lastResult.status),
            actionCount,
            before: formatBytes(fieldNumber(lastResult.beforeBytes)),
            after: formatBytes(fieldNumber(lastResult.afterBytes))
          })}
                </div> : null}
        </div>;
}
function SystemDoctorPanel() {
  const t = useT();
  const [payload, setPayload] = useState<DoctorPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch("/api/system/doctor", {
        cache: "no-store",
        credentials: "same-origin"
      });
      const data = await response.json().catch(() => null);
      if (response.ok) {
        setPayload(data);
      } else {
        setPayload(null);
        setError(apiErrorMessage(data, response.status));
      }
    } catch (err) {
      setPayload(null);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void load();
  }, []);
  const checks = payload?.checks || [];
  const actions = payload?.repairPlan?.actions || [];
  const visibleChecks = checks.some(check => check.status !== "ok") ? checks.filter(check => check.status !== "ok").slice(0, 8) : checks.slice(0, 8);
  const statusLabels: Record<string, string> = {
    ok: t("app.admin.dashboard.operations.center.advanced.status.ok"),
    warning: t("app.admin.dashboard.operations.center.advanced.status.warning"),
    error: t("app.admin.dashboard.operations.center.advanced.status.error"),
    info: t("app.admin.dashboard.operations.center.advanced.status.info")
  };
  return <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <div className="text-sm font-semibold text-foreground">{t("app.admin.dashboard.operations.center.advanced.systemDoctor.title")}</div>
                    <div className="text-xs leading-5 text-muted-foreground">{ti(t, "k6e8c71c3b5")}</div>
                    <div className="mt-1 max-w-3xl text-xs leading-5 text-muted-foreground">
                        {t("app.admin.dashboard.operations.center.advanced.systemDoctor.description")}
                    </div>
                </div>
                <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
                    {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                    {t("app.admin.dashboard.operations.center.advanced.refresh")}
                </Button>
            </div>
            <div className="grid gap-3 md:grid-cols-4">
                <div className="rounded-xl border border-border bg-muted/50 p-3 text-sm">
                    <div className="text-xs text-muted-foreground">{t("app.admin.dashboard.operations.center.advanced.status")}</div>
                    <div className={payload?.summary?.status === "ok" ? "font-semibold text-emerald-700" : "font-semibold text-amber-700"}>{payload?.summary?.status ? statusLabels[payload.summary.status] || payload.summary.status : t("app.admin.dashboard.operations.center.advanced.status.unavailable")}</div>
                </div>
                {["ok", "warning", "error"].map(key => <div key={key} className="rounded-xl border border-border bg-muted/50 p-3 text-sm">
                        <div className="text-xs text-muted-foreground">{statusLabels[key] || key}</div>
                        <div className="font-semibold text-foreground">{payload?.summary?.counts?.[key] ?? 0}</div>
                    </div>)}
            </div>
            {payload?.generatedAt ? <div className="rounded-xl border border-border bg-card p-3 text-xs leading-5 text-muted-foreground">
                    {t("app.admin.dashboard.operations.center.advanced.systemDoctor.lastRun", {
          generatedAt: payload.generatedAt,
          checkCount: checks.length
        })}
                </div> : null}
            {error ? <div className="rounded-xl border border-red-200 bg-red-50 p-3 text-xs leading-5 text-red-700">
                    {t("app.admin.dashboard.operations.center.advanced.systemDoctor.loadFailed", {
          error
        })}
                </div> : null}
            {!error && !loading && payload && !checks.length ? <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-800">
                    {t("app.admin.dashboard.operations.center.advanced.systemDoctor.noChecks")}
                </div> : null}
            <div className="grid gap-2 md:grid-cols-2">
                {visibleChecks.map(check => <div key={check.id} className="rounded-xl border border-border p-3 text-xs leading-5">
                        <div className="flex items-center justify-between gap-3">
                            <span className="font-semibold text-foreground">{check.title || check.id}</span>
                            <span className={check.status === "ok" ? "text-emerald-700" : check.status === "error" ? "text-red-700" : "text-amber-700"}>{statusLabels[String(check.status || "info")] || check.status || "info"}</span>
                        </div>
                        <div className="text-muted-foreground">{check.summary}</div>
                    </div>)}
            </div>
            {checks.length > visibleChecks.length ? <div className="text-xs text-muted-foreground">
                    {t("app.admin.dashboard.operations.center.advanced.systemDoctor.omittedChecks", {
          omittedCount: checks.length - visibleChecks.length
        })}
                </div> : null}
            {actions.length ? <div className="rounded-xl border border-border bg-card p-3 text-xs leading-5 text-muted-foreground">
                    <div className="mb-2 font-semibold text-foreground">{t("app.admin.dashboard.operations.center.advanced.repairPlan")}</div>
                    {actions.map(action => <div key={action.id} className="mb-2">
                            <span className="font-medium text-foreground">{action.title}</span>
                            <span className="text-muted-foreground"> · {action.requiresConfirmation ? ti(t, "k9deb52e20d") : ti(t, "k6530601635")}</span>
                            <div>{action.description}</div>
                        </div>)}
                </div> : null}
        </div>;
}
function ConfigMigrationPanel() {
  const t = useT();
  const [plan, setPlan] = useState<ConfigMigrationPlan | null>(null);
  const [ledger, setLedger] = useState<{
    ledgerPath?: string;
    migrations?: JsonRecord[];
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastApplyResult, setLastApplyResult] = useState<JsonRecord | null>(null);
  const [loading, setLoading] = useState(false);
  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [planResponse, ledgerResponse] = await Promise.all([fetch("/api/config/migrations/plan?target=storage_retention_balanced", {
        cache: "no-store",
        credentials: "same-origin"
      }), fetch("/api/config/migrations", {
        cache: "no-store",
        credentials: "same-origin"
      })]);
      const planPayload = await planResponse.json().catch(() => null);
      const ledgerPayload = await ledgerResponse.json().catch(() => null);
      if (planResponse.ok) {
        setPlan(planPayload);
      } else {
        setPlan(null);
        setError(apiErrorMessage(planPayload, planResponse.status));
      }
      if (ledgerResponse.ok) {
        setLedger(ledgerPayload);
      }
    } catch (err) {
      setPlan(null);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };
  const apply = async () => {
    if (!window.confirm(ti(t, "k42be496f31"))) return;
    setLoading(true);
    setError(null);
    try {
      const response = await fetch("/api/config/migrations/apply", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          target: "storage_retention_balanced",
          reason: "admin_apply_storage_budget_defaults"
        })
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        setError(apiErrorMessage(payload, response.status));
        return;
      } else {
        setLastApplyResult(payload);
      }
      await load();
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void load();
  }, []);
  const changes = plan?.changes || [];
  const migrations = ledger?.migrations || [];
  const planStatus = String(plan?.status || "");
  const migrationReady = planStatus === "ready";
  return <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <div className="text-sm font-semibold text-foreground">{t("app.admin.dashboard.operations.center.advanced.configMigration.title")}</div>
                    <div className="text-xs leading-5 text-muted-foreground">{ti(t, "k10851755fd")}</div>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>{ti(t, "k38108eaa1d")}</Button>
                    <Button size="sm" onClick={() => void apply()} disabled={loading || !migrationReady}>{ti(t, "k458914f447")}</Button>
                </div>
            </div>
            {error ? <div className="rounded-xl border border-red-200 bg-red-50 p-3 text-xs leading-5 text-red-700">
                    {t("app.admin.dashboard.operations.center.advanced.configMigration.loadFailed", {
          error
        })}
                </div> : null}
            {plan ? <div className="rounded-xl border border-border p-3 text-xs leading-5">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                        <div className="font-semibold text-foreground">{plan.target || "storage_retention_balanced"} · {plan.status}</div>
                        <div className={migrationReady ? "rounded-full bg-amber-50 px-2 py-1 font-medium text-amber-700" : "rounded-full bg-emerald-50 px-2 py-1 font-medium text-emerald-700"}>
                            {migrationReady ? t("app.admin.dashboard.operations.center.advanced.configMigration.ready") : t("app.admin.dashboard.operations.center.advanced.configMigration.noActionNeeded")}
                        </div>
                    </div>
                    <div className="mt-2 text-muted-foreground">{plan.reason}</div>
                    <div className="mt-2 grid gap-2 md:grid-cols-3">
                        <div className="rounded-lg bg-muted/50 p-2">
                            <div className="text-muted-foreground">{t("app.admin.dashboard.operations.center.advanced.configMigration.changeCount")}</div>
                            <div className="font-semibold text-foreground">{changes.length}</div>
                        </div>
                        <div className="rounded-lg bg-muted/50 p-2">
                            <div className="text-muted-foreground">{t("app.admin.dashboard.operations.center.advanced.configMigration.reversible")}</div>
                            <div className="font-semibold text-foreground">{plan.reversible ? t("app.admin.dashboard.operations.center.page.k2ae24b34") : t("app.admin.dashboard.operations.center.page.k8d9f05ae")}</div>
                        </div>
                        <div className="rounded-lg bg-muted/50 p-2">
                            <div className="text-muted-foreground">{t("app.admin.dashboard.operations.center.advanced.configMigration.impact")}</div>
                            <div className="font-semibold text-foreground">{(plan.runtimeImpact || []).join(", ") || "-"}</div>
                        </div>
                    </div>
                    <div className="mt-2 max-h-48 overflow-auto rounded-lg bg-muted/50 p-2 font-mono text-[11px] text-muted-foreground">
                        {changes.length ? changes.slice(0, 40).map(item => <div key={item.path}>{item.path}: {JSON.stringify(item.before)} → {JSON.stringify(item.after)}</div>) : t("app.admin.dashboard.operations.center.advanced.configMigration.currentlyTargetState")}
                    </div>
                </div> : !loading && !error ? <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-800">
                    {t("app.admin.dashboard.operations.center.advanced.configMigration.noPlan")}
                </div> : null}
            {lastApplyResult ? <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-xs leading-5 text-emerald-700">
                    {t("app.admin.dashboard.operations.center.advanced.configMigration.applyResult", {
          status: fieldText(lastApplyResult.status, "-")
        })}
                </div> : null}
            {ledger?.ledgerPath ? <div className="rounded-xl border border-border bg-card p-3 text-xs leading-5 text-muted-foreground">
                    {t("app.admin.dashboard.operations.center.advanced.configMigration.ledgerPath", {
          ledgerPath: ledger.ledgerPath
        })}
                </div> : null}
            <div className="grid gap-2 md:grid-cols-2">
                {migrations.slice(0, 6).map(item => <div key={String(item.id)} className="rounded-xl border border-border p-3 text-xs leading-5">
                        <div className="font-semibold text-foreground">{String(item.id)}</div>
                        <div className="text-muted-foreground">{String(item.status)} · {String(item.createdAt || "")}</div>
                        <div className="break-all text-muted-foreground">{String(item.backupPath || "")}</div>
                    </div>)}
            </div>
        </div>;
}

function RuntimeEpisodeFabricPanel() {
  const t = useT();
  const [payload, setPayload] = useState<RuntimeEpisodeOverviewPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch("/api/runtime-episodes/overview?limit=120", {
        cache: "no-store",
        credentials: "same-origin"
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        setPayload(null);
        setError(apiErrorMessage(data, response.status));
        return;
      }
      setPayload(data);
    } catch (err) {
      setPayload(null);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void load();
  }, []);
  const summary = payload?.summary || {};
  const activeStates = new Set(["detected", "routed", "queued", "leased", "active", "waiting_child", "waiting_external", "waiting_approval"]);
  const episodes = payload?.episodes || [];
  const activeEpisodes = episodes.filter(item => activeStates.has(String(item.state || "")));
  const queue = payload?.queue || [];
  const activeQueue = queue.filter(item => activeStates.has(String(item.state || "")));
  const leases = payload?.leases || [];
  const activeLeases = leases.filter(item => String(item.state || "") === "active");
  const handoffs = payload?.handoffs || [];
  const cardItems = [{
    label: t("app.admin.dashboard.operations.center.advanced.episodeFabric.episodes"),
    value: Number(summary.episodeCount || episodes.length || 0),
    hint: t("app.admin.dashboard.operations.center.advanced.episodeFabric.activeCount", {
      count: activeEpisodes.length
    })
  }, {
    label: t("app.admin.dashboard.operations.center.advanced.episodeFabric.queue"),
    value: Number(summary.queueCount || queue.length || 0),
    hint: t("app.admin.dashboard.operations.center.advanced.episodeFabric.activeCount", {
      count: activeQueue.length
    })
  }, {
    label: t("app.admin.dashboard.operations.center.advanced.episodeFabric.leases"),
    value: Number(summary.activeLeaseCount || activeLeases.length || 0),
    hint: t("app.admin.dashboard.operations.center.advanced.episodeFabric.workerLease")
  }, {
    label: t("app.admin.dashboard.operations.center.advanced.episodeFabric.handoffs"),
    value: handoffs.length,
    hint: t("app.admin.dashboard.operations.center.advanced.episodeFabric.typedArtifacts")
  }];
  const renderEpisode = (item: JsonRecord) => {
    const id = fieldText(item.episodeId || item.id || item.episode_id, "-");
    const kind = fieldText(item.kind, "runtime");
    const state = fieldText(item.state, "unknown");
    const targetKind = fieldText(item.targetKind || item.target_kind, "local_runtime");
    return <div key={id} className="rounded-xl border border-border bg-card p-3 text-xs leading-5">
              <div className="flex items-center justify-between gap-3">
                  <span className="truncate font-semibold text-foreground">{kind}</span>
                  <span className="rounded-full bg-muted px-2 py-0.5 font-medium text-muted-foreground">{state}</span>
              </div>
              <div className="truncate font-mono text-[11px] text-muted-foreground">{id}</div>
              <div className="text-muted-foreground">{targetKind}</div>
              <div className="line-clamp-2 text-muted-foreground">{fieldText(item.reason || item.errorMessage || item.error_message, "")}</div>
          </div>;
  };
  const renderQueue = (item: JsonRecord) => {
    const id = fieldText(item.episode_id || item.episodeId || item.id, "-");
    return <div key={`${id}:${fieldText(item.state, "")}`} className="rounded-xl border border-border bg-card p-3 text-xs leading-5">
              <div className="flex items-center justify-between gap-3">
                  <span className="truncate font-mono text-[11px] text-foreground">{id}</span>
                  <span className="rounded-full bg-muted px-2 py-0.5 font-medium text-muted-foreground">{fieldText(item.state, "queued")}</span>
              </div>
              <div className="text-muted-foreground">
                  {t("app.admin.dashboard.operations.center.advanced.episodeFabric.priority", {
        priority: String(item.priority || 0)
      })} · {fieldText(item.scheduled_at || item.updated_at || item.created_at, "-")}
              </div>
          </div>;
  };
  const renderLease = (item: JsonRecord) => {
    const id = fieldText(item.episode_id || item.episodeId || item.id, "-");
    return <div key={`${id}:${fieldText(item.worker_id, "")}`} className="rounded-xl border border-border bg-card p-3 text-xs leading-5">
              <div className="flex items-center justify-between gap-3">
                  <span className="truncate font-semibold text-foreground">{fieldText(item.worker_id, "worker")}</span>
                  <span className="rounded-full bg-emerald-50 px-2 py-0.5 font-medium text-emerald-700">{fieldText(item.state, "active")}</span>
              </div>
              <div className="truncate font-mono text-[11px] text-muted-foreground">{id}</div>
              <div className="text-muted-foreground">{fieldText(item.progress, "")}</div>
              <div className="text-muted-foreground/80">{fieldText(item.heartbeat_at || item.lease_expires_at, "")}</div>
          </div>;
  };
  const renderHandoff = (item: JsonRecord) => {
    const payloadRecord = asRecord(item.payload);
    const id = fieldText(item.handoffId || item.handoffRefId || item.id || payloadRecord.handoffId || payloadRecord.handoffRefId, "-");
    return <div key={id} className="rounded-xl border border-border bg-card p-3 text-xs leading-5">
              <div className="flex items-center justify-between gap-3">
                  <span className="truncate font-semibold text-foreground">{fieldText(item.kind || payloadRecord.kind, "handoff")}</span>
                  <span className="rounded-full bg-sky-50 px-2 py-0.5 font-medium text-sky-700">{fieldText(item.status || payloadRecord.status, "ready")}</span>
              </div>
              <div className="truncate font-mono text-[11px] text-muted-foreground">{id}</div>
              <div className="line-clamp-2 text-muted-foreground">{fieldText(item.compactSummary || payloadRecord.compactSummary || payloadRecord.summary, "")}</div>
          </div>;
  };
  return <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <div className="text-sm font-semibold text-foreground">{t("app.admin.dashboard.operations.center.advanced.episodeFabric.title")}</div>
                    <div className="text-xs leading-5 text-muted-foreground">{t("app.admin.dashboard.operations.center.advanced.episodeFabric.description")}</div>
                </div>
                <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
                    {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                    {t("app.admin.dashboard.operations.center.advanced.refresh")}
                </Button>
            </div>
            {error ? <div className="rounded-xl border border-red-200 bg-red-50 p-3 text-xs leading-5 text-red-700">
                    {t("app.admin.dashboard.operations.center.advanced.episodeFabric.loadFailed", {
        error
      })}
                </div> : null}
            <div className="grid gap-3 md:grid-cols-4">
                {cardItems.map(item => <div key={item.label} className="rounded-xl border border-border bg-muted/50 p-3 text-sm">
                        <div className="text-xs text-muted-foreground">{item.label}</div>
                        <div className="font-semibold text-foreground">{item.value}</div>
                        <div className="text-xs text-muted-foreground">{item.hint}</div>
                    </div>)}
            </div>
            <div className="grid gap-3 xl:grid-cols-2">
                <div className="rounded-xl border border-border bg-muted/50 p-3">
                    <div className="mb-2 text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">{t("app.admin.dashboard.operations.center.advanced.episodeFabric.activeEpisodes")}</div>
                    <div className="max-h-80 space-y-2 overflow-auto pr-1">
                        {(activeEpisodes.length ? activeEpisodes : episodes.slice(0, 8)).map(item => renderEpisode(item))}
                        {!episodes.length && !loading ? <div className="rounded-xl border border-dashed border-border bg-card p-4 text-xs text-muted-foreground">{t("app.admin.dashboard.operations.center.advanced.episodeFabric.empty")}</div> : null}
                    </div>
                </div>
                <div className="rounded-xl border border-border bg-muted/50 p-3">
                    <div className="mb-2 text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">{t("app.admin.dashboard.operations.center.advanced.episodeFabric.queueAndLeases")}</div>
                    <div className="grid max-h-80 gap-2 overflow-auto pr-1 md:grid-cols-2">
                        {(activeQueue.length ? activeQueue : queue.slice(0, 6)).map(item => renderQueue(item))}
                        {(activeLeases.length ? activeLeases : leases.slice(0, 6)).map(item => renderLease(item))}
                        {!queue.length && !leases.length && !loading ? <div className="rounded-xl border border-dashed border-border bg-card p-4 text-xs text-muted-foreground md:col-span-2">{t("app.admin.dashboard.operations.center.advanced.episodeFabric.emptyQueue")}</div> : null}
                    </div>
                </div>
            </div>
            <div className="rounded-xl border border-border bg-muted/50 p-3">
                <div className="mb-2 text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">{t("app.admin.dashboard.operations.center.advanced.episodeFabric.recentHandoffs")}</div>
                <div className="grid max-h-72 gap-2 overflow-auto pr-1 md:grid-cols-3">
                    {handoffs.slice(0, 12).map(item => renderHandoff(item))}
                    {!handoffs.length && !loading ? <div className="rounded-xl border border-dashed border-border bg-card p-4 text-xs text-muted-foreground md:col-span-3">{t("app.admin.dashboard.operations.center.advanced.episodeFabric.emptyHandoff")}</div> : null}
                </div>
            </div>
        </div>;
}
function stringifyLogDetails(value: unknown) {
  if (value == null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
function logTimestamp(value: JsonRecord) {
  return String(value.createdAt || value.created_at || value.updatedAt || value.updated_at || value.timestamp || value.started_at || value.finished_at || "");
}
function OperationLogsPanel() {
  const t = useT();
  const [source, setSource] = useState<OperationLogSource>("all");
  const [items, setItems] = useState<OperationLogItem[]>([]);
  const [loading, setLoading] = useState(false);
  const load = async () => {
    setLoading(true);
    try {
      const requests: Array<Promise<{
        kind: string;
        payload: JsonRecord;
      }>> = [];
      if (source === "all" || source === "runtime") {
        requests.push(fetch("/api/runs?limit=40", {
          cache: "no-store"
        }).then(async response => ({
          kind: "runtime",
          payload: response.ok ? await response.json().catch(() => ({})) : {}
        })));
      }
      if (source === "all" || source === "audit" || source === "hook" || source === "safety" || source === "storage") {
        const params = new URLSearchParams({
          limit: "80"
        });
        if (source === "hook") params.set("source_type", "HOOK");
        if (source === "safety") params.set("source_type", "SAFETY");
        if (source === "storage") params.set("source_type", "STORAGE_RETENTION");
        requests.push(fetch(`/api/audit/logs?${params.toString()}`, {
          cache: "no-store"
        }).then(async response => ({
          kind: "audit",
          payload: response.ok ? await response.json().catch(() => ({})) : {}
        })));
      }
      if (source === "all" || source === "cron") {
        requests.push(fetch("/api/cron/logs?limit=80", {
          cache: "no-store"
        }).then(async response => ({
          kind: "cron",
          payload: response.ok ? await response.json().catch(() => ({})) : {}
        })));
      }
      const responses = await Promise.all(requests);
      const nextItems: OperationLogItem[] = [];
      for (const item of responses) {
        if (item.kind === "runtime") {
          for (const run of Array.isArray(item.payload?.runs) ? item.payload.runs : []) {
            const record = asRecord(run);
            nextItems.push({
              id: fieldText(record.id || record.runId, `run-${nextItems.length}`),
              timestamp: logTimestamp(record),
              source: "Runtime Runs",
              status: fieldText(record.status, "unknown"),
              action: fieldText(record.runtimeKind || record.runtime_kind || record.kind, "run"),
              runId: fieldText(record.id || record.runId),
              sessionId: fieldText(record.sessionId || record.session_id),
              summary: fieldText(record.summary || record.title || record.name || record.status, "runtime run"),
              details: stringifyLogDetails(record.lastEvent || record.error || record.metadata || "")
            });
          }
        }
        if (item.kind === "audit") {
          for (const log of Array.isArray(item.payload?.logs) ? item.payload.logs : []) {
            const record = asRecord(log);
            nextItems.push({
              id: fieldText(record.id, `audit-${nextItems.length}`),
              timestamp: logTimestamp(record),
              source: fieldText(record.source_type || record.sourceType, "Audit"),
              status: fieldText(record.status, "unknown"),
              action: fieldText(record.action, "audit"),
              runId: fieldText(record.run_id || record.runId),
              sessionId: fieldText(record.session_id || record.sessionId),
              summary: fieldText(record.summary || record.message || record.action, "audit log"),
              details: stringifyLogDetails(record.details)
            });
          }
        }
        if (item.kind === "cron") {
          for (const log of Array.isArray(item.payload?.logs) ? item.payload.logs : []) {
            const record = asRecord(log);
            nextItems.push({
              id: fieldText(record.id || record.execution_id, `cron-${nextItems.length}`),
              timestamp: logTimestamp(record),
              source: "Cron",
              status: fieldText(record.status || record.result, "unknown"),
              action: fieldText(record.job_id || record.jobId || record.name, "cron job"),
              runId: fieldText(record.run_id || record.runId),
              sessionId: fieldText(record.session_id || record.sessionId),
              summary: fieldText(record.summary || record.message || record.error || record.status, "cron execution"),
              details: stringifyLogDetails(record.details || record.payload || record.error)
            });
          }
        }
      }
      nextItems.sort((a, b) => String(b.timestamp).localeCompare(String(a.timestamp)));
      setItems(nextItems.slice(0, 120));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source]);
  return <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <AdminHoverInfo content={t("app.admin.dashboard.operations.center.logs.description")}>
                    <h2 className="text-sm font-semibold text-foreground">{t("app.admin.dashboard.operations.center.logs.title")}</h2>
                </AdminHoverInfo>
                <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
                    {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                    {t("app.admin.dashboard.operations.center.page.kd4db8d84")}
                </Button>
            </div>
            <div className="flex flex-wrap gap-2">
                {OPERATION_LOG_SOURCES.map(item => <button key={item} type="button" onClick={() => setSource(item)} className={`rounded-full px-3 py-1 text-xs font-medium transition ${source === item ? "bg-slate-950 text-white" : "bg-muted text-muted-foreground hover:bg-muted"}`}>

                        {t(`app.admin.dashboard.operations.center.logs.source.${item}`)}
                    </button>)}
            </div>
            <div className="max-h-[520px] space-y-2 overflow-auto rounded-2xl border border-border bg-card p-3">
                {items.length === 0 ? <div className="rounded-xl border border-dashed border-border p-5 text-sm text-muted-foreground">
                        {t("app.admin.dashboard.operations.center.logs.empty")}
                    </div> : items.map(item => <div key={`${item.source}-${item.id}`} className="rounded-xl border border-border bg-muted/50 px-3 py-2 text-xs">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                            <div className="font-semibold text-foreground">{item.action}</div>
                            <div className="flex flex-wrap items-center gap-2 text-muted-foreground">
                                <span>{item.source}</span>
                                <span>{item.status}</span>
                                <span>{item.timestamp || "-"}</span>
                            </div>
                        </div>
                        <div className="mt-1 text-foreground">{item.summary}</div>
                        {item.details ? <div className="mt-2 line-clamp-3 break-all text-muted-foreground">{item.details}</div> : null}
                    </div>)}
            </div>
        </div>;
}
type ToolObservation = {
  id?: string;
  rawRef?: string;
  toolName?: string;
  toolCallId?: string;
  runtimeKind?: string;
  surface?: string;
  rawChars?: number;
  visibleChars?: number;
  rawSha256?: string;
  created_at?: string;
  createdAt?: string;
  preview?: string;
  previewChars?: number;
  omittedChars?: number;
  redacted?: boolean;
  budget?: JsonRecord;
  metadata?: JsonRecord;
};
type CompactionRecord = {
  id?: string;
  run_id?: string;
  session_id?: string;
  target_role?: string;
  trigger_reason?: string;
  summary_method?: string;
  estimated_saved_tokens?: number;
  covered_message_count?: number;
  created_at?: string;
};
function compactId(value?: string) {
  const text = String(value || "");
  if (text.length <= 18) return text || "-";
  return `${text.slice(0, 9)}...${text.slice(-6)}`;
}
function EvidencePanel() {
  const t = useT();
  const [items, setItems] = useState<ToolObservation[]>([]);
  const [compactions, setCompactions] = useState<CompactionRecord[]>([]);
  const [selected, setSelected] = useState<ToolObservation | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [filters, setFilters] = useState({
    runId: "",
    sessionId: "",
    toolName: "",
    runtimeKind: ""
  });
  const [loading, setLoading] = useState(false);
  const [revealing, setRevealing] = useState(false);
  const buildQuery = (cursor?: string | null) => {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value.trim()) params.set(key, value.trim());
    });
    params.set("limit", "50");
    if (cursor) params.set("cursor", cursor);
    return params.toString();
  };
  const load = async (mode: "replace" | "append" = "replace") => {
    setLoading(true);
    try {
      const query = buildQuery(mode === "append" ? nextCursor : null);
      const [observationsResponse, compactionsResponse] = await Promise.all([fetch(`/api/observability/tool-observations?${query}`, {
        cache: "no-store"
      }), fetch("/api/observability/compactions?limit=12", {
        cache: "no-store"
      })]);
      const observations = await observationsResponse.json().catch(() => ({}));
      const compactionPayload = await compactionsResponse.json().catch(() => ({}));
      if (observationsResponse.ok) {
        const nextItems = Array.isArray(observations.items) ? observations.items : [];
        setItems(current => mode === "append" ? [...current, ...nextItems] : nextItems);
        setNextCursor(observations.nextCursor || null);
        if (mode === "replace") setSelected(nextItems[0] || null);
      }
      if (compactionsResponse.ok) {
        setCompactions(Array.isArray(compactionPayload.items) ? compactionPayload.items : []);
      }
    } finally {
      setLoading(false);
    }
  };
  const reveal = async () => {
    if (!selected?.id) return;
    setRevealing(true);
    try {
      const response = await fetch(`/api/observability/tool-observations/${encodeURIComponent(selected.id)}/reveal`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          maxChars: 12000
        })
      });
      const payload = await response.json().catch(() => null);
      if (response.ok && payload) setSelected(payload);
    } finally {
      setRevealing(false);
    }
  };
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const selectedRunId = String(selected?.metadata?.runId || selected?.metadata?.run_id || "");
  const selectedSessionId = String(selected?.metadata?.sessionId || selected?.metadata?.session_id || "");
  const relatedCompactions = compactions.filter(item => selectedRunId && item.run_id === selectedRunId || selectedSessionId && item.session_id === selectedSessionId);
  return <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <AdminHoverInfo content={t("app.admin.dashboard.operations.center.evidence.description")} panelClassName="text-sm leading-6">

                    <h2 className="text-sm font-semibold text-foreground">{t("app.admin.dashboard.operations.center.evidence.title")}</h2>
                </AdminHoverInfo>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
                        {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                        {t("app.admin.dashboard.operations.center.page.kd4db8d84")}
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => selected?.rawRef && navigator.clipboard?.writeText(selected.rawRef)} disabled={!selected?.rawRef}>
                        {t("app.admin.dashboard.operations.center.evidence.copyRawRef")}
                    </Button>
                </div>
            </div>

            <div className="grid gap-2 md:grid-cols-4">
                <Input value={filters.runId} onChange={event => setFilters({
        ...filters,
        runId: event.target.value
      })} placeholder={t("app.admin.dashboard.operations.center.evidence.runId")} />
                <Input value={filters.sessionId} onChange={event => setFilters({
        ...filters,
        sessionId: event.target.value
      })} placeholder={t("app.admin.dashboard.operations.center.evidence.sessionId")} />
                <Input value={filters.toolName} onChange={event => setFilters({
        ...filters,
        toolName: event.target.value
      })} placeholder={t("app.admin.dashboard.operations.center.evidence.toolName")} />
                <Input value={filters.runtimeKind} onChange={event => setFilters({
        ...filters,
        runtimeKind: event.target.value
      })} placeholder={t("app.admin.dashboard.operations.center.evidence.runtimeKind")} />
            </div>

            <div className="grid min-h-0 gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
                <div className="max-h-[520px] space-y-2 overflow-auto rounded-2xl border border-border bg-card p-3 shadow-sm xl:max-h-[680px]">
                    {items.length === 0 ? <div className="rounded-xl border border-dashed border-border p-4 text-sm text-muted-foreground">{t("app.admin.dashboard.operations.center.evidence.empty")}</div> : items.map(item => <button key={item.id || item.rawRef} type="button" onClick={() => setSelected(item)} className={`w-full rounded-xl border px-3 py-2 text-left text-xs transition ${selected?.id === item.id ? "border-slate-900 bg-slate-950 text-white" : "border-border bg-muted/50 text-foreground hover:border-input"}`}>

                            <div className="flex items-center justify-between gap-2">
                                <span className="truncate font-semibold">{item.toolName || "unknown"}</span>
                                <span className="shrink-0 opacity-70">{formatRuntimeKindLabel(item.runtimeKind, t)}</span>
                            </div>
                            <div className="mt-1 truncate opacity-70">{item.createdAt || item.created_at || "-"}</div>
                        </button>)}
                    {nextCursor ? <Button className="w-full" variant="outline" size="sm" onClick={() => void load("append")} disabled={loading}>
                            {t("app.admin.dashboard.operations.center.evidence.loadMore")}
                        </Button> : null}
                </div>

                <div className="min-w-0 space-y-4 overflow-auto rounded-2xl border border-border bg-card p-4 shadow-sm xl:max-h-[680px]">
                    {selected ? <>
                            <div className="grid gap-2 text-xs md:grid-cols-2">
                                <div className="rounded-xl border border-border p-3">
                                    <div className="text-muted-foreground">{t("app.admin.dashboard.operations.center.evidence.size")}</div>
                                    <div className="mt-1 font-semibold text-foreground">{selected.rawChars || 0} / {selected.visibleChars || 0}</div>
                                </div>
                                <div className="rounded-xl border border-border p-3">
                                    <div className="text-muted-foreground">{t("app.admin.dashboard.operations.center.evidence.omitted", {
                                      omitted_chars: selected.omittedChars || 0,
                                      redacted: selected.redacted ? "yes" : "no",
                                    })}</div>
                                    <div className="mt-1 font-semibold text-foreground">{selected.redacted ? t("app.admin.dashboard.operations.center.evidence.redactedYes") : t("app.admin.dashboard.operations.center.evidence.redactedNo")}</div>
                                </div>
                            </div>
                            <details className="rounded-xl border border-border/60 bg-muted/20 p-3 text-xs">
                                <summary className="cursor-pointer font-medium text-muted-foreground">{t("components.common.technicalDetails")}</summary>
                                <TechnicalReferenceDetails className="mt-3" items={[
                                  { label: t("components.common.rawReference"), value: selected.rawRef },
                                  { label: t("app.admin.dashboard.operations.center.evidence.hash"), value: selected.rawSha256 },
                                ]} />
                                <div className="mt-3 grid gap-2 md:grid-cols-2">
                                    <pre className="max-h-44 overflow-auto whitespace-pre-wrap break-all rounded-xl bg-slate-950 p-3 text-white">{JSON.stringify(selected.budget || {}, null, 2)}</pre>
                                    <pre className="max-h-44 overflow-auto whitespace-pre-wrap break-all rounded-xl bg-slate-950 p-3 text-white">{JSON.stringify(selected.metadata || {}, null, 2)}</pre>
                                </div>
                            </details>
                            <div className="rounded-xl border border-border p-3 text-xs">
                                <div className="font-semibold text-foreground">{t("app.admin.dashboard.operations.center.evidence.relatedCompactions")}</div>
                                <div className="mt-2 space-y-1 text-muted-foreground">
                                    {relatedCompactions.length ? relatedCompactions.slice(0, 3).map(item => <div key={item.id} className="flex flex-wrap items-center justify-between gap-2">
                                            <span className="font-mono">{compactId(item.id)}</span>
                                            <span>{item.summary_method || "-"}</span>
                                            <span>{t("app.admin.dashboard.operations.center.evidence.savedTokens", {
                    tokens: item.estimated_saved_tokens || 0
                  })}</span>
                                        </div>) : t("app.admin.dashboard.operations.center.evidence.noRelatedCompactions")}
                                </div>
                            </div>
                            <div>
                                <div className="mb-2 flex items-center justify-between gap-2">
                                    <div className="text-sm font-semibold text-foreground">{t("app.admin.dashboard.operations.center.evidence.preview")}</div>
                                    <div className="flex gap-2">
                                        <Button variant="outline" size="sm" onClick={() => navigator.clipboard?.writeText(selected.preview || "")}>
                                            {t("app.admin.dashboard.operations.center.evidence.copyPreview")}
                                        </Button>
                                        <Button size="sm" onClick={() => void reveal()} disabled={revealing}>
                                            {revealing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                                            {t("app.admin.dashboard.operations.center.evidence.reveal")}
                                        </Button>
                                    </div>
                                </div>
                                <pre className="max-h-[320px] overflow-auto whitespace-pre-wrap break-all rounded-2xl border border-border bg-muted/50 p-4 text-xs leading-5 text-foreground">{selected.preview || ""}</pre>
                            </div>
                        </> : <div className="rounded-xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">{t("app.admin.dashboard.operations.center.evidence.select")}</div>}
                </div>
            </div>

            <AdvancedSection title={t("app.admin.dashboard.operations.center.evidence.compactions")} defaultOpen={false}>
                <div className="space-y-2">
                    {compactions.length === 0 ? <div className="rounded-xl border border-dashed border-border p-4 text-sm text-muted-foreground">{t("app.admin.dashboard.operations.center.evidence.noCompactions")}</div> : compactions.map(item => <div key={item.id} className="grid gap-2 rounded-xl border border-border p-3 text-xs md:grid-cols-5">
                            <div className="font-mono text-muted-foreground">{compactId(item.id)}</div>
                            <div>{item.target_role || "-"}</div>
                            <div>{item.summary_method || "-"}</div>
                            <div>{t("app.admin.dashboard.operations.center.evidence.savedTokens", {
              tokens: item.estimated_saved_tokens || 0
            })}</div>
                            <div className="truncate text-muted-foreground">{item.trigger_reason || "-"}</div>
                        </div>)}
                </div>
            </AdvancedSection>
        </div>;
}
function RunLedgerPanel({
  runId
}: {
  runId: string | null;
}) {
  const t = useT();
  const [ledger, setLedger] = useState<RunLedgerPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = async () => {
    if (!runId) return;
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/ledger`, {
        cache: "no-store"
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        setError(String(payload?.detail || payload?.error || "Run ledger unavailable"));
        setLedger(null);
        return;
      }
      setLedger(payload || null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Run ledger unavailable");
      setLedger(null);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);
  if (!runId) {
    return null;
  }
  const timeline = Array.isArray(ledger?.timeline) ? ledger.timeline : [];
  const refs = ledger?.refs || {};
  return <div className="rounded-3xl border border-border bg-card p-5 shadow-sm">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <AdminHoverInfo content={tg(t, "2d4b9c7f")}>
                    <div>
                        <h2 className="text-lg font-semibold text-foreground">Run Ledger</h2>
                        <p className="mt-1 text-xs text-muted-foreground">{t("app.admin.dashboard.operations.center.runLedgerDescription")}</p>
                    </div>
                </AdminHoverInfo>
                <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
                    {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                    {ti(t, "k38108eaa1d")}
                </Button>
            </div>
            <TechnicalReferenceDetails className="mt-4" items={[
              { label: t("components.common.runReference"), value: runId },
            ]} />
            {error ? <div className="mt-4 rounded-2xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">{error}</div> : null}
            {ledger ? <div className="mt-4 grid gap-4 xl:grid-cols-[260px_minmax(0,1fr)]">
                    <div className="space-y-2 rounded-2xl border border-border/60 bg-muted/50 p-3 text-sm text-muted-foreground">
                        <div>{ti(t, "kbf0ac89783")}<span className="font-medium text-foreground">{ledger.status || "-"}</span></div>
                        <div>Runtime：<span className="font-medium text-foreground">{ledger.runtimeKind || "-"}</span></div>
                        <div>{ti(t, "k9c9db830ea")}<span className="font-medium text-foreground">{ledger.nextAction || "-"}</span></div>
                        {Object.entries(refs).map(([key, value]) => <div key={key}>{key}: <span className="font-mono text-xs text-foreground">{Array.isArray(value) ? value.length : 0}</span></div>)}
                    </div>
                    <div className="max-h-[420px] space-y-2 overflow-auto pr-1">
                        {timeline.length === 0 ? <div className="rounded-2xl border border-dashed border-border p-5 text-sm text-muted-foreground">{ti(t, "k0e212bccad")}</div> : timeline.map(item => <div key={item.id || `${item.type}-${item.ts}`} className="rounded-2xl border border-border/60 bg-card p-3 shadow-sm">
                                <div className="flex flex-wrap items-center gap-2 text-xs">
                                    <span className="rounded-full bg-slate-950 px-2 py-0.5 font-medium text-white">{item.type || "event"}</span>
                                    <span className="text-muted-foreground">{item.source || "-"}</span>
                                    <span className="font-mono text-muted-foreground/80">{item.ts || "-"}</span>
                                </div>
                                {item.summary ? <p className="mt-2 text-sm text-foreground">{item.summary}</p> : null}
                                {item.refs && Object.keys(item.refs).length > 0 ? <pre className="mt-2 max-h-28 overflow-auto rounded-xl bg-muted/50 p-2 text-xs text-muted-foreground">
                                        {JSON.stringify(item.refs, null, 2)}
                                    </pre> : null}
                            </div>)}
                    </div>
                </div> : !loading && !error ? <div className="mt-4 rounded-2xl border border-dashed border-border p-5 text-sm text-muted-foreground">{ti(t, "k22fc006983")}</div> : null}
        </div>;
}
export default function OperationsCenterPage() {
  const t = useT();
  const router = useRouter();
  const searchParams = useSearchParams();
  const runtime = useRuntimeOpsData();
  const [summary, setSummary] = useState<SummaryPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const requestedTab = searchParams.get("tab") || "overview";
  const activeTab = VALID_TABS.has(requestedTab) ? requestedTab : "overview";
  const focusRunId = searchParams.get("focusRun");
  const focusSessionId = searchParams.get("focusSession");
  const loadSummary = async () => {
    setLoading(true);
    try {
      const response = await fetch("/api/operations-center/summary", {
        cache: "no-store"
      });
      const payload = await response.json().catch(() => null);
      if (response.ok) {
        setSummary(payload);
      }
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void loadSummary();
  }, []);
  const streamableHttpIssues = summary?.health?.mcp?.streamableHttpIssues || [];
  const degradedServers = summary?.health?.mcp?.degradedServers || [];
  const pendingApprovalMismatch = typeof summary?.pendingApprovals === "number" && summary.pendingApprovals !== runtime.approvals.length;
  return <AdminPageShell>
            <AdminPageHeader title={"app.admin.dashboard.operations.center.page.k756910c0"} description={"app.admin.dashboard.operations.center.page.k5c84b2ac"} actions={<Button variant="outline" onClick={() => void loadSummary()} disabled={loading}>
                        {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                        {t("app.admin.dashboard.operations.center.page.kd4db8d84")}
                    </Button>} />

            <DomainSummaryStrip items={[{
      label: "app.admin.dashboard.operations.center.page.k61329e7f",
      value: runtime.approvals.length,
      description: "app.admin.dashboard.operations.center.page.k0ecf1629"
    }, {
      label: "app.admin.dashboard.operations.center.page.k69570f96",
      value: runtime.runs.filter(run => run.status === "running").length,
      description: "app.admin.dashboard.operations.center.page.kc166b15e"
    }, {
      label: "app.admin.dashboard.operations.center.page.k22d985d6",
      value: runtime.runs.filter(run => ["paused", "failed", "waiting_input"].includes(run.status || "")).length,
      description: "app.admin.dashboard.operations.center.page.k6b24ed69"
    }, {
      label: "app.admin.dashboard.operations.center.page.k0280ab58",
      value: summary?.health?.status === "ok" ? t("app.admin.dashboard.operations.center.page.k3f88d199") : t("app.admin.dashboard.operations.center.page.kab954a08"),
      description: t("app.admin.dashboard.operations.center.page.summary.visibleTools", {
        visible_tools: summary?.health?.mcp_tools ?? "-"
      })
    }, {
      label: "app.admin.dashboard.operations.center.page.kc67937eb",
      value: summary?.health?.memory?.mode === "fts5_only_degraded" ? t("app.admin.dashboard.operations.center.page.k8da7daa5") : summary?.health?.memory?.mode === "sqlite_fts5_plus_chromadb" ? t("app.admin.dashboard.operations.center.page.kdbf6b1a0") : t("app.admin.dashboard.operations.center.page.k76ebff7c"),
      description: summary?.health?.memory?.interpreterDrift ? "app.admin.dashboard.operations.center.page.k5e58cf57" : summary?.health?.memory?.chromadb?.available === false ? "app.admin.dashboard.operations.center.page.k22d69497" : summary?.health?.memory?.mode === "sqlite_fts5_plus_chromadb" ? "app.admin.dashboard.operations.center.page.k33fb7ffa" : "app.admin.dashboard.operations.center.page.k9d4e92f0"
    }]} />

            {summary?.health?.memory?.mode === "fts5_only_degraded" || summary?.health?.memory?.interpreterDrift ? <StatusNotice title={"app.admin.dashboard.operations.center.page.k400d7e2f"} description={summary?.health?.memory?.interpreterDrift ? t("app.admin.dashboard.operations.center.page.warning.interpreterDrift", {
      interpreter_path: summary?.health?.memory?.interpreterPath || t("app.admin.dashboard.operations.center.page.unknown"),
      expected_path: summary?.health?.memory?.expectedInterpreterPath || "Engine .venv"
    }) : "app.admin.dashboard.operations.center.page.ke5b335d8"} tone="warning" /> : null}

            {summary?.health?.mcp?.executionImpacted || (summary?.health?.mcp?.streamableHttpIssues?.length || 0) > 0 ? <StatusNotice title={"app.admin.dashboard.operations.center.page.kb5668925"} description={summary?.health?.mcp?.executionImpacted ? "app.admin.dashboard.operations.center.page.kc1e3ce76" : summary?.health?.mcp?.backgroundReconnectOnly ? "app.admin.dashboard.operations.center.page.k2d3e075a" : "app.admin.dashboard.operations.center.page.kdda9746a"} tone="warning" /> : null}

            <div className="grid gap-4 xl:grid-cols-3">
                <div className="rounded-2xl border border-border bg-card p-5 shadow-sm">
                    <div className="text-sm font-medium text-foreground">{t("app.admin.dashboard.operations.center.page.k7190d60a")}</div>
                    <div className="mt-3 space-y-2 text-sm text-muted-foreground">
                        <div><span className="font-medium text-foreground">{t("app.admin.dashboard.operations.center.page.kfbd6399e")}</span>{summary?.health?.memory?.mode || t("app.admin.dashboard.operations.center.page.k76ebff7c")}</div>
                        <div><span className="font-medium text-foreground">{t("app.admin.dashboard.operations.center.page.kc6b5096c")}</span><span className="break-all font-mono text-xs">{summary?.health?.memory?.interpreterPath || t("app.admin.dashboard.operations.center.page.k76ebff7c")}</span></div>
                        <div><span className="font-medium text-foreground">ChromaDB：</span>{summary?.health?.memory?.chromadb?.available ? t("app.admin.dashboard.operations.center.page.k8b78e9e2", {
              summary_health_memory_chromadb_version: summary?.health?.memory?.chromadb?.version || t("app.admin.dashboard.operations.center.page.unknownVersion")
            }) : t("app.admin.dashboard.operations.center.page.kd2037c91")}</div>
                        <div><span className="font-medium text-foreground">{t("app.admin.dashboard.operations.center.page.k224983a9")}</span>{summary?.health?.memory?.vectorBackend?.ready ? "ready" : "not ready"}</div>
                    </div>
                </div>
                <div className="rounded-2xl border border-border bg-card p-5 shadow-sm">
                    <div className="text-sm font-medium text-foreground">{t("app.admin.dashboard.operations.center.page.k127a42f4")}</div>
                    <div className="mt-3 space-y-2 text-sm text-muted-foreground">
                        {summary?.health?.memory?.warnings?.length ? summary.health.memory.warnings.map(warning => <div key={warning} className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">
                                    {warning}
                                </div>) : <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs leading-5 text-emerald-900">
                                {t("app.admin.dashboard.operations.center.page.k8a57c639")}
                            </div>}
                    </div>
                </div>
                <div className="rounded-2xl border border-border bg-card p-5 shadow-sm">
                    <div className="text-sm font-medium text-foreground">{t("app.admin.dashboard.operations.center.page.k778a6bf7")}</div>
                    <div className="mt-3 space-y-2 text-sm text-muted-foreground">
                        <div><span className="font-medium text-foreground">{t("app.admin.dashboard.operations.center.page.ke583c052")}</span>{summary?.health?.mcp?.configured ?? 0}</div>
                        <div><span className="font-medium text-foreground">{t("app.admin.dashboard.operations.center.page.k8f5a01b3")}</span>{summary?.health?.mcp?.connected ?? 0}</div>
                        <div><span className="font-medium text-foreground">{t("app.admin.dashboard.operations.center.page.k7c7ba6d0")}</span>{summary?.health?.mcp?.degraded ?? 0}</div>
                        <div><span className="font-medium text-foreground">{t("app.admin.dashboard.operations.center.page.k37dfd1b7")}</span>{summary?.health?.mcp?.executionImpacted ? t("app.admin.dashboard.operations.center.page.k2ae24b34") : t("app.admin.dashboard.operations.center.page.k8d9f05ae")}</div>
                        {summary?.health?.mcp?.backgroundReconnectOnly ? <div className="rounded-xl border border-sky-200 bg-sky-50 px-3 py-2 text-xs leading-5 text-sky-900">
                                {t("app.admin.dashboard.operations.center.page.k0b0a6aba")}
                            </div> : null}
                        {streamableHttpIssues.length > 0 ? <div className="space-y-2">
                                <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">
                                    {t("app.admin.dashboard.operations.center.page.k5325bd42", {
                streamableHttpIssues_length: streamableHttpIssues.length
              })}
                                </div>
                                {streamableHttpIssues.slice(0, 3).map(issue => <div key={`${issue.name}-${issue.status}`} className="rounded-xl border border-border bg-muted/50 px-3 py-2 text-xs leading-5 text-foreground">
                                        <div><span className="font-medium text-foreground">{issue.name || "unknown server"}</span> · {issue.status || "unknown"}</div>
                                        <div>{issue.executionImpacted ? t("app.admin.dashboard.operations.center.page.k81feb590") : issue.impact === "background_reconnect" ? t("app.admin.dashboard.operations.center.page.k92c7108e") : t("app.admin.dashboard.operations.center.page.k91c91e24")}</div>
                                        {issue.lastError ? <div className="text-muted-foreground">{issue.lastError}</div> : null}
                                    </div>)}
                            </div> : degradedServers.length > 0 ? <div className="space-y-2">
                                <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">
                                    {t("app.admin.dashboard.operations.center.page.k8ffbe607", {
                degradedServers_length: degradedServers.length
              })}
                                </div>
                                {degradedServers.slice(0, 2).map(server => <div key={`${server.name}-${server.status}`} className="rounded-xl border border-border bg-muted/50 px-3 py-2 text-xs leading-5 text-foreground">
                                        <div><span className="font-medium text-foreground">{server.name || "unknown server"}</span> · {server.transport || "unknown"} · {server.status || "unknown"}</div>
                                        <div>{server.impact === "background_reconnect" ? t("app.admin.dashboard.operations.center.page.k92c7108e") : t("app.admin.dashboard.operations.center.page.k0e3d684b")} </div>
                                        {server.lastError ? <div className="text-muted-foreground">{server.lastError}</div> : null}
                                    </div>)}
                            </div> : <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs leading-5 text-emerald-900">
                                {t("app.admin.dashboard.operations.center.page.k30e0aa83")}
                            </div>}
                    </div>
                </div>
            </div>

            <Tabs value={activeTab} onValueChange={value => router.replace(`/admin/operations-center?tab=${encodeURIComponent(value)}`, {
      scroll: false
    })} className="space-y-4">
                <TabsList className="grid w-full grid-cols-5 rounded-2xl bg-card shadow-sm">
                    <TabsTrigger value="overview">{t("app.admin.dashboard.operations.center.page.kbd84d331")}</TabsTrigger>
                    <TabsTrigger value="approvals">{t("app.admin.dashboard.operations.center.page.k61dba659")}</TabsTrigger>
                    <TabsTrigger value="runs">{t("app.admin.dashboard.operations.center.page.k1a586b06")}</TabsTrigger>
                    <TabsTrigger value="evidence">{t("app.admin.dashboard.operations.center.evidence.tab")}</TabsTrigger>
                    <TabsTrigger value="advanced">{t("app.admin.dashboard.operations.center.page.kdce17454")}</TabsTrigger>
                </TabsList>

                <TabsContent value="overview" className="space-y-4">
                    {pendingApprovalMismatch ? <StatusNotice title={"app.admin.dashboard.operations.center.page.kb951aaf4"} description={t("app.admin.dashboard.operations.center.page.warning.pendingApprovalMismatch", {
          summary_count: summary?.pendingApprovals ?? "-",
          approval_count: runtime.approvals.length
        })} tone="warning" /> : null}
                    {runtime.approvals.length > 0 ? <StatusNotice title={"app.admin.dashboard.operations.center.page.kca01fb70"} description={"app.admin.dashboard.operations.center.page.ke9a1b1b4"} tone="warning" /> : null}
                    <div className="grid gap-4 xl:grid-cols-2">
                        <PendingApprovalsPanel hook={runtime} focusRunId={focusRunId} focusSessionId={focusSessionId} />
                        <RecentRunsPanel hook={runtime} focusRunId={focusRunId} focusSessionId={focusSessionId} />
                    </div>
                </TabsContent>

                <TabsContent value="approvals">
                    <PendingApprovalsPanel hook={runtime} focusRunId={focusRunId} focusSessionId={focusSessionId} />
                </TabsContent>

                <TabsContent value="runs" className="space-y-4">
                    {runtime.runs.some(run => ["paused", "failed", "waiting_input"].includes(run.status || "")) ? <StatusNotice title={"app.admin.dashboard.operations.center.page.k9eb5cbb2"} description={"app.admin.dashboard.operations.center.page.ke692c9ed"} tone="success" /> : null}
                    <RunLedgerPanel runId={focusRunId} />
                    <RecentRunsPanel hook={runtime} focusRunId={focusRunId} focusSessionId={focusSessionId} />
                </TabsContent>

                <TabsContent value="evidence">
                    <EvidencePanel />
                </TabsContent>

                <TabsContent value="advanced">
                    <AdvancedSection title={t("app.admin.dashboard.operations.center.advanced.systemDoctor.title")} defaultOpen>
                        <SystemDoctorPanel />
                    </AdvancedSection>
                    <AdvancedSection title={t("app.admin.dashboard.operations.center.advanced.configMigration.title")} defaultOpen={false}>
                        <ConfigMigrationPanel />
                    </AdvancedSection>
                    <AdvancedSection title={t("app.admin.dashboard.operations.center.advanced.episodeFabric.title")} defaultOpen={false}>
                        <RuntimeEpisodeFabricPanel />
                    </AdvancedSection>
                    <AdvancedSection title={t("app.admin.dashboard.operations.center.advanced.storageRetention.title")} defaultOpen={false}>
                        <StorageRetentionPanel />
                    </AdvancedSection>
                    <AdvancedSection title={t("app.admin.dashboard.operations.center.logs.title")} defaultOpen>
                        <OperationLogsPanel />
                    </AdvancedSection>
                    <AdvancedSection title={"app.admin.dashboard.operations.center.page.k428237fe"} defaultOpen>
                        <AuditLogsPanel />
                    </AdvancedSection>
                </TabsContent>
            </Tabs>
        </AdminPageShell>;
}
