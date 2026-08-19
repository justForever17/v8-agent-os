export const SESSION_RUNTIME_IDS = [
  "chat",
  "engineering",
  "engineering_lane",
  "memory",
  "automation",
  "extensions",
  "creative_media",
  "research",
  "network_supervisor",
  "subagent_swarm",
  "computer_use",
  "rpa",
  "desktop_live",
] as const;

export type SessionRuntimeId = (typeof SESSION_RUNTIME_IDS)[number];

export const SUPERVISOR_RUNTIME_MODES = [
  "auto",
  "engineering",
  "research",
  "creative_media",
  "computer_use",
  "rpa",
] as const;

export type SupervisorRuntimeMode = (typeof SUPERVISOR_RUNTIME_MODES)[number];

export function isSupervisorRuntimeMode(value: unknown): value is SupervisorRuntimeMode {
  return typeof value === "string" && (SUPERVISOR_RUNTIME_MODES as readonly string[]).includes(value);
}

export function normalizeSupervisorRuntimeMode(
  value: unknown,
  fallback: SupervisorRuntimeMode = "auto",
): SupervisorRuntimeMode {
  return isSupervisorRuntimeMode(value) ? value : fallback;
}

export type SessionRuntimeScope = "session" | "active_run";
export type SessionRuntimeVisibility = "visible" | "hidden" | "history_only" | "excluded";
export type SessionRuntimeEventTarget =
  | "message"
  | "runtime_card"
  | "runtime_timeline"
  | "hud"
  | "todos_hud"
  | "approval"
  | "artifact"
  | "terminal"
  | "process"
  | "context"
  | "workbench"
  | "history";

export type SessionRuntimeEventSource = {
  plane?: string;
  component?: string;
  node?: string;
  agent_id?: string;
};

export type AdminResourceKind =
  | "artifact_content"
  | "workspace_file"
  | "admin_api"
  | "external_url";

export type PathPlane =
  | "runtime_private"
  | "workspace_download"
  | "workspace_artifact"
  | "channel_delivery_stage";

export type AdminResourceRef = {
  kind: AdminResourceKind;
  adminPath?: string;
  signedUrl?: string;
  artifactId?: string;
  sessionId?: string;
  workspaceId?: string;
  projectId?: string;
  workspacePath?: string;
  workspaceRoot?: string;
  workspaceRelativePath?: string;
  url?: string;
  mimeType?: string;
  displayLabel?: string;
  displaySubtitle?: string;
  previewBlockedReason?: string;
  previewable?: boolean;
  downloadable?: boolean;
  sourcePath?: string;
  surfaceVisible?: boolean;
  pathPlane?: PathPlane;
};

export type AdminProcessRef = {
  processId: string;
  commandId?: string;
  sessionId?: string | null;
  runId?: string | null;
  title?: string;
  commandPreview?: string;
  status?: string;
  backend?: string;
  terminalMode?: "auto" | "pipe" | "pty" | string;
  resolvedTerminalMode?: "pipe" | "pty" | string;
  interactive?: boolean;
  usesTty?: boolean;
  canTerminate?: boolean;
  canInput?: boolean;
  outputAdminPath?: string;
  streamAdminPath?: string;
  inputAdminPath?: string;
  terminateAdminPath?: string;
  sourceMessageId?: string;
  toolCallId?: string;
  startedAt?: string;
  completedAt?: string | null;
  timeoutSeconds?: number | null;
  deadlineAt?: string | null;
  timedOut?: boolean;
  failureKind?: string | null;
  failureMessage?: string | null;
  terminationReason?: string | null;
  secondsSinceOutput?: number | null;
  secondsSinceInput?: number | null;
  ttyMode?: string;
  screenMode?: string;
  screenSnapshot?: string;
  stableScreenSnapshot?: string;
  screenVersion?: number | null;
  rawFrameVersion?: number | null;
  rawBytes?: number | null;
  cursor?: {
    row?: number;
    col?: number;
  } | null;
  cols?: number | null;
  rows?: number | null;
  alternateScreen?: boolean;
  awaitingInput?: boolean;
  observationState?: "busy" | "awaiting_input" | "render_stalled" | "idle" | string;
  textEncoding?: string | null;
  encodingState?: "clean" | "suspect_mojibake" | "undecodable" | string;
  encodingNotes?: string | null;
  lastScreenAt?: string | null;
  lastRawFrameAt?: string | null;
  lastRawFramePreview?: string | null;
  commandDiagnostics?: Record<string, unknown> | null;
};

export type ContextReferenceType = "file" | "memory" | "search" | "web";

export type ContextReferenceItem = {
  id: string;
  type: ContextReferenceType;
  label: string;
  details?: string;
  toolName?: string;
  toolCallId?: string;
  sourceMessageId?: string;
  resourceRef?: AdminResourceRef | null;
};

export type SessionCoordinationIntent = "inform" | "correct" | "request";

export type SessionCoordinationReplyStatus =
  | "acknowledged"
  | "accepted"
  | "conflict"
  | "blocked"
  | "completed";

export type SessionCoordinationState =
  | "awaiting_authorization"
  | "queued"
  | "promoted"
  | "injected"
  | "replied"
  | "cancelled"
  | "blocked"
  | "failed"
  | "expired";

export type SessionCoordinationMessageRef = {
  messageId: string;
  threadId: string;
  messageType: "request" | "reply";
  sourceSessionId: string;
  targetSessionId: string;
  intent: SessionCoordinationIntent;
  authority: "current_user_explicit" | "ask_user_approved" | "bounded_reply";
  state: SessionCoordinationState;
  summary: string;
  replyStatus?: SessionCoordinationReplyStatus;
  replyToMessageId?: string;
  hopCount: 1 | 2;
  maxHops: 2;
  detailRef: string;
  evidenceRefs?: string[];
  direction?: "incoming" | "outgoing";
  createdAt: string;
  updatedAt: string;
  errorCode?: string;
};

export type SessionRuntimeEventName =
  | "agent_start"
  | "text_chunk"
  | "reasoning_chunk"
  | "tool_start"
  | "tool_result"
  | "artifact_recorded"
  | "runtime_progress"
  | "runtime_event"
  | "ask_user"
  | "approval_requested"
  | "approval_resolved"
  | "safety_blocked"
  | "lane_updated"
  | "human_guidance"
  | "session_coordination"
  | "context_governance_changed"
  | "workbench_document_opened"
  | "workbench_document_updated"
  | "workbench_document_unavailable"
  | "run_controlled"
  | "done"
  | "error";

export type AuthoritativeRuntimeTimelineEntry = {
  id: string;
  seq: number;
  runId?: string;
  runtimeId: SessionRuntimeId;
  topic: string;
  kind: "progress" | "tool" | "governance" | "artifact" | "handoff";
  summary: string;
  actorLabel?: string;
  timestamp: number;
  status?: string;
  dedupeKey?: string;
  replacesEventId?: string;
  metadata?: Record<string, unknown>;
};

export type SessionTodoItem = {
  id?: string | null;
  content?: string | null;
  status?: string | null;
};

export type ActiveRunScopedTodos = {
  taskId?: string | null;
  taskName?: string | null;
  runId?: string | null;
  sessionId?: string | null;
  updatedAt?: string | null;
  isActive?: boolean;
  isStale?: boolean;
  allCompleted?: boolean;
  items: SessionTodoItem[];
};

export type SessionSourceRef = {
  sourceId: string;
  sessionId?: string | null;
  workspaceId?: string | null;
  messageId?: string | null;
  sourceKind?: string | null;
  title?: string | null;
  mimeType?: string | null;
  workspacePath?: string | null;
  workspaceRelativePath?: string | null;
  externalUrl?: string | null;
  previewUrl?: string | null;
  resourceRef?: Record<string, unknown> | null;
  metadata?: Record<string, unknown>;
  createdAt?: string | null;
};

export type AuthoritativeSessionSnapshot = {
  session?: Record<string, unknown> | null;
  sessionId?: string;
  latestSeq?: number;
  messagesOmitted?: boolean;
  messages?: unknown[];
  approvals?: unknown[];
  askUserInteractions?: unknown[];
  controls?: Record<string, unknown> | null;
  recoverable?: unknown;
  artifacts?: unknown[];
  sources?: SessionSourceRef[];
  processes?: AdminProcessRef[];
  contextReferences?: ContextReferenceItem[];
  contextGovernance?: Record<string, unknown> | null;
  contextGovernanceHistory?: Record<string, unknown>[];
  sessionCoordinationMessages?: SessionCoordinationMessageRef[];
  todos?: ActiveRunScopedTodos | null;
  workflowProjection?: Record<string, unknown> | null;
  projection?: Record<string, unknown> | null;
  runtimeTimeline?: unknown[];
  currentRun?: Record<string, unknown> | null;
  runtimeStatus?: string | null;
  summary?: Record<string, unknown> | null;
  source?: string | null;
  workflow?: Record<string, unknown> | null;
  snapshot?: {
    messages?: unknown[];
    artifacts?: unknown[];
    sources?: SessionSourceRef[];
    [key: string]: unknown;
  };
};

export type SessionToolResultStatus =
  | "running"
  | "completed"
  | "failed"
  | "blocked"
  | "waiting"
  | "timed_out"
  | "terminated"
  | "unknown";

const SESSION_TOOL_RESULT_STATUSES = new Set<SessionToolResultStatus>([
  "running",
  "completed",
  "failed",
  "blocked",
  "waiting",
  "timed_out",
  "terminated",
  "unknown",
]);

export function normalizeSessionToolResultStatus(value: unknown): SessionToolResultStatus | undefined {
  const raw = String(value || "").trim().toLowerCase();
  const aliases: Record<string, SessionToolResultStatus> = {
    success: "completed",
    succeeded: "completed",
    ok: "completed",
    error: "failed",
    failure: "failed",
    timeout: "timed_out",
    deadline_exceeded: "timed_out",
    cancelled: "terminated",
    canceled: "terminated",
    stopped: "terminated",
    interrupted: "terminated",
    safety_blocked: "blocked",
    denied: "blocked",
    rejected: "blocked",
    awaiting_input: "waiting",
    waiting_input: "waiting",
    waiting_approval: "waiting",
    approval_required: "waiting",
    queued: "running",
    pending: "running",
    starting: "running",
    streaming: "running",
  };
  const normalized = (aliases[raw] || raw) as SessionToolResultStatus;
  return SESSION_TOOL_RESULT_STATUSES.has(normalized) ? normalized : undefined;
}

export type NormalizedSessionRuntimeEvent = {
  type: string;
  name?: SessionRuntimeEventName | string;
  topic?: string;
  runtimeId?: SessionRuntimeId;
  ownerRuntimeId?: SessionRuntimeId;
  ownerAgentKind?: "supervisor" | "runtime" | "subagent" | "shard" | string;
  ownerAgentId?: string;
  ownerStreamKey?: string;
  traceGroupId?: string;
  surfaceTargets?: SessionRuntimeEventTarget[];
  scope: SessionRuntimeScope;
  visibility: SessionRuntimeVisibility;
  targets: SessionRuntimeEventTarget[];
  seq?: number;
  session_id?: string;
  conversation_id?: string;
  run_id?: string;
  message_id?: string;
  node_id?: string;
  transcript_version?: number;
  event_id?: string;
  ts?: string;
  status?: string;
  actorLabel?: string;
  content?: string;
  reasoningKind?: "raw_thinking" | "summary" | "provider_reasoning" | "hidden" | string;
  error?: string;
  tool?: {
    toolCallId?: string;
    toolInvocationId?: string;
    toolName?: string;
    args?: unknown;
    result?: unknown;
    resultStatus?: SessionToolResultStatus;
    resultReasonCode?: string;
    agentVisibleResult?: unknown;
    agentVisibleChars?: number;
    mcpApp?: McpAppViewRef;
  };
  mcpApp?: McpAppViewRef;
  data?: Record<string, unknown>;
  artifact?: Record<string, unknown>;
  source?: SessionRuntimeEventSource;
  raw?: Record<string, unknown>;
};

export type PluginGrantScope = "task" | "session";

export type PluginReferenceStatus =
  | "ready"
  | "not_installed"
  | "needs_configuration"
  | "offline"
  | "invalid";

export type PluginReferenceSummary = {
  pluginId: string;
  displayName: string;
  status: PluginReferenceStatus;
  configurationUrl: string;
  grantScope: PluginGrantScope;
  componentIds?: string[];
  description?: string;
};

export type PluginReferenceSelection = {
  pluginId: string;
  scope: PluginGrantScope;
  componentIds?: string[];
  name?: string;
};

export type V8ActionRequestKind =
  | "secret_input"
  | "user_presence"
  | "unlock_required"
  | "computer_use_handoff"
  | "rpa_handoff";

export type V8ActionRequestState =
  | "pending"
  | "submitted"
  | "expired"
  | "cancelled"
  | "failed";

export type V8ActionRequestRef = {
  actionRequestId: string;
  sessionId: string;
  kind: V8ActionRequestKind;
  state: V8ActionRequestState;
  title: string;
  description?: string;
  targetLabel?: string;
  fields?: Array<{
    id: string;
    kind: "secret" | "text" | "boolean" | "choice";
    label: string;
    help?: string | null;
    required: boolean;
    options?: string[];
    autocomplete?: string;
  }>;
  submitLabel?: string;
  expiresAt: string;
  result?: Record<string, unknown>;
  error?: { code?: string; message?: string } | null;
};

export type McpAppViewRef = {
  appInstanceId: string;
  serverName?: string;
  resourceUri: string;
  toolInvocationId?: string;
  initialToolResultRef?: string | null;
  csp?: Record<string, unknown>;
  permissions?: Record<string, unknown>;
  status?: string;
  renderer?: "figma" | string;
  pluginId?: string;
  pluginDigest?: string;
  grantId?: string;
  expiresAt?: string;
  title?: string;
  externalUrl?: string;
  thumbnailUrl?: string;
  fileKey?: string;
  nodeId?: string;
  presentation?: {
    web?: "inline" | "edge_to_edge";
    phone?: "inline" | "modal";
  };
  allowedFrameOrigins?: string[];
  actionRequest?: V8ActionRequestRef;
};

export type WorkbenchMode = "closed" | "split" | "focus";

export type WorkbenchDocumentLifecycle = "session" | "runtime";

export type WorkbenchDocumentStatus =
  | "available"
  | "loading"
  | "ready"
  | "unavailable";

export type WorkbenchDocumentCapability =
  | "read"
  | "search"
  | "copy"
  | "download"
  | "interact"
  | "navigate"
  | "control"
  | "focus";

type WorkbenchDocumentBase<
  Kind extends string,
  Renderer extends string,
  SubjectRef extends Record<string, unknown>,
> = {
  kind: Kind;
  documentId: string;
  title: string;
  renderer: Renderer;
  lifecycle: WorkbenchDocumentLifecycle;
  status: WorkbenchDocumentStatus;
  capabilities: WorkbenchDocumentCapability[];
  subjectRef: SubjectRef;
  createdAt?: string;
  updatedAt?: string;
  unavailableReason?: string;
};

export type SessionOverviewWorkbenchDocumentRef = WorkbenchDocumentBase<
  "session_overview",
  "session_overview",
  { sessionId: string }
>;

export type SubagentActivityWorkbenchDocumentRef = WorkbenchDocumentBase<
  "subagent_activity",
  "subagent_activity",
  { sessionId: string; delegationId: string }
>;

export type WorkspaceFileWorkbenchDocumentRef = WorkbenchDocumentBase<
  "workspace_file",
  "code" | "text" | "markdown" | "html" | "metadata",
  { sessionId: string; workspacePath: string; line?: number }
>;

export type ArtifactWorkbenchDocumentRef = WorkbenchDocumentBase<
  "artifact",
  "image" | "video" | "audio" | "code" | "text" | "markdown" | "html" | "pdf" | "model_3d" | "download",
  { artifactId: string; sessionId?: string }
>;

export type UiAppWorkbenchDocumentRef = WorkbenchDocumentBase<
  "ui_app",
  "mcp_app" | "figma_canvas",
  { app: McpAppViewRef }
>;

export type BrowserWorkbenchDocumentRef = WorkbenchDocumentBase<
  "browser",
  "browser",
  { browserSessionId: string; sessionId: string }
>;

export type WorkbenchDocumentRef =
  | SessionOverviewWorkbenchDocumentRef
  | SubagentActivityWorkbenchDocumentRef
  | WorkspaceFileWorkbenchDocumentRef
  | ArtifactWorkbenchDocumentRef
  | UiAppWorkbenchDocumentRef
  | BrowserWorkbenchDocumentRef;

export type SessionRealtimeStore = {
  snapshot: AuthoritativeSessionSnapshot | null;
  latestSeq: number;
  lastSnapshotFingerprint: string;
  lastRuntimeEvent: NormalizedSessionRuntimeEvent | null;
  unreadProgressHint: boolean;
};
