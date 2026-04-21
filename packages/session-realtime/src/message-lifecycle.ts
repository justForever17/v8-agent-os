import type { AdminResourceRef, NormalizedSessionRuntimeEvent } from "./contract.js";
import { deriveAdminResourceRefFromArtifactLike } from "./resources.js";
import { isTodoToolRuntimePayload, shouldForwardRuntimeEventToRealtimeSurface } from "./event-normalizer.js";

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
  toolName?: string;
  args?: unknown;
  result?: unknown;
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
  "type" | "name" | "content" | "data" | "run_id" | "error" | "targets" | "visibility" | "topic" | "runtimeId" | "seq" | "message_id" | "node_id" | "transcript_version"
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
  agentName?: string;
  agentAvatar?: string;
  agentRoleLabel?: string;
  agentType?: "supervisor" | "agent" | "user";
};

export type SessionStreamExecutionNode = {
  id: string;
  kind: "execution";
  executionType: "reasoning" | "tool_call" | "tool_result" | "runtime_progress" | "agent_start";
  timestamp: number;
  agentName?: string;
  agentAvatar?: string;
  agentRoleLabel?: string;
  agentType?: "supervisor" | "agent" | "user";
  content?: string;
  time?: number;
  startTime?: number;
  toolCallId?: string;
  toolName?: string;
  args?: unknown;
  result?: unknown;
  topic?: string;
  label?: string;
  data?: Record<string, unknown>;
};

export type SessionStreamGovernanceNode = {
  id: string;
  kind: "governance";
  governanceType: "ask_user" | "approval_request" | "approval_resolved" | "run_controlled" | "safety_blocked" | "context_governance" | "lane_updated";
  timestamp: number;
  agentName?: string;
  agentAvatar?: string;
  agentRoleLabel?: string;
  agentType?: "supervisor" | "agent" | "user";
  approvalId?: string;
  approvalKind?: string;
  interactionKind?: string;
  question?: string;
  toolCallId?: string;
  requestInfo?: unknown;
  topic?: string;
  status?: string;
  reason?: string;
};

export type SessionStreamArtifactNode = {
  id: string;
  kind: "artifact";
  timestamp: number;
  agentName?: string;
  agentAvatar?: string;
  agentRoleLabel?: string;
  agentType?: "supervisor" | "agent" | "user";
  artifact: SessionStreamArtifact;
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

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
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
) {
  const normalizedContent = String(content || "").trim();
  if (!normalizedContent) {
    return;
  }
  const lastNode = Array.isArray(message.nodes) ? message.nodes[message.nodes.length - 1] : undefined;
  if (
    lastNode
    && lastNode.kind === "narrative"
    && lastNode.role === "assistant"
    && lastNode.agentName === profile.agentName
  ) {
    lastNode.content = `${String(lastNode.content || "")}${normalizedContent}`;
  } else {
    appendNode(message, {
      id: nextId("node", options?.createId),
      kind: "narrative",
      role: "assistant",
      content: normalizedContent,
      timestamp: Date.now(),
      ...profile,
    });
  }
  message.content = `${String(message.content || "")}${normalizedContent}`;
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
  if (nodeId) {
    const existingIndex = nodes.findIndex((candidate) => String(candidate.id || "").trim() === nodeId);
    if (existingIndex >= 0) {
      nodes[existingIndex] = {
        ...nodes[existingIndex],
        ...node,
      } as SessionStreamTimelineNode;
      message.nodes = nodes;
      message.timestamp = Date.now();
      return nodes[existingIndex];
    }
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
    .filter((node): node is SessionStreamNarrativeNode => node.kind === "narrative" && node.role === "assistant")
    .map((node) => String(node.content || ""))
    .join("");
}

function appendArtifactNode(
  message: SessionStreamMessage,
  artifact: SessionStreamArtifact | null,
  profile: SessionAgentProfile,
  options?: SessionStreamLifecycleOptions,
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
  return {
    id: nextId("assistant", options?.createId),
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
  if (
    event.runtimeId === "subagent_swarm"
    || event.runtimeId === "planner_lane"
    || String(event.topic || "").startsWith("subagent.")
    || String(event.topic || "").startsWith("planner.")
    || String(event.topic || "").startsWith("chat.planner_mode.")
    || String(event.topic || "").startsWith("chat.task_planning_mode.")
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
  const targets = Array.isArray(event.targets) ? event.targets : [];
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
  const todoToolEvent = (event.type === "tool_start" || event.type === "tool_result") && isTodoToolRuntimePayload({
    ...eventData,
    toolName: event.tool?.toolName || eventData.toolName || eventData.tool_name,
    tool: {
      ...asRecord(eventData.tool),
      toolName: event.tool?.toolName || eventData.toolName || eventData.tool_name,
    },
  });

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
    appendArtifactNode(message, resolveArtifact(event), nextActiveAgentProfile, options);
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
        timestamp: Date.now(),
        ...nextActiveAgentProfile,
      });
    }
  } else if (event.type === "text_chunk") {
    const current = ensureCurrent();
    current.uiStreamPhase = "streaming";
    const eventData = asRecord(event.data);
    const content = String(event.content || "");
    const snapshot = typeof eventData.snapshot === "string" ? eventData.snapshot : undefined;
    const explicitNode = event.node_id
      ? (Array.isArray(current.nodes)
        ? current.nodes.find((node) => String(node.id || "").trim() === event.node_id)
        : undefined)
      : undefined;
    const lastNode = explicitNode || (Array.isArray(current.nodes) ? current.nodes[current.nodes.length - 1] : undefined);
    if (lastNode && lastNode.kind === "narrative" && lastNode.role === "assistant" && lastNode.agentName === nextActiveAgentProfile.agentName) {
      if (snapshot !== undefined) {
        lastNode.content = snapshot;
        current.content = deriveNarrativeContentFromNodes(current);
      } else {
        const suffix = computeStreamingSuffix(String(lastNode.content || ""), content);
        if (suffix) {
          lastNode.content = `${String(lastNode.content || "")}${suffix}`;
          current.content = `${String(current.content || "")}${suffix}`;
        }
      }
    } else {
      upsertTimelineNode(current, {
        id: event.node_id || nextId("node", options?.createId),
        kind: "narrative",
        role: "assistant",
        content: snapshot ?? content,
        timestamp: Date.now(),
        ...nextActiveAgentProfile,
      });
      current.content = snapshot !== undefined
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
    const explicitNode = event.node_id
      ? (Array.isArray(current.nodes)
        ? current.nodes.find((node) => String(node.id || "").trim() === event.node_id)
        : undefined)
      : undefined;
    const lastNode = explicitNode || (Array.isArray(current.nodes) ? current.nodes[current.nodes.length - 1] : undefined);
    if (lastNode && lastNode.kind === "execution" && lastNode.executionType === "reasoning" && !lastNode.time) {
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
        time: 0,
        startTime: Date.now(),
        timestamp: Date.now(),
        ...nextActiveAgentProfile,
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
    upsertTimelineNode(current, {
      id: event.node_id || nextId("node", options?.createId),
      kind: "execution",
      executionType: "tool_call",
      toolCallId: event.tool?.toolCallId || (typeof eventData.toolCallId === "string" ? eventData.toolCallId : undefined),
      toolName: event.tool?.toolName || (typeof eventData.toolName === "string" ? eventData.toolName : undefined),
      args: event.tool?.args ?? eventData.args ?? asRecord(eventData.tool).args,
      timestamp: Date.now(),
      ...nextActiveAgentProfile,
    });
    if (narrativeContent) {
      appendNarrativeContent(current, narrativeContent, nextActiveAgentProfile, options);
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
    const toolCallId = event.tool?.toolCallId || (typeof eventData.toolCallId === "string" ? eventData.toolCallId : undefined);
    const narrativeContent = typeof event.content === "string" && event.content.trim()
      ? event.content.trim()
      : typeof eventData.content === "string" && eventData.content.trim()
        ? eventData.content.trim()
        : typeof eventData.message === "string" && eventData.message.trim()
          ? eventData.message.trim()
          : "";
    if (event.node_id) {
      upsertTimelineNode(current, {
        id: event.node_id,
        kind: "execution",
        executionType: "tool_result",
        toolCallId,
        toolName: event.tool?.toolName || (typeof eventData.toolName === "string" ? eventData.toolName : undefined),
        result: event.tool?.result ?? eventData.result,
        timestamp: Date.now(),
        ...nextActiveAgentProfile,
      });
    } else {
      const existingToolCall = (current.nodes || []).find((node) =>
        node.kind === "execution"
        && node.executionType === "tool_call"
        && node.toolCallId === toolCallId,
      ) as SessionStreamExecutionNode | undefined;

      if (existingToolCall) {
        existingToolCall.result = event.tool?.result ?? eventData.result;
        existingToolCall.toolName = existingToolCall.toolName
          || event.tool?.toolName
          || (typeof eventData.toolName === "string" ? eventData.toolName : undefined);
        existingToolCall.timestamp = Date.now();
      } else {
        upsertTimelineNode(current, {
          id: nextId("node", options?.createId),
          kind: "execution",
          executionType: "tool_result",
          toolCallId,
          toolName: event.tool?.toolName || (typeof eventData.toolName === "string" ? eventData.toolName : undefined),
          result: event.tool?.result ?? eventData.result,
          timestamp: Date.now(),
          ...nextActiveAgentProfile,
        });
      }
    }
    if (narrativeContent) {
      appendNarrativeContent(current, narrativeContent, nextActiveAgentProfile, options);
    }
    appendEventArtifactIfPresent(current);
  } else if (event.type === "custom_event" && event.name === "artifact_recorded") {
    const current = ensureCurrent();
    current.uiStreamPhase = current.content ? "streaming" : "agent_started";
    appendArtifactNode(current, resolveArtifact(event), nextActiveAgentProfile, options);
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
      appendNarrativeContent(current, narrativeContent, nextActiveAgentProfile, options);
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
      timestamp: Date.now(),
      ...nextActiveAgentProfile,
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
      appendNarrativeContent(current, narrativeContent, nextActiveAgentProfile, options);
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
      timestamp: Date.now(),
      ...nextActiveAgentProfile,
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
      appendNarrativeContent(current, narrativeContent, nextActiveAgentProfile, options);
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
      timestamp: Date.now(),
      ...nextActiveAgentProfile,
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
      timestamp: Date.now(),
      ...nextActiveAgentProfile,
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
      appendNarrativeContent(current, narrativeContent, nextActiveAgentProfile, options);
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
      timestamp: Date.now(),
      ...nextActiveAgentProfile,
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
      timestamp: Date.now(),
      ...nextActiveAgentProfile,
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
      timestamp: Date.now(),
      ...nextActiveAgentProfile,
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
