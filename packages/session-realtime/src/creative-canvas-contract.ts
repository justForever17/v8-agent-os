import {
  collectAliasedText,
  collectAliasedTextFromRecords,
  collectAuthorityDeclarations,
} from "./authority-declarations.js";

export const CREATIVE_CANVAS_CONTRACT_START = "[CANVAS EXECUTION CONTRACT v1]";
export const CREATIVE_CANVAS_CONTRACT_END = "[/CANVAS EXECUTION CONTRACT]";
export const CREATIVE_CANVAS_CONTRACT_SCHEMA = "v8.creative_canvas_task.v1";

export type CreativeCanvasExecutionContract = Record<string, unknown> & {
  schema: typeof CREATIVE_CANVAS_CONTRACT_SCHEMA;
  canvasOperationId: string;
  actionId: string;
};

export type CreativeCanvasMessageLike = {
  role?: unknown;
  content?: unknown;
  metadata?: unknown;
};

export type CreativeCanvasHumanSurfaceProjection = {
  kind: "canvas_message";
  text: string;
  copyText: string;
  hideAttachments: true;
  hideInternalMetadata: true;
};

export type CreativeCanvasDispatchInput = {
  content?: unknown;
  data?: unknown;
  metadata?: unknown;
  contextMentions?: unknown;
  attachments?: unknown;
  canvasSupervisorDirect?: unknown;
};

export type CreativeCanvasDispatchClassification =
  | {
      kind: "ordinary";
      privileged: false;
    }
  | {
      kind: "invalid_canvas_direct";
      privileged: false;
      reason: string;
    }
  | {
      kind: "canvas_supervisor_direct";
      privileged: true;
      routeKind: "supervisor" | "creative_media";
      canvasOperationId: string;
      actionId: string;
      contract: CreativeCanvasExecutionContract;
    };

export type CreativeCanvasAuthorityKind = "source" | "artifact" | "graph" | "run" | "operation";

export type CreativeCanvasAuthorityScope = {
  sessionId: string;
  workspaceId: string;
};

export type CreativeCanvasAuthorityItem = {
  kind: CreativeCanvasAuthorityKind;
  id: string;
  sessionId: string;
  workspaceId: string;
  value: Record<string, unknown>;
};

export type CreativeCanvasAuthorityRejection = {
  kind: CreativeCanvasAuthorityKind;
  index: number;
  id: string | null;
  reason: "missing_id" | "missing_session_id" | "missing_workspace_id" | "conflicting_authority" | "session_mismatch" | "workspace_mismatch";
  actualSessionId: string | null;
  actualWorkspaceId: string | null;
};

export type CreativeCanvasAuthorityProjection = {
  scope: CreativeCanvasAuthorityScope;
  accepted: CreativeCanvasAuthorityItem[];
  rejected: CreativeCanvasAuthorityRejection[];
};

export const CREATIVE_CANVAS_GRAPH_RUN_STATE_TOPIC = "canvas.graph.run.state";
export const CREATIVE_CANVAS_GRAPH_RUN_STATE_SCHEMA = "v8.creative_canvas_graph_run_state.v1";

export const CREATIVE_CANVAS_GRAPH_RUN_STATES = [
  "queued",
  "running",
  "cancelling",
  "cancelled",
  "failed",
  "interrupted",
  "recovered",
  "completed",
] as const;

export type CreativeCanvasGraphRunState = (typeof CREATIVE_CANVAS_GRAPH_RUN_STATES)[number];
export type CreativeCanvasGraphRunTransition = "recovered" | "retry_failed_branch";

export type CreativeCanvasGraphRunStateProjection = {
  schema: typeof CREATIVE_CANVAS_GRAPH_RUN_STATE_SCHEMA;
  topic: typeof CREATIVE_CANVAS_GRAPH_RUN_STATE_TOPIC;
  sessionId: string;
  workspaceId: string;
  graphId: string;
  graphRunId: string;
  canvasOperationId: string;
  runId: string | null;
  status: CreativeCanvasGraphRunState;
  transition: CreativeCanvasGraphRunTransition | null;
  retryOfGraphRunId: string | null;
  canRetryFailedBranch: boolean;
};

export type CreativeCanvasGraphRunHumanSurfaceProjection = {
  kind: "canvas_graph_run_state";
  status: CreativeCanvasGraphRunState;
  transition: CreativeCanvasGraphRunTransition | null;
  stateKey: `canvas.graph.run.${CreativeCanvasGraphRunState}`;
  canRetryFailedBranch: boolean;
};

function recordOf(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function recordList(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(recordOf).filter((item) => Object.keys(item).length > 0) : [];
}

function combinedRecordList(...values: unknown[]): Record<string, unknown>[] {
  return values.flatMap(recordList);
}

function strictCombinedRecordList(...values: unknown[]): {
  records: Record<string, unknown>[];
  invalid: boolean;
} {
  const records: Record<string, unknown>[] = [];
  for (const value of values) {
    if (value === undefined || value === null) continue;
    if (!Array.isArray(value)) return { records, invalid: true };
    for (const item of value) {
      const record = recordOf(item);
      if (!Object.keys(record).length) return { records, invalid: true };
      records.push(record);
    }
  }
  return { records, invalid: false };
}

function aliasedStringArray(record: Record<string, unknown>, keys: readonly string[]): {
  values: string[];
  invalid: boolean;
  conflicted: boolean;
} {
  const declared: string[][] = [];
  for (const key of keys) {
    if (!Object.prototype.hasOwnProperty.call(record, key)) continue;
    const raw = record[key];
    if (!Array.isArray(raw) || raw.some((item) => typeof item !== "string" || !item.trim())) {
      return { values: [], invalid: true, conflicted: false };
    }
    declared.push(Array.from(new Set(raw.map((item) => item.trim()))));
  }
  const signatures = new Set(declared.map((items) => JSON.stringify([...items].sort())));
  return {
    values: declared[0] || [],
    invalid: false,
    conflicted: signatures.size > 1,
  };
}

function sameStringSet(left: Iterable<string>, right: Iterable<string>): boolean {
  const leftSet = new Set(left);
  const rightSet = new Set(right);
  return leftSet.size === rightSet.size && Array.from(leftSet).every((value) => rightSet.has(value));
}

function canvasOperationIds(value: unknown): {
  ids: string[];
  present: boolean;
  conflicted: boolean;
} {
  const result: string[] = [];
  let present = false;
  let conflicted = false;
  for (const mention of recordList(value)) {
    if (text(mention.kind).toLowerCase() !== "canvas_operation") continue;
    present = true;
    const declared = collectAliasedText(
      mention,
      ["id", "canvasOperationId", "canvas_operation_id", "operationId", "operation_id"],
    );
    if (declared.conflicted) conflicted = true;
    const id = declared.value;
    if (id && !result.includes(id)) result.push(id);
  }
  return { ids: result, present, conflicted };
}

export function parseCreativeCanvasExecutionContract(content: unknown): CreativeCanvasExecutionContract | null {
  const source = typeof content === "string" ? content : "";
  const start = source.indexOf(CREATIVE_CANVAS_CONTRACT_START);
  const end = start >= 0
    ? source.indexOf(CREATIVE_CANVAS_CONTRACT_END, start + CREATIVE_CANVAS_CONTRACT_START.length)
    : -1;
  if (
    start < 0
    || end < 0
    || source.indexOf(CREATIVE_CANVAS_CONTRACT_START, start + CREATIVE_CANVAS_CONTRACT_START.length) >= 0
    || source.indexOf(CREATIVE_CANVAS_CONTRACT_END, end + CREATIVE_CANVAS_CONTRACT_END.length) >= 0
  ) return null;
  const encoded = source.slice(start + CREATIVE_CANVAS_CONTRACT_START.length, end).trim();
  if (!encoded || encoded.length > 262_144) return null;
  try {
    const contract = recordOf(JSON.parse(encoded));
    const operation = collectAliasedText(contract, ["canvasOperationId", "canvas_operation_id"]);
    const action = collectAliasedText(contract, ["actionId", "action_id"]);
    if (
      contract.schema !== CREATIVE_CANVAS_CONTRACT_SCHEMA
      || operation.conflicted
      || action.conflicted
      || !operation.value
      || !action.value
    ) return null;
    return {
      ...contract,
      canvasOperationId: operation.value,
      actionId: action.value,
    } as CreativeCanvasExecutionContract;
  } catch {
    return null;
  }
}

export function isCreativeCanvasUserMessage(message: CreativeCanvasMessageLike): boolean {
  if (text(message.role).toLowerCase() !== "user") return false;
  if (typeof message.content === "string" && message.content.includes(CREATIVE_CANVAS_CONTRACT_START)) return true;
  if (parseCreativeCanvasExecutionContract(message.content)) return true;
  const metadata = recordOf(message.metadata);
  if (canvasOperationIds(combinedRecordList(metadata.contextMentions, metadata.context_mentions)).present) return true;
  return [metadata.composerPresentation, metadata.composer_presentation]
    .map(recordOf)
    .some((presentation) => recordList(presentation.references).some(
      (reference) => text(reference.kind).toLowerCase() === "canvas_resource",
    ));
}

/**
 * Returns a deliberately closed Human Surface value. Raw content, attachments,
 * paths, ids, mask data and execution contracts stay on the Runtime Surface.
 */
export function projectCreativeCanvasHumanSurfaceMessage(
  message: CreativeCanvasMessageLike,
  localizedText: string,
): CreativeCanvasHumanSurfaceProjection | null {
  if (!isCreativeCanvasUserMessage(message)) return null;
  const safeText = localizedText.trim();
  if (!safeText) return null;
  return {
    kind: "canvas_message",
    text: safeText,
    copyText: safeText,
    hideAttachments: true,
    hideInternalMetadata: true,
  };
}

function invalidCanvasDirect(reason: string): CreativeCanvasDispatchClassification {
  return { kind: "invalid_canvas_direct", privileged: false, reason };
}

/**
 * Client-side contract classification only. Engine remains the authority and
 * must repeat resource ownership, graph recovery and provider validations.
 */
export function classifyCreativeCanvasDispatch(
  input: CreativeCanvasDispatchInput,
): CreativeCanvasDispatchClassification {
  const data = recordOf(input.data);
  const metadata = recordOf(input.metadata);
  const requested = input.canvasSupervisorDirect === true || data.canvasSupervisorDirect === true;
  if (!requested) return { kind: "ordinary", privileged: false };

  const contract = parseCreativeCanvasExecutionContract(input.content);
  if (!contract) return invalidCanvasDirect("invalid_execution_contract");
  const contextMentions = strictCombinedRecordList(
    input.contextMentions,
    data.contextMentions,
    data.context_mentions,
    metadata.contextMentions,
    metadata.context_mentions,
  );
  if (contextMentions.invalid) return invalidCanvasDirect("invalid_canvas_operation_mention");
  const operationMentions = canvasOperationIds(contextMentions.records);
  const operationIds = operationMentions.ids;
  if (operationMentions.conflicted || operationIds.length !== 1) {
    return invalidCanvasDirect("invalid_canvas_operation_mention");
  }
  const canvasOperationId = text(contract.canvasOperationId);
  if (operationIds[0] !== canvasOperationId) return invalidCanvasDirect("operation_id_mismatch");

  const attachments = strictCombinedRecordList(input.attachments, data.attachments, metadata.attachments);
  if (attachments.invalid) return invalidCanvasDirect("invalid_canvas_attachment");
  const attachmentSourceIds = new Set<string>();
  for (const attachment of attachments.records) {
    const attachmentMetadata = recordOf(attachment.metadata);
    const attachmentOperation = collectAliasedText(
      attachmentMetadata,
      ["canvasOperationId", "canvas_operation_id"],
    );
    if (attachmentOperation.conflicted || attachmentOperation.value !== canvasOperationId) {
      return invalidCanvasDirect("attachment_operation_id_mismatch");
    }
    const attachmentSource = collectAliasedText(attachment, ["sourceId", "source_id", "id"]);
    if (attachmentSource.conflicted) return invalidCanvasDirect("attachment_source_id_conflict");
    if (!attachmentSource.value) return invalidCanvasDirect("invalid_canvas_attachment");
    attachmentSourceIds.add(attachmentSource.value);
  }

  const resources = recordOf(contract.resources);
  const declaredSourceIds = aliasedStringArray(resources, ["sourceIds", "source_ids"]);
  if (declaredSourceIds.invalid || declaredSourceIds.conflicted) {
    return invalidCanvasDirect("invalid_source_id_contract");
  }
  const sourceIds = declaredSourceIds.values;
  if (!sameStringSet(sourceIds, attachmentSourceIds)) {
    return invalidCanvasDirect("unbound_source_id");
  }

  const execution = recordOf(contract.execution);
  const routeKind = text(execution.tool) === "creative_media_jobs" ? "creative_media" : "supervisor";
  if (routeKind === "creative_media") {
    const args = recordOf(execution.arguments);
    const request = recordOf(args.request);
    const requestOperation = collectAliasedText(request, ["canvasOperationId", "canvas_operation_id"]);
    const requestSource = collectAliasedText(request, ["sourceId", "source_id"]);
    const requestSources = aliasedStringArray(request, ["sourceIds", "source_ids"]);
    if (text(args.action) !== "create") return invalidCanvasDirect("invalid_creative_media_action");
    if (requestOperation.conflicted || requestOperation.value !== canvasOperationId) {
      return invalidCanvasDirect("creative_media_operation_id_mismatch");
    }
    if (requestSource.conflicted || requestSources.invalid || requestSources.conflicted) {
      return invalidCanvasDirect("creative_media_source_binding_mismatch");
    }
    const operationKind = text(request.operationKind || request.operation_kind);
    if (operationKind !== "canvas.graph.execute") {
      const requestSourceIds = new Set(requestSources.values);
      if (requestSource.value) requestSourceIds.add(requestSource.value);
      if (!sameStringSet(sourceIds, requestSourceIds)) {
        return invalidCanvasDirect("creative_media_source_binding_mismatch");
      }
    }
  }

  return {
    kind: "canvas_supervisor_direct",
    privileged: true,
    routeKind,
    canvasOperationId,
    actionId: text(contract.actionId),
    contract,
  };
}

const AUTHORITY_ID_KEY_GROUPS: Record<CreativeCanvasAuthorityKind, readonly (readonly string[])[]> = {
  source: [["sourceId", "source_id"], ["id"]],
  artifact: [["artifactId", "artifact_id"], ["id"]],
  graph: [["graphId", "graph_id", "canvasGraphId", "canvas_graph_id"], ["id"]],
  run: [["graphRunId", "graph_run_id", "canvasGraphRunId", "canvas_graph_run_id"], ["runId", "run_id"], ["id"]],
  operation: [["canvasOperationId", "canvas_operation_id", "operationId", "operation_id"], ["id"]],
};

function authorityRecordId(
  records: readonly Record<string, unknown>[],
  kind: CreativeCanvasAuthorityKind,
): { id: string; conflicted: boolean } {
  for (const keys of AUTHORITY_ID_KEY_GROUPS[kind]) {
    const declared = collectAliasedTextFromRecords(records, keys);
    if (declared.conflicted) return { id: declared.value, conflicted: true };
    if (declared.value) return { id: declared.value, conflicted: false };
  }
  return { id: "", conflicted: false };
}

export function projectCreativeCanvasAuthorityScope(input: {
  scope: CreativeCanvasAuthorityScope;
  records: Partial<Record<CreativeCanvasAuthorityKind, readonly unknown[]>>;
}): CreativeCanvasAuthorityProjection {
  const scope = {
    sessionId: text(input.scope.sessionId),
    workspaceId: text(input.scope.workspaceId),
  };
  const accepted: CreativeCanvasAuthorityItem[] = [];
  const rejected: CreativeCanvasAuthorityRejection[] = [];

  for (const kind of ["source", "artifact", "graph", "run", "operation"] as const) {
    const values = input.records[kind] || [];
    values.forEach((raw, index) => {
      const value = recordOf(raw);
      const authority = collectAuthorityDeclarations(value);
      const records = authority.records;
      const authorityId = authorityRecordId(records, kind);
      const id = authorityId.id;
      const declaredSessionIds = Array.from(authority.sessionIds);
      const declaredWorkspaceIds = Array.from(authority.workspaceIds);
      const completeScopes = authority.recordDeclarations.flatMap((declaration) => (
        declaration.sessionIds.length === 1 && declaration.workspaceIds.length === 1
          ? [{ sessionId: declaration.sessionIds[0], workspaceId: declaration.workspaceIds[0] }]
          : []
      ));
      const sessionId = completeScopes[0]?.sessionId || declaredSessionIds[0] || "";
      const workspaceId = completeScopes[0]?.workspaceId || declaredWorkspaceIds[0] || "";
      let reason: CreativeCanvasAuthorityRejection["reason"] | null = null;
      if (authorityId.conflicted) reason = "conflicting_authority";
      else if (!id) reason = "missing_id";
      else if (declaredSessionIds.length > 1 || declaredWorkspaceIds.length > 1) reason = "conflicting_authority";
      else if (!declaredSessionIds.length) reason = "missing_session_id";
      else if (!declaredWorkspaceIds.length) reason = "missing_workspace_id";
      else if (!completeScopes.length) reason = "conflicting_authority";
      else if (sessionId !== scope.sessionId) reason = "session_mismatch";
      else if (workspaceId !== scope.workspaceId) reason = "workspace_mismatch";
      if (reason) {
        rejected.push({
          kind,
          index,
          id: id || null,
          reason,
          actualSessionId: sessionId || null,
          actualWorkspaceId: workspaceId || null,
        });
        return;
      }
      accepted.push({ kind, id, sessionId, workspaceId, value });
    });
  }
  return { scope, accepted, rejected };
}

function isCreativeCanvasGraphRunState(value: string): value is CreativeCanvasGraphRunState {
  return (CREATIVE_CANVAS_GRAPH_RUN_STATES as readonly string[]).includes(value);
}

/**
 * Normalizes only the canonical typed graph event. It deliberately ignores
 * summaries and errors, so user-facing prose can never manufacture graph state.
 */
export function normalizeCreativeCanvasGraphRunStateEvent(
  event: unknown,
  expectedScope?: CreativeCanvasAuthorityScope,
): CreativeCanvasGraphRunStateProjection | null {
  const envelope = recordOf(event);
  if (text(envelope.topic).toLowerCase() !== CREATIVE_CANVAS_GRAPH_RUN_STATE_TOPIC) return null;
  const payload = recordOf(envelope.data);
  if (payload.schema !== CREATIVE_CANVAS_GRAPH_RUN_STATE_SCHEMA) return null;

  const authority = collectAuthorityDeclarations(payload);
  if (authority.conflicted || authority.sessionIds.size !== 1 || authority.workspaceIds.size !== 1) return null;
  const sessionId = authority.sessionIds.values().next().value || "";
  const workspaceId = authority.workspaceIds.values().next().value || "";
  const graph = collectAliasedText(payload, ["graphId", "graph_id"]);
  const graphRun = collectAliasedText(payload, ["graphRunId", "graph_run_id"]);
  const operation = collectAliasedText(payload, ["canvasOperationId", "canvas_operation_id"]);
  const chatRun = collectAliasedText(payload, ["runId", "run_id"]);
  const retryOfGraphRun = collectAliasedText(payload, ["retryOfGraphRunId", "retry_of_graph_run_id"]);
  if (graph.conflicted || graphRun.conflicted || operation.conflicted || chatRun.conflicted || retryOfGraphRun.conflicted) {
    return null;
  }
  const graphId = graph.value;
  const graphRunId = graphRun.value;
  const canvasOperationId = operation.value;
  const runId = chatRun.value || null;
  const status = text(payload.status).toLowerCase();
  if (
    !sessionId
    || !workspaceId
    || !graphId
    || !graphRunId
    || !canvasOperationId
    || !isCreativeCanvasGraphRunState(status)
  ) return null;
  if (expectedScope) {
    const expectedSessionId = text(expectedScope.sessionId);
    const expectedWorkspaceId = text(expectedScope.workspaceId);
    if (!expectedSessionId || !expectedWorkspaceId) return null;
    if (sessionId !== expectedSessionId || workspaceId !== expectedWorkspaceId) return null;
  }

  const rawTransition = text(payload.transition).toLowerCase();
  const transition: CreativeCanvasGraphRunTransition | null = rawTransition === "recovered"
    || rawTransition === "retry_failed_branch"
    ? rawTransition
    : null;
  if (rawTransition && !transition) return null;
  const retryOfGraphRunId = retryOfGraphRun.value || null;
  if (transition === "retry_failed_branch" && !retryOfGraphRunId) return null;
  const recovery = recordOf(payload.recovery);
  const canRetryFailedBranch = (
    (status === "failed" || status === "interrupted")
    && recovery.canRetry === true
    && text(recovery.mode) === "failed_branch"
  );
  return {
    schema: CREATIVE_CANVAS_GRAPH_RUN_STATE_SCHEMA,
    topic: CREATIVE_CANVAS_GRAPH_RUN_STATE_TOPIC,
    sessionId,
    workspaceId,
    graphId,
    graphRunId,
    canvasOperationId,
    runId,
    status,
    transition,
    retryOfGraphRunId,
    canRetryFailedBranch,
  };
}

export function projectCreativeCanvasGraphRunHumanSurface(
  event: CreativeCanvasGraphRunStateProjection,
): CreativeCanvasGraphRunHumanSurfaceProjection {
  return {
    kind: "canvas_graph_run_state",
    status: event.status,
    transition: event.transition,
    stateKey: `canvas.graph.run.${event.status}`,
    canRetryFailedBranch: event.canRetryFailedBranch,
  };
}
