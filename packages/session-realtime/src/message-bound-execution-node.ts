import {
  buildCollaborationMicroStages,
  type BuildCollaborationMicroStageOptions,
  type CollaborationMicroStage,
  type CollaborationMicroStageActivityInput,
} from "./collaboration-micro-stage.js";

export type MessageBoundExecutionNodeKind =
  | "runtime"
  | "subagent"
  | "tool"
  | "handoff"
  | "governance";

export type MessageBoundExecutionNodeStatus =
  | "pending"
  | "active"
  | "completed"
  | "failed"
  | "degraded"
  | "cancelled";

export type MessageBoundExecutionNode = {
  id: string;
  messageId: string;
  nodeId: string;
  kind: MessageBoundExecutionNodeKind;
  status: MessageBoundExecutionNodeStatus;
  label: string;
  summary: string;
  timestamp: number;
  sequence: number;
  runId?: string;
  runtimeId?: string;
  toolCallId?: string;
  toolName?: string;
  episodeId?: string;
  dispatchGroup?: string;
  topic?: string;
  detailRef?: string;
  sourceEventIds: string[];
  data?: Record<string, unknown>;
};

export type MessageBoundExecutionTimelineNode = {
  id?: string | null;
  nodeId?: string | null;
  kind?: string | null;
  executionType?: string | null;
  topic?: string | null;
  label?: string | null;
  content?: string | null;
  status?: string | null;
  toolCallId?: string | null;
  toolInvocationId?: string | null;
  toolName?: string | null;
  runtimeId?: string | null;
  runId?: string | null;
  timestamp?: string | number | null;
  detailRef?: string | null;
  rawRef?: string | null;
  data?: Record<string, unknown> | null;
};

export type MessageBoundExecutionMessage = {
  id?: string | null;
  runId?: string | null;
  timestamp?: string | number | null;
  nodes?: MessageBoundExecutionTimelineNode[] | null;
};

export type MessageBoundCollaborationMicroStagePlacement = {
  id: string;
  anchorNodeId: string;
  anchorSequence: number;
  sourceNodeIds: string[];
  stages: CollaborationMicroStage[];
};

function readRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

function readString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function readTimestamp(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Date.parse(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return 0;
}

export function getMessageBoundExecutionTimelineNodeIdentityCandidates(
  node: MessageBoundExecutionTimelineNode | null | undefined,
): string[] {
  if (!node) return [];
  return Array.from(new Set([
    readString(node.nodeId),
    readString(node.id),
    readString(node.toolInvocationId),
    readString(node.toolCallId),
  ].filter(Boolean)));
}

function readNestedData(node: MessageBoundExecutionTimelineNode): Record<string, unknown> {
  const data = readRecord(node.data);
  const episode = readRecord(data.episode);
  const handoff = readRecord(data.handoff);
  const handoffRef = readRecord(data.handoffRef);
  if (Object.keys(episode).length > 0) return episode;
  if (Object.keys(handoffRef).length > 0) return handoffRef;
  if (Object.keys(handoff).length > 0) return handoff;
  return data;
}

function inferKind(node: MessageBoundExecutionTimelineNode): MessageBoundExecutionNodeKind | null {
  if (node.kind !== "execution") return null;
  const executionType = readString(node.executionType);
  const topic = readString(node.topic).toLowerCase();
  const toolName = readString(node.toolName).toLowerCase();
  if (executionType === "tool_call" || executionType === "tool_result") {
    if (toolName === "runtime_broker" || topic.includes("runtime")) return "runtime";
    if (toolName === "delegation_broker" || topic.includes("delegation") || topic.includes("subagent")) return "subagent";
    return "tool";
  }
  if (executionType !== "runtime_progress" && executionType !== "agent_start") return null;
  if (topic.startsWith("handoff.ref.")) return "handoff";
  if (topic.includes("delegation") || topic.includes("subagent")) return "subagent";
  if (topic.startsWith("runtime.") || topic.startsWith("capability.need.")) return "runtime";
  return null;
}

function inferStatus(node: MessageBoundExecutionTimelineNode): MessageBoundExecutionNodeStatus {
  const data = readNestedData(node);
  const text = [
    readString(node.status),
    readString(data.status),
    readString(data.state),
    readString(data.phase),
    readString(data.dispatchStatus || data.dispatch_status),
    readString(node.topic),
    readString(node.label),
    readString(node.content),
  ].join(" ").toLowerCase();
  if (/(cancel|cancelled|canceled|interrupt)/.test(text)) return "cancelled";
  if (/(degraded|partial|recover|missing_tasks|missing tasks)/.test(text)) return "degraded";
  if (/(fail|error|reject|blocked|stalled)/.test(text)) return "failed";
  if (/(complete|finish|done|success|succeeded|merged|ready|handoff)/.test(text)) return "completed";
  if (/(queued|pending|waiting)/.test(text)) return "pending";
  return "active";
}

function makeNodeId(messageId: string, node: MessageBoundExecutionTimelineNode, index: number) {
  return readString(node.nodeId)
    || readString(node.id)
    || readString(node.toolInvocationId)
    || readString(node.toolCallId)
    || `${messageId}:execution:${index}`;
}

function getDetailRef(node: MessageBoundExecutionTimelineNode): string {
  const data = readNestedData(node);
  return readString(node.detailRef)
    || readString(node.rawRef)
    || readString(data.detailRef)
    || readString(data.detail_ref)
    || readString(data.rawRef)
    || readString(data.raw_ref);
}

function getSummary(node: MessageBoundExecutionTimelineNode): string {
  const data = readNestedData(node);
  return readString(data.compactSummary)
    || readString(data.taskGoal)
    || readString(data.task_goal)
    || readString(data.summary)
    || readString(node.content)
    || readString(node.label)
    || readString(node.topic);
}

function getRuntimeId(node: MessageBoundExecutionTimelineNode): string {
  const data = readNestedData(node);
  return readString(node.runtimeId)
    || readString(data.runtimeId)
    || readString(data.runtime_id)
    || readString(data.runtimeKind)
    || readString(data.runtime_kind)
    || readString(data.kind);
}

function getRunId(message: MessageBoundExecutionMessage, node: MessageBoundExecutionTimelineNode): string {
  const data = readNestedData(node);
  return readString(node.runId)
    || readString(data.runId)
    || readString(data.run_id)
    || readString(data.rootRunId)
    || readString(data.root_run_id)
    || readString(message.runId);
}

export function buildMessageBoundExecutionNodes(
  messages: MessageBoundExecutionMessage[] = [],
): MessageBoundExecutionNode[] {
  const result: MessageBoundExecutionNode[] = [];
  for (const message of messages) {
    const messageId = readString(message.id);
    if (!messageId || !Array.isArray(message.nodes)) continue;
    message.nodes.forEach((node, index) => {
      const kind = inferKind(node);
      if (!kind) return;
      const data = readNestedData(node);
      const nodeId = makeNodeId(messageId, node, index);
      const timestamp = readTimestamp(node.timestamp) || readTimestamp(message.timestamp);
      const sourceEventId = readString(node.id) || nodeId;
      result.push({
        id: `${messageId}:${nodeId}`,
        messageId,
        nodeId,
        kind,
        status: inferStatus(node),
        label: readString(node.label) || readString(node.toolName) || readString(node.topic) || kind,
        summary: getSummary(node),
        timestamp,
        sequence: result.length,
        runId: getRunId(message, node) || undefined,
        runtimeId: getRuntimeId(node) || undefined,
        toolCallId: readString(node.toolInvocationId) || readString(node.toolCallId) || undefined,
        toolName: readString(node.toolName) || undefined,
        episodeId: readString(data.episodeId) || readString(data.episode_id) || readString(data.producerEpisodeId) || readString(data.producer_episode_id) || undefined,
        dispatchGroup: readString(data.dispatchGroup) || readString(data.dispatch_group) || undefined,
        topic: readString(node.topic) || undefined,
        detailRef: getDetailRef(node) || undefined,
        sourceEventIds: [sourceEventId],
        data: Object.keys(data).length > 0 ? data : undefined,
      });
    });
  }
  return result;
}

export function messageBoundExecutionNodesToStageActivities(
  nodes: MessageBoundExecutionNode[] = [],
): CollaborationMicroStageActivityInput[] {
  return nodes
    .filter((node) => node.kind === "runtime" || node.kind === "subagent" || node.kind === "handoff")
    .map((node) => ({
      id: node.nodeId,
      topic: node.topic || (node.kind === "subagent" ? "delegation_broker.dispatch" : "runtime.episode.active"),
      summary: node.summary,
      timestamp: node.timestamp,
      runtimeId: node.kind === "subagent" ? "subagent_swarm" : node.runtimeId,
      data: {
        ...(node.data || {}),
        runId: node.runId,
        episodeId: node.episodeId,
        producerEpisodeId: node.episodeId,
        dispatchGroup: node.dispatchGroup,
        kind: node.kind === "subagent" ? "subagent_swarm" : node.runtimeId,
        status: node.status,
        compactSummary: node.summary,
        detailRef: node.detailRef,
      },
    }));
}

export function buildCollaborationMicroStagesFromMessageBoundNodes(
  nodes: MessageBoundExecutionNode[] = [],
  options: BuildCollaborationMicroStageOptions = {},
): CollaborationMicroStage[] {
  return buildCollaborationMicroStages(
    messageBoundExecutionNodesToStageActivities(nodes),
    options,
  );
}

export function buildMessageBoundCollaborationMicroStagePlacement(
  nodes: MessageBoundExecutionNode[] = [],
  options: BuildCollaborationMicroStageOptions = {},
): MessageBoundCollaborationMicroStagePlacement | null {
  const stages = buildCollaborationMicroStagesFromMessageBoundNodes(nodes, options);
  if (stages.length === 0) return null;

  const sourceNodeIds = new Set(
    stages.flatMap((stage) => stage.sourceActivityIds.map((value) => readString(value)).filter(Boolean)),
  );
  const orderedSources = nodes
    .filter((node) => sourceNodeIds.has(node.nodeId))
    .sort((left, right) => left.sequence - right.sequence);
  const anchor = orderedSources[0];
  if (!anchor) return null;

  return {
    id: `collaboration-stage:${anchor.messageId}:${anchor.nodeId}`,
    anchorNodeId: anchor.nodeId,
    anchorSequence: anchor.sequence,
    sourceNodeIds: Array.from(new Set(orderedSources.map((node) => node.nodeId))),
    stages,
  };
}
