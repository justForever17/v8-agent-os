export type SubagentReturnTimelineNode = {
  id?: string | null;
  kind?: string | null;
  role?: string | null;
  executionType?: string | null;
  topic?: string | null;
  timestamp?: number | null;
  eventSeq?: number | null;
  eventId?: string | null;
  runId?: string | null;
  ownerRuntimeId?: string | null;
  ownerAgentKind?: string | null;
  ownerAgentId?: string | null;
  ownerMessageId?: string | null;
  content?: string | null;
  label?: string | null;
  toolName?: string | null;
  toolCallId?: string | null;
  args?: unknown;
  result?: unknown;
  agentVisibleResult?: unknown;
  artifact?: unknown;
  data?: Record<string, unknown> | null;
};

export type SubagentReturnMessage = {
  id?: string | null;
  nodes?: SubagentReturnTimelineNode[] | null;
};

export type SubagentActivityEvent = {
  eventId: string;
  eventSeq: number;
  ownerMessageId?: string | null;
  topic: string;
  kind: "started" | "reasoning" | "tool_call" | "tool_result" | "narrative" | "artifact" | "status" | "completed" | "failed";
  timestamp: number;
  status?: string | null;
  node: SubagentReturnTimelineNode;
};

export type SubagentReturnProjection = {
  id: string;
  delegationId: string | null;
  invocationId: string | null;
  parentDelegationId: string | null;
  parentInvocationId: string | null;
  depth: number;
  name: string;
  roleLabel: string | null;
  avatar: string | null;
  family: string | null;
  taskGoal: string | null;
  status: string;
  summary: string | null;
  selfCheck: string | null;
  acceptanceStatus: string | null;
  acceptanceSummary: string | null;
  artifactRefs: string[];
  timestamp: number;
  startedEventSeq: number | null;
  completedEventSeq: number | null;
  events: SubagentActivityEvent[];
  children: SubagentReturnProjection[];
};

function recordOf(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function compactTruth(value: unknown, limit = 1600): string | null {
  const normalized = text(value).replace(/\n{3,}/g, "\n\n");
  if (
    !normalized
    || (/^[\[{]/.test(normalized) && /[\]}]$/.test(normalized))
    || /<think\b|toolobs:\/\/|rawRef\s*:/i.test(normalized)
  ) return null;
  return normalized.length <= limit ? normalized : `${normalized.slice(0, limit - 1).trimEnd()}…`;
}

const TRANSCRIPT_ROLE_PREFIX = /^(ai|assistant|tool|system|human|user):\s*/i;
const TRANSCRIPT_ROLE_BOUNDARY = /\n(?=(?:ai|assistant|tool|system|human|user):\s*)/i;

function humanConclusion(value: unknown): unknown {
  const normalized = text(value);
  if (!normalized || !/(?:^|\n)(?:ai|assistant|tool|system|human|user):\s*/i.test(normalized)) return value;
  const assistantConclusions = normalized
    .split(TRANSCRIPT_ROLE_BOUNDARY)
    .map((segment) => {
      const match = TRANSCRIPT_ROLE_PREFIX.exec(segment);
      if (!match) return null;
      const role = match[1].toLowerCase();
      const content = segment.slice(match[0].length).trim();
      if (!content || !["ai", "assistant"].includes(role)) return null;
      if (/^(?:使用工具|调用工具|tool\b)/i.test(content)) return null;
      return content;
    })
    .filter((item): item is string => Boolean(item));
  return assistantConclusions[assistantConclusions.length - 1] || "";
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? Array.from(new Set(value.map((item) => text(item)).filter(Boolean))).slice(0, 12)
    : [];
}

function numberValue(value: unknown, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function normalizedStatus(topic: string, raw: unknown): string {
  const explicit = text(raw).toLowerCase();
  if (explicit) return explicit;
  if (topic.endsWith(".completed")) return "completed";
  if (topic.endsWith(".failed")) return "failed";
  if (topic.includes("waiting")) return "waiting";
  return "running";
}

const INTERNAL_KEY_PATTERN = /^(?:_diagnostics|diagnostics|raw|rawRef|traceRef|runtimeContext|provider(?:Id|Name|Payload)?|run_?id|episode(?:Id|_id)?|commandSession|toolPolicy)$/i;
const INTERNAL_LINEAGE_KEY_PATTERN = /(?:^|_)(?:source|target|outer|parent|child|producer)?_?(?:run|episode)_?(?:id|ids)$/i;
const INTERNAL_ID_PATTERN = /\b(?:run|episode)_[A-Za-z0-9][A-Za-z0-9_-]*\b/g;

function sanitizeHumanValue(value: unknown, depth = 0): unknown {
  if (depth > 5 || value == null) return value;
  if (typeof value === "string") {
    const cleaned = value.replace(INTERNAL_ID_PATTERN, "内部引用");
    return cleaned.length <= 4000 ? cleaned : `${cleaned.slice(0, 3999)}…`;
  }
  if (typeof value === "number" || typeof value === "boolean") return value;
  if (Array.isArray(value)) return value.slice(0, 40).map((item) => sanitizeHumanValue(item, depth + 1));
  const source = recordOf(value);
  const output: Record<string, unknown> = {};
  for (const [key, nested] of Object.entries(source)) {
    if (INTERNAL_KEY_PATTERN.test(key) || INTERNAL_LINEAGE_KEY_PATTERN.test(key)) continue;
    output[key] = sanitizeHumanValue(nested, depth + 1);
  }
  return output;
}

function nestedRecord(record: Record<string, unknown>, ...keys: string[]) {
  for (const key of keys) {
    const candidate = recordOf(record[key]);
    if (Object.keys(candidate).length) return candidate;
  }
  return {};
}

function mergedPayload(node: SubagentReturnTimelineNode) {
  const outer = recordOf(node.data);
  return { ...outer, ...recordOf(outer.data) };
}

function runtimeContext(payload: Record<string, unknown>) {
  return nestedRecord(payload, "runtimeContext", "runtime_context");
}

function episodeFromPayload(payload: Record<string, unknown>) {
  const nested = nestedRecord(payload, "episode");
  if (Object.keys(nested).length) return nested;

  const episodeId = text(
    payload.episodeId
      || payload.episode_id
      || payload.producerEpisodeId
      || payload.producer_episode_id,
  );
  const kind = text(payload.episodeKind || payload.episode_kind || payload.runtimeKind || payload.runtime_kind);
  if (!episodeId && !kind) return {};
  return {
    episodeId,
    kind,
    parentEpisodeId: payload.parentEpisodeId || payload.parent_episode_id,
    rootEpisodeId: payload.rootEpisodeId || payload.root_episode_id,
    state: payload.state || payload.status,
    targetLabel: payload.targetLabel || payload.target_label || payload.agentName || payload.agent_name,
    reason: payload.taskGoal || payload.task_goal || payload.reason,
  };
}

function taskBriefFromEpisode(episode: Record<string, unknown>) {
  const inputs = nestedRecord(episode, "inputs");
  const need = nestedRecord(episode, "need");
  const fromInputs = nestedRecord(inputs, "taskBrief", "task_brief");
  return Object.keys(fromInputs).length ? fromInputs : nestedRecord(need, "taskBrief", "task_brief");
}

function isDelegationEpisode(episode: Record<string, unknown>) {
  const kind = text(episode.kind || episode.runtimeKind || episode.runtime_kind).toLowerCase();
  const targetKind = text(episode.targetKind || episode.target_kind).toLowerCase();
  return kind === "delegation" || targetKind === "local_agent" || targetKind === "subagent";
}

function humanNameFromEpisodeId(value: unknown): string {
  const episodeId = text(value);
  const parts = episodeId.split("::").filter(Boolean);
  const slug = parts[parts.length - 1] || "";
  if (!slug || !/^[a-z0-9][a-z0-9-]*$/i.test(slug)) return "";
  return slug
    .split("-")
    .filter(Boolean)
    .map((part) => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
    .join(" ");
}

function humanNameFromHandoff(value: unknown): string {
  const summary = text(value);
  const match = /^\s*(?:-\s*Summary:\s*)?\[([^\]\n]{1,80})\s+执行完毕\]/i.exec(summary);
  return match?.[1]?.trim() || "";
}

function eventSeqOf(node: SubagentReturnTimelineNode, fallbackIndex: number) {
  return numberValue(node.eventSeq || recordOf(node.data).seq, fallbackIndex + 1);
}

function eventTimestampOf(node: SubagentReturnTimelineNode, eventSeq: number) {
  return numberValue(node.timestamp || recordOf(node.data).timestamp, eventSeq);
}

function deterministicEventId(node: SubagentReturnTimelineNode, topic: string, delegationId: string, eventSeq: number) {
  return text(node.eventId || node.id) || `runtime:${delegationId}:${eventSeq}:${topic}`;
}

function baseProjection(input: {
  id: string;
  delegationId?: string | null;
  invocationId?: string | null;
  parentDelegationId?: string | null;
  parentInvocationId?: string | null;
  depth?: number;
  name?: string;
  roleLabel?: string | null;
  taskGoal?: string | null;
  timestamp?: number;
}): SubagentReturnProjection {
  return {
    id: input.id,
    delegationId: input.delegationId || null,
    invocationId: input.invocationId || null,
    parentDelegationId: input.parentDelegationId || null,
    parentInvocationId: input.parentInvocationId || null,
    depth: input.depth || (input.parentDelegationId || input.parentInvocationId ? 2 : 1),
    name: input.name || "Subagent",
    roleLabel: input.roleLabel || null,
    avatar: null,
    family: null,
    taskGoal: input.taskGoal || null,
    status: "running",
    summary: null,
    selfCheck: null,
    acceptanceStatus: null,
    acceptanceSummary: null,
    artifactRefs: [],
    timestamp: input.timestamp || 0,
    startedEventSeq: null,
    completedEventSeq: null,
    events: [],
    children: [],
  };
}

function projectionIdentityFromNode(node: SubagentReturnTimelineNode) {
  const payload = mergedPayload(node);
  const context = runtimeContext(payload);
  const episode = episodeFromPayload(payload);
  const delegationId = text(
    payload.delegationId
      || payload.delegation_id
      || context.delegation_id
      || context.delegationId
      || episode.episodeId
      || episode.episode_id
      || episode.id,
  );
  const invocationId = text(payload.invocationId || payload.invocation_id || episode.invocationId || episode.invocation_id);
  const taskBriefId = text(payload.taskBriefId || payload.task_brief_id);
  return { payload, context, episode, delegationId, invocationId, taskBriefId };
}

function addActivityEvent(
  item: SubagentReturnProjection,
  node: SubagentReturnTimelineNode,
  fallbackIndex: number,
  kind: SubagentActivityEvent["kind"],
  topic: string,
  displayNode: SubagentReturnTimelineNode,
) {
  const eventSeq = eventSeqOf(node, fallbackIndex);
  const eventId = deterministicEventId(node, topic, item.delegationId || item.id, eventSeq);
  if (item.events.some((event) => event.eventId === eventId)) return;
  const timestamp = eventTimestampOf(node, eventSeq);
  item.events.push({
    eventId,
    eventSeq,
    ownerMessageId: text(node.ownerMessageId || mergedPayload(node).ownerMessageId || mergedPayload(node).message_id) || null,
    topic,
    kind,
    timestamp,
    status: text(mergedPayload(node).status) || null,
    node: {
      ...displayNode,
      id: displayNode.id || `subagent-event:${eventId}`,
      eventId,
      eventSeq,
      timestamp,
      ownerRuntimeId: "subagent_swarm",
      ownerAgentKind: "subagent",
      ownerAgentId: item.name,
    },
  });
  item.timestamp = Math.max(item.timestamp, timestamp);
}

function activityFromNode(
  item: SubagentReturnProjection,
  node: SubagentReturnTimelineNode,
  fallbackIndex: number,
  topic: string,
) {
  const payload = mergedPayload(node);
  const lowerTopic = topic.toLowerCase();
  const content = compactTruth(node.content || payload.snapshot || payload.content || payload.summary || payload.message, 2400);
  const tool = nestedRecord(payload, "tool");
  const toolName = text(node.toolName || tool.toolName || tool.tool_name || payload.toolName || payload.tool_name) || "工具调用";
  const toolCallId = text(node.toolCallId || tool.toolCallId || tool.toolInvocationId || payload.toolCallId || payload.toolInvocationId);

  if (lowerTopic.includes("reasoning") || node.executionType === "reasoning") {
    if (!content) return;
    addActivityEvent(item, node, fallbackIndex, "reasoning", topic, {
      kind: "execution",
      executionType: "reasoning",
      content,
      data: { reasoningKind: text(payload.reasoningKind || payload.reasoning_kind) || "summary" },
    });
    return;
  }
  if (lowerTopic.includes("tool.started") || node.executionType === "tool_call") {
    addActivityEvent(item, node, fallbackIndex, "tool_call", topic, {
      kind: "execution",
      executionType: "tool_call",
      toolName,
      toolCallId,
      args: sanitizeHumanValue(node.args ?? tool.args ?? payload.args ?? payload.request),
    });
    return;
  }
  if (lowerTopic.includes("tool.finished") || node.executionType === "tool_result") {
    addActivityEvent(item, node, fallbackIndex, "tool_result", topic, {
      kind: "execution",
      executionType: "tool_result",
      toolName,
      toolCallId,
      result: sanitizeHumanValue(
        node.agentVisibleResult
          ?? tool.agentVisibleResult
          ?? tool.agent_visible_result
          ?? payload.agentVisibleResult
          ?? payload.agent_visible_result
          ?? node.result
          ?? tool.result
          ?? payload.result,
      ),
    });
    return;
  }
  if (lowerTopic.includes("artifact") || node.kind === "artifact") {
    const artifact = recordOf(node.artifact || payload.artifact);
    if (!Object.keys(artifact).length) return;
    addActivityEvent(item, node, fallbackIndex, "artifact", topic, {
      kind: "artifact",
      artifact: sanitizeHumanValue(artifact),
    });
    return;
  }
  if ((lowerTopic.includes("text") || node.kind === "narrative") && content) {
    addActivityEvent(item, node, fallbackIndex, "narrative", topic, {
      kind: "narrative",
      role: "assistant",
      content,
      data: {},
    });
  }
}

function mergeTerminalProjection(
  item: SubagentReturnProjection,
  node: SubagentReturnTimelineNode,
  fallbackIndex: number,
  topic: string,
  payload: Record<string, unknown>,
) {
  const acceptance = recordOf(payload.supervisorAcceptance || payload.supervisor_acceptance);
  const summary = compactTruth(
    humanConclusion(
      payload.resultText
      || payload.result_text
      || payload.summary
      || payload.compactTranscript
      || payload.taskGoal
      || payload.task_goal,
    ),
  );
  const selfCheckCandidate = compactTruth(payload.localSelfCheck || payload.local_self_check, 900);
  const selfCheck = selfCheckCandidate?.startsWith("Subagent branch completed; supervisor must still")
    ? null
    : selfCheckCandidate;
  item.name = text(payload.subagentName || payload.subagent_name || payload.targetLabel || payload.target_label || payload.subagentId || payload.subagent_id) || item.name;
  item.roleLabel = text(payload.subagentRoleLabel || payload.subagent_role_label || payload.roleLabel || payload.role_label) || item.roleLabel;
  item.avatar = item.depth > 1 ? null : text(payload.subagentAvatar || payload.subagent_avatar) || item.avatar;
  item.family = text(payload.subagentFamily || payload.subagent_family || payload.family) || item.family;
  item.taskGoal = compactTruth(payload.taskGoal || payload.task_goal, 360) || item.taskGoal;
  item.status = normalizedStatus(topic, payload.status);
  item.summary = summary || item.summary;
  item.selfCheck = selfCheck && selfCheck !== item.summary ? selfCheck : item.selfCheck;
  item.acceptanceStatus = text(acceptance.status) || item.acceptanceStatus;
  item.acceptanceSummary = compactTruth(acceptance.summary || acceptance.reason, 500) || item.acceptanceSummary;
  item.artifactRefs = Array.from(new Set([
    ...item.artifactRefs,
    ...stringArray(payload.adoptedArtifactRefs || payload.artifactRefs || payload.artifact_refs),
  ])).slice(0, 12);
  const terminalKind = topic.endsWith(".failed") || ["failed", "degraded", "blocked", "cancelled"].includes(item.status)
    ? "failed"
    : topic.endsWith(".completed") || ["completed", "ready", "ok", "succeeded"].includes(item.status)
      ? "completed"
      : "status";
  const terminalLabel = terminalKind === "failed"
    ? "协作任务未完成"
    : terminalKind === "completed"
      ? "协作结果已回流"
      : "协作状态已更新";
  addActivityEvent(item, node, fallbackIndex, terminalKind, topic, {
    kind: "execution",
    executionType: "runtime_progress",
    label: terminalLabel,
    data: { status: item.status },
  });
  const seq = eventSeqOf(node, fallbackIndex);
  if (terminalKind === "completed" || terminalKind === "failed") item.completedEventSeq = seq;
}

function isGenericCollaborationStatus(event: SubagentActivityEvent) {
  return event.kind === "status"
    && event.node.kind === "execution"
    && event.node.executionType === "runtime_progress"
    && text(event.node.label) === "协作状态已更新";
}

export function buildSubagentReturnProjection(
  messages: SubagentReturnMessage[] | null | undefined,
  runtimeNodes: SubagentReturnTimelineNode[] | null | undefined = [],
): SubagentReturnProjection[] {
  const nodes: SubagentReturnTimelineNode[] = [];
  for (const message of messages || []) {
    for (const rawNode of message.nodes || []) nodes.push({ ...rawNode, ownerMessageId: rawNode.ownerMessageId || message.id });
  }
  for (const node of runtimeNodes || []) nodes.push(node);
  nodes.sort((left, right) => {
    const leftSeq = numberValue(left.eventSeq, 0);
    const rightSeq = numberValue(right.eventSeq, 0);
    if (leftSeq && rightSeq && leftSeq !== rightSeq) return leftSeq - rightSeq;
    const leftTime = numberValue(left.timestamp, 0);
    const rightTime = numberValue(right.timestamp, 0);
    return leftTime - rightTime;
  });

  const byId = new Map<string, SubagentReturnProjection>();
  const ensure = (input: Parameters<typeof baseProjection>[0]) => {
    const existing = byId.get(input.id);
    if (existing) {
      if (input.delegationId) existing.delegationId = input.delegationId;
      if (input.invocationId) existing.invocationId = input.invocationId;
      if (input.parentDelegationId) existing.parentDelegationId = input.parentDelegationId;
      if (input.parentInvocationId) existing.parentInvocationId = input.parentInvocationId;
      if (input.name && existing.name === "Subagent") existing.name = input.name;
      if (input.roleLabel) existing.roleLabel = input.roleLabel;
      if (input.taskGoal && !existing.taskGoal) existing.taskGoal = input.taskGoal;
      existing.depth = Math.max(existing.depth, input.depth || 1);
      existing.timestamp = Math.max(existing.timestamp, input.timestamp || 0);
      return existing;
    }
    const created = baseProjection(input);
    byId.set(input.id, created);
    return created;
  };

  nodes.forEach((node, index) => {
    const topic = text(node.topic || recordOf(node.data).topic).toLowerCase();
    if (!topic) return;
    const { payload, context, episode, delegationId, invocationId, taskBriefId } = projectionIdentityFromNode(node);

    if (topic.startsWith("runtime.episode.") || topic === "delegation.child.requested") {
      if (!isDelegationEpisode(episode)) return;
      const episodeId = text(episode.episodeId || episode.episode_id || episode.id);
      if (!episodeId) return;
      const progress = nestedRecord(payload, "progress");
      const timelineNode = nestedRecord(progress, "timelineNode", "timeline_node");
      const parentEpisodeId = text(episode.parentEpisodeId || episode.parent_episode_id || progress.parentDelegationId || progress.parent_delegation_id || payload.parentDelegationId || payload.parent_delegation_id);
      const parentDelegationId = parentEpisodeId.startsWith("subagent::") ? parentEpisodeId : null;
      const taskBrief = taskBriefFromEpisode(episode);
      const inputs = nestedRecord(episode, "inputs");
      const explicitDepth = numberValue(progress.delegationDepth || progress.delegation_depth || payload.delegationDepth || payload.delegation_depth, 0);
      const item = ensure({
        id: episodeId,
        delegationId: episodeId,
        invocationId: text(episode.invocationId || episode.invocation_id) || null,
        parentDelegationId,
        depth: explicitDepth || (parentDelegationId ? 2 : 1),
        name: text(progress.agentName || progress.agent_name || episode.targetLabel || episode.target_label || episode.targetId || episode.target_id || inputs.agentName || inputs.agentId)
          || humanNameFromEpisodeId(episodeId)
          || "Subagent",
        roleLabel: text(progress.agentRoleLabel || progress.agent_role_label || payload.subagentRoleLabel || payload.subagent_role_label),
        taskGoal: compactTruth(taskBrief.goal || episode.reason || inputs.taskGoal || inputs.reason || node.label || node.content, 360),
        timestamp: eventTimestampOf(node, index + 1),
      });
      const state = text(progress.status || episode.state || payload.status).toLowerCase();
      item.status = normalizedStatus(topic, state);
      if (Object.keys(timelineNode).length) {
        const embeddedTopic = text(timelineNode.topic).toLowerCase();
        if (embeddedTopic) {
          activityFromNode(item, {
            ...timelineNode,
            id: text(timelineNode.id) || `${text(node.id || node.eventId)}:timeline`,
            eventId: text(node.eventId || node.id),
            eventSeq: numberValue(node.eventSeq, index + 1),
            timestamp: numberValue(node.timestamp, index + 1),
            ownerMessageId: node.ownerMessageId,
            data: {
              ...recordOf(timelineNode.data),
              ownerRuntimeId: "subagent_swarm",
              ownerAgentKind: "subagent",
              ownerAgentId: item.name,
              delegationId: episodeId,
            },
          }, index, embeddedTopic);
          return;
        }
      }
      const kind: SubagentActivityEvent["kind"] = topic.endsWith(".started")
        ? "started"
        : topic.endsWith(".completed")
          ? "completed"
          : topic.endsWith(".failed") || topic.endsWith(".degraded")
            ? "failed"
            : "status";
      const label = kind === "started"
        ? "已接入协作任务"
        : kind === "completed"
          ? "协作执行已完成"
          : kind === "failed"
            ? "协作执行失败"
            : state.includes("waiting_child")
              ? "正在等待下级协作结果"
              : "协作状态已更新";
      addActivityEvent(item, node, index, kind, topic, {
        kind: "execution",
        executionType: "runtime_progress",
        label,
        data: { status: item.status },
      });
      const seq = eventSeqOf(node, index);
      if (kind === "started" && !item.startedEventSeq) item.startedEventSeq = seq;
      if (kind === "completed" || kind === "failed") item.completedEventSeq = seq;
      const handoff = nestedRecord(payload, "handoff", "handoffRef");
      if ((kind === "completed" || kind === "failed") && Object.keys(handoff).length) {
        const compactSummary = compactTruth(
          humanConclusion(handoff.compactSummary || handoff.compact_summary || handoff.summary),
        );
        item.name = humanNameFromHandoff(handoff.compactSummary || handoff.compact_summary || handoff.summary) || item.name;
        item.summary = compactSummary || item.summary;
        item.artifactRefs = Array.from(new Set([
          ...item.artifactRefs,
          ...stringArray(handoff.artifactRefs || handoff.artifact_refs),
          ...stringArray(handoff.evidenceRefs || handoff.evidence_refs),
        ])).slice(0, 12);
      }
      return;
    }

    const isTerminal = topic.startsWith("subagent.task.");
    const isFineGrained = text(node.ownerRuntimeId || payload.ownerRuntimeId || payload.runtimeId).toLowerCase() === "subagent_swarm"
      || text(node.ownerAgentKind || payload.ownerAgentKind).toLowerCase() === "subagent"
      || topic.startsWith("subagent.");
    if (!isTerminal && !isFineGrained) return;
    const identity = delegationId || taskBriefId || invocationId;
    if (!identity) return;
    const parentDelegationId = text(payload.parentDelegationId || payload.parent_delegation_id) || null;
    const parentInvocationId = text(payload.parentInvocationId || payload.parent_invocation_id) || null;
    const item = ensure({
      id: identity,
      delegationId: delegationId || null,
      invocationId: invocationId || null,
      parentDelegationId,
      parentInvocationId,
      depth: numberValue(payload.delegationDepth || payload.delegation_depth, parentDelegationId || parentInvocationId ? 2 : 1),
      name: text(payload.subagentName || payload.targetLabel || payload.ownerAgentId || node.ownerAgentId || context.subagent_id || context.subagentId) || "Subagent",
      roleLabel: text(payload.subagentRoleLabel || payload.subagent_role_label || payload.roleLabel || payload.role_label),
      taskGoal: compactTruth(payload.taskGoal || payload.task_goal, 360),
      timestamp: eventTimestampOf(node, index + 1),
    });
    if (isTerminal) {
      mergeTerminalProjection(item, node, index, topic, payload);
    } else if (topic.startsWith("handoff.ref.")) {
      const handoff = nestedRecord(payload, "handoff", "handoffRef");
      mergeTerminalProjection(item, node, index, topic, {
        ...payload,
        ...handoff,
        resultText: handoff.compactSummary || handoff.compact_summary || handoff.summary || node.label,
        status: handoff.status || payload.status,
      });
    } else {
      activityFromNode(item, node, index, topic);
    }
  });

  const items = Array.from(byId.values());
  for (const item of items) {
    item.events.sort((left, right) => left.eventSeq - right.eventSeq || left.timestamp - right.timestamp || left.eventId.localeCompare(right.eventId));
    const meaningfulEvents = item.events.filter((event) => !isGenericCollaborationStatus(event));
    if (meaningfulEvents.length) {
      item.events = meaningfulEvents;
    } else if (item.events.length > 1) {
      item.events = item.events.slice(-1);
    }
    if (!item.startedEventSeq && item.events.length) item.startedEventSeq = item.events[0].eventSeq;
  }
  const byDelegationId = new Map(items.filter((item) => item.delegationId).map((item) => [item.delegationId as string, item]));
  const byInvocationId = new Map(items.filter((item) => item.invocationId).map((item) => [item.invocationId as string, item]));
  const stableOrder = (left: SubagentReturnProjection, right: SubagentReturnProjection) => (
    numberValue(left.startedEventSeq, Number.MAX_SAFE_INTEGER)
      - numberValue(right.startedEventSeq, Number.MAX_SAFE_INTEGER)
    || left.timestamp - right.timestamp
    || left.id.localeCompare(right.id)
  );
  const roots: SubagentReturnProjection[] = [];
  for (const item of items) {
    const parent = (item.parentDelegationId && byDelegationId.get(item.parentDelegationId))
      || (item.parentInvocationId && byInvocationId.get(item.parentInvocationId));
    if (parent && parent.id !== item.id) parent.children.push(item);
    else roots.push(item);
  }
  for (const item of items) item.children.sort(stableOrder);
  return roots.sort(stableOrder);
}
