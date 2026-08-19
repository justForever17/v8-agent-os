import type { AdminResourceRef, McpAppViewRef, NormalizedSessionRuntimeEvent, SessionToolResultStatus } from "./contract.js";
import { normalizeSessionToolResultStatus } from "./contract.js";
import { deriveAdminResourceRefFromArtifactLike } from "./resources.js";
import { isTodoToolRuntimePayload, shouldForwardRuntimeEventToRealtimeSurface } from "./event-normalizer.js";
import { buildSessionRuntimeEventIdentity } from "./event-sequence.js";

export type SessionStreamPhase =
  | "placeholder"
  | "agent_started"
  | "task_planning"
  | "tooling"
  | "artifact_ready"
  | "waiting_input"
  | "streaming"
  | "settling"
  | "error";

export type SessionAgentProfile = {
  agentName?: string;
  agentAvatar?: string;
  agentRoleLabel?: string;
};

export type SessionStreamToolPayload = {
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

export type SessionStreamArtifact = {
  id?: string;
  artifactId?: string;
  title?: string;
  displayLabel?: string;
  displaySubtitle?: string;
  kind?: string;
  previewUrl?: string;
  externalUrl?: string;
  sourcePath?: string;
  workspacePath?: string;
  mimeType?: string;
  resourceRef?: AdminResourceRef | null;
  [key: string]: unknown;
};

export type SessionStreamUiEvent = Pick<
  NormalizedSessionRuntimeEvent,
  "type" | "name" | "content" | "data" | "run_id" | "error" | "targets" | "visibility" | "topic" | "runtimeId" | "seq" | "event_id" | "ts" | "message_id" | "node_id" | "transcript_version" | "reasoningKind"
> & {
  agent?: {
    id?: string;
    name?: string;
    avatar?: string;
    roleLabel?: string;
  };
  tool?: SessionStreamToolPayload;
  artifact?: SessionStreamArtifact | null;
};

export type SessionStreamNarrativeNode = {
  id: string;
  kind: "narrative";
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: number;
  eventSeq?: number;
  eventId?: string;
  runId?: string;
  agentName?: string;
  agentAvatar?: string;
  agentRoleLabel?: string;
  agentType?: "supervisor" | "agent" | "user";
  ownerRuntimeId?: string;
  ownerAgentKind?: string;
  ownerAgentId?: string;
  ownerStreamKey?: string;
  traceGroupId?: string;
  displayInMessage?: boolean;
  finalized?: boolean;
  partial?: boolean;
};

export type SessionStreamExecutionNode = {
  id: string;
  kind: "execution";
  executionType: "reasoning" | "tool_call" | "tool_result" | "runtime_progress" | "agent_start";
  timestamp: number;
  eventSeq?: number;
  eventId?: string;
  runId?: string;
  agentName?: string;
  agentAvatar?: string;
  agentRoleLabel?: string;
  agentType?: "supervisor" | "agent" | "user";
  content?: string;
  reasoningKind?: string;
  time?: number;
  startTime?: number;
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
  topic?: string;
  label?: string;
  data?: Record<string, unknown>;
  ownerRuntimeId?: string;
  ownerAgentKind?: string;
  ownerAgentId?: string;
  ownerStreamKey?: string;
  traceGroupId?: string;
  displayInMessage?: boolean;
};

export type SessionStreamGovernanceNode = {
  id: string;
  kind: "governance";
  governanceType: "ask_user" | "approval_request" | "approval_resolved" | "run_controlled" | "safety_blocked" | "context_governance" | "lane_updated" | "human_guidance" | "session_coordination";
  timestamp: number;
  eventSeq?: number;
  eventId?: string;
  runId?: string;
  agentName?: string;
  agentAvatar?: string;
  agentRoleLabel?: string;
  agentType?: "supervisor" | "agent" | "user";
  approvalId?: string;
  approvalKind?: string;
  interactionKind?: string;
  question?: string;
  toolCallId?: string;
  toolInvocationId?: string;
  requestInfo?: unknown;
  topic?: string;
  status?: string;
  reason?: string;
  ownerRuntimeId?: string;
  ownerAgentKind?: string;
  ownerAgentId?: string;
  ownerStreamKey?: string;
  traceGroupId?: string;
  displayInMessage?: boolean;
};

export type SessionStreamArtifactNode = {
  id: string;
  kind: "artifact";
  timestamp: number;
  eventSeq?: number;
  eventId?: string;
  runId?: string;
  agentName?: string;
  agentAvatar?: string;
  agentRoleLabel?: string;
  agentType?: "supervisor" | "agent" | "user";
  artifact: SessionStreamArtifact;
  ownerRuntimeId?: string;
  ownerAgentKind?: string;
  ownerAgentId?: string;
  ownerStreamKey?: string;
  traceGroupId?: string;
  displayInMessage?: boolean;
};

export type SessionStreamTimelineNode =
  | SessionStreamNarrativeNode
  | SessionStreamExecutionNode
  | SessionStreamGovernanceNode
  | SessionStreamArtifactNode;

export type SessionStreamMessage = {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  timestamp?: number;
  runId?: string;
  renderKey?: string;
  agentName?: string;
  agentAvatar?: string;
  agentRoleLabel?: string;
  agentType?: "supervisor" | "agent" | "user";
  nodes?: SessionStreamTimelineNode[];
  images?: string[];
  artifacts?: SessionStreamArtifact[];
  metadata?: Record<string, unknown>;
  uiEphemeral?: boolean;
  uiStreamPhase?: SessionStreamPhase;
};

export type SessionStreamState<TMessage extends SessionStreamMessage = SessionStreamMessage> = {
  currentAiMsg: TMessage | undefined;
  activeAgentProfile: SessionAgentProfile;
};

export type SessionStreamLifecycleOptions = {
  createId?: (prefix: string) => string;
  defaultAgentProfile?: Required<SessionAgentProfile>;
  resolveAgentProfile?: (
    event: SessionStreamUiEvent,
    fallback: SessionAgentProfile,
    defaultAgentProfile: Required<SessionAgentProfile>,
  ) => SessionAgentProfile;
  resolveArtifact?: (event: SessionStreamUiEvent) => SessionStreamArtifact | null;
  coalescedRuntimeTopics?: string[];
};

const DEFAULT_AGENT_PROFILE: Required<SessionAgentProfile> = {
  agentName: "智能主管",
  agentAvatar: "/brand-mark.png",
  agentRoleLabel: "主理人",
};

const DEFAULT_COALESCED_RUNTIME_TOPICS = new Set([
  "computer_use.step.heartbeat",
  "computer_use.step.waiting_for_window",
  "computer_use.action.settle_wait_started",
]);

function nextId(prefix: string, createId?: (prefix: string) => string) {
  if (createId) {
    return createId(prefix);
  }
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function eventTimestamp(event: Pick<SessionStreamUiEvent, "ts">) {
  const parsed = typeof event.ts === "string" ? Date.parse(event.ts) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : Date.now();
}

function eventTimelineFields(event: SessionStreamUiEvent) {
  return {
    timestamp: eventTimestamp(event),
    eventSeq: Number(event.seq || 0) || undefined,
    eventId: buildSessionRuntimeEventIdentity(event),
    runId: String(event.run_id || "").trim() || undefined,
  };
}

function applyEventTimelineFields(
  node: SessionStreamTimelineNode,
  fields: ReturnType<typeof eventTimelineFields>,
) {
  if (!node.eventSeq && fields.eventSeq) node.eventSeq = fields.eventSeq;
  if (!node.eventId && fields.eventId) node.eventId = fields.eventId;
  if (!node.runId && fields.runId) node.runId = fields.runId;
  if (!Number.isFinite(Number(node.timestamp || 0)) || Number(node.timestamp || 0) <= 0) {
    node.timestamp = fields.timestamp;
  }
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function recordedAttachmentUrl(attachment: Record<string, unknown>): string {
  return firstNonEmptyString(
    attachment.previewUrl,
    attachment.publicUrl,
    attachment.url,
    attachment.workspacePath,
    attachment.workspaceRelativePath,
  );
}

function attachmentClassification(value: unknown) {
  const attachment = asRecord(value);
  const declaredKind = String(attachment.mediaKind || attachment.previewKind || attachment.kind || "").toLowerCase();
  const declaredMime = String(attachment.mimeType || attachment.mime_type || attachment.type || "").toLowerCase();
  const probe = [
    attachment.name,
    recordedAttachmentUrl(attachment),
  ].map((value) => String(value || "").toLowerCase()).join(" ");
  return { declaredKind, declaredMime, probe };
}

export function isClientVisualAttachment(value: unknown): boolean {
  const { declaredKind, declaredMime, probe } = attachmentClassification(value);
  if (declaredKind === "image" || declaredKind === "video" || declaredMime.startsWith("image/") || declaredMime.startsWith("video/")) {
    return true;
  }
  if (
    declaredKind === "audio"
    || declaredKind === "document"
    || declaredKind === "file"
    || declaredMime.startsWith("audio/")
    || (declaredMime && declaredMime !== "application/octet-stream")
  ) {
    return false;
  }
  // WebM is intentionally excluded from an extension-only visual guess: old
  // voice uploads used it too. A real WebM video carries video/webm metadata.
  return /\.(png|jpe?g|webp|gif|bmp|heic|heif|mp4|mov|m4v|mkv|avi)(?:[?#\s].*)?$/i.test(probe);
}

export function isClientAudioAttachment(value: unknown): boolean {
  const { declaredKind, declaredMime, probe } = attachmentClassification(value);
  if (declaredKind === "image" || declaredKind === "video" || declaredMime.startsWith("image/") || declaredMime.startsWith("video/")) {
    return false;
  }
  if (declaredKind === "audio" || declaredMime.startsWith("audio/")) {
    return true;
  }
  if (
    declaredKind === "document"
    || declaredKind === "file"
    || (declaredMime && declaredMime !== "application/octet-stream")
  ) {
    return false;
  }
  return /\.(mp3|m4a|wav|ogg|opus|aac|flac|webm)(?:[?#\s].*)?$/i.test(probe);
}

function buildRecordedUserMessage(
  event: SessionStreamUiEvent,
  eventData: Record<string, unknown>,
): SessionStreamMessage {
  const timelineFields = eventTimelineFields(event);
  const clientMessageId = firstNonEmptyString(eventData.clientMessageId, eventData.client_message_id);
  const messageId = firstNonEmptyString(
    event.message_id,
    eventData.message_id,
    eventData.messageId,
    clientMessageId,
    event.event_id,
    buildSessionRuntimeEventIdentity(event),
  );
  const content = firstNonEmptyString(eventData.content, event.content);
  const attachments = Array.isArray(eventData.attachments)
    ? eventData.attachments.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    : [];
  const nonVisualUrls = new Set(
    attachments
      .filter((attachment) => !isClientVisualAttachment(attachment))
      .map(recordedAttachmentUrl)
      .filter(Boolean)
      .map((value) => value.toLowerCase()),
  );
  const images = (Array.isArray(eventData.images) ? eventData.images : [])
    .map((value) => String(value || "").trim())
    .filter((value) => value && !nonVisualUrls.has(value.toLowerCase()));
  const metadata = {
    ...asRecord(eventData.metadata),
    ...(clientMessageId ? { clientMessageId } : {}),
    ...(attachments.length ? { attachments } : {}),
    transcriptVersion: event.transcript_version,
  };
  const nodes: SessionStreamTimelineNode[] = [];
  if (content) {
    nodes.push({
      id: event.node_id || `${messageId}:narrative`,
      kind: "narrative",
      role: "user",
      content,
      agentType: "user",
      ...timelineFields,
    });
  }
  attachments.forEach((attachment, index) => {
    const attachmentId = firstNonEmptyString(attachment.id, attachment.sourceId, attachment.source_id, `${messageId}:attachment:${index}`);
    const name = firstNonEmptyString(attachment.name, `attachment-${index + 1}`);
    const mimeType = firstNonEmptyString(attachment.mimeType, attachment.type);
    const mediaKind = firstNonEmptyString(attachment.mediaKind, mimeType.split("/", 1)[0], "file");
    nodes.push({
      id: `${messageId}:source:${attachmentId}`,
      kind: "artifact",
      agentType: "user",
      artifact: {
        ...attachment,
        id: attachmentId,
        sourceId: firstNonEmptyString(attachment.sourceId, attachment.source_id, attachment.id) || undefined,
        resourceRole: "source",
        kind: mediaKind,
        title: name,
        displayLabel: name,
        previewUrl: firstNonEmptyString(attachment.previewUrl, attachment.publicUrl, attachment.url) || undefined,
        externalUrl: firstNonEmptyString(attachment.publicUrl, attachment.url) || undefined,
        sourcePath: firstNonEmptyString(attachment.workspaceRelativePath, attachment.workspacePath, attachment.url) || undefined,
        mimeType: mimeType || undefined,
      },
      ...timelineFields,
    });
  });
  return {
    id: messageId,
    role: "user",
    content,
    runId: timelineFields.runId,
    timestamp: timelineFields.timestamp,
    agentType: "user",
    nodes,
    images,
    artifacts: [],
    metadata,
  };
}

function firstNonEmptyString(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return "";
}

function resolveSurfaceToolCallId(
  event: SessionStreamUiEvent,
  eventData: Record<string, unknown>,
  nestedToolData: Record<string, unknown>,
  fallbackId: string,
): string {
  return firstNonEmptyString(
    event.tool?.toolInvocationId,
    event.tool?.toolCallId,
    eventData.toolInvocationId,
    eventData.tool_call_id,
    eventData.toolCallId,
    nestedToolData.toolInvocationId,
    nestedToolData.tool_invocation_id,
    nestedToolData.toolCallId,
    nestedToolData.tool_call_id,
    fallbackId,
  );
}

function isCommandToolName(toolName: unknown): boolean {
  if (typeof toolName !== "string") {
    return false;
  }
  return ["run_system_command", "execute_system_command", "command_session_broker", "read_background_output", "send_background_input"].includes(toolName);
}

function looksLikeCommandControlJson(value: unknown): boolean {
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    const metadataKeys = ["ok", "kind", "summary", "recommendedNextAction", "state", "awaitingInput"];
    return metadataKeys.filter((key) => key in record).length >= 2;
  }
  if (typeof value !== "string") {
    return false;
  }
  const trimmed = value.trim();
  return trimmed.startsWith("{")
    && trimmed.includes('"')
    && (trimmed.includes('"summary"') || trimmed.includes('"recommendedNextAction"') || trimmed.includes('"state"'))
    && (trimmed.includes('"kind"') || trimmed.includes('"ok"') || trimmed.includes('"mode"'));
}

function looksLikeTerminalResult(value: unknown): boolean {
  if (typeof value !== "string") {
    return false;
  }
  const trimmed = value.trimStart();
  return trimmed.startsWith("$ ")
    || trimmed.includes("\n<stdout>")
    || trimmed.includes("\n<stderr>")
    || trimmed.includes("[exit code:")
    || trimmed.includes("[completed with no output]")
    || trimmed.includes("[waiting for input]");
}

function chooseAgentVisibleToolResult(toolName: unknown, agentVisibleResult: unknown, fallbackResult: unknown): unknown {
  if (isCommandToolName(toolName) && looksLikeCommandControlJson(agentVisibleResult) && looksLikeTerminalResult(fallbackResult)) {
    return fallbackResult;
  }
  return agentVisibleResult ?? fallbackResult;
}

function eventTargetsMessage(event: Pick<SessionStreamUiEvent, "targets" | "data">) {
  const targets = Array.isArray(event.targets) ? event.targets : [];
  if (targets.includes("message")) {
    return true;
  }
  const data = asRecord(event.data);
  const surfaceTargets = Array.isArray(data.surfaceTargets) ? data.surfaceTargets : [];
  return surfaceTargets.includes("message");
}

function eventDisplayInMessage(event: Pick<SessionStreamUiEvent, "targets" | "data">) {
  const data = asRecord(event.data);
  if (data.displayInMessage === false || data.display_in_message === false) {
    return false;
  }
  if (data.displayInMessage === true || data.display_in_message === true) {
    return true;
  }
  return eventTargetsMessage(event);
}

function ownerFieldsFromEvent(event: Pick<SessionStreamUiEvent, "runtimeId" | "data" | "targets">) {
  const data = asRecord(event.data);
  return {
    ownerRuntimeId:
      typeof data.ownerRuntimeId === "string"
        ? data.ownerRuntimeId
        : typeof data.owner_runtime_id === "string"
          ? data.owner_runtime_id
          : event.runtimeId,
    ownerAgentKind:
      typeof data.ownerAgentKind === "string"
        ? data.ownerAgentKind
        : typeof data.owner_agent_kind === "string"
          ? data.owner_agent_kind
          : undefined,
    ownerAgentId:
      typeof data.ownerAgentId === "string"
        ? data.ownerAgentId
        : typeof data.owner_agent_id === "string"
          ? data.owner_agent_id
          : undefined,
    ownerStreamKey:
      typeof data.ownerStreamKey === "string"
        ? data.ownerStreamKey
        : typeof data.owner_stream_key === "string"
          ? data.owner_stream_key
          : undefined,
    traceGroupId:
      typeof data.traceGroupId === "string"
        ? data.traceGroupId
        : typeof data.trace_group_id === "string"
          ? data.trace_group_id
          : undefined,
    displayInMessage: eventDisplayInMessage(event),
  };
}

function nodeDisplayInMessage(node: SessionStreamTimelineNode) {
  return node.displayInMessage !== false;
}

function resolveDefaultAgentProfile(options?: SessionStreamLifecycleOptions) {
  return options?.defaultAgentProfile || DEFAULT_AGENT_PROFILE;
}

function buildAgentProfileFromEvent(
  event: SessionStreamUiEvent,
  fallback: SessionAgentProfile,
  defaultAgentProfile: Required<SessionAgentProfile>,
) {
  const eventData = asRecord(event.data);
  const agentData = asRecord(eventData.agent);
  return {
    agentName:
      event.agent?.name
      || (typeof eventData.agentName === "string" ? eventData.agentName : "")
      || (typeof agentData.name === "string" ? agentData.name : "")
      || fallback.agentName
      || defaultAgentProfile.agentName,
    agentAvatar:
      event.agent?.avatar
      || (typeof eventData.agentAvatar === "string" ? eventData.agentAvatar : "")
      || (typeof agentData.avatar === "string" ? agentData.avatar : "")
      || fallback.agentAvatar
      || defaultAgentProfile.agentAvatar,
    agentRoleLabel:
      event.agent?.roleLabel
      || (typeof eventData.agentRoleLabel === "string" ? eventData.agentRoleLabel : "")
      || (typeof agentData.roleLabel === "string" ? agentData.roleLabel : "")
      || fallback.agentRoleLabel
      || defaultAgentProfile.agentRoleLabel,
  };
}

function resolveArtifactFromEvent(event: SessionStreamUiEvent): SessionStreamArtifact | null {
  const artifact = event.artifact || asRecord(event.data).artifact || event.data;
  const record = asRecord(artifact);
  if (
    !record.id
    && !record.artifactId
    && !record.artifact_id
    && !record.workspacePath
    && !record.workspace_path
    && !record.sourcePath
    && !record.source_path
    && !record.previewUrl
    && !record.preview_url
    && !record.externalUrl
    && !record.external_url
    && !record.resourceRef
    && !record.title
  ) {
    return null;
  }
  return {
    id: typeof record.id === "string" ? record.id : undefined,
    artifactId:
      typeof record.artifactId === "string"
        ? record.artifactId
        : typeof record.artifact_id === "string"
          ? record.artifact_id
          : undefined,
    title: typeof record.title === "string" ? record.title : undefined,
    displayLabel:
      typeof record.displayLabel === "string"
        ? record.displayLabel
        : typeof record.display_label === "string"
          ? record.display_label
          : undefined,
    displaySubtitle:
      typeof record.displaySubtitle === "string"
        ? record.displaySubtitle
        : typeof record.display_subtitle === "string"
          ? record.display_subtitle
          : undefined,
    kind: typeof record.kind === "string" ? record.kind : undefined,
    previewUrl:
      typeof record.previewUrl === "string"
        ? record.previewUrl
        : typeof record.preview_url === "string"
          ? record.preview_url
          : undefined,
    externalUrl:
      typeof record.externalUrl === "string"
        ? record.externalUrl
        : typeof record.external_url === "string"
          ? record.external_url
          : undefined,
    sourcePath:
      typeof record.sourcePath === "string"
        ? record.sourcePath
        : typeof record.source_path === "string"
          ? record.source_path
          : undefined,
    workspacePath:
      typeof record.workspacePath === "string"
        ? record.workspacePath
        : typeof record.workspace_path === "string"
          ? record.workspace_path
          : undefined,
    mimeType:
      typeof record.mimeType === "string"
        ? record.mimeType
        : typeof record.mime_type === "string"
          ? record.mime_type
          : undefined,
    resourceRef: deriveAdminResourceRefFromArtifactLike(record),
    ...record,
  };
}

function artifactKey(artifact: SessionStreamArtifact) {
  return String(
    artifact.id
    || artifact.artifactId
    || artifact.workspacePath
    || artifact.sourcePath
    || artifact.resourceRef?.adminPath
    || artifact.resourceRef?.url
    || artifact.previewUrl
    || artifact.externalUrl
    || artifact.title
    || "",
  ).trim();
}

function ensureAssistantIdentity(
  message: SessionStreamMessage,
  profile: SessionAgentProfile,
  options?: { overwrite?: boolean },
) {
  if (options?.overwrite || !message.agentName) {
    message.agentName = profile.agentName || message.agentName || DEFAULT_AGENT_PROFILE.agentName;
  }
  if (options?.overwrite || !message.agentAvatar) {
    message.agentAvatar = profile.agentAvatar || message.agentAvatar || DEFAULT_AGENT_PROFILE.agentAvatar;
  }
  if (options?.overwrite || !message.agentRoleLabel) {
    message.agentRoleLabel = profile.agentRoleLabel || message.agentRoleLabel || DEFAULT_AGENT_PROFILE.agentRoleLabel;
  }
  if (options?.overwrite || !message.agentType) {
    message.agentType = message.agentType || "supervisor";
  }
}

function shouldPromoteMessageIdentity(
  currentMessage: SessionStreamMessage,
  profile: SessionAgentProfile,
  defaultAgentProfile: Required<SessionAgentProfile>,
) {
  const currentName = String(currentMessage.agentName || "").trim();
  const currentRole = String(currentMessage.agentRoleLabel || "").trim();
  const nextName = String(profile.agentName || "").trim();
  const nextRole = String(profile.agentRoleLabel || "").trim();
  return (
    !currentName
    || currentName === defaultAgentProfile.agentName
    || currentRole === defaultAgentProfile.agentRoleLabel
    || nextName === defaultAgentProfile.agentName
    || nextRole === defaultAgentProfile.agentRoleLabel
  );
}

function upsertCurrentAiMessage<TMessage extends SessionStreamMessage>(localMessages: TMessage[], currentAiMsg: TMessage) {
  const index = localMessages.findIndex((message) => message.id === currentAiMsg.id);
  if (index >= 0) {
    localMessages[index] = currentAiMsg;
  } else {
    localMessages.push(currentAiMsg);
  }
  return currentAiMsg;
}

function ensureCurrentAiMessage<TMessage extends SessionStreamMessage>(
  localMessages: TMessage[],
  currentAiMsg: TMessage | undefined,
  activeAgentProfile: SessionAgentProfile,
  runId: string | undefined,
  options?: SessionStreamLifecycleOptions,
) {
  let nextCurrentAiMsg = currentAiMsg;
  if (!nextCurrentAiMsg) {
    nextCurrentAiMsg = buildAssistantMessage(activeAgentProfile, runId, "placeholder", options) as TMessage;
    localMessages.push(nextCurrentAiMsg);
  }
  if (!Array.isArray(nextCurrentAiMsg.nodes)) {
    nextCurrentAiMsg.nodes = [];
  }
  if (!Array.isArray(nextCurrentAiMsg.images)) {
    nextCurrentAiMsg.images = [];
  }
  if (!Array.isArray(nextCurrentAiMsg.artifacts)) {
    nextCurrentAiMsg.artifacts = [];
  }
  if (runId) {
    nextCurrentAiMsg.runId = runId;
  }
  ensureAssistantIdentity(nextCurrentAiMsg, activeAgentProfile);
  return nextCurrentAiMsg;
}

function appendNode(message: SessionStreamMessage, node: SessionStreamTimelineNode) {
  const nodes = Array.isArray(message.nodes) ? message.nodes : [];
  nodes.push(node);
  message.nodes = nodes;
  message.timestamp = Date.now();
}

function buildRuntimeProgressNode(
  topic: string,
  label: string,
  data: Record<string, unknown>,
  profile: SessionAgentProfile,
  options?: SessionStreamLifecycleOptions,
): SessionStreamExecutionNode {
  return {
    id: nextId("node", options?.createId),
    kind: "execution",
    executionType: "runtime_progress",
    topic,
    label,
    data,
    timestamp: Date.now(),
    ...profile,
  };
}

function appendNarrativeContent(
  message: SessionStreamMessage,
  content: string,
  profile: SessionAgentProfile,
  options?: SessionStreamLifecycleOptions,
  nodeOptions?: Pick<SessionStreamNarrativeNode, "ownerRuntimeId" | "ownerAgentKind" | "ownerAgentId" | "ownerStreamKey" | "displayInMessage" | "eventSeq" | "eventId" | "runId" | "timestamp">,
) {
  if (nodeOptions?.displayInMessage === false) {
    return;
  }
  const normalizedContent = String(content || "").trim();
  if (!normalizedContent) {
    return;
  }
  const lastNode = Array.isArray(message.nodes) ? message.nodes[message.nodes.length - 1] : undefined;
  if (
    canMergeNarrativeNode(lastNode, profile, nodeOptions)
  ) {
    lastNode.content = `${String(lastNode.content || "")}${normalizedContent}`;
    const { eventSeq, eventId, runId, timestamp, ...displayFields } = nodeOptions || {};
    Object.assign(lastNode, displayFields);
    applyEventTimelineFields(lastNode, { eventSeq, eventId: eventId || "", runId, timestamp: timestamp || Date.now() });
  } else {
    appendNode(message, {
      id: nextId("node", options?.createId),
      kind: "narrative",
      role: "assistant",
      content: normalizedContent,
      timestamp: Date.now(),
      ...profile,
      ...nodeOptions,
    });
  }
  message.content = `${String(message.content || "")}${normalizedContent}`;
}

function narrativeStreamKeyMatches(
  node: Pick<SessionStreamNarrativeNode, "ownerStreamKey"> | undefined,
  nodeOptions?: Pick<SessionStreamNarrativeNode, "ownerStreamKey">,
) {
  const incomingKey = String(nodeOptions?.ownerStreamKey || "").trim();
  const existingKey = String(node?.ownerStreamKey || "").trim();
  if (incomingKey) {
    return existingKey === incomingKey;
  }
  return !existingKey;
}

function timelineNodeFinalized(node: SessionStreamTimelineNode | undefined) {
  return Boolean(node && "finalized" in node && (node as { finalized?: boolean }).finalized === true);
}

function canMergeNarrativeNode(
  node: SessionStreamTimelineNode | undefined,
  profile: SessionAgentProfile,
  nodeOptions?: Pick<SessionStreamNarrativeNode, "ownerStreamKey">,
): node is SessionStreamNarrativeNode {
  return Boolean(
    node
    && node.kind === "narrative"
    && node.role === "assistant"
    && !timelineNodeFinalized(node)
    && node.agentName === profile.agentName
    && narrativeStreamKeyMatches(node, nodeOptions),
  );
}

function executionStreamKeyMatches(
  node: Pick<SessionStreamExecutionNode, "ownerStreamKey"> | undefined,
  nodeOptions?: Pick<SessionStreamExecutionNode, "ownerStreamKey">,
) {
  const incomingKey = String(nodeOptions?.ownerStreamKey || "").trim();
  const existingKey = String(node?.ownerStreamKey || "").trim();
  if (incomingKey) {
    return existingKey === incomingKey;
  }
  return !existingKey;
}

function findMessageById<TMessage extends SessionStreamMessage>(localMessages: TMessage[], messageId?: string) {
  const normalizedId = String(messageId || "").trim();
  if (!normalizedId) {
    return undefined;
  }
  return localMessages.find((message) => String(message.id || "").trim() === normalizedId);
}

function upsertTimelineNode(message: SessionStreamMessage, node: SessionStreamTimelineNode) {
  const nodes = Array.isArray(message.nodes) ? [...message.nodes] : [];
  const nodeId = String(node.id || "").trim();
  const existingIndexById = nodeId
    ? nodes.findIndex((candidate) => String(candidate.id || "").trim() === nodeId)
    : -1;
  const executionType = node.kind === "execution" ? node.executionType : undefined;
  const toolIdentity = node.kind === "execution" && (executionType === "tool_call" || executionType === "tool_result")
    ? String(node.toolInvocationId || node.toolCallId || "").trim()
    : "";
  const existingIndexByToolIdentity = toolIdentity
    ? nodes.findIndex((candidate) => {
      if (candidate.kind !== "execution" || candidate.executionType !== executionType) return false;
      const candidateIdentity = String(candidate.toolInvocationId || candidate.toolCallId || "").trim();
      if (!candidateIdentity || candidateIdentity !== toolIdentity) return false;
      const incomingRunId = String(node.runId || "").trim();
      const candidateRunId = String(candidate.runId || "").trim();
      if (incomingRunId && candidateRunId && incomingRunId !== candidateRunId) return false;
      const incomingStreamKey = String(node.ownerStreamKey || "").trim();
      const candidateStreamKey = String(candidate.ownerStreamKey || "").trim();
      return !incomingStreamKey || !candidateStreamKey || incomingStreamKey === candidateStreamKey;
    })
    : -1;
  const existingIndex = existingIndexById >= 0 ? existingIndexById : existingIndexByToolIdentity;
  if (existingIndex >= 0) {
    const existing = nodes[existingIndex];
    if (timelineNodeFinalized(existing) && existing.kind === "narrative" && node.kind === "narrative") {
      const existingContent = String((existing as SessionStreamNarrativeNode).content || "");
      const incomingContent = String((node as SessionStreamNarrativeNode).content || "");
      if (existingContent === incomingContent) {
        return existing;
      }
      const appendOnlyNode = {
        ...node,
        id: `${nodeId}:append:${nodes.length}`,
      } as SessionStreamTimelineNode;
      nodes.push(appendOnlyNode);
      message.nodes = nodes;
      message.timestamp = Date.now();
      return appendOnlyNode;
    }
    nodes[existingIndex] = {
      ...existing,
      ...node,
    } as SessionStreamTimelineNode;
    message.nodes = nodes;
    message.timestamp = Date.now();
    return nodes[existingIndex];
  }
  nodes.push(node);
  message.nodes = nodes;
  message.timestamp = Date.now();
  return node;
}

function applyTranscriptVersion(message: SessionStreamMessage, event: SessionStreamUiEvent) {
  if (typeof event.transcript_version !== "number" || !Number.isFinite(event.transcript_version)) {
    return;
  }
  message.metadata = {
    ...(message.metadata || {}),
    transcriptVersion: event.transcript_version,
  };
}

function deriveNarrativeContentFromNodes(message: SessionStreamMessage): string {
  return (Array.isArray(message.nodes) ? message.nodes : [])
    .filter((node): node is SessionStreamNarrativeNode => node.kind === "narrative" && node.role === "assistant" && nodeDisplayInMessage(node))
    .map((node) => String(node.content || ""))
    .join("");
}

function appendArtifactNode(
  message: SessionStreamMessage,
  artifact: SessionStreamArtifact | null,
  profile: SessionAgentProfile,
  options?: SessionStreamLifecycleOptions,
  timelineFields?: ReturnType<typeof eventTimelineFields>,
) {
  if (!artifact) {
    return;
  }
  const currentArtifacts = Array.isArray(message.artifacts) ? message.artifacts : [];
  const key = artifactKey(artifact);
  if (!key || currentArtifacts.some((item) => artifactKey(item) === key)) {
    return;
  }
  message.artifacts = [...currentArtifacts, artifact];
  appendNode(message, {
    id: nextId("node", options?.createId),
    kind: "artifact",
    artifact,
    timestamp: Date.now(),
    ...profile,
    ...(timelineFields || {}),
  });
}

function longestOverlapSuffixPrefix(current: string, incoming: string) {
  const maxOverlap = Math.min(current.length, incoming.length);
  for (let size = maxOverlap; size > 0; size -= 1) {
    if (current.slice(-size) === incoming.slice(0, size)) {
      return size;
    }
  }
  return 0;
}

function computeStreamingSuffix(current: string, incoming: string) {
  const nextChunk = String(incoming || "");
  if (!nextChunk) {
    return "";
  }
  const existing = String(current || "");
  if (!existing) {
    return nextChunk;
  }
  if (nextChunk === existing) {
    return "";
  }
  if (nextChunk.startsWith(existing)) {
    return nextChunk.slice(existing.length);
  }
  if (existing.startsWith(nextChunk)) {
    return "";
  }
  const nestedIndex = nextChunk.indexOf(existing);
  if (nestedIndex >= 0) {
    return nextChunk.slice(nestedIndex + existing.length);
  }
  const overlap = longestOverlapSuffixPrefix(existing, nextChunk);
  return overlap > 0 ? nextChunk.slice(overlap) : nextChunk;
}

export function isActiveAssistantStreamPhase(phase?: SessionStreamPhase | null) {
  return (
    phase === "placeholder"
    || phase === "agent_started"
    || phase === "task_planning"
    || phase === "tooling"
    || phase === "artifact_ready"
    || phase === "waiting_input"
    || phase === "streaming"
    || phase === "settling"
  );
}

export function buildAssistantMessage(
  activeAgentProfile: SessionAgentProfile,
  runId?: string,
  phase: SessionStreamPhase = "placeholder",
  options?: SessionStreamLifecycleOptions,
): SessionStreamMessage {
  const defaultAgentProfile = resolveDefaultAgentProfile(options);
  const resolvedProfile = {
    ...defaultAgentProfile,
    ...activeAgentProfile,
  };
  const id = nextId("assistant", options?.createId);
  return {
    id,
    renderKey: id,
    role: "assistant",
    content: "",
    runId,
    nodes: [],
    images: [],
    artifacts: [],
    agentName: resolvedProfile.agentName,
    agentAvatar: resolvedProfile.agentAvatar,
    agentRoleLabel: resolvedProfile.agentRoleLabel,
    agentType: "supervisor",
    timestamp: Date.now(),
    uiEphemeral: true,
    uiStreamPhase: phase,
  };
}

export function deriveRealtimeStreamState<TMessage extends SessionStreamMessage = SessionStreamMessage>(
  messages: TMessage[],
  options?: SessionStreamLifecycleOptions,
): SessionStreamState<TMessage> {
  const defaultAgentProfile = resolveDefaultAgentProfile(options);
  const lastAssistant = [...messages].reverse().find((message) => message.role === "assistant");
  if (!lastAssistant) {
    return {
      currentAiMsg: undefined,
      activeAgentProfile: { ...defaultAgentProfile },
    };
  }

  const nodes = Array.isArray(lastAssistant.nodes) ? lastAssistant.nodes : [];
  const lastAgentNode = [...nodes].reverse().find((node) => node.agentName || node.agentAvatar || node.agentRoleLabel);
  const currentAiMsg = lastAssistant.uiEphemeral || isActiveAssistantStreamPhase(lastAssistant.uiStreamPhase)
    ? lastAssistant
    : undefined;

  return {
    currentAiMsg,
    activeAgentProfile: {
      agentName: lastAgentNode?.agentName || lastAssistant.agentName || defaultAgentProfile.agentName,
      agentAvatar: lastAgentNode?.agentAvatar || lastAssistant.agentAvatar || defaultAgentProfile.agentAvatar,
      agentRoleLabel: lastAgentNode?.agentRoleLabel || lastAssistant.agentRoleLabel || defaultAgentProfile.agentRoleLabel,
    },
  };
}

export function shouldApplyRuntimeEventToMessage(
  event: Pick<SessionStreamUiEvent, "type" | "name" | "targets" | "visibility" | "runtimeId" | "topic">,
) {
  if (!shouldForwardRuntimeEventToRealtimeSurface(event)) {
    return false;
  }
  const targets = Array.isArray(event.targets) ? event.targets : [];
  const messageScopedTypes = new Set(["agent_start", "text_chunk", "reasoning_chunk", "tool_start", "tool_result", "done", "error"]);
  if (messageScopedTypes.has(event.type) && !targets.includes("message")) {
    return false;
  }
  if (
    event.runtimeId === "subagent_swarm"
    || event.runtimeId === "engineering"
    || event.runtimeId === "engineering_lane"
    || event.runtimeId === "research"
    || event.runtimeId === "creative_media"
    || event.runtimeId === "computer_use"
    || event.runtimeId === "rpa"
    || String(event.topic || "").startsWith("subagent.")
    || String(event.topic || "").startsWith("engineering.")
    || String(event.topic || "").startsWith("engineering_lane.")
    || String(event.topic || "").startsWith("research.")
    || String(event.topic || "").startsWith("creative_media.")
    || String(event.topic || "").startsWith("computer_use.")
    || String(event.topic || "").startsWith("rpa.")
  ) {
    return false;
  }
  const todoToolEvent = isTodoToolRuntimePayload(event);
  if (
    event.type === "agent_start"
    || event.type === "text_chunk"
    || event.type === "reasoning_chunk"
    || ((event.type === "tool_start" || event.type === "tool_result") && !todoToolEvent)
    || event.type === "done"
    || event.type === "error"
  ) {
    return true;
  }
  if (event.type !== "custom_event") {
    return false;
  }
  return targets.includes("message")
    || event.name === "artifact_recorded"
    || event.name === "ask_user";
}

export function applyRealtimeEventToMessages<TMessage extends SessionStreamMessage = SessionStreamMessage>(
  event: SessionStreamUiEvent,
  localMessages: TMessage[],
  currentAiMsg: TMessage | undefined,
  activeAgentProfile: SessionAgentProfile,
  options?: SessionStreamLifecycleOptions,
): SessionStreamState<TMessage> {
  let nextCurrentAiMsg = currentAiMsg;
  const defaultAgentProfile = resolveDefaultAgentProfile(options);
  const resolveAgentProfile = options?.resolveAgentProfile || buildAgentProfileFromEvent;
  const resolveArtifact = options?.resolveArtifact || resolveArtifactFromEvent;
  const runtimeCoalesceTopics = new Set(options?.coalescedRuntimeTopics || DEFAULT_COALESCED_RUNTIME_TOPICS);
  let nextActiveAgentProfile = activeAgentProfile;
  const eventData = asRecord(event.data);
  const ownerFields = ownerFieldsFromEvent(event);
  const timelineFields = eventTimelineFields(event);
  if (!shouldApplyRuntimeEventToMessage(event)) {
    return {
      currentAiMsg: nextCurrentAiMsg,
      activeAgentProfile: nextActiveAgentProfile,
    };
  }
  const todoToolEvent = (event.type === "tool_start" || event.type === "tool_result") && isTodoToolRuntimePayload({
    ...eventData,
    toolName: event.tool?.toolName || eventData.toolName || eventData.tool_name,
    tool: {
      ...asRecord(eventData.tool),
      toolName: event.tool?.toolName || eventData.toolName || eventData.tool_name,
    },
  });

  if (event.type === "custom_event" && event.name === "message_user_recorded") {
    const recorded = buildRecordedUserMessage(event, eventData) as TMessage;
    const clientMessageId = String(recorded.metadata?.clientMessageId || "").trim();
    const existingIndex = localMessages.findIndex((message) => (
      String(message.id || "").trim() === recorded.id
      || (clientMessageId && String(message.metadata?.clientMessageId || "").trim() === clientMessageId)
    ));
    if (existingIndex >= 0) {
      const existing = localMessages[existingIndex];
      localMessages[existingIndex] = {
        ...existing,
        ...recorded,
        renderKey: existing.renderKey,
        metadata: {
          ...(existing.metadata || {}),
          ...(recorded.metadata || {}),
        },
      } as TMessage;
    } else {
      localMessages.push(recorded);
    }
    return {
      currentAiMsg: undefined,
      activeAgentProfile: nextActiveAgentProfile,
    };
  }

  const ensureCurrent = () => {
    const explicitMessage = findMessageById(localMessages, event.message_id);
    if (explicitMessage) {
      nextCurrentAiMsg = explicitMessage;
      if (!Array.isArray(nextCurrentAiMsg.nodes)) {
        nextCurrentAiMsg.nodes = [];
      }
      if (!Array.isArray(nextCurrentAiMsg.images)) {
        nextCurrentAiMsg.images = [];
      }
      if (!Array.isArray(nextCurrentAiMsg.artifacts)) {
        nextCurrentAiMsg.artifacts = [];
      }
      if (event.run_id) {
        nextCurrentAiMsg.runId = event.run_id;
      }
      ensureAssistantIdentity(nextCurrentAiMsg, nextActiveAgentProfile);
      applyTranscriptVersion(nextCurrentAiMsg, event);
      return nextCurrentAiMsg;
    }
    nextCurrentAiMsg = ensureCurrentAiMessage(localMessages, nextCurrentAiMsg, nextActiveAgentProfile, event.run_id, options);
    if (event.message_id) {
      nextCurrentAiMsg.id = event.message_id;
    }
    applyTranscriptVersion(nextCurrentAiMsg, event);
    return nextCurrentAiMsg;
  };
  const appendEventArtifactIfPresent = (message: SessionStreamMessage) => {
    if (!event.artifact) {
      return;
    }
    appendArtifactNode(message, resolveArtifact(event), nextActiveAgentProfile, options, timelineFields);
  };

  if (event.type === "agent_start") {
    nextActiveAgentProfile = resolveAgentProfile(event, nextActiveAgentProfile, defaultAgentProfile);
    const current = ensureCurrent();
    current.uiStreamPhase = "agent_started";
    if (shouldPromoteMessageIdentity(current, nextActiveAgentProfile, defaultAgentProfile)) {
      ensureAssistantIdentity(current, nextActiveAgentProfile, { overwrite: true });
    }
    if (event.node_id) {
      upsertTimelineNode(current, {
        id: event.node_id,
        kind: "execution",
        executionType: "agent_start",
        ...nextActiveAgentProfile,
        ...timelineFields,
      });
    }
  } else if (event.type === "text_chunk") {
    const current = ensureCurrent();
    current.uiStreamPhase = "streaming";
    const eventData = asRecord(event.data);
    const content = String(event.content || "");
    const snapshot = typeof eventData.snapshot === "string" ? eventData.snapshot : undefined;
    const narrativeLifecycle = {
      finalized: eventData.finalized === true || eventData.isFinal === true,
      partial: eventData.partial === true,
    };
    const explicitNode = event.node_id
      ? (Array.isArray(current.nodes)
        ? current.nodes.find((node): node is SessionStreamNarrativeNode =>
          String(node.id || "").trim() === event.node_id
          && node.kind === "narrative"
          && node.role === "assistant"
          && !timelineNodeFinalized(node)
        )
        : undefined)
      : undefined;
    const lastNode = explicitNode || (Array.isArray(current.nodes) ? current.nodes[current.nodes.length - 1] : undefined);
    const narrativeNode = explicitNode || (canMergeNarrativeNode(lastNode, nextActiveAgentProfile, ownerFields) ? lastNode : undefined);
    if (narrativeNode) {
      Object.assign(narrativeNode, ownerFields, narrativeLifecycle);
      applyEventTimelineFields(narrativeNode, timelineFields);
      if (snapshot !== undefined) {
        narrativeNode.content = snapshot;
        current.content = deriveNarrativeContentFromNodes(current);
      } else {
        const suffix = computeStreamingSuffix(String(narrativeNode.content || ""), content);
        if (suffix) {
          narrativeNode.content = `${String(narrativeNode.content || "")}${suffix}`;
          current.content = `${String(current.content || "")}${suffix}`;
        }
      }
    } else {
      upsertTimelineNode(current, {
        id: event.node_id || nextId("node", options?.createId),
        kind: "narrative",
        role: "assistant",
        content: snapshot ?? content,
        ...nextActiveAgentProfile,
        ...ownerFields,
        ...narrativeLifecycle,
        ...timelineFields,
      });
      current.content = snapshot !== undefined
        ? deriveNarrativeContentFromNodes(current)
        : ownerFields.displayInMessage === false
          ? deriveNarrativeContentFromNodes(current)
          : `${String(current.content || "")}${content}`;
    }
    appendEventArtifactIfPresent(current);
  } else if (event.type === "reasoning_chunk") {
    const current = ensureCurrent();
    current.uiStreamPhase = current.content ? "streaming" : "agent_started";
    const eventData = asRecord(event.data);
    const content = String(event.content || "");
    const snapshot = typeof eventData.snapshot === "string" ? eventData.snapshot : undefined;
    const reasoningKind = String(event.reasoningKind || eventData.reasoningKind || "").trim() || undefined;
    const startTime = Number(eventData.startTime || eventData.start_time || timelineFields.timestamp) || timelineFields.timestamp;
    const durationMs = Math.max(0, Number(eventData.durationMs || eventData.duration_ms || 0) || 0);
    const explicitNode = event.node_id
      ? (Array.isArray(current.nodes)
        ? current.nodes.find((node) => String(node.id || "").trim() === event.node_id)
        : undefined)
      : undefined;
    const lastNode = explicitNode || (Array.isArray(current.nodes) ? current.nodes[current.nodes.length - 1] : undefined);
    if (
      lastNode
      && lastNode.kind === "execution"
      && lastNode.executionType === "reasoning"
      && executionStreamKeyMatches(lastNode, ownerFields)
    ) {
      Object.assign(lastNode, ownerFields);
      applyEventTimelineFields(lastNode, timelineFields);
      lastNode.startTime = startTime;
      lastNode.time = durationMs;
      lastNode.data = {
        ...(lastNode.data || {}),
        startTime,
        durationMs,
      };
      if (reasoningKind) {
        lastNode.reasoningKind = reasoningKind;
        lastNode.data = {
          ...(lastNode.data || {}),
          reasoningKind,
        };
      }
      if (snapshot !== undefined) {
        lastNode.content = snapshot;
      } else {
        const suffix = computeStreamingSuffix(String(lastNode.content || ""), content);
        if (suffix) {
          lastNode.content = `${String(lastNode.content || "")}${suffix}`;
        }
      }
    } else {
      upsertTimelineNode(current, {
        id: event.node_id || nextId("node", options?.createId),
        kind: "execution",
        executionType: "reasoning",
        content: snapshot ?? content,
        reasoningKind,
        time: durationMs,
        startTime,
        data: {
          reasoningKind,
          reasoningSurface: eventData.reasoningSurface,
          startTime,
          durationMs,
        },
        ...nextActiveAgentProfile,
        ...ownerFields,
        ...timelineFields,
      });
    }
  } else if (event.type === "tool_start") {
    if (todoToolEvent) {
      return {
        currentAiMsg: nextCurrentAiMsg,
        activeAgentProfile: nextActiveAgentProfile,
      };
    }
    const current = ensureCurrent();
    current.uiStreamPhase = current.content ? "streaming" : "agent_started";
    const narrativeContent = typeof event.content === "string" && event.content.trim()
      ? event.content.trim()
      : typeof eventData.content === "string" && eventData.content.trim()
        ? eventData.content.trim()
        : typeof eventData.message === "string" && eventData.message.trim()
          ? eventData.message.trim()
          : "";
    const eventToolData = asRecord(eventData.tool);
    const nodeId = event.node_id || nextId("node", options?.createId);
    const toolCallId = resolveSurfaceToolCallId(event, eventData, eventToolData, nodeId);
    upsertTimelineNode(current, {
      id: nodeId,
      kind: "execution",
      executionType: "tool_call",
      toolCallId,
      toolInvocationId: toolCallId,
      toolName: event.tool?.toolName || (typeof eventData.toolName === "string" ? eventData.toolName : undefined),
      args: event.tool?.args ?? eventData.args ?? asRecord(eventData.tool).args,
      ...nextActiveAgentProfile,
      ...ownerFields,
      ...timelineFields,
    });
    if (narrativeContent) {
      appendNarrativeContent(current, narrativeContent, nextActiveAgentProfile, options, { ...ownerFields, ...timelineFields });
    }
    appendEventArtifactIfPresent(current);
  } else if (event.type === "tool_result") {
    if (todoToolEvent) {
      return {
        currentAiMsg: nextCurrentAiMsg,
        activeAgentProfile: nextActiveAgentProfile,
      };
    }
    const current = ensureCurrent();
    current.uiStreamPhase = current.content ? "streaming" : "agent_started";
    const eventToolData = asRecord(eventData.tool);
    const toolName = event.tool?.toolName || (typeof eventData.toolName === "string" ? eventData.toolName : undefined);
    const reusableCall = [...(current.nodes || [])].reverse().find((node) =>
      node.kind === "execution"
      && node.executionType === "tool_call"
      && !("result" in node && node.result !== undefined)
      && (
        !toolName
        || !node.toolName
        || node.toolName === toolName
      ),
    ) as SessionStreamExecutionNode | undefined;
    const resultNodeId = event.node_id || nextId("node", options?.createId);
    const toolCallId = resolveSurfaceToolCallId(
      event,
      eventData,
      eventToolData,
      reusableCall?.toolInvocationId || reusableCall?.toolCallId || resultNodeId,
    );
    const agentVisibleResult = event.tool?.agentVisibleResult
      ?? eventData.agentVisibleResult
      ?? eventData.agent_visible_result
      ?? eventData.agentVisibleOutput
      ?? eventData.agent_visible_output
      ?? eventToolData.agentVisibleResult
      ?? eventToolData.agent_visible_result
      ?? eventToolData.agentVisibleOutput
      ?? eventToolData.agent_visible_output;
    const agentVisibleChars = typeof event.tool?.agentVisibleChars === "number"
      ? event.tool.agentVisibleChars
      : typeof eventData.agentVisibleChars === "number"
        ? eventData.agentVisibleChars
        : typeof eventData.agent_visible_chars === "number"
          ? eventData.agent_visible_chars
          : typeof eventToolData.agentVisibleChars === "number"
            ? eventToolData.agentVisibleChars
            : typeof eventToolData.agent_visible_chars === "number"
              ? eventToolData.agent_visible_chars
              : undefined;
    const fallbackResult = event.tool?.result ?? eventData.result;
    const resultStatus = event.tool?.resultStatus
      ?? normalizeSessionToolResultStatus(eventData.resultStatus)
      ?? normalizeSessionToolResultStatus(eventData.result_status)
      ?? normalizeSessionToolResultStatus(eventToolData.resultStatus)
      ?? normalizeSessionToolResultStatus(eventToolData.result_status)
      ?? normalizeSessionToolResultStatus(eventToolData.status);
    const resultReasonCode = event.tool?.resultReasonCode
      ?? firstNonEmptyString(
        eventData.resultReasonCode,
        eventData.result_reason_code,
        eventToolData.resultReasonCode,
        eventToolData.result_reason_code,
      )
      ?? undefined;
    const displayResult = chooseAgentVisibleToolResult(toolName, agentVisibleResult, fallbackResult);
    const displayAgentVisibleResult = displayResult;
    const mcpApp = event.tool?.mcpApp
      || (eventData.mcpApp && typeof eventData.mcpApp === "object" ? eventData.mcpApp as McpAppViewRef : undefined)
      || (eventToolData.mcpApp && typeof eventToolData.mcpApp === "object" ? eventToolData.mcpApp as McpAppViewRef : undefined);
    const narrativeContent = typeof event.content === "string" && event.content.trim()
      ? event.content.trim()
      : typeof eventData.content === "string" && eventData.content.trim()
        ? eventData.content.trim()
        : typeof eventData.message === "string" && eventData.message.trim()
          ? eventData.message.trim()
          : "";
    if (event.node_id) {
      upsertTimelineNode(current, {
        id: resultNodeId,
        kind: "execution",
        executionType: "tool_result",
        toolCallId,
        toolInvocationId: toolCallId,
        toolName,
        result: displayResult,
        resultStatus,
        resultReasonCode,
        agentVisibleResult: displayAgentVisibleResult,
        agentVisibleChars,
        mcpApp,
        data: eventData,
        ...nextActiveAgentProfile,
        ...ownerFields,
        ...timelineFields,
      });
    } else {
      const existingToolCall = (current.nodes || []).find((node) =>
        node.kind === "execution"
        && node.executionType === "tool_call"
        && (node.toolInvocationId === toolCallId || node.toolCallId === toolCallId),
      ) as SessionStreamExecutionNode | undefined;

      if (existingToolCall) {
        existingToolCall.result = displayResult;
        existingToolCall.resultStatus = resultStatus;
        existingToolCall.resultReasonCode = resultReasonCode;
        existingToolCall.agentVisibleResult = displayAgentVisibleResult;
        existingToolCall.agentVisibleChars = agentVisibleChars;
        existingToolCall.mcpApp = mcpApp || existingToolCall.mcpApp;
        existingToolCall.data = eventData;
        Object.assign(existingToolCall, ownerFields);
        existingToolCall.toolName = existingToolCall.toolName
          || toolName;
        existingToolCall.timestamp = Date.now();
        applyEventTimelineFields(existingToolCall, timelineFields);
      } else {
        upsertTimelineNode(current, {
          id: nextId("node", options?.createId),
          kind: "execution",
          executionType: "tool_result",
          toolCallId,
          toolInvocationId: toolCallId,
          toolName,
          result: displayResult,
          resultStatus,
          resultReasonCode,
          agentVisibleResult: displayAgentVisibleResult,
          agentVisibleChars,
          mcpApp,
          data: eventData,
          ...nextActiveAgentProfile,
          ...ownerFields,
          ...timelineFields,
        });
      }
    }
    if (narrativeContent) {
      appendNarrativeContent(current, narrativeContent, nextActiveAgentProfile, options, { ...ownerFields, ...timelineFields });
    }
    appendEventArtifactIfPresent(current);
  } else if (event.type === "custom_event" && event.name === "artifact_recorded") {
    const current = ensureCurrent();
    current.uiStreamPhase = current.content ? "streaming" : "agent_started";
    appendArtifactNode(current, resolveArtifact(event), nextActiveAgentProfile, options, timelineFields);
  } else if (event.type === "custom_event" && (event.name === "runtime_progress" || event.name === "runtime_event")) {
    const current = ensureCurrent();
    current.uiStreamPhase = current.content ? "streaming" : "agent_started";
    const eventData = asRecord(event.data);
    const topic = typeof eventData.topic === "string" ? eventData.topic : event.name === "runtime_event" ? "runtime" : "runtime_progress";
    const label = typeof eventData.label === "string"
      ? eventData.label
      : typeof eventData.summary === "string"
        ? eventData.summary
        : typeof eventData.message === "string"
          ? eventData.message
          : topic;
    const targets = Array.isArray(event.targets) ? event.targets : [];
    const narrativeContent = typeof event.content === "string" && event.content.trim()
      ? event.content.trim()
      : typeof eventData.content === "string" && eventData.content.trim()
        ? eventData.content.trim()
        : typeof eventData.message === "string" && eventData.message.trim()
          ? eventData.message.trim()
          : "";
    if (targets.includes("message") && narrativeContent && narrativeContent !== label) {
      appendNarrativeContent(current, narrativeContent, nextActiveAgentProfile, options, timelineFields);
    }
    appendEventArtifactIfPresent(current);
  } else if (event.type === "custom_event" && event.name === "ask_user") {
    const current = ensureCurrent();
    current.uiStreamPhase = current.content ? "streaming" : "agent_started";
    const eventData = asRecord(event.data);
    appendNode(current, {
      id: nextId("node", options?.createId),
      kind: "governance",
      governanceType: "ask_user",
      approvalId: typeof eventData.approvalId === "string" ? eventData.approvalId : undefined,
      approvalKind: typeof eventData.approvalKind === "string" ? eventData.approvalKind : undefined,
      interactionKind: typeof eventData.interactionKind === "string" ? eventData.interactionKind : "ask_user",
      question: typeof eventData.question === "string" ? eventData.question : undefined,
      toolCallId: typeof eventData.toolCallId === "string" ? eventData.toolCallId : undefined,
      requestInfo: eventData.request,
      ...nextActiveAgentProfile,
      ...timelineFields,
    });
  } else if (event.type === "custom_event" && event.name === "approval_requested") {
    const current = ensureCurrent();
    current.uiStreamPhase = current.content ? "streaming" : "agent_started";
    const eventData = asRecord(event.data);
    const narrativeContent = typeof event.content === "string" && event.content.trim()
      ? event.content.trim()
      : typeof eventData.message === "string" && eventData.message.trim()
        ? eventData.message.trim()
        : "";
    if (narrativeContent && Array.isArray(event.targets) && event.targets.includes("message")) {
      appendNarrativeContent(current, narrativeContent, nextActiveAgentProfile, options, timelineFields);
    }
    appendNode(current, {
      id: nextId("node", options?.createId),
      kind: "governance",
      governanceType: "approval_request",
      approvalId: typeof eventData.approvalId === "string" ? eventData.approvalId : undefined,
      approvalKind: typeof eventData.approvalKind === "string" ? eventData.approvalKind : undefined,
      interactionKind: typeof eventData.interactionKind === "string" ? eventData.interactionKind : undefined,
      question: typeof eventData.question === "string" ? eventData.question : undefined,
      toolCallId: typeof eventData.toolCallId === "string" ? eventData.toolCallId : undefined,
      requestInfo: eventData.request,
      ...nextActiveAgentProfile,
      ...timelineFields,
    });
  } else if (event.type === "custom_event" && event.name === "approval_resolved") {
    const current = ensureCurrent();
    current.uiStreamPhase = current.content ? "streaming" : "agent_started";
    const eventData = asRecord(event.data);
    const narrativeContent = typeof event.content === "string" && event.content.trim()
      ? event.content.trim()
      : typeof eventData.message === "string" && eventData.message.trim()
        ? eventData.message.trim()
        : "";
    if (narrativeContent && Array.isArray(event.targets) && event.targets.includes("message")) {
      appendNarrativeContent(current, narrativeContent, nextActiveAgentProfile, options, timelineFields);
    }
    appendNode(current, {
      id: nextId("node", options?.createId),
      kind: "governance",
      governanceType: "approval_resolved",
      approvalId: typeof eventData.approval_id === "string" ? eventData.approval_id : typeof eventData.approvalId === "string" ? eventData.approvalId : undefined,
      approvalKind: typeof eventData.approval_kind === "string" ? eventData.approval_kind : typeof eventData.approvalKind === "string" ? eventData.approvalKind : undefined,
      topic: typeof eventData.topic === "string" ? eventData.topic : undefined,
      status: typeof eventData.status === "string" ? eventData.status : undefined,
      reason: typeof eventData.reason === "string" ? eventData.reason : undefined,
      ...nextActiveAgentProfile,
      ...timelineFields,
    });
  } else if (event.type === "custom_event" && event.name === "run_controlled") {
    const current = ensureCurrent();
    current.uiStreamPhase = current.content ? "streaming" : "agent_started";
    const eventData = asRecord(event.data);
    appendNode(current, {
      id: nextId("node", options?.createId),
      kind: "governance",
      governanceType: "run_controlled",
      topic: typeof eventData.topic === "string" ? eventData.topic : undefined,
      status: typeof eventData.status === "string" ? eventData.status : undefined,
      reason: typeof eventData.reason === "string" ? eventData.reason : undefined,
      ...nextActiveAgentProfile,
      ...timelineFields,
    });
  } else if (event.type === "custom_event" && event.name === "safety_blocked") {
    const current = ensureCurrent();
    current.uiStreamPhase = current.content ? "streaming" : "agent_started";
    const eventData = asRecord(event.data);
    const narrativeContent = typeof event.content === "string" && event.content.trim()
      ? event.content.trim()
      : typeof eventData.message === "string" && eventData.message.trim()
        ? eventData.message.trim()
        : "";
    if (narrativeContent && Array.isArray(event.targets) && event.targets.includes("message")) {
      appendNarrativeContent(current, narrativeContent, nextActiveAgentProfile, options, timelineFields);
    }
    appendNode(current, {
      id: nextId("node", options?.createId),
      kind: "governance",
      governanceType: "safety_blocked",
      topic: typeof eventData.topic === "string" ? eventData.topic : undefined,
      status: typeof eventData.status === "string" ? eventData.status : "blocked",
      reason: typeof eventData.reason === "string"
        ? eventData.reason
        : (narrativeContent || (typeof eventData.label === "string" ? eventData.label : undefined)),
      ...nextActiveAgentProfile,
      ...timelineFields,
    });
  } else if (event.type === "custom_event" && event.name === "context_governance_changed") {
    const current = ensureCurrent();
    current.uiStreamPhase = current.content ? "streaming" : "agent_started";
    const eventData = asRecord(event.data);
    appendNode(current, {
      id: nextId("node", options?.createId),
      kind: "governance",
      governanceType: "context_governance",
      topic: typeof eventData.topic === "string" ? eventData.topic : undefined,
      status: typeof eventData.status === "string" ? eventData.status : undefined,
      reason: typeof eventData.label === "string" ? eventData.label : undefined,
      requestInfo: eventData,
      ...nextActiveAgentProfile,
      ...timelineFields,
    });
  } else if (event.type === "custom_event" && event.name === "lane_updated") {
    const current = ensureCurrent();
    current.uiStreamPhase = current.content ? "streaming" : "agent_started";
    const eventData = asRecord(event.data);
    appendNode(current, {
      id: nextId("node", options?.createId),
      kind: "governance",
      governanceType: "lane_updated",
      topic: typeof eventData.topic === "string" ? eventData.topic : "run.lane.updated",
      status: typeof eventData.status === "string" ? eventData.status : undefined,
      reason: typeof eventData.label === "string"
        ? eventData.label
        : typeof event.content === "string" && event.content.trim()
          ? event.content.trim()
          : undefined,
      ...nextActiveAgentProfile,
      ...timelineFields,
    });
  } else if (event.type === "custom_event" && event.name === "human_guidance") {
    const eventData = asRecord(event.data);
    const targets = Array.isArray(event.targets) ? event.targets : [];
    if (!targets.includes("message") && eventDisplayInMessage(event) !== true) {
      return {
        currentAiMsg: nextCurrentAiMsg,
        activeAgentProfile: nextActiveAgentProfile,
      };
    }
    const current = ensureCurrent();
    current.uiStreamPhase = current.content ? "streaming" : "agent_started";
    const queueMessage = asRecord(eventData.queueMessage);
    const content = String(
      eventData.content
      || queueMessage.content
      || event.content
      || eventData.summary
      || "",
    ).trim();
    const nodeId = event.node_id || String(eventData.node_id || "").trim() || nextId("node", options?.createId);
    upsertTimelineNode(current, {
      id: nodeId,
      kind: "governance",
      governanceType: "human_guidance",
      topic: typeof eventData.topic === "string" ? eventData.topic : "human_guidance.injected",
      status: typeof eventData.state === "string"
        ? eventData.state
        : typeof eventData.status === "string"
          ? eventData.status
          : "injected",
      reason: typeof eventData.summary === "string" ? eventData.summary : "human_guidance",
      question: content,
      requestInfo: {
        queueMessageId: queueMessage.id,
        clientMessageId: queueMessage.clientMessageId,
        state: queueMessage.state || eventData.state,
      },
      ...nextActiveAgentProfile,
      ...ownerFields,
      ...timelineFields,
    });
  } else if (event.type === "custom_event" && event.name === "session_coordination") {
    const eventData = asRecord(event.data);
    const targets = Array.isArray(event.targets) ? event.targets : [];
    if (!targets.includes("message") && eventDisplayInMessage(event) !== true) {
      return {
        currentAiMsg: nextCurrentAiMsg,
        activeAgentProfile: nextActiveAgentProfile,
      };
    }
    const current = ensureCurrent();
    current.uiStreamPhase = current.content ? "streaming" : "agent_started";
    const content = String(eventData.summary || event.content || "").trim();
    const nodeId = event.node_id || String(eventData.node_id || "").trim() || nextId("node", options?.createId);
    upsertTimelineNode(current, {
      id: nodeId,
      kind: "governance",
      governanceType: "session_coordination",
      topic: typeof eventData.topic === "string" ? eventData.topic : "session_coordination.queued",
      status: typeof eventData.state === "string"
        ? eventData.state
        : typeof eventData.status === "string"
          ? eventData.status
          : "queued",
      reason: typeof eventData.direction === "string" ? eventData.direction : "incoming",
      question: content,
      requestInfo: eventData,
      ...nextActiveAgentProfile,
      ...ownerFields,
      ...timelineFields,
    });
  } else if (event.type === "done") {
    if (nextCurrentAiMsg) {
      nextCurrentAiMsg.uiStreamPhase = "settling";
      appendEventArtifactIfPresent(nextCurrentAiMsg);
      upsertCurrentAiMessage(localMessages, nextCurrentAiMsg);
    }
    nextCurrentAiMsg = undefined;
  } else if (event.type === "error") {
    if (nextCurrentAiMsg) {
      nextCurrentAiMsg.uiStreamPhase = "error";
      upsertCurrentAiMessage(localMessages, nextCurrentAiMsg);
    }
    nextCurrentAiMsg = undefined;
  }

  if (nextCurrentAiMsg) {
    nextCurrentAiMsg = upsertCurrentAiMessage(localMessages, nextCurrentAiMsg);
  }

  return {
    currentAiMsg: nextCurrentAiMsg,
    activeAgentProfile: nextActiveAgentProfile,
  };
}

export function isLifecycleTerminalEvent(event: Pick<NormalizedSessionRuntimeEvent, "type" | "name">) {
  return event.type === "done" || event.type === "error";
}

export function shouldAuthoritativelyRefreshOnRuntimeEvent(
  event: Pick<NormalizedSessionRuntimeEvent, "type" | "name" | "topic" | "seq" | "visibility" | "targets">,
) {
  if (!shouldForwardRuntimeEventToRealtimeSurface(event)) {
    return false;
  }

  const topic = String(event.topic || "").trim().toLowerCase();
  if (!topic || topic.includes("heartbeat")) {
    return false;
  }

  if (event.type === "done" || event.type === "agent_start" || event.type === "error") {
    return true;
  }

  if (event.type === "custom_event" && event.name === "message_user_recorded") {
    return true;
  }

  if (event.type === "tool_start" || event.type === "tool_result") {
    if (isTodoToolRuntimePayload({
      ...asRecord((event as { data?: unknown }).data),
      toolName: (event as { tool?: { toolName?: string } }).tool?.toolName,
    })) {
      return true;
    }
    return true;
  }

  if (Array.isArray(event.targets) && event.targets.includes("process")) {
    return true;
  }

  if (event.type === "custom_event"
    && (
      event.name === "ask_user"
      || event.name === "approval_requested"
      || event.name === "artifact_recorded"
      || event.name === "run_controlled"
      || event.name === "approval_resolved"
      || event.name === "safety_blocked"
      || event.name === "lane_updated"
      || event.name === "context_governance_changed"
    )) {
    return true;
  }

  return false;
}
