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
  eventTs?: string;
  runId?: string;
  eventSource?: Record<string, unknown> | null;
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
