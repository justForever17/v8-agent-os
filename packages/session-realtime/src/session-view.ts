import type {
  ActiveRunScopedTodos,
  AdminProcessRef,
  AuthoritativeSessionSnapshot,
  ContextReferenceItem,
  SessionRealtimeStore,
} from "./contract.js";
import { buildSessionRealtimeProjection, deriveAuthoritativeSessionProjection } from "./cdc.js";

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function asNullableRecord(value: unknown): Record<string, unknown> | null {
  const record = asRecord(value);
  return Object.keys(record).length > 0 ? record : null;
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function asNumber(value: unknown): number | null {
  const normalized = Number(value);
  return Number.isFinite(normalized) ? normalized : null;
}

function asBoolean(value: unknown): boolean {
  return value === true;
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value
        .map((item) => asString(item))
        .filter((item): item is string => Boolean(item))
    : [];
}

export type SessionCurrentRunView = Record<string, unknown> & {
  id?: string | null;
  runId?: string | null;
  run_id?: string | null;
  session_id?: string | null;
  status?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  trigger_source?: string | null;
  metadata?: Record<string, unknown>;
};

export type SessionApprovalView = Record<string, unknown> & {
  id?: string;
  approvalId?: string;
  approval_id?: string;
  runId?: string;
  run_id?: string;
  approvalKind?: string;
  approval_kind?: string;
  status?: string;
  question?: string;
  prompt?: string;
  toolCallId?: string;
  tool_call_id?: string;
  request?: Record<string, unknown>;
};

export type SessionWorkflowView = Record<string, unknown> & {
  rootRunId?: string;
  status?: string;
  recoverable?: boolean;
  ownerRuntime?: string;
  ownerAgentId?: string;
  currentStepId?: string;
  currentStepKey?: string;
  currentStepTitle?: string;
  currentStepStatus?: string;
};

export type SessionControlsView = Record<string, unknown> & {
  runId?: string;
  canResume?: boolean;
  canRetry?: boolean;
  canInterrupt?: boolean;
  canApprove?: boolean;
  canReject?: boolean;
  canOpenApproval?: boolean;
  pendingApprovalCount?: number;
  recoverable?: boolean;
  workflowStatus?: string;
  stepStatus?: string;
};

export type SessionRecoverableView = Record<string, unknown> & {
  recoverable?: boolean;
  strategy?: string;
  workflowStatus?: string;
  currentStepStatus?: string;
  canResume?: boolean;
  canRetry?: boolean;
};

export type SessionSummaryView = Record<string, unknown> & {
  workflowStatus?: string;
  statusLabel?: string;
  stepStatus?: string;
  ownerRuntime?: string;
  currentStepTitle?: string;
  pendingApprovalCount?: number;
  previewExcerpt?: string;
  lastNarrativeExcerpt?: string;
  lastRuntimeSummary?: string;
  lastActivityAt?: string;
  hasDurablePreview?: boolean;
};

export type ContextGovernanceView = Record<string, unknown> & {
  context_policy_version?: number;
  runtime_kind?: string;
  target_role?: string;
  resolved_model_id?: string;
  context_window_tokens?: number;
  original_message_count?: number;
  estimated_input_tokens?: number;
  trigger_reason?: string;
  compaction_applied?: boolean;
  compaction_method?: string;
  block_types?: string[];
  block_count?: number;
  estimated_saved_tokens?: number;
  block_summaries?: Array<Record<string, unknown>>;
  resolved_scope?: string;
  scope_chain?: string[];
  durable_flush?: Record<string, unknown> | null;
  eventTs?: string;
  runId?: string;
  eventSource?: Record<string, unknown> | null;
};

export type ContextGovernanceDigest = {
  id: string;
  eventTs: string | null;
  runtimeKind: string | null;
  targetRole: string | null;
  resolvedScope: string | null;
  scopeChain: string[];
  blockTypes: string[];
  blockCount: number | null;
  blockSummaryLines: string[];
  triggerReason: string | null;
  compactionApplied: boolean;
  compactionMethod: string | null;
  estimatedSavedTokens: number | null;
  durableFlush: Record<string, unknown> | null;
  durableFlushReason: string | null;
  contextWindowTokens: number | null;
  modelId: string | null;
};

export type AuthoritativeSessionView = {
  todos: ActiveRunScopedTodos | null;
  currentRun: SessionCurrentRunView | null;
  runtimeStatus: string | null;
  workflow: SessionWorkflowView | null;
  approvals: SessionApprovalView[];
  controls: SessionControlsView | null;
  recoverable: SessionRecoverableView | null;
  summary: SessionSummaryView | null;
  source: string | null;
  contextGovernance: ContextGovernanceView | null;
  contextGovernanceHistory: ContextGovernanceView[];
  runtimeTimeline: Array<Record<string, unknown>>;
  workflowProjection: Record<string, unknown> | null;
  projection: Record<string, unknown> | null;
  processes: AdminProcessRef[];
  contextReferences: ContextReferenceItem[];
};

export type DeriveAuthoritativeSessionViewResult = {
  store: SessionRealtimeStore;
  snapshot: AuthoritativeSessionSnapshot | null;
  view: AuthoritativeSessionView | null;
};

export function buildAuthoritativeSessionView(store: SessionRealtimeStore): AuthoritativeSessionView | null {
  const projection = buildSessionRealtimeProjection(store);
  const snapshot = store.snapshot;
  if (!snapshot) {
    return null;
  }
  const rawProjection = asRecord(projection.projection);
  const rawWorkflowProjection = asRecord(projection.workflowProjection);
  const workflow =
    asNullableRecord(rawProjection.workflow)
    || asNullableRecord(snapshot.workflow)
    || asNullableRecord(rawWorkflowProjection);
  const contextGovernance =
    asNullableRecord(projection.contextGovernance)
    || asNullableRecord(snapshot.contextGovernance)
    || asNullableRecord(rawProjection.contextGovernance)
    || asNullableRecord(rawProjection.context_governance)
    || asNullableRecord(rawWorkflowProjection.contextGovernance)
    || asNullableRecord(rawWorkflowProjection.context_governance);
  const contextGovernanceHistorySource =
    Array.isArray(projection.contextGovernanceHistory)
      ? projection.contextGovernanceHistory
      : Array.isArray(snapshot.contextGovernanceHistory)
        ? snapshot.contextGovernanceHistory
        : Array.isArray(rawProjection.contextGovernanceHistory)
          ? rawProjection.contextGovernanceHistory
          : Array.isArray(rawProjection.context_governance_history)
            ? rawProjection.context_governance_history
            : Array.isArray(rawWorkflowProjection.contextGovernanceHistory)
              ? rawWorkflowProjection.contextGovernanceHistory
              : Array.isArray(rawWorkflowProjection.context_governance_history)
                ? rawWorkflowProjection.context_governance_history
                : [];
  const contextGovernanceHistory = contextGovernanceHistorySource
    .map((item) => asNullableRecord(item))
    .filter((item): item is Record<string, unknown> => Boolean(item));

  return {
    todos: projection.todos,
    currentRun: (projection.currentRun as SessionCurrentRunView | null) || null,
    runtimeStatus: projection.runtimeStatus,
    workflow: workflow as SessionWorkflowView | null,
    approvals: (projection.approvals as SessionApprovalView[]) || [],
    controls: (projection.controls as SessionControlsView | null) || null,
    recoverable: (projection.recoverable as SessionRecoverableView | null) || null,
    summary: (projection.summary as SessionSummaryView | null) || null,
    source: projection.source,
    contextGovernance: contextGovernance as ContextGovernanceView | null,
    contextGovernanceHistory: contextGovernanceHistory as ContextGovernanceView[],
    runtimeTimeline: projection.runtimeTimeline,
    workflowProjection: rawWorkflowProjection,
    projection: rawProjection,
    processes: projection.processes,
    contextReferences: projection.contextReferences,
  };
}

export function deriveAuthoritativeSessionView(raw: unknown): DeriveAuthoritativeSessionViewResult {
  const { store, snapshot } = deriveAuthoritativeSessionProjection(raw);
  return {
    store,
    snapshot,
    view: buildAuthoritativeSessionView(store),
  };
}

export function normalizeContextGovernanceDigest(
  raw: Record<string, unknown> | ContextGovernanceView | null | undefined,
  fallbackIndex = 0,
): ContextGovernanceDigest | null {
  const record = asNullableRecord(raw);
  if (!record) {
    return null;
  }

  const blockSummaryLines = Array.isArray(record.block_summaries)
    ? record.block_summaries
        .map((item) => {
          const summaryRecord = asRecord(item);
          return (
            asString(summaryRecord.summary)
            || asString(summaryRecord.label)
            || asString(summaryRecord.block_type)
            || asString(summaryRecord.type)
            || asString(summaryRecord.name)
          );
        })
        .filter((item): item is string => Boolean(item))
    : [];

  const eventTs = asString(record.eventTs) || asString(record.event_ts);
  const runId = asString(record.runId) || asString(record.run_id);
  const targetRole = asString(record.target_role) || asString(record.targetRole);
  const runtimeKind = asString(record.runtime_kind) || asString(record.runtimeKind);
  const resolvedScope = asString(record.resolved_scope) || asString(record.resolvedScope);
  const durableFlush =
    asNullableRecord(record.durable_flush)
    || asNullableRecord(record.durableFlush)
    || null;

  return {
    id:
      asString(record.id)
      || eventTs
      || runId
      || `${runtimeKind || "context"}:${targetRole || "role"}:${fallbackIndex}`,
    eventTs,
    runtimeKind,
    targetRole,
    resolvedScope,
    scopeChain: asStringArray(record.scope_chain || record.scopeChain),
    blockTypes: asStringArray(record.block_types || record.blockTypes),
    blockCount: asNumber(record.block_count || record.blockCount),
    blockSummaryLines,
    triggerReason: asString(record.trigger_reason) || asString(record.triggerReason),
    compactionApplied: asBoolean(record.compaction_applied) || asBoolean(record.compactionApplied),
    compactionMethod: asString(record.compaction_method) || asString(record.compactionMethod),
    estimatedSavedTokens: asNumber(record.estimated_saved_tokens || record.estimatedSavedTokens),
    durableFlush,
    durableFlushReason:
      asString(durableFlush?.reason)
      || asString((durableFlush as Record<string, unknown> | null)?.status)
      || null,
    contextWindowTokens: asNumber(record.context_window_tokens || record.contextWindowTokens),
    modelId: asString(record.resolved_model_id) || asString(record.resolvedModelId),
  };
}

export function normalizeContextGovernanceHistory(
  raw: Array<Record<string, unknown> | ContextGovernanceView> | null | undefined,
): ContextGovernanceDigest[] {
  return Array.isArray(raw)
    ? raw
        .map((item, index) => normalizeContextGovernanceDigest(item, index))
        .filter((item): item is ContextGovernanceDigest => Boolean(item))
    : [];
}
