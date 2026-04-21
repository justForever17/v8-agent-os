export const SESSION_RUNTIME_IDS = [
  "chat",
  "planner_lane",
  "memory",
  "automation",
  "extensions",
  "network_supervisor",
  "plugin_host_tool",
  "plugin_host_channel",
  "subagent_swarm",
  "computer_use",
  "rpa",
  "desktop_live",
] as const;

export type SessionRuntimeId = (typeof SESSION_RUNTIME_IDS)[number];

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
  | "context_governance_changed"
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

export type AuthoritativeSessionSnapshot = {
  session?: Record<string, unknown> | null;
  sessionId?: string;
  latestSeq?: number;
  messages?: unknown[];
  approvals?: unknown[];
  askUserInteractions?: unknown[];
  controls?: Record<string, unknown> | null;
  recoverable?: unknown;
  artifacts?: unknown[];
  processes?: AdminProcessRef[];
  contextReferences?: ContextReferenceItem[];
  contextGovernance?: Record<string, unknown> | null;
  contextGovernanceHistory?: Record<string, unknown>[];
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
    [key: string]: unknown;
  };
};

export type NormalizedSessionRuntimeEvent = {
  type: string;
  name?: SessionRuntimeEventName | string;
  topic?: string;
  runtimeId?: SessionRuntimeId;
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
  error?: string;
  tool?: {
    toolCallId?: string;
    toolName?: string;
    args?: unknown;
    result?: unknown;
  };
  data?: Record<string, unknown>;
  artifact?: Record<string, unknown>;
  source?: SessionRuntimeEventSource;
  raw?: Record<string, unknown>;
};

export type SessionRealtimeStore = {
  snapshot: AuthoritativeSessionSnapshot | null;
  latestSeq: number;
  lastSnapshotFingerprint: string;
  lastRuntimeEvent: NormalizedSessionRuntimeEvent | null;
  unreadProgressHint: boolean;
};
