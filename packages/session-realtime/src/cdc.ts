import type {
  ActiveRunScopedTodos,
  AdminProcessRef,
  AuthoritativeSessionSnapshot,
  ContextReferenceItem,
  ContextReferenceType,
  NormalizedSessionRuntimeEvent,
  SessionRuntimeId,
  SessionRealtimeStore,
  SessionTodoItem,
} from "./contract.js";
import { coerceAdminProcessRef } from "./resources.js";
import {
  applyRealtimeEventToMessages,
  deriveRealtimeStreamState,
  shouldApplyRuntimeEventToMessage,
  type SessionAgentProfile,
  type SessionStreamLifecycleOptions,
  type SessionStreamMessage,
  type SessionStreamUiEvent,
} from "./message-lifecycle.js";

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function asRecordArray(...candidates: unknown[]): Array<Record<string, unknown>> {
  for (const candidate of candidates) {
    if (Array.isArray(candidate) && candidate.some((item) => item && typeof item === "object")) {
      return candidate.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object");
    }
  }
  return [];
}

function normalizeTodoItems(value: unknown): SessionTodoItem[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    .map((item) => ({
      id: typeof item.id === "string" ? item.id : null,
      content: typeof (item.content || item.title || item.text) === "string" ? String(item.content || item.title || item.text) : null,
      status: typeof item.status === "string" ? item.status : null,
    }));
}

export function isAskUserInteractionApproval(value: unknown) {
  const record = asRecord(value);
  const request = asRecord(record.request);
  const approvalKind = String(record.approval_kind || record.approvalKind || "").trim().toLowerCase();
  const interactionKind = String(request.interactionKind || request.interaction_kind || record.interactionKind || record.interaction_kind || "").trim().toLowerCase();
  return interactionKind === "ask_user"
    || approvalKind === "ask_user";
}

function normalizeProcesses(value: unknown): AdminProcessRef[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => coerceAdminProcessRef(item))
    .filter((item): item is AdminProcessRef => Boolean(item));
}

function normalizeContextReferences(value: unknown): ContextReferenceItem[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    .map((item) => {
      const type: ContextReferenceType =
        item.type === "file" || item.type === "memory" || item.type === "search" || item.type === "web"
          ? item.type
          : "file";
      return {
        id: typeof item.id === "string" ? item.id : "",
        type,
        label: typeof item.label === "string" ? item.label : "",
        details: typeof item.details === "string" ? item.details : undefined,
        toolName:
          typeof item.toolName === "string"
            ? item.toolName
            : typeof item.tool_name === "string"
              ? item.tool_name
              : undefined,
        toolCallId:
          typeof item.toolCallId === "string"
            ? item.toolCallId
            : typeof item.tool_call_id === "string"
              ? item.tool_call_id
              : undefined,
        sourceMessageId:
          typeof item.sourceMessageId === "string"
            ? item.sourceMessageId
            : typeof item.source_message_id === "string"
              ? item.source_message_id
              : undefined,
      };
    })
    .filter((item) => Boolean(item.id && item.label));
}

export function createInitialSessionRealtimeState(): SessionRealtimeStore {
  return {
    snapshot: null,
    latestSeq: 0,
    lastSnapshotFingerprint: "",
    lastRuntimeEvent: null,
    unreadProgressHint: false,
  };
}

export type SessionRealtimeProjection = {
  todos: ActiveRunScopedTodos | null;
  approvals: Array<Record<string, unknown>>;
  controls: Record<string, unknown> | null;
  recoverable: unknown;
  runtimeTimeline: Array<Record<string, unknown>>;
  currentRun: Record<string, unknown> | null;
  runtimeStatus: string | null;
  summary: Record<string, unknown> | null;
  workflowProjection: Record<string, unknown> | null;
  projection: Record<string, unknown> | null;
  source: string | null;
  processes: AdminProcessRef[];
  contextReferences: ContextReferenceItem[];
  contextGovernance: Record<string, unknown> | null;
  contextGovernanceHistory: Array<Record<string, unknown>>;
};

export type ApplyAuthoritativeSessionSnapshotResult = {
  store: SessionRealtimeStore;
  projection: SessionRealtimeProjection;
  snapshot: AuthoritativeSessionSnapshot | null;
};

export type SessionRealtimeMessageState<TMessage = SessionStreamMessage> = {
  currentAiMsg: TMessage | undefined;
  activeAgentProfile: SessionAgentProfile;
  pendingRuntimeEvents: SessionStreamUiEvent[];
};

export type FlushQueuedRuntimeEventsOptions<TMessage = SessionStreamMessage> = {
  cloneMessages?: (messages: TMessage[]) => TMessage[];
  normalizeMessages?: (messages: TMessage[]) => TMessage[];
  lifecycleOptions?: SessionStreamLifecycleOptions;
};

export function coerceAuthoritativeSessionSnapshot(raw: unknown): AuthoritativeSessionSnapshot | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }

  const root = raw as Record<string, unknown>;
  const nestedSnapshot = asRecord(root.snapshot);
  const projection = asRecord(root.projection);
  const workflowProjection = asRecord(root.workflowProjection || projection.workflowProjection);
  const effectiveProjection = Object.keys(projection).length > 0 ? projection : root;
  const todosSource = effectiveProjection.todos || root.todos || workflowProjection.todos;
  const todosRecord = asRecord(todosSource);

  const todos: ActiveRunScopedTodos | null =
    Array.isArray(todosSource) || Array.isArray(todosRecord.items) || Array.isArray(todosRecord.todo)
      ? {
          taskId: typeof todosRecord.taskId === "string" ? todosRecord.taskId : typeof todosRecord.task_id === "string" ? String(todosRecord.task_id) : null,
          taskName: typeof todosRecord.taskName === "string" ? todosRecord.taskName : typeof todosRecord.task_name === "string" ? String(todosRecord.task_name) : null,
          runId: typeof todosRecord.runId === "string" ? todosRecord.runId : typeof todosRecord.run_id === "string" ? String(todosRecord.run_id) : null,
          sessionId: typeof todosRecord.sessionId === "string" ? todosRecord.sessionId : typeof todosRecord.session_id === "string" ? String(todosRecord.session_id) : null,
          updatedAt: typeof todosRecord.updatedAt === "string" ? todosRecord.updatedAt : typeof todosRecord.updated_at === "string" ? String(todosRecord.updated_at) : null,
          isActive: Boolean(todosRecord.isActive),
          isStale: Boolean(todosRecord.isStale),
          allCompleted: Boolean(todosRecord.allCompleted),
          items: normalizeTodoItems(Array.isArray(todosSource) ? todosSource : (todosRecord.items || todosRecord.todo)),
        }
      : null;

  return {
    session: asRecord(root.session),
    sessionId:
      typeof root.sessionId === "string"
        ? root.sessionId
        : typeof root.session_id === "string"
          ? root.session_id
          : undefined,
    latestSeq: Number(root.latestSeq || root.latest_seq || asRecord(root.snapshot).latest_seq || 0) || 0,
    messages: Array.isArray(root.messages)
      ? root.messages
      : Array.isArray(nestedSnapshot.messages)
        ? (nestedSnapshot.messages as unknown[])
        : undefined,
    approvals: asRecordArray(effectiveProjection.approvals, root.approvals, workflowProjection.approvals),
    controls: asRecord(effectiveProjection.controls || root.controls),
    recoverable: effectiveProjection.recoverable ?? root.recoverable ?? null,
    artifacts: Array.isArray(asRecord(root.snapshot).artifacts)
      ? (nestedSnapshot.artifacts as unknown[])
      : Array.isArray(root.artifacts)
        ? (root.artifacts as unknown[])
        : undefined,
    processes: normalizeProcesses(
      root.processes
      || effectiveProjection.processes
      || nestedSnapshot.processes
      || workflowProjection.processes,
    ),
    contextReferences: normalizeContextReferences(
      root.contextReferences
      || root.context_references
      || effectiveProjection.contextReferences
      || effectiveProjection.context_references
      || nestedSnapshot.contextReferences
      || nestedSnapshot.context_references
      || workflowProjection.contextReferences
      || workflowProjection.context_references,
    ),
    contextGovernance: asRecord(
      root.contextGovernance
      || root.context_governance
      || effectiveProjection.contextGovernance
      || effectiveProjection.context_governance
      || nestedSnapshot.contextGovernance
      || nestedSnapshot.context_governance
      || workflowProjection.contextGovernance
      || workflowProjection.context_governance,
    ),
    contextGovernanceHistory: asRecordArray(
      root.contextGovernanceHistory,
      root.context_governance_history,
      effectiveProjection.contextGovernanceHistory,
      effectiveProjection.context_governance_history,
      nestedSnapshot.contextGovernanceHistory,
      nestedSnapshot.context_governance_history,
      workflowProjection.contextGovernanceHistory,
      workflowProjection.context_governance_history,
    ),
    todos,
    workflowProjection: Object.keys(workflowProjection).length > 0 ? workflowProjection : null,
    projection: Object.keys(effectiveProjection).length > 0 ? effectiveProjection : null,
    runtimeTimeline: asRecordArray(
      effectiveProjection.runtimeTimeline,
      effectiveProjection.runtimeEvents,
      root.runtimeTimeline,
      root.runtimeEvents,
      nestedSnapshot.runtimeTimeline,
      nestedSnapshot.runtimeEvents,
      workflowProjection.runtimeTimeline,
      workflowProjection.runtimeEvents,
      workflowProjection.eventTail,
      workflowProjection.activities,
    ),
    currentRun: asRecord(
      effectiveProjection.currentRun
      || root.currentRun
      || nestedSnapshot.currentRun
      || workflowProjection.currentRun,
    ),
    runtimeStatus:
      typeof effectiveProjection.runtimeStatus === "string"
        ? effectiveProjection.runtimeStatus
        : typeof root.runtimeStatus === "string"
          ? root.runtimeStatus
          : typeof nestedSnapshot.runtimeStatus === "string"
            ? nestedSnapshot.runtimeStatus
          : typeof workflowProjection.runtimeStatus === "string"
            ? workflowProjection.runtimeStatus
            : null,
    summary: asRecord(effectiveProjection.summary || root.summary || nestedSnapshot.summary || workflowProjection.summary),
    source:
      typeof effectiveProjection.source === "string"
        ? effectiveProjection.source
        : typeof root.source === "string"
          ? root.source
          : null,
    workflow: asRecord(effectiveProjection.workflow || root.workflow),
    snapshot: nestedSnapshot as AuthoritativeSessionSnapshot["snapshot"],
  };
}

export function deriveAuthoritativeSessionProjection(
  raw: unknown,
): { store: SessionRealtimeStore; projection: SessionRealtimeProjection; snapshot: AuthoritativeSessionSnapshot | null } {
  const store = applySnapshotToSessionRealtimeState(createInitialSessionRealtimeState(), raw);
  return {
    store,
    snapshot: store.snapshot,
    projection: buildSessionRealtimeProjection(store),
  };
}

export function buildSessionRealtimeProjection(store: SessionRealtimeStore): SessionRealtimeProjection {
  return {
    todos: selectAuthoritativeTodos(store),
    approvals: selectAuthoritativeApprovals(store) as Array<Record<string, unknown>>,
    controls: selectAuthoritativeControls(store) as Record<string, unknown> | null,
    recoverable: selectAuthoritativeRecoverable(store),
    runtimeTimeline: selectAuthoritativeRuntimeTimeline(store) as Array<Record<string, unknown>>,
    currentRun: selectAuthoritativeCurrentRun(store) as Record<string, unknown> | null,
    runtimeStatus: selectAuthoritativeRuntimeStatus(store),
    summary: selectAuthoritativeSummary(store) as Record<string, unknown> | null,
    workflowProjection: selectAuthoritativeWorkflowProjection(store) as Record<string, unknown> | null,
    projection: selectAuthoritativeProjection(store) as Record<string, unknown> | null,
    source: selectAuthoritativeSource(store),
    processes: selectActiveProcesses(store),
    contextReferences: selectContextReferences(store),
    contextGovernance: selectAuthoritativeContextGovernance(store) as Record<string, unknown> | null,
    contextGovernanceHistory: selectAuthoritativeContextGovernanceHistory(store) as Array<Record<string, unknown>>,
  };
}

export function buildAuthoritativeSnapshotFingerprint(snapshot: AuthoritativeSessionSnapshot | null) {
  if (!snapshot) return "";
  const messages = Array.isArray(snapshot.messages) ? snapshot.messages : [];
  const approvals = Array.isArray(snapshot.approvals) ? snapshot.approvals : [];
  const todos = Array.isArray(snapshot.todos?.items) ? snapshot.todos.items : [];
  const runtimeTimeline = Array.isArray(snapshot.runtimeTimeline) ? snapshot.runtimeTimeline : [];
  const contextGovernance = asRecord(snapshot.contextGovernance);
  const contextGovernanceHistory = asRecordArray(snapshot.contextGovernanceHistory);
  const summary = asRecord(snapshot.summary);
  const currentRun = asRecord(snapshot.currentRun);

  const messageFingerprint = messages.map((message) => {
    const item = asRecord(message);
    const artifacts = Array.isArray(item.artifacts) ? item.artifacts : [];
    const images = Array.isArray(item.images) ? item.images : [];
    return [
      String(item.id || "").trim(),
      String(item.role || "").trim(),
      String(item.runId || item.run_id || "").trim(),
      String(item.content || ""),
      String(images.length),
      String(artifacts.length),
    ].join("¦");
  }).join("¶");

  const todoFingerprint = todos.map((todo) => [
    String(todo.id || "").trim(),
    String(todo.content || "").trim(),
    String(todo.status || "").trim(),
  ].join("¦")).join("¶");

  const approvalFingerprint = approvals.map((approval) => {
    const item = asRecord(approval);
    const request = asRecord(item.request);
    return [
      String(item.id || item.approval_id || "").trim(),
      String(item.run_id || item.runId || "").trim(),
      String(item.approval_kind || item.approvalKind || "").trim(),
      String(request.question || request.prompt || "").trim(),
    ].join("¦");
  }).join("¶");

  const runtimeFingerprint = runtimeTimeline.map((event) => {
    const item = asRecord(event);
    return [
      String(item.id || item.event_id || "").trim(),
      String(item.topic || item.name || "").trim(),
      String(item.seq || "").trim(),
      String(item.summary || item.label || "").trim(),
    ].join("¦");
  }).join("¶");

  return [
    String(snapshot.latestSeq || 0),
    messageFingerprint,
    approvalFingerprint,
    todoFingerprint,
    runtimeFingerprint,
    JSON.stringify(contextGovernance),
    JSON.stringify(contextGovernanceHistory),
    String(snapshot.runtimeStatus || currentRun.status || "").trim(),
    String(summary.workflowStatus || "").trim(),
    String(summary.currentStepTitle || "").trim(),
  ].join("§");
}

export function applySnapshotToSessionRealtimeState(state: SessionRealtimeStore, rawSnapshot: unknown): SessionRealtimeStore {
  const snapshot = coerceAuthoritativeSessionSnapshot(rawSnapshot);
  if (!snapshot) {
    return state;
  }
  return {
    ...state,
    snapshot,
    latestSeq: Math.max(state.latestSeq, snapshot.latestSeq || 0),
    lastSnapshotFingerprint: buildAuthoritativeSnapshotFingerprint(snapshot),
    unreadProgressHint: false,
  };
}

export function applyAuthoritativeSessionSnapshot(
  state: SessionRealtimeStore,
  rawSnapshot: unknown,
): ApplyAuthoritativeSessionSnapshotResult {
  const store = applySnapshotToSessionRealtimeState(state, rawSnapshot);
  return {
    store,
    snapshot: store.snapshot,
    projection: buildSessionRealtimeProjection(store),
  };
}

export function applyRuntimeEventToSessionRealtimeState(
  state: SessionRealtimeStore,
  runtimeEvent: NormalizedSessionRuntimeEvent,
): SessionRealtimeStore {
  return {
    ...state,
    latestSeq: Math.max(state.latestSeq, runtimeEvent.seq || 0),
    lastRuntimeEvent: runtimeEvent,
    unreadProgressHint: runtimeEvent.visibility === "visible" ? true : state.unreadProgressHint,
  };
}

export function createInitialSessionRealtimeMessageState<TMessage = SessionStreamMessage>(
  messages: TMessage[] = [],
  options?: SessionStreamLifecycleOptions,
): SessionRealtimeMessageState<TMessage> {
  const derived = deriveRealtimeStreamState(messages as SessionStreamMessage[], options);
  return {
    currentAiMsg: derived.currentAiMsg as TMessage | undefined,
    activeAgentProfile: derived.activeAgentProfile,
    pendingRuntimeEvents: [],
  };
}

export function syncSessionRealtimeMessageState<TMessage = SessionStreamMessage>(
  messages: TMessage[],
  options?: SessionStreamLifecycleOptions,
): SessionRealtimeMessageState<TMessage> {
  return createInitialSessionRealtimeMessageState(messages, options);
}

export function queueSessionRealtimeRuntimeEvent<TMessage = SessionStreamMessage>(
  state: SessionRealtimeMessageState<TMessage>,
  runtimeEvent: SessionStreamUiEvent,
) {
  if (!shouldApplyRuntimeEventToMessage(runtimeEvent)) {
    return false;
  }
  state.pendingRuntimeEvents.push(runtimeEvent);
  return true;
}

function defaultCloneMessages<TMessage>(messages: TMessage[]) {
  return messages.map((message) => ({
    ...(message as Record<string, unknown>),
    nodes: Array.isArray((message as SessionStreamMessage).nodes) ? (message as SessionStreamMessage).nodes?.map((node) => ({ ...node })) : [],
    images: Array.isArray((message as SessionStreamMessage).images) ? [...((message as SessionStreamMessage).images || [])] : [],
    artifacts: Array.isArray((message as SessionStreamMessage).artifacts)
      ? ((message as SessionStreamMessage).artifacts || []).map((artifact) => ({ ...artifact }))
      : [],
    metadata: (message as SessionStreamMessage).metadata ? { ...((message as SessionStreamMessage).metadata || {}) } : undefined,
  })) as TMessage[];
}

export function flushQueuedSessionRealtimeRuntimeEvents<TMessage = SessionStreamMessage>(
  messages: TMessage[],
  state: SessionRealtimeMessageState<TMessage>,
  options: FlushQueuedRuntimeEventsOptions<TMessage> = {},
) {
  if (state.pendingRuntimeEvents.length === 0) {
    return {
      messages,
      state,
      changed: false,
    };
  }

  const cloneMessages = options.cloneMessages || defaultCloneMessages<TMessage>;
  const normalizeMessages = options.normalizeMessages || ((nextMessages: TMessage[]) => nextMessages);
  const localMessages = cloneMessages(messages);
  let nextCurrentAiMsg = state.currentAiMsg;
  let nextActiveAgentProfile = state.activeAgentProfile;

  for (const runtimeEvent of state.pendingRuntimeEvents) {
    const result = applyRealtimeEventToMessages(
      runtimeEvent,
      localMessages as SessionStreamMessage[],
      nextCurrentAiMsg as SessionStreamMessage | undefined,
      nextActiveAgentProfile,
      options.lifecycleOptions,
    );
    nextCurrentAiMsg = result.currentAiMsg as TMessage | undefined;
    nextActiveAgentProfile = result.activeAgentProfile;
  }

  state.pendingRuntimeEvents = [];
  state.currentAiMsg = nextCurrentAiMsg;
  state.activeAgentProfile = nextActiveAgentProfile;

  return {
    messages: normalizeMessages(localMessages),
    state,
    changed: true,
  };
}

export function buildRuntimeVisibilityMap(events: NormalizedSessionRuntimeEvent[]) {
  const runtimeIds = new Map<SessionRuntimeId, NormalizedSessionRuntimeEvent[]>();
  for (const event of events) {
    if (!event.runtimeId) {
      continue;
    }
    const bucket = runtimeIds.get(event.runtimeId) || [];
    bucket.push(event);
    runtimeIds.set(event.runtimeId, bucket);
  }
  return runtimeIds;
}

export function selectAuthoritativeMessages(state: SessionRealtimeStore) {
  return Array.isArray(state.snapshot?.messages) ? state.snapshot.messages || [] : [];
}

export function selectAuthoritativeTodos(state: SessionRealtimeStore) {
  return state.snapshot?.todos || null;
}

export function selectAuthoritativeArtifacts(state: SessionRealtimeStore) {
  return Array.isArray(state.snapshot?.artifacts) ? state.snapshot?.artifacts || [] : [];
}

export function selectActiveProcesses(state: SessionRealtimeStore) {
  return Array.isArray(state.snapshot?.processes) ? state.snapshot.processes || [] : [];
}

export function selectContextReferences(state: SessionRealtimeStore) {
  return Array.isArray(state.snapshot?.contextReferences) ? state.snapshot.contextReferences || [] : [];
}

export function selectAuthoritativeContextGovernance(state: SessionRealtimeStore) {
  return state.snapshot?.contextGovernance || null;
}

export function selectAuthoritativeContextGovernanceHistory(state: SessionRealtimeStore) {
  return Array.isArray(state.snapshot?.contextGovernanceHistory) ? state.snapshot?.contextGovernanceHistory || [] : [];
}

export function selectProcessByToolCallId(state: SessionRealtimeStore, toolCallId: string | null | undefined) {
  const normalizedToolCallId = String(toolCallId || "").trim();
  if (!normalizedToolCallId) {
    return null;
  }
  return selectActiveProcesses(state).find((processRef) => String(processRef.toolCallId || "").trim() === normalizedToolCallId) || null;
}

export function selectAuthoritativeApprovals(state: SessionRealtimeStore) {
  return Array.isArray(state.snapshot?.approvals) ? state.snapshot.approvals || [] : [];
}

export function selectAskUserApprovals(state: SessionRealtimeStore) {
  return selectAuthoritativeApprovals(state).filter((approval) => isAskUserInteractionApproval(approval));
}

export function selectGovernanceApprovals(state: SessionRealtimeStore) {
  return selectAuthoritativeApprovals(state).filter((approval) => !isAskUserInteractionApproval(approval));
}

export function selectAuthoritativeRuntimeTimeline(state: SessionRealtimeStore) {
  return Array.isArray(state.snapshot?.runtimeTimeline) ? state.snapshot.runtimeTimeline || [] : [];
}

export function selectAuthoritativeCurrentRun(state: SessionRealtimeStore) {
  return state.snapshot?.currentRun || null;
}

export function selectAuthoritativeRuntimeStatus(state: SessionRealtimeStore) {
  return state.snapshot?.runtimeStatus || null;
}

export function selectAuthoritativeSummary(state: SessionRealtimeStore) {
  return state.snapshot?.summary || null;
}

export function selectAuthoritativeWorkflowProjection(state: SessionRealtimeStore) {
  return state.snapshot?.workflowProjection || null;
}

export function selectAuthoritativeProjection(state: SessionRealtimeStore) {
  return state.snapshot?.projection || null;
}

export function selectAuthoritativeControls(state: SessionRealtimeStore) {
  return state.snapshot?.controls || null;
}

export function selectAuthoritativeRecoverable(state: SessionRealtimeStore) {
  return state.snapshot?.recoverable || null;
}

export function selectAuthoritativeSource(state: SessionRealtimeStore) {
  return state.snapshot?.source || null;
}
