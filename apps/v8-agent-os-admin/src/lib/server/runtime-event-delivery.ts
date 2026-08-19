type JsonRecord = Record<string, unknown>;

type RuntimeSequenceGap = {
    expectedSeq: number;
    observedSeq: number;
    missingFromSeq: number;
    missingToSeq: number;
};

type RuntimeEventObservation = {
    acceptEvent?: boolean;
};

export function shouldDeliverRuntimeEventObservation(observation: unknown) {
  const candidate = observation && typeof observation === "object"
    ? observation as RuntimeEventObservation
    : {};
  return candidate.acceptEvent !== false;
}

/**
 * An empty replay page can still carry a newer durable watermark.  In that
 * case the missing rows were pruned (or the page raced a snapshot), so the
 * proxy must request an authoritative snapshot instead of polling the same
 * cursor forever.
 */
export function shouldRequestSnapshotForEmptyEventPage(latestSeq: unknown, contiguousSeq: unknown) {
  const latest = Number(latestSeq || 0) || 0;
  const contiguous = Number(contiguousSeq || 0) || 0;
  return latest > 0 && latest > contiguous;
}

export class RuntimeEventGapRecoveryThrottle {
    private active = false;
    private lastRequestAt = 0;

    constructor(private readonly retryIntervalMs = 2_000) {}

    shouldRequestSnapshot(gap: RuntimeSequenceGap | undefined, nowMs = Date.now()) {
        if (!gap) {
            this.active = false;
            this.lastRequestAt = 0;
            return false;
        }
        if (!this.active || nowMs - this.lastRequestAt >= this.retryIntervalMs) {
            this.active = true;
            this.lastRequestAt = nowMs;
            return true;
        }
        return false;
    }
}

function asRecord(value: unknown): JsonRecord {
    return value && typeof value === "object" && !Array.isArray(value) ? value as JsonRecord : {};
}

function readString(...values: unknown[]) {
    for (const value of values) {
        if (typeof value === "string" && value.trim()) {
            return value.trim();
        }
        if (typeof value === "number" && Number.isFinite(value)) {
            return String(value);
        }
    }
    return "";
}

function readNestedString(record: JsonRecord, path: string) {
    let current: unknown = record;
    for (const part of path.split(".")) {
        current = asRecord(current)[part];
    }
    return readString(current);
}

export function buildRuntimeEventDedupeKey(record: JsonRecord, data: JsonRecord, payload: JsonRecord) {
    const topic = readString(record.topic, data.topic, payload.topic, record.name).toLowerCase();
    const runId = readString(record.run_id, record.runId, data.run_id, data.runId, payload.run_id, payload.runId, "run");
    const runtimeId = readString(record.runtimeId, record.runtime_id, data.runtimeId, data.runtime_id, payload.runtimeId, payload.runtime_id, "runtime");
    const status = readString(record.status, data.status, payload.status, "state");
    const explicit = readString(record.dedupeKey, data.dedupeKey, data.dedupe_key, payload.dedupeKey, payload.dedupe_key);
    if (topic === "runtime.episode.progress") {
        const progress = {
            ...asRecord(payload.progress),
            ...asRecord(data.progress),
            ...asRecord(record.progress),
        };
        const timelineNode = asRecord(progress.timelineNode);
        const episodeId = readString(
            readNestedString(data, "episode.episodeId"),
            readNestedString(data, "episode.episode_id"),
            readNestedString(data, "episode.id"),
            readNestedString(payload, "episode.episodeId"),
            readNestedString(payload, "episode.episode_id"),
            readNestedString(payload, "episode.id"),
            data.episodeId,
            data.episode_id,
            payload.episodeId,
            payload.episode_id,
        );
        const progressIdentity = readString(
            timelineNode.id,
            timelineNode.eventId,
            timelineNode.event_id,
            timelineNode.nodeId,
            timelineNode.node_id,
            timelineNode.segmentId,
            timelineNode.segment_id,
            timelineNode.toolCallId,
            timelineNode.tool_call_id,
            progress.timelineNodeId,
            progress.timeline_node_id,
            progress.segmentId,
            progress.segment_id,
            progress.toolCallId,
            progress.tool_call_id,
            progress.toolInvocationId,
            progress.tool_invocation_id,
        );
        if (!progressIdentity) {
            return "";
        }
        const stage = readString(progress.stage, data.stage, payload.stage, "progress");
        return `runtime-episode-progress:${runId}:${episodeId || "episode"}:${stage}:${progressIdentity}`;
    }
    if (explicit) {
        return explicit;
    }
    if (topic.startsWith("runtime.episode.")) {
        const episodeId = readString(
            readNestedString(data, "episode.episodeId"),
            readNestedString(data, "episode.episode_id"),
            readNestedString(data, "episode.id"),
            readNestedString(payload, "episode.episodeId"),
            readNestedString(payload, "episode.episode_id"),
            readNestedString(payload, "episode.id"),
            data.episodeId,
            data.episode_id,
            payload.episodeId,
            payload.episode_id,
        );
        return `runtime-episode:${runId}:${episodeId || topic}:${topic}:${status}`;
    }
    if (topic.startsWith("delegation.") || topic.startsWith("subagent.")) {
        const dispatchGroup = readString(data.dispatchGroup, data.dispatch_group, payload.dispatchGroup, payload.dispatch_group, data.episodeId, payload.episodeId);
        const summary = readString(data.summary, data.message, payload.summary, payload.message, record.content);
        if (dispatchGroup || /missing|未形成|未确认/i.test(summary)) {
            return `delegation:${runId}:${dispatchGroup || topic}:${topic}:${status}`;
        }
    }
    if (topic.endsWith(".delta") || topic === "run.text.delta" || topic === "run.reasoning.delta") {
        return `stream:${runId}:${runtimeId}:${topic}`;
    }
    return "";
}

export function buildRuntimeEventDeliveryIdentity({
    eventId,
    dedupeKey,
    topic,
}: {
    eventId?: unknown;
    dedupeKey?: unknown;
    topic?: unknown;
}) {
    const durableEventId = readString(eventId);
    if (durableEventId) {
        return `event:${durableEventId}`;
    }
    const normalizedTopic = readString(topic).toLowerCase();
    if (normalizedTopic.endsWith(".delta")) {
        return "";
    }
    const fallback = readString(dedupeKey);
    return fallback ? `fallback:${fallback}` : "";
}
