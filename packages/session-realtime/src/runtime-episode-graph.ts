export type RuntimeEpisodeGraphStatus = "active" | "completed" | "failed" | "pending";

export type RuntimeEpisodeGraphNode = {
  id: string;
  parentId: string | null;
  label: string;
  subtitle: string;
  status: RuntimeEpisodeGraphStatus;
  depth: number;
  eventCount: number;
  timestamp: number;
};

export type RuntimeEpisodeGraphActivity = {
  id: string;
  topic?: string | null;
  summary?: string | null;
  timestamp?: number | string | null;
  data?: Record<string, unknown> | null;
};

export type RuntimeEpisodeGraphOptions = {
  rootLabel?: string;
  rootSubtitle?: string;
  kindLabels?: Record<string, string>;
};

function readRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

function readString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function readTimestamp(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Date.parse(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return 0;
}

export function isRuntimeEpisodeGraphActivity(activity: Pick<RuntimeEpisodeGraphActivity, "topic">): boolean {
  const topic = String(activity.topic || "").trim();
  return topic.startsWith("capability.need.")
    || topic.startsWith("runtime.episode.")
    || topic.startsWith("delegation.child.")
    || topic.startsWith("handoff.ref.");
}

function getEpisodePayload(activity: RuntimeEpisodeGraphActivity): Record<string, unknown> {
  const data = readRecord(activity.data);
  const episode = readRecord(data.episode);
  return Object.keys(episode).length > 0 ? episode : data;
}

function getHandoffPayload(activity: RuntimeEpisodeGraphActivity): Record<string, unknown> {
  const data = readRecord(activity.data);
  const handoffRef = readRecord(data.handoffRef);
  if (Object.keys(handoffRef).length > 0) return handoffRef;
  const handoff = readRecord(data.handoff);
  return Object.keys(handoff).length > 0 ? handoff : data;
}

function normalizeStatus(value: string): RuntimeEpisodeGraphStatus | null {
  const normalized = value.trim().toLowerCase();
  if (!normalized) return null;
  if (/(fail|error|reject|blocked|cancel|stalled)/.test(normalized)) return "failed";
  if (/(complete|finish|done|success|succeeded|merged|ready)/.test(normalized)) return "completed";
  if (/(start|dispatch|run|active|progress|queued|waiting|leased|routed|detected)/.test(normalized)) return "active";
  return null;
}

function inferStatus(activity: RuntimeEpisodeGraphActivity, data: Record<string, unknown>): RuntimeEpisodeGraphStatus {
  const topic = readString(activity.topic);
  if (topic.startsWith("handoff.ref.")) {
    const explicit = normalizeStatus(readString(data.status));
    return explicit || "completed";
  }
  const explicit = normalizeStatus(readString(data.status) || readString(data.state) || readString(data.phase));
  if (explicit) return explicit;
  const topicStatus = normalizeStatus(`${topic} ${activity.summary || ""}`);
  if (topicStatus) return topicStatus;
  return "pending";
}

function labelForKind(kind: string, labels: Record<string, string>) {
  return labels[kind] || kind || labels.runtime || "Runtime";
}

function upsertNode(
  nodes: Map<string, RuntimeEpisodeGraphNode>,
  next: RuntimeEpisodeGraphNode,
) {
  const existing = nodes.get(next.id);
  if (!existing) {
    nodes.set(next.id, next);
    return;
  }
  nodes.set(next.id, {
    ...existing,
    parentId: existing.parentId || next.parentId,
    label: existing.label || next.label,
    subtitle: next.subtitle || existing.subtitle,
    status: next.status === "pending" ? existing.status : next.status,
    eventCount: existing.eventCount + Math.max(1, next.eventCount),
    timestamp: Math.max(existing.timestamp, next.timestamp),
  });
}

export function buildRuntimeEpisodeGraph(
  activities: RuntimeEpisodeGraphActivity[],
  options: RuntimeEpisodeGraphOptions = {},
): RuntimeEpisodeGraphNode[] {
  const labels = options.kindLabels || {};
  const nodes = new Map<string, RuntimeEpisodeGraphNode>();
  nodes.set("supervisor", {
    id: "supervisor",
    parentId: null,
    label: options.rootLabel || "Supervisor",
    subtitle: options.rootSubtitle || "router",
    status: "active",
    depth: 0,
    eventCount: 0,
    timestamp: 0,
  });

  const orderedActivities = [...activities].sort((left, right) => {
    const leftTs = readTimestamp(left.timestamp);
    const rightTs = readTimestamp(right.timestamp);
    if (leftTs !== rightTs) return leftTs - rightTs;
    return String(left.id || "").localeCompare(String(right.id || ""));
  });

  for (const activity of orderedActivities) {
    if (!isRuntimeEpisodeGraphActivity(activity)) continue;
    const topic = readString(activity.topic);
    const timestamp = readTimestamp(activity.timestamp);
    if (topic.startsWith("handoff.ref.")) {
      const handoff = getHandoffPayload(activity);
      const producerId = readString(handoff.producerEpisodeId) || readString(handoff.producer_episode_id);
      if (!producerId) continue;
      const handoffId = readString(handoff.handoffRefId)
        || readString(handoff.handoffId)
        || readString(handoff.artifactId)
        || readString(activity.id)
        || `${producerId}:${timestamp}`;
      const handoffSummary = readString(handoff.compactSummary) || readString(handoff.summary) || readString(activity.summary);
      upsertNode(nodes, {
        id: producerId,
        parentId: null,
        label: "",
        subtitle: handoffSummary,
        status: inferStatus(activity, handoff),
        depth: 1,
        eventCount: 1,
        timestamp,
      });
      upsertNode(nodes, {
        id: `handoff:${handoffId}`,
        parentId: producerId,
        label: labelForKind("handoff", labels),
        subtitle: handoffSummary,
        status: inferStatus(activity, handoff),
        depth: 2,
        eventCount: 1,
        timestamp,
      });
      continue;
    }

    const data = getEpisodePayload(activity);
    const id = readString(data.episodeId) || readString(data.needId) || readString(data.need_id) || readString(activity.id);
    if (!id) continue;
    const kind = readString(data.kind) || readString(data.runtimeKind) || "runtime";
    const parentId = readString(data.parentEpisodeId) || readString(data.parent_episode_id) || "supervisor";
    const reason = readString(data.reason) || readString(data.summary) || readString(activity.summary);
    const grants = Array.isArray(data.requiredRuntimeAccess)
      ? data.requiredRuntimeAccess.map((item) => readString(item)).filter(Boolean)
      : [];

    upsertNode(nodes, {
      id,
      parentId,
      label: labelForKind(kind, labels),
      subtitle: reason || grants.slice(0, 2).join(" · "),
      status: inferStatus(activity, data),
      depth: 1,
      eventCount: 1,
      timestamp,
    });
  }

  const visited = new Set<string>();
  const resolveDepth = (id: string): number => {
    const node = nodes.get(id);
    if (!node || !node.parentId || node.parentId === id) return 0;
    if (visited.has(id)) return node.depth || 1;
    visited.add(id);
    const parentDepth = nodes.has(node.parentId) ? resolveDepth(node.parentId) : 0;
    node.depth = Math.min(8, parentDepth + 1);
    return node.depth;
  };

  for (const id of nodes.keys()) {
    resolveDepth(id);
  }

  return Array.from(nodes.values()).sort((left, right) => {
    if (left.depth !== right.depth) return left.depth - right.depth;
    return left.timestamp - right.timestamp;
  });
}
