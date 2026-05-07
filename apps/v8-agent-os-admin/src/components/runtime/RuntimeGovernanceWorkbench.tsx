"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Eye, RefreshCw, Route, Save, Settings2, SlidersHorizontal } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useT } from "@/components/providers/LocaleProvider";
import { useToast } from "@/components/ui/use-toast";
import { ApprovalRecord, RunRecord, RUN_LABELS, formatWhen } from "@/components/runtime/use-runtime-ops";
import { CORE_RUNTIME_KINDS, getRuntimeControlHref, isLockedRuntimeKind } from "@/lib/runtime-admin";
import { ir, tg, ti } from "@/i18n/admin-legacy";
type RuntimePolicy = {
  enabled?: boolean;
  auto_route?: boolean;
  expose_direct_tools?: boolean;
  priority?: number;
  notes?: string;
};
type RuntimeCapability = {
  key: string;
  label: string;
  risk_level?: string;
};
type RuntimeDescriptor = {
  kind: string;
  displayName: string;
  summary?: string;
  responsibilities?: string[];
  promptHints?: string[];
  visibility?: string;
  capabilities?: RuntimeCapability[];
  metadata?: Record<string, unknown>;
  policy: RuntimePolicy;
};
type CapabilityRecommendation = {
  kind: string;
  displayName: string;
  score: number;
  matchedKeywords?: string[];
  matchedSignals?: string[];
};
type CapabilitySnapshot = {
  count: number;
  recommendations?: CapabilityRecommendation[];
  runtimes?: RuntimeDescriptor[];
};
type RuntimePresetId = "balanced" | "conservative" | "debug";
type SessionWorkflowView = {
  status?: string;
  recoverable?: boolean;
  ownerRuntime?: string;
  currentStepTitle?: string;
  currentStepStatus?: string;
  updatedAt?: string;
};
type SessionSummary = {
  id: string;
  title?: string;
  source?: string;
  workflow?: SessionWorkflowView | null;
  approvals?: ApprovalRecord[];
  recoverableView?: {
    recoverable?: boolean;
    canResume?: boolean;
    canRetry?: boolean;
    workflowStatus?: string;
  } | null;
  summary?: {
    previewExcerpt?: string;
    workflowStatus?: string;
  } | null;
};
type SessionDetail = {
  id: string;
  source?: string;
  workflow?: SessionWorkflowView | null;
  workflowProjection?: Record<string, unknown> | null;
  runtimeTimeline?: Array<Record<string, unknown>>;
  approvals?: ApprovalRecord[];
  controls?: {
    canResume?: boolean;
    canRetry?: boolean;
    canInterrupt?: boolean;
  } | null;
  recoverable?: {
    recoverable?: boolean;
    workflowStatus?: string;
  } | null;
  summary?: {
    previewExcerpt?: string;
    workflowStatus?: string;
  } | null;
  messages?: Array<{
    id?: string;
    role?: string;
    content?: string;
    createdAt?: string;
  }>;
};
type RuntimeLiveStats = {
  kind: string;
  totalRuns: number;
  activeRuns: number;
  failedRuns: number;
  pendingApprovals: number;
  recoverableSessions: number;
};
type MemoryAuditLog = {
  id?: string;
  source_type?: string;
  action?: string;
  status?: string;
  details?: string;
  timestamp?: string;
};
type MemoryDashboard = {
  extractions?: {
    summary?: Record<string, number>;
  };
  maintenance?: {
    summary?: Record<string, number>;
  };
  workflows?: Record<string, unknown>;
};
const PRESET_LABELS: Record<RuntimePresetId, {
  title: string;
  description: string;
}> = {
  balanced: {
    title: "components.runtime.RuntimeGovernanceWorkbench.k0590e788",
    description: "components.runtime.RuntimeGovernanceWorkbench.k4a43ff73"
  },
  conservative: {
    title: "components.runtime.RuntimeGovernanceWorkbench.k1de80f3c",
    description: "components.runtime.RuntimeGovernanceWorkbench.k7b94c86c"
  },
  debug: {
    title: "components.runtime.RuntimeGovernanceWorkbench.ka3d1efbb",
    description: "components.runtime.RuntimeGovernanceWorkbench.kcd231fad"
  }
};
const NONCORE_RUNTIME_KINDS = ["plugin_host", "computer_use", "rpa"] as const;
function normalizePolicy(policy?: RuntimePolicy): Required<RuntimePolicy> {
  return {
    enabled: policy?.enabled ?? true,
    auto_route: policy?.auto_route ?? true,
    expose_direct_tools: policy?.expose_direct_tools ?? true,
    priority: typeof policy?.priority === "number" ? policy.priority : 100,
    notes: policy?.notes ?? ""
  };
}
function managedToolSummary(runtime: RuntimeDescriptor) {
  const metadata = runtime.metadata || {};
  const exact = Array.isArray(metadata.managedToolNames) ? metadata.managedToolNames.map(String) : [];
  const prefixes = Array.isArray(metadata.managedToolPrefixes) ? metadata.managedToolPrefixes.map(String) : [];
  return {
    exact,
    prefixes,
    hasManaged: exact.length > 0 || prefixes.length > 0
  };
}
function buildPresetPolicy(runtime: RuntimeDescriptor, preset: RuntimePresetId): Required<RuntimePolicy> {
  const current = normalizePolicy(runtime.policy);
  const managed = managedToolSummary(runtime);
  const isPrimary = runtime.visibility === "primary";
  if (preset === "debug") {
    return {
      enabled: true,
      auto_route: true,
      expose_direct_tools: managed.hasManaged ? true : current.expose_direct_tools,
      priority: Math.min(current.priority, 80),
      notes: ir("k1797d539df")
    };
  }
  if (preset === "conservative") {
    return {
      enabled: true,
      auto_route: isPrimary || current.auto_route,
      expose_direct_tools: false,
      priority: isPrimary ? 40 : Math.max(current.priority, 120),
      notes: ir("k481a959b15")
    };
  }
  return {
    enabled: true,
    auto_route: true,
    expose_direct_tools: managed.hasManaged ? false : current.expose_direct_tools,
    priority: isPrimary ? 30 : current.priority,
    notes: ir("k0a2d4271e2")
  };
}
function riskLabel(t: ReturnType<typeof useT>, value?: string) {
  if (value === "high") return ti(t, "k7a83b6c0e3");
  if (value === "medium") return ti(t, "k83a55f1235");
  if (value === "low") return ti(t, "k117a434875");
  return ti(t, "k86f9195e25");
}
function inferRunRuntime(run: RunRecord): string {
  const metadata = run.metadata || {};
  const runtime = typeof metadata.runtime === "string" && metadata.runtime.trim() ? metadata.runtime : null;
  if (runtime) return runtime;
  if (typeof run.run_type === "string" && run.run_type.trim()) return run.run_type;
  return "chat";
}
function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
function asRecordArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item)) : [];
}
function asString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}
function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(item => asString(item)).filter(Boolean) : [];
}
function parseMemoryAuditDetails(value?: string) {
  if (!value) return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}
function compactNumber(value: unknown) {
  const num = Number(value || 0);
  if (!Number.isFinite(num)) return 0;
  return num;
}
function extractPlannerInspector(detail: SessionDetail | null) {
  if (!detail) return null;
  const workflowProjection = asRecord(detail.workflowProjection);
  const steps = asRecordArray(workflowProjection.steps);
  const plannerSteps = steps.filter(step => asString(step.ownerRuntime) === "planner_lane");
  const latestPlannerStep = plannerSteps[plannerSteps.length - 1] || null;
  const plannerInput = asRecord(latestPlannerStep?.input);
  const plannerPlan = asRecord(plannerInput.plannerPlan);
  const runtimeTimeline = asRecordArray(detail.runtimeTimeline);
  const plannerEntries = runtimeTimeline.filter(entry => asString(entry.runtimeId) === "planner_lane");
  const latestPlannerEntry = plannerEntries[plannerEntries.length - 1] || null;
  const latestPlannerMetadata = asRecord(latestPlannerEntry?.metadata);
  const selectedDelegations = asRecordArray(latestPlannerMetadata.selectedDelegations);
  const taskBriefs = asRecordArray(latestPlannerMetadata.taskBriefs).length > 0 ? asRecordArray(latestPlannerMetadata.taskBriefs) : asRecordArray(plannerPlan.taskBriefs);
  const dependencies = asRecordArray(latestPlannerMetadata.dependencies);
  const riskFlags = asStringArray(latestPlannerMetadata.riskFlags).length > 0 ? asStringArray(latestPlannerMetadata.riskFlags) : asStringArray(plannerPlan.riskFlags);
  const executionStrategy = asString(latestPlannerMetadata.executionStrategy) || asString(plannerPlan.executionStrategy);
  const planSummary = asString(latestPlannerMetadata.planSummary) || asString(plannerPlan.planSummary);
  const planId = asString(latestPlannerMetadata.planId) || asString(plannerPlan.planId);
  const globalAcceptanceContract = asString(latestPlannerMetadata.globalAcceptanceContract) || asString(plannerPlan.globalAcceptanceContract);
  const traceRef = asRecord(latestPlannerMetadata.traceRef);
  if (!planId && !planSummary && taskBriefs.length === 0 && selectedDelegations.length === 0) {
    return null;
  }
  return {
    planId,
    planSummary,
    executionStrategy,
    globalAcceptanceContract,
    taskBriefs,
    selectedDelegations,
    riskFlags,
    dependencies,
    traceRef,
    stepTitle: asString(latestPlannerStep?.title)
  };
}
function isRecoverableSession(session: SessionSummary) {
  return Boolean(session.workflow?.recoverable || session.recoverableView?.recoverable);
}
type RuntimeGovernanceWorkbenchProps = {
  embedded?: boolean;
};
export function RuntimeGovernanceWorkbench({
  embedded = false
}: RuntimeGovernanceWorkbenchProps) {
  const {
    toast
  } = useToast();
  const t = useT();
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [query, setQuery] = useState(tg(t, "f8c5ad8f"));
  const [snapshot, setSnapshot] = useState<CapabilitySnapshot>({
    count: 0,
    recommendations: [],
    runtimes: []
  });
  const [draftPolicies, setDraftPolicies] = useState<Record<string, Required<RuntimePolicy>>>({});
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [activeRuntimeKind, setActiveRuntimeKind] = useState<string | null>(null);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [selectedSessionDetail, setSelectedSessionDetail] = useState<SessionDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [memoryDashboard, setMemoryDashboard] = useState<MemoryDashboard | null>(null);
  const [memoryAuditLogs, setMemoryAuditLogs] = useState<MemoryAuditLog[]>([]);
  const runtimes = useMemo(() => snapshot.runtimes || [], [snapshot.runtimes]);
  const recommendations = useMemo(() => snapshot.recommendations || [], [snapshot.recommendations]);
  const runtimeNameMap = useMemo(() => new Map(runtimes.map(item => [item.kind, item.displayName] as const)), [runtimes]);
  const summary = useMemo(() => ({
    enabled: runtimes.filter(item => normalizePolicy(item.policy).enabled).length,
    autoRoute: runtimes.filter(item => normalizePolicy(item.policy).auto_route).length,
    directTools: runtimes.filter(item => normalizePolicy(item.policy).expose_direct_tools).length
  }), [runtimes]);
  const groupedRuntimes = useMemo(() => {
    const sections: Array<{
      key: string;
      title: string;
      description: string;
      runtimeKinds: string[];
    }> = [{
      key: "core",
      title: "components.runtime.RuntimeGovernanceWorkbench.k1bb93e07",
      description: "components.runtime.RuntimeGovernanceWorkbench.kecf19787",
      runtimeKinds: [...CORE_RUNTIME_KINDS]
    }, {
      key: "noncore",
      title: "components.runtime.RuntimeGovernanceWorkbench.k29368dac",
      description: "components.runtime.RuntimeGovernanceWorkbench.k875af2ed",
      runtimeKinds: [...NONCORE_RUNTIME_KINDS]
    }];
    const knownKinds = new Set(sections.flatMap(section => section.runtimeKinds));
    const overflow = runtimes.filter(item => !knownKinds.has(item.kind));
    if (overflow.length) {
      sections.push({
        key: "other",
        title: "components.runtime.RuntimeGovernanceWorkbench.k2f02da37",
        description: "components.runtime.RuntimeGovernanceWorkbench.k69ebb082",
        runtimeKinds: overflow.map(item => item.kind)
      });
    }
    return sections.map(section => ({
      ...section,
      runtimes: section.runtimeKinds.map(kind => runtimes.find(item => item.kind === kind)).filter((item): item is RuntimeDescriptor => Boolean(item))
    }));
  }, [runtimes]);
  const runMap = useMemo(() => new Map(runs.map(run => [run.id, run] as const)), [runs]);
  const sessionMap = useMemo(() => new Map(sessions.map(session => [session.id, session] as const)), [sessions]);
  const observability = useMemo(() => {
    const stats = new Map<string, RuntimeLiveStats>();
    for (const runtime of runtimes) {
      stats.set(runtime.kind, {
        kind: runtime.kind,
        totalRuns: 0,
        activeRuns: 0,
        failedRuns: 0,
        pendingApprovals: 0,
        recoverableSessions: 0
      });
    }
    for (const run of runs) {
      const kind = inferRunRuntime(run);
      const bucket = stats.get(kind) || {
        kind,
        totalRuns: 0,
        activeRuns: 0,
        failedRuns: 0,
        pendingApprovals: 0,
        recoverableSessions: 0
      };
      bucket.totalRuns += 1;
      if (["running", "waiting_approval", "waiting_input", "paused"].includes(run.status || "")) bucket.activeRuns += 1;
      if (["failed", "cancelled"].includes(run.status || "")) bucket.failedRuns += 1;
      stats.set(kind, bucket);
    }
    for (const approval of approvals) {
      let kind = "chat";
      if (approval.run_id && runMap.has(approval.run_id)) {
        kind = inferRunRuntime(runMap.get(approval.run_id)!);
      } else if (approval.session_id && sessionMap.has(approval.session_id)) {
        kind = sessionMap.get(approval.session_id)?.workflow?.ownerRuntime || "chat";
      } else if ((approval.approval_kind || "").startsWith("rpa")) {
        kind = "rpa";
      }
      const bucket = stats.get(kind) || {
        kind,
        totalRuns: 0,
        activeRuns: 0,
        failedRuns: 0,
        pendingApprovals: 0,
        recoverableSessions: 0
      };
      bucket.pendingApprovals += 1;
      stats.set(kind, bucket);
    }
    for (const session of sessions) {
      const kind = session.workflow?.ownerRuntime || "chat";
      if (!isRecoverableSession(session)) continue;
      const bucket = stats.get(kind) || {
        kind,
        totalRuns: 0,
        activeRuns: 0,
        failedRuns: 0,
        pendingApprovals: 0,
        recoverableSessions: 0
      };
      bucket.recoverableSessions += 1;
      stats.set(kind, bucket);
    }
    return Array.from(stats.values()).sort((a, b) => a.kind.localeCompare(b.kind));
  }, [approvals, runMap, runtimes, runs, sessionMap, sessions]);
  const failedRuns = useMemo(() => runs.filter(run => ["failed", "cancelled"].includes(run.status || "")), [runs]);
  const recoverableSessions = useMemo(() => sessions.filter(session => isRecoverableSession(session)), [sessions]);
  const filteredFailedRuns = useMemo(() => activeRuntimeKind ? failedRuns.filter(run => inferRunRuntime(run) === activeRuntimeKind) : failedRuns, [activeRuntimeKind, failedRuns]);
  const filteredApprovals = useMemo(() => {
    if (!activeRuntimeKind) return approvals;
    return approvals.filter(approval => {
      if (approval.run_id && runMap.has(approval.run_id)) {
        return inferRunRuntime(runMap.get(approval.run_id)!) === activeRuntimeKind;
      }
      if (approval.session_id && sessionMap.has(approval.session_id)) {
        return (sessionMap.get(approval.session_id)?.workflow?.ownerRuntime || "chat") === activeRuntimeKind;
      }
      return (approval.approval_kind || "").startsWith(activeRuntimeKind);
    });
  }, [activeRuntimeKind, approvals, runMap, sessionMap]);
  const filteredRecoverableSessions = useMemo(() => activeRuntimeKind ? recoverableSessions.filter(session => (session.workflow?.ownerRuntime || "chat") === activeRuntimeKind) : recoverableSessions, [activeRuntimeKind, recoverableSessions]);
  const plannerInspector = useMemo(() => extractPlannerInspector(selectedSessionDetail), [selectedSessionDetail]);
  const memoryExtractionSummary = memoryDashboard?.extractions?.summary || {};
  const memoryMaintenanceSummary = memoryDashboard?.maintenance?.summary || {};
  const loadSnapshot = useCallback(async (nextQuery?: string) => {
    const suffix = nextQuery?.trim() ? `?query=${encodeURIComponent(nextQuery.trim())}` : "";
    const res = await fetch(`/api/runtime-capabilities${suffix}`, {
      cache: "no-store"
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data?.detail || data?.error || t("components.runtime.RuntimeGovernanceWorkbench.k4a84157b"));
    }
    const payload = data as CapabilitySnapshot;
    setSnapshot(payload);
    setDraftPolicies(current => {
      const next = {
        ...current
      };
      for (const runtime of payload.runtimes || []) {
        next[runtime.kind] = current[runtime.kind] || normalizePolicy(runtime.policy);
      }
      return next;
    });
  }, []);
  const loadObservability = useCallback(async () => {
    const [runsRes, approvalsRes, sessionsRes, memoryRes, auditRes] = await Promise.all([fetch("/api/runs?limit=40", {
      cache: "no-store"
    }), fetch("/api/approvals?status=pending", {
      cache: "no-store"
    }), fetch("/api/conversations", {
      cache: "no-store"
    }), fetch("/api/memory/dashboard", {
      cache: "no-store"
    }), fetch("/api/audit/logs?source_type=MEMORY&limit=50", {
      cache: "no-store"
    })]);
    const runsData = runsRes.ok ? await runsRes.json().catch(() => ({})) : {};
    const approvalsData = approvalsRes.ok ? await approvalsRes.json().catch(() => ({})) : {};
    const sessionsData = sessionsRes.ok ? await sessionsRes.json().catch(() => []) : [];
    const memoryData = memoryRes.ok ? await memoryRes.json().catch(() => ({})) : {};
    const auditData = auditRes.ok ? await auditRes.json().catch(() => ({})) : {};
    setRuns(Array.isArray(runsData?.runs) ? runsData.runs : []);
    setApprovals(Array.isArray(approvalsData?.approvals) ? approvalsData.approvals : []);
    setSessions(Array.isArray(sessionsData) ? sessionsData : []);
    setMemoryDashboard(memoryData || null);
    setMemoryAuditLogs(Array.isArray(auditData?.logs) ? auditData.logs : []);
  }, []);
  const loadAll = useCallback(async (nextQuery?: string) => {
    await Promise.all([loadSnapshot(nextQuery), loadObservability()]);
  }, [loadObservability, loadSnapshot]);
  const inspectSession = useCallback(async (sessionId: string) => {
    setSelectedSessionId(sessionId);
    setDetailLoading(true);
    try {
      const res = await fetch(`/api/conversations/${encodeURIComponent(sessionId)}`, {
        cache: "no-store"
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data?.detail || data?.error || tg(t, "e947d30a"));
      }
      setSelectedSessionDetail(data as SessionDetail);
    } catch (error) {
      toast({
        variant: "destructive",
        title: "components.runtime.RuntimeGovernanceWorkbench.kb6ea6822",
        description: error instanceof Error ? error.message : t("app.admin.dashboard.subagents.page.externalWorkers.unknownError")
      });
    } finally {
      setDetailLoading(false);
    }
  }, [toast]);
  useEffect(() => {
    void (async () => {
      try {
        await loadAll(tg(t, "f8c5ad8f"));
      } catch (error) {
        toast({
          variant: "destructive",
          title: "components.runtime.RuntimeGovernanceWorkbench.k4a84157b",
          description: error instanceof Error ? error.message : t("app.admin.dashboard.subagents.page.externalWorkers.unknownError")
        });
      } finally {
        setLoading(false);
      }
    })();
  }, [loadAll, toast]);
  const patchPolicy = (kind: string, patch: Partial<Required<RuntimePolicy>>) => {
    setDraftPolicies(current => ({
      ...current,
      [kind]: {
        ...(current[kind] || normalizePolicy(runtimes.find(item => item.kind === kind)?.policy)),
        ...patch
      }
    }));
  };
  const handleSearch = async () => {
    setBusyKey("query");
    try {
      await loadSnapshot(query);
    } catch (error) {
      toast({
        variant: "destructive",
        title: "components.runtime.RuntimeGovernanceWorkbench.ke7a17970",
        description: error instanceof Error ? error.message : t("app.admin.dashboard.subagents.page.externalWorkers.unknownError")
      });
    } finally {
      setBusyKey(null);
    }
  };
  const handleRefresh = async () => {
    setLoading(true);
    try {
      await loadAll(query);
      if (selectedSessionId) {
        await inspectSession(selectedSessionId);
      }
    } catch (error) {
      toast({
        variant: "destructive",
        title: "components.runtime.RuntimeGovernanceWorkbench.kaeec4304",
        description: error instanceof Error ? error.message : t("app.admin.dashboard.subagents.page.externalWorkers.unknownError")
      });
    } finally {
      setLoading(false);
    }
  };
  const savePolicy = async (kind: string) => {
    const payload = draftPolicies[kind];
    if (!payload) return;
    setBusyKey(`save:${kind}`);
    try {
      const res = await fetch(`/api/runtime-capabilities/${encodeURIComponent(kind)}/policy`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          enabled: payload.enabled,
          autoRoute: payload.auto_route,
          exposeDirectTools: payload.expose_direct_tools,
          priority: Number(payload.priority || 100),
          notes: payload.notes
        })
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.detail || data?.error || t("components.runtime.RuntimeStabilityPanel.k12769ce1"));
      toast({
        title: "components.runtime.RuntimeGovernanceWorkbench.k93e608a9",
        description: tg(t, "287cdbd3", {
          value1: kind
        })
      });
      await loadAll(query);
    } catch (error) {
      toast({
        variant: "destructive",
        title: "components.runtime.RuntimeGovernanceWorkbench.k12769ce1",
        description: error instanceof Error ? error.message : t("app.admin.dashboard.subagents.page.externalWorkers.unknownError")
      });
    } finally {
      setBusyKey(null);
    }
  };
  const resetPolicy = async (kind: string) => {
    setBusyKey(`reset:${kind}`);
    try {
      const res = await fetch(`/api/runtime-capabilities/${encodeURIComponent(kind)}/policy`, {
        method: "DELETE"
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.detail || data?.error || t("components.runtime.RuntimeGovernanceWorkbench.kbb48c954"));
      toast({
        title: "components.runtime.RuntimeGovernanceWorkbench.kd5cd9463",
        description: tg(t, "df562ea8", {
          value1: kind
        })
      });
      await loadAll(query);
    } catch (error) {
      toast({
        variant: "destructive",
        title: "components.runtime.RuntimeGovernanceWorkbench.kbb48c954",
        description: error instanceof Error ? error.message : t("app.admin.dashboard.subagents.page.externalWorkers.unknownError")
      });
    } finally {
      setBusyKey(null);
    }
  };
  const applyPreset = async (preset: RuntimePresetId) => {
    setBusyKey(`preset:${preset}`);
    try {
      await Promise.all(runtimes.map(async runtime => {
        const policy = buildPresetPolicy(runtime, preset);
        const res = await fetch(`/api/runtime-capabilities/${encodeURIComponent(runtime.kind)}/policy`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json"
          },
          body: JSON.stringify({
            enabled: policy.enabled,
            autoRoute: policy.auto_route,
            exposeDirectTools: policy.expose_direct_tools,
            priority: Number(policy.priority || 100),
            notes: policy.notes
          })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data?.detail || data?.error || tg(t, "34d44cc0", {
          value1: runtime.kind
        }));
      }));
      toast({
        title: "components.runtime.RuntimeGovernanceWorkbench.kbd7964c6",
        description: PRESET_LABELS[preset].title
      });
      await loadAll(query);
    } catch (error) {
      toast({
        variant: "destructive",
        title: "components.runtime.RuntimeGovernanceWorkbench.k5c114cf0",
        description: error instanceof Error ? error.message : t("app.admin.dashboard.subagents.page.externalWorkers.unknownError")
      });
    } finally {
      setBusyKey(null);
    }
  };
  return <div className="space-y-6">
            <div className="flex flex-wrap items-center justify-between gap-4">
                {embedded ? <div className="text-sm text-muted-foreground">
                        {tg(t, "89a6bc19")}
                    </div> : <div>
                        <h1 className="text-3xl font-bold tracking-tight">{tg(t, "d1dd5c23")}</h1>
                        <p className="mt-1 text-muted-foreground">{tg(t, "b9baa9b6")}</p>
                    </div>}
                <div className="flex flex-wrap gap-2">
                    {activeRuntimeKind ? <Button variant="outline" onClick={() => setActiveRuntimeKind(null)}>
                            {tg(t, "52b1fba0")}
                        </Button> : null}
                    <Button variant="outline" onClick={() => void handleRefresh()} disabled={loading}>
                        <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
                        {tg(t, "0f6f6484")}
                    </Button>
                </div>
            </div>

            <div className="grid gap-4 md:grid-cols-4">
                <Card className="border-border/60"><CardHeader className="pb-2"><CardDescription>{tg(t, "32f538d7")}</CardDescription><CardTitle className="text-3xl">{snapshot.count || 0}</CardTitle></CardHeader><CardContent className="text-xs text-muted-foreground">{tg(t, "6d95b1d5")}</CardContent></Card>
                <Card className="border-border/60"><CardHeader className="pb-2"><CardDescription>{t("app.admin.dashboard.engineeringLane.enabledState")}</CardDescription><CardTitle className="text-3xl">{summary.enabled}</CardTitle></CardHeader><CardContent className="text-xs text-muted-foreground">{tg(t, "0c353b83")}</CardContent></Card>
                <Card className="border-border/60"><CardHeader className="pb-2"><CardDescription>{tg(t, "57fce0c4")}</CardDescription><CardTitle className="text-3xl">{approvals.length}</CardTitle></CardHeader><CardContent className="text-xs text-muted-foreground">{tg(t, "1bae8500")}</CardContent></Card>
                <Card className="border-border/60"><CardHeader className="pb-2"><CardDescription>{tg(t, "0801745d")}</CardDescription><CardTitle className="text-3xl">{recoverableSessions.length}</CardTitle></CardHeader><CardContent className="text-xs text-muted-foreground">{tg(t, "b6eae25e")}</CardContent></Card>
            </div>

            <Card className="border-border/60">
                <CardHeader>
                    <CardTitle className="text-lg">{t("components.runtime.RuntimeGovernanceWorkbench.memoryObservabilityTitle")}</CardTitle>
                    <CardDescription>{t("components.runtime.RuntimeGovernanceWorkbench.memoryObservabilityDescription")}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="grid gap-3 md:grid-cols-4">
                        {[[t("components.runtime.RuntimeGovernanceWorkbench.memoryMetricCompleted"), memoryExtractionSummary.completed], [t("components.runtime.RuntimeGovernanceWorkbench.memoryMetricSkipped"), memoryExtractionSummary.skipped], [t("components.runtime.RuntimeGovernanceWorkbench.memoryMetricPersisted"), memoryExtractionSummary.persisted], [t("components.runtime.RuntimeGovernanceWorkbench.memoryMetricBackfilled"), memoryMaintenanceSummary.summaryBackfilled]].map(([label, value]) => <div key={String(label)} className="rounded-xl border bg-muted/20 p-3">
                                <div className="text-xs text-muted-foreground">{label}</div>
                                <div className="mt-2 text-2xl font-semibold">{compactNumber(value)}</div>
                            </div>)}
                    </div>
                    <div className="max-h-72 space-y-2 overflow-y-auto pr-1">
                        {memoryAuditLogs.length ? memoryAuditLogs.slice(0, 12).map(log => {
            const details = parseMemoryAuditDetails(log.details);
            return <div key={log.id || `${log.timestamp}-${log.action}`} className="rounded-xl border bg-background p-3">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <Badge variant="outline">{String(details.action || log.action || "memory")}</Badge>
                                        <Badge variant={String(log.status || "").toUpperCase() === "SUCCESS" ? "default" : "secondary"}>{log.status || "INFO"}</Badge>
                                        {details.callsLlm === true ? <Badge>{t("components.runtime.RuntimeGovernanceWorkbench.memoryCallsLlm")}</Badge> : <Badge variant="secondary">{t("components.runtime.RuntimeGovernanceWorkbench.memoryNoLlm")}</Badge>}
                                        <span className="text-xs text-muted-foreground">{formatWhen(log.timestamp)}</span>
                                    </div>
                                    <div className="mt-2 grid gap-x-4 gap-y-1 text-xs text-muted-foreground md:grid-cols-4">
                                        <span>{t("components.runtime.RuntimeGovernanceWorkbench.memoryTrigger")}: {String(details.trigger || "—")}</span>
                                        <span>{t("components.runtime.RuntimeGovernanceWorkbench.memoryInputChars")}: {String(details.inputCharEstimate ?? "—")}</span>
                                        <span>{t("components.runtime.RuntimeGovernanceWorkbench.memoryWritten")}: {String(details.persistedKnowledgeCount ?? details.chunkCount ?? details.summaryBackfilledCount ?? "—")}</span>
                                        <span>{t("components.runtime.RuntimeGovernanceWorkbench.memorySkipReason")}: {String(details.skipReason || details.rejectReason || "—")}</span>
                                    </div>
                                </div>;
          }) : <div className="rounded-xl border border-dashed bg-muted/20 p-6 text-sm text-muted-foreground">
                                {t("components.runtime.RuntimeGovernanceWorkbench.memoryNoAuditLogs")}
                            </div>}
                    </div>
                </CardContent>
            </Card>

            <div className="grid gap-6 xl:grid-cols-[1.15fr_0.85fr]">
                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-lg"><Route className="h-5 w-5 text-primary" />{tg(t, "b80577b9")}</CardTitle>
                        <CardDescription>{tg(t, "fde33445")}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="flex flex-col gap-3 md:flex-row">
                            <Input value={query} onChange={event => setQuery(event.target.value)} placeholder="components.runtime.RuntimeGovernanceWorkbench.kab2a9ac2" />
                            <Button onClick={() => void handleSearch()} disabled={busyKey === "query"}>{tg(t, "189d876a")}</Button>
                        </div>
                        <div className="space-y-3">
                            {recommendations.length === 0 ? <div className="rounded-xl border border-dashed p-4 text-sm text-muted-foreground">{tg(t, "5e356906")}</div> : recommendations.map(item => <div key={item.kind} className="rounded-2xl border border-border/60 p-4">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <div className="text-sm font-medium">{item.displayName}</div>
                                        <Badge variant="outline">{item.kind}</Badge>
                                        <Badge>{item.score.toFixed(1)}</Badge>
                                        <Button variant="outline" size="sm" onClick={() => setActiveRuntimeKind(item.kind)}>
                                            {tg(t, "fe2da584")}
                                        </Button>
                                    </div>
                                    <div className="mt-2 text-xs text-muted-foreground">{tg(t, "d2f4d8e7")}{item.matchedKeywords?.length ? item.matchedKeywords.join("、") : t("app.admin.dashboard.engineeringLane.none")} {tg(t, "125a32b5")}{item.matchedSignals?.length ? item.matchedSignals.join("、") : t("app.admin.dashboard.engineeringLane.none")}</div>
                                </div>)}
                        </div>
                    </CardContent>
                </Card>

                <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-lg"><SlidersHorizontal className="h-5 w-5 text-primary" />{tg(t, "68b8eea2")}</CardTitle>
                        <CardDescription>{tg(t, "b55e7b2c")}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        {(Object.keys(PRESET_LABELS) as RuntimePresetId[]).map(preset => <div key={preset} className="rounded-2xl border border-border/60 p-4">
                                <div className="text-sm font-medium">{PRESET_LABELS[preset].title}</div>
                                <div className="mt-1 text-xs text-muted-foreground">{PRESET_LABELS[preset].description}</div>
                                <Button className="mt-3" variant="outline" onClick={() => void applyPreset(preset)} disabled={busyKey === `preset:${preset}`}>
                                    {tg(t, "d6988b11")}
                                </Button>
                            </div>)}
                    </CardContent>
                </Card>
            </div>

            <Card className="border-border/60">
                    <CardHeader>
                        <CardTitle className="text-lg">{tg(t, "a90cbb36")}</CardTitle>
                        <CardDescription>{tg(t, "8b3b5a7e")}</CardDescription>
                </CardHeader>
                <CardContent>
                    <ScrollArea className="h-[640px] pr-4">
                        <div className="space-y-6">
                            {groupedRuntimes.map(section => <div key={section.key} className="space-y-4">
                                    <div className="rounded-2xl border border-border/60 bg-muted/30 px-4 py-3">
                                        <div className="text-sm font-semibold text-slate-900">{section.title}</div>
                                        <div className="mt-1 text-xs leading-5 text-muted-foreground">{section.description}</div>
                                    </div>
                                    <div className="space-y-4">
                                        {section.runtimes.map(runtime => {
                  const policy = draftPolicies[runtime.kind] || normalizePolicy(runtime.policy);
                  const managed = managedToolSummary(runtime);
                  const live = observability.find(item => item.kind === runtime.kind);
                  const highlighted = activeRuntimeKind === runtime.kind;
                  const isCoreRuntime = CORE_RUNTIME_KINDS.includes(runtime.kind as (typeof CORE_RUNTIME_KINDS)[number]);
                  const isLockedRuntime = isLockedRuntimeKind(runtime.kind);
                  const controlHref = getRuntimeControlHref(runtime.kind);
                  return <div key={runtime.kind} className={`rounded-2xl border p-4 ${highlighted ? "border-primary/60 bg-primary/5" : "border-border/60"}`}>
                                                    <div className="flex flex-wrap items-start justify-between gap-4">
                                                        <div>
                                                            <div className="flex flex-wrap items-center gap-2">
                                                                <div className="text-base font-semibold">{runtime.displayName}</div>
                                                                <Badge variant="outline">{runtime.kind}</Badge>
                                                                <Badge variant={isCoreRuntime ? "default" : "secondary"}>{tg(t, "abc9e6a5")}</Badge>
                                                                <Badge variant="secondary">{runtime.visibility || "internal"}</Badge>
                                                                {policy.expose_direct_tools ? <Badge>direct tools</Badge> : <Badge variant="secondary">runtime-only</Badge>}
                                                                <Button variant="outline" size="sm" onClick={() => setActiveRuntimeKind(highlighted ? null : runtime.kind)}>
                                                                    {tg(t, "ee2e9ccf")}
                                                                </Button>
                                                                {controlHref ? <Button variant="outline" size="sm" asChild>
                                                                        <Link href={controlHref}>{tg(t, "149cf40f")}</Link>
                                                                    </Button> : null}
                                                            </div>
                                                            <div className="mt-2 text-sm text-muted-foreground">{runtime.summary || tg(t, "fe61b3df")}</div>
                                                            <div className="mt-2 flex flex-wrap gap-2">
                                                                {runtime.capabilities?.slice(0, 6).map(item => <Badge key={`${runtime.kind}:${item.key}`} variant="outline">{item.label} · {riskLabel(t, item.risk_level)}</Badge>)}
                                                            </div>
                                                            <div className="mt-3 flex flex-wrap gap-2 text-xs">
                                                                <Badge variant="secondary">{tg(t, "609a49cc")}{live?.totalRuns ?? 0}</Badge>
                                                                <Badge variant="secondary">{tg(t, "bba105af")}{live?.activeRuns ?? 0}</Badge>
                                                                <Badge variant="secondary">{tg(t, "8703d7ba")}{live?.failedRuns ?? 0}</Badge>
                                                                <Badge variant="secondary">{tg(t, "7e1beb07")}{live?.pendingApprovals ?? 0}</Badge>
                                                                <Badge variant="secondary">{tg(t, "3516c469")}{live?.recoverableSessions ?? 0}</Badge>
                                                            </div>
                                                        </div>
                                                        <div className="flex gap-2">
                                                            <Button variant="outline" onClick={() => void resetPolicy(runtime.kind)} disabled={busyKey === `reset:${runtime.kind}` || busyKey === `save:${runtime.kind}`}>{tg(t, "616090e7")}</Button>
                                                            <Button onClick={() => void savePolicy(runtime.kind)} disabled={busyKey === `save:${runtime.kind}` || busyKey === `reset:${runtime.kind}`}><Save className="mr-2 h-4 w-4" />{t("app.admin.dashboard.creativeMedia.saving")}</Button>
                                                        </div>
                                                    </div>
                                                    <div className="mt-4 grid gap-4 md:grid-cols-3">
                                                        <div className="rounded-xl border border-border/50 p-3"><div className="flex items-center justify-between"><Label htmlFor={`${runtime.kind}-enabled`}>{t("app.admin.dashboard.creativeMedia.tableEnabled")}</Label><Switch id={`${runtime.kind}-enabled`} checked={policy.enabled} onCheckedChange={checked => patchPolicy(runtime.kind, {
                            enabled: checked
                          })} disabled={isLockedRuntime} /></div><p className="mt-2 text-xs text-muted-foreground">{isLockedRuntime ? tg(t, "e4d0d527") : tg(t, "7abc4e39")}</p></div>
                                                        <div className="rounded-xl border border-border/50 p-3"><div className="flex items-center justify-between"><Label htmlFor={`${runtime.kind}-auto`}>{tg(t, "8b713b45")}</Label><Switch id={`${runtime.kind}-auto`} checked={policy.auto_route} onCheckedChange={checked => patchPolicy(runtime.kind, {
                            auto_route: checked
                          })} /></div><p className="mt-2 text-xs text-muted-foreground">{tg(t, "c70e191c")}</p></div>
                                                        <div className="rounded-xl border border-border/50 p-3"><div className="flex items-center justify-between"><Label htmlFor={`${runtime.kind}-direct`}>{tg(t, "d3374d07")}</Label><Switch id={`${runtime.kind}-direct`} checked={policy.expose_direct_tools} onCheckedChange={checked => patchPolicy(runtime.kind, {
                            expose_direct_tools: checked
                          })} /></div><p className="mt-2 text-xs text-muted-foreground">{tg(t, "f4a0b48a")}</p></div>
                                                    </div>
                                                    <div className="mt-4 grid gap-4 xl:grid-cols-[0.38fr_0.62fr]">
                                                        <div className="rounded-xl border border-border/50 p-3">
                                                            <Label htmlFor={`${runtime.kind}-priority`}>Priority</Label>
                                                            <Input id={`${runtime.kind}-priority`} className="mt-2" type="number" value={policy.priority} onChange={event => patchPolicy(runtime.kind, {
                          priority: Number(event.target.value || 100)
                        })} />
                                                            <div className="mt-2 text-xs text-muted-foreground">managed exact：{managed.exact.length ? managed.exact.join("、") : t("app.admin.dashboard.engineeringLane.none")}</div>
                                                            <div className="mt-1 text-xs text-muted-foreground">managed prefixes：{managed.prefixes.length ? managed.prefixes.join("、") : t("app.admin.dashboard.engineeringLane.none")}</div>
                                                        </div>
                                                        <div>
                                                            <Label htmlFor={`${runtime.kind}-notes`}>{tg(t, "e0361480")}</Label>
                                                            <Textarea id={`${runtime.kind}-notes`} className="mt-2 min-h-[110px]" value={policy.notes} onChange={event => patchPolicy(runtime.kind, {
                          notes: event.target.value
                        })} placeholder="components.runtime.RuntimeGovernanceWorkbench.kaba1447c" />
                                                            {runtime.promptHints?.length ? <div className="mt-2 rounded-lg bg-muted/40 p-3 text-xs text-muted-foreground"><Settings2 className="mr-2 inline h-3 w-3" />{tg(t, "b8471587")}{runtime.promptHints.join("；")}</div> : null}
                                                        </div>
                                                    </div>
                                                </div>;
                })}
                                    </div>
                                </div>)}
                        </div>
                    </ScrollArea>
                </CardContent>
            </Card>

            <div className="grid gap-6 xl:grid-cols-2">
                <Card className="border-border/60 xl:col-span-2">
                    <CardHeader>
                        <CardTitle className="text-lg">{tg(t, "7de49f33")}</CardTitle>
                        <CardDescription>
                            {activeRuntimeKind ? tg(t, "c7d51e88", {
              value1: runtimeNameMap.get(activeRuntimeKind) || activeRuntimeKind
            }) : tg(t, "8c44493c")}
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="grid gap-6 xl:grid-cols-3">
                        <div className="space-y-3">
                            <div className="text-sm font-medium">{tg(t, "728a21b9")}</div>
                            {filteredFailedRuns.length === 0 ? <div className="rounded-xl border border-dashed p-4 text-sm text-muted-foreground">{tg(t, "3504228b")}</div> : filteredFailedRuns.slice(0, 6).map(run => <div key={run.id} className="rounded-2xl border border-border/60 p-4">
                                        <div className="flex flex-wrap items-center gap-2">
                                            <Badge>{t(RUN_LABELS[run.status || "failed"] || run.status || "failed")}</Badge>
                                            <Badge variant="outline">{runtimeNameMap.get(inferRunRuntime(run)) || inferRunRuntime(run)}</Badge>
                                            {run.trigger_source ? <Badge variant="secondary">{run.trigger_source}</Badge> : null}
                                        </div>
                                        <div className="mt-2 text-xs text-muted-foreground">Run: {run.id}</div>
                                        {run.session_id ? <div className="mt-1 text-xs text-muted-foreground">Session: {run.session_id}</div> : null}
                                        <div className="mt-1 text-xs text-muted-foreground">{tg(t, "32d77333")}{formatWhen(run.started_at || run.created_at)}</div>
                                        {run.session_id ? <Button className="mt-3" variant="outline" size="sm" onClick={() => void inspectSession(run.session_id!)}>
                                                <Eye className="mr-2 h-4 w-4" />
                                                {tg(t, "cc59de60")}
                                            </Button> : null}
                                    </div>)}
                        </div>

                        <div className="space-y-3">
                            <div className="text-sm font-medium">{tg(t, "a3e9f963")}</div>
                            {filteredApprovals.length === 0 ? <div className="rounded-xl border border-dashed p-4 text-sm text-muted-foreground">{tg(t, "3aabdb65")}</div> : filteredApprovals.slice(0, 6).map(approval => {
              const runtimeKind = approval.run_id && runMap.has(approval.run_id) ? inferRunRuntime(runMap.get(approval.run_id)!) : approval.session_id && sessionMap.has(approval.session_id) ? sessionMap.get(approval.session_id)?.workflow?.ownerRuntime || "chat" : (approval.approval_kind || "").startsWith("rpa") ? "rpa" : "chat";
              return <div key={approval.id} className="rounded-2xl border border-border/60 p-4">
                                            <div className="flex flex-wrap items-center gap-2">
                                                <Badge>{approval.approval_kind || "approval"}</Badge>
                                                <Badge variant="outline">{runtimeNameMap.get(runtimeKind) || runtimeKind}</Badge>
                                            </div>
                                            <div className="mt-2 text-xs text-muted-foreground">{approval.request?.question || approval.request?.prompt || tg(t, "7ab8b802")}</div>
                                            {approval.session_id ? <Button className="mt-3" variant="outline" size="sm" onClick={() => void inspectSession(approval.session_id!)}>
                                                    <Eye className="mr-2 h-4 w-4" />
                                                    {tg(t, "cc59de60")}
                                                </Button> : null}
                                        </div>;
            })}
                        </div>

                        <div className="space-y-3">
                            <div className="text-sm font-medium">{tg(t, "0801745d")}</div>
                            {filteredRecoverableSessions.length === 0 ? <div className="rounded-xl border border-dashed p-4 text-sm text-muted-foreground">{tg(t, "c0272102")}</div> : filteredRecoverableSessions.slice(0, 6).map(session => <div key={session.id} className="rounded-2xl border border-border/60 p-4">
                                        <div className="flex flex-wrap items-center gap-2">
                                            <Badge variant="outline">{runtimeNameMap.get(session.workflow?.ownerRuntime || "chat") || session.workflow?.ownerRuntime || "chat"}</Badge>
                                            <Badge>{session.workflow?.status || session.recoverableView?.workflowStatus || "recoverable"}</Badge>
                                        </div>
                                        <div className="mt-2 text-sm font-medium">{session.title || session.id}</div>
                                        <div className="mt-1 text-xs text-muted-foreground">{session.summary?.previewExcerpt || tg(t, "96f0ac0d")}</div>
                                        <Button className="mt-3" variant="outline" size="sm" onClick={() => void inspectSession(session.id)}>
                                            <Eye className="mr-2 h-4 w-4" />
                                            {tg(t, "cc59de60")}
                                        </Button>
                                    </div>)}
                        </div>
                    </CardContent>
                </Card>

                {selectedSessionId ? <Card className="border-border/60 xl:col-span-2">
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2 text-lg"><Eye className="h-5 w-5 text-primary" />{tg(t, "6a2ef7e7")}</CardTitle>
                            <CardDescription>{selectedSessionId}{selectedSessionDetail?.source ? tg(t, "424cef36", {
              value1: selectedSessionDetail.source
            }) : ""}</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            {detailLoading ? <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">{tg(t, "b337421b")}</div> : selectedSessionDetail ? <>
                                    <div className="grid gap-4 md:grid-cols-4">
                                        <div className="rounded-xl border border-border/50 p-3"><div className="text-xs text-muted-foreground">workflow</div><div className="mt-2 text-sm font-medium">{selectedSessionDetail.workflow?.status || selectedSessionDetail.recoverable?.workflowStatus || "unknown"}</div></div>
                                        <div className="rounded-xl border border-border/50 p-3"><div className="text-xs text-muted-foreground">owner runtime</div><div className="mt-2 text-sm font-medium">{runtimeNameMap.get(selectedSessionDetail.workflow?.ownerRuntime || "chat") || selectedSessionDetail.workflow?.ownerRuntime || "chat"}</div></div>
                                        <div className="rounded-xl border border-border/50 p-3"><div className="text-xs text-muted-foreground">recoverable</div><div className="mt-2 text-sm font-medium">{t("components.plugin.host.PluginHostWorkbench.k2ae24b34")}</div></div>
                                        <div className="rounded-xl border border-border/50 p-3"><div className="text-xs text-muted-foreground">approvals</div><div className="mt-2 text-sm font-medium">{selectedSessionDetail.approvals?.length || 0}</div></div>
                                    </div>
                                    {plannerInspector ? <div className="rounded-xl border border-border/50 p-4">
                                            <div className="flex flex-wrap items-center gap-2">
                                                <div className="text-sm font-medium">Planner Inspector</div>
                                                {plannerInspector.executionStrategy ? <Badge variant="secondary">{plannerInspector.executionStrategy}</Badge> : null}
                                                {plannerInspector.planId ? <Badge variant="outline">{plannerInspector.planId}</Badge> : null}
                                            </div>
                                            <div className="mt-2 text-xs text-muted-foreground">
                                                {plannerInspector.stepTitle || "planner_lane"}{plannerInspector.traceRef.runId ? ` · run ${String(plannerInspector.traceRef.runId)}` : ""}{plannerInspector.traceRef.planId ? ` · trace ${String(plannerInspector.traceRef.planId)}` : ""}
                                            </div>
                                            <div className="mt-4 grid gap-3 md:grid-cols-4">
                                                <div className="rounded-lg border border-border/50 p-3">
                                                    <div className="text-xs text-muted-foreground">plan summary</div>
                                                    <div className="mt-2 text-sm font-medium">{plannerInspector.planSummary || "n/a"}</div>
                                                </div>
                                                <div className="rounded-lg border border-border/50 p-3">
                                                    <div className="text-xs text-muted-foreground">task briefs</div>
                                                    <div className="mt-2 text-sm font-medium">{plannerInspector.taskBriefs.length}</div>
                                                </div>
                                                <div className="rounded-lg border border-border/50 p-3">
                                                    <div className="text-xs text-muted-foreground">selected delegations</div>
                                                    <div className="mt-2 text-sm font-medium">{plannerInspector.selectedDelegations.length}</div>
                                                </div>
                                                <div className="rounded-lg border border-border/50 p-3">
                                                    <div className="text-xs text-muted-foreground">risk flags</div>
                                                    <div className="mt-2 text-sm font-medium">{plannerInspector.riskFlags.length || 0}</div>
                                                </div>
                                            </div>
                                            <div className="mt-4 grid gap-4 xl:grid-cols-[0.58fr_0.42fr]">
                                                <div className="rounded-lg border border-border/50 p-3">
                                                    <div className="text-sm font-medium">Broker-ready task briefs</div>
                                                    <div className="mt-3 space-y-3">
                                                        {plannerInspector.taskBriefs.length > 0 ? plannerInspector.taskBriefs.map((taskBrief, index) => <div key={asString(taskBrief.taskBriefId) || `task-brief:${index}`} className="rounded-lg bg-muted/40 p-3">
                                                                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                                                                    <span>{asString(taskBrief.taskBriefId) || `task-${index + 1}`}</span>
                                                                    {asString(taskBrief.executionLaneHint) ? <Badge variant="outline">{asString(taskBrief.executionLaneHint)}</Badge> : null}
                                                                    {asString(taskBrief.parallelGroup) ? <Badge variant="secondary">{asString(taskBrief.parallelGroup)}</Badge> : null}
                                                                </div>
                                                                <div className="mt-2 text-sm font-medium">{asString(taskBrief.goal) || "n/a"}</div>
                                                                <div className="mt-2 space-y-1 text-xs text-muted-foreground">
                                                                    <div>writeSet：{asStringArray(taskBrief.writeSet).join(" / ") || "n/a"}</div>
                                                                    <div>behaviorScope：{asStringArray(taskBrief.behaviorScope).join(" / ") || "n/a"}</div>
                                                                    <div>requiredCapabilities：{asStringArray(taskBrief.requiredCapabilities).join(" / ") || "n/a"}</div>
                                                                    <div>acceptance：{asString(taskBrief.acceptanceContract) || "n/a"}</div>
                                                                </div>
                                                            </div>) : <div className="rounded-lg border border-dashed p-3 text-xs text-muted-foreground">{tg(t, "b0094e1b")}</div>}
                                                    </div>
                                                </div>
                                                <div className="space-y-4">
                                                    <div className="rounded-lg border border-border/50 p-3">
                                                        <div className="text-sm font-medium">Selected delegations</div>
                                                        <div className="mt-3 space-y-2">
                                                            {plannerInspector.selectedDelegations.length > 0 ? plannerInspector.selectedDelegations.map((item, index) => <div key={asString(item.delegationId) || `delegation:${index}`} className="rounded-lg bg-muted/40 p-3 text-xs text-muted-foreground">
                                                                    <div className="font-medium text-foreground">{asString(item.targetLabel) || asString(item.targetId) || "delegation target"}</div>
                                                                    <div className="mt-1">lane：{asString(item.lane) || "n/a"} · status：{asString(item.status) || "n/a"}</div>
                                                                    <div className="mt-1">taskBriefId：{asString(item.taskBriefId) || "n/a"}</div>
                                                                </div>) : <div className="rounded-lg border border-dashed p-3 text-xs text-muted-foreground">{tg(t, "7968d4a5")}</div>}
                                                        </div>
                                                    </div>
                                                    <div className="rounded-lg border border-border/50 p-3">
                                                        <div className="text-sm font-medium">Acceptance & risks</div>
                                                        <div className="mt-3 space-y-2 text-xs text-muted-foreground">
                                                            <div>globalAcceptance：{plannerInspector.globalAcceptanceContract || "n/a"}</div>
                                                            <div>riskFlags：{plannerInspector.riskFlags.join(" / ") || "n/a"}</div>
                                                            <div>dependencies：{plannerInspector.dependencies.length > 0 ? plannerInspector.dependencies.length : "0"}</div>
                                                        </div>
                                                    </div>
                                                </div>
                                            </div>
                                        </div> : null}
                                    <div className="grid gap-4 xl:grid-cols-[0.42fr_0.58fr]">
                                        <div className="rounded-xl border border-border/50 p-4">
                                            <div className="text-sm font-medium">{tg(t, "4bafdc6d")}</div>
                                            <div className="mt-3 space-y-2 text-xs text-muted-foreground">
                                                <div>canResume：{selectedSessionDetail.controls?.canResume ? "true" : "false"}</div>
                                                <div>canRetry：{selectedSessionDetail.controls?.canRetry ? "true" : "false"}</div>
                                                <div>canInterrupt：{selectedSessionDetail.controls?.canInterrupt ? "true" : "false"}</div>
                                                <div>currentStep：{selectedSessionDetail.workflow?.currentStepTitle || "n/a"} · {selectedSessionDetail.workflow?.currentStepStatus || "n/a"}</div>
                                            </div>
                                        </div>
                                        <div className="rounded-xl border border-border/50 p-4">
                                            <div className="text-sm font-medium">{tg(t, "61f44e37")}</div>
                                            <div className="mt-3 space-y-3">
                                                {(selectedSessionDetail.messages || []).slice(-3).map((message, index) => <div key={message.id || `${message.role || "msg"}:${index}`} className="rounded-lg bg-muted/40 p-3">
                                                        <div className="text-xs text-muted-foreground">{message.role || "message"} · {formatWhen(message.createdAt)}</div>
                                                        <div className="mt-2 whitespace-pre-wrap text-sm">{message.content || tg(t, "e6598f07")}</div>
                                                    </div>)}
                                            </div>
                                        </div>
                                    </div>
                                </> : <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">{tg(t, "c3c86707")}</div>}
                        </CardContent>
                    </Card> : null}

            </div>
        </div>;
}
