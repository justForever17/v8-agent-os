export type SubagentReturnTimelineNode = {
  id?: string | null;
  kind?: string | null;
  executionType?: string | null;
  topic?: string | null;
  timestamp?: number | null;
  data?: Record<string, unknown> | null;
};

export type SubagentReturnMessage = {
  nodes?: SubagentReturnTimelineNode[] | null;
};

export type SubagentReturnProjection = {
  id: string;
  delegationId: string | null;
  invocationId: string | null;
  parentDelegationId: string | null;
  parentInvocationId: string | null;
  depth: number;
  name: string;
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
  return "running";
}

function projectionFromNode(node: SubagentReturnTimelineNode, fallbackIndex: number): SubagentReturnProjection | null {
  const topic = text(node.topic).toLowerCase();
  if (!topic.startsWith("subagent.task.")) return null;
  if (node.kind && node.kind !== "execution" && node.kind !== "tool") return null;
  const outer = recordOf(node.data);
  const payload = { ...outer, ...recordOf(outer.data) };
  const delegationId = text(payload.delegationId || payload.delegation_id) || null;
  const invocationId = text(payload.invocationId || payload.invocation_id) || null;
  const taskBriefId = text(payload.taskBriefId || payload.task_brief_id);
  const parentDelegationId = text(payload.parentDelegationId || payload.parent_delegation_id) || null;
  const parentInvocationId = text(payload.parentInvocationId || payload.parent_invocation_id) || null;
  const depth = numberValue(payload.delegationDepth || payload.delegation_depth, parentDelegationId || parentInvocationId ? 2 : 1);
  const acceptance = recordOf(payload.supervisorAcceptance || payload.supervisor_acceptance);
  const summary = compactTruth(
    payload.resultText
      || payload.result_text
      || payload.summary
      || payload.compactTranscript
      || payload.taskGoal
      || payload.task_goal,
  );
  const selfCheckCandidate = compactTruth(payload.localSelfCheck || payload.local_self_check, 900);
  const selfCheck = selfCheckCandidate?.startsWith("Subagent branch completed; supervisor must still")
    ? null
    : selfCheckCandidate;
  const id = delegationId || taskBriefId || invocationId || text(node.id) || `subagent-return:${fallbackIndex}`;
  return {
    id,
    delegationId,
    invocationId,
    parentDelegationId,
    parentInvocationId,
    depth,
    name: text(payload.subagentName || payload.subagent_name || payload.targetLabel || payload.target_label || payload.subagentId || payload.subagent_id) || "Subagent",
    avatar: depth > 1 ? null : text(payload.subagentAvatar || payload.subagent_avatar) || null,
    family: text(payload.subagentFamily || payload.subagent_family || payload.family) || null,
    taskGoal: compactTruth(payload.taskGoal || payload.task_goal, 360),
    status: normalizedStatus(topic, payload.status),
    summary,
    selfCheck: selfCheck && selfCheck !== summary ? selfCheck : null,
    acceptanceStatus: text(acceptance.status) || null,
    acceptanceSummary: compactTruth(acceptance.summary || acceptance.reason, 500),
    artifactRefs: stringArray(payload.adoptedArtifactRefs || payload.artifactRefs || payload.artifact_refs),
    timestamp: numberValue(node.timestamp || payload.timestamp, fallbackIndex),
    children: [],
  };
}

export function buildSubagentReturnProjection(
  messages: SubagentReturnMessage[] | null | undefined,
  runtimeNodes: SubagentReturnTimelineNode[] | null | undefined = [],
): SubagentReturnProjection[] {
  const byId = new Map<string, SubagentReturnProjection>();
  let fallbackIndex = 0;
  const collectNode = (node: SubagentReturnTimelineNode) => {
    const item = projectionFromNode(node, fallbackIndex++);
    if (!item) return;
    const existing = byId.get(item.id);
    if (!existing || item.timestamp >= existing.timestamp) byId.set(item.id, item);
  };
  for (const message of messages || []) {
    for (const node of message.nodes || []) {
      collectNode(node);
    }
  }
  for (const node of runtimeNodes || []) collectNode(node);

  const items = Array.from(byId.values()).sort((left, right) => left.timestamp - right.timestamp);
  const byDelegationId = new Map(items.filter((item) => item.delegationId).map((item) => [item.delegationId as string, item]));
  const byInvocationId = new Map(items.filter((item) => item.invocationId).map((item) => [item.invocationId as string, item]));
  const roots: SubagentReturnProjection[] = [];
  for (const item of items) {
    const parent = (item.parentDelegationId && byDelegationId.get(item.parentDelegationId))
      || (item.parentInvocationId && byInvocationId.get(item.parentInvocationId));
    if (parent && parent.id !== item.id) parent.children.push(item);
    else roots.push(item);
  }
  return roots.sort((left, right) => right.timestamp - left.timestamp);
}
