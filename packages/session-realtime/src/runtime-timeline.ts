import type { AuthoritativeRuntimeTimelineEntry } from "./contract.js";
import { getRuntimeRegistryEntry, isRealtimeSurfaceRuntimeId, normalizeRuntimeId } from "./runtime-registry.js";
import { isEffectiveContextGovernancePayload, normalizeSessionRuntimeEvent, type NormalizeRuntimeEventOptions } from "./event-normalizer.js";

function parseTimelineTimestamp(raw: unknown): number {
  if (typeof raw === "number" && Number.isFinite(raw)) {
    return raw;
  }
  if (typeof raw === "string" && raw.trim()) {
    const timestamp = Date.parse(raw);
    if (Number.isFinite(timestamp)) {
      return timestamp;
    }
  }
  return 0;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? value as Record<string, unknown> : {};
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

function readNestedString(record: Record<string, unknown>, ...paths: string[]) {
  for (const path of paths) {
    const parts = path.split(".");
    let current: unknown = record;
    for (const part of parts) {
      current = asRecord(current)[part];
    }
    const normalized = readString(current);
    if (normalized) {
      return normalized;
    }
  }
  return "";
}

function buildTimelineDedupeKey(input: {
  runtimeId?: string;
  topic?: string;
  runId?: string;
  status?: string;
  metadata?: Record<string, unknown>;
  fallbackId?: string;
}) {
  const topic = readString(input.topic).toLowerCase();
  const runtimeId = readString(input.runtimeId) || "runtime";
  const runId = readString(input.runId) || "run";
  const status = readString(input.status) || "state";
  const metadata = asRecord(input.metadata);
  const explicit = readString(metadata.dedupeKey, metadata.dedupe_key);
  if (explicit) {
    return explicit;
  }
  if (topic.startsWith("runtime.episode.") || topic.startsWith("handoff.ref.")) {
    const episodeId = readNestedString(
      metadata,
      "episode.episodeId",
      "episode.episode_id",
      "episode.id",
      "episode.needId",
      "episodeId",
      "episode_id",
      "needId",
    ) || readString(input.fallbackId);
    const handoffId = readNestedString(
      metadata,
      "handoff.handoffId",
      "handoff.handoff_id",
      "handoff.handoffRefId",
      "handoff.handoff_ref_id",
      "handoffRef.handoffId",
      "handoffRef.handoff_id",
    );
    return `runtime-episode:${runId}:${episodeId || handoffId || topic}:${topic}:${status}`;
  }
  if (topic.startsWith("delegation.") || topic.startsWith("subagent.")) {
    const dispatchGroup = readString(
      metadata.dispatchGroup,
      metadata.dispatch_group,
      metadata.episodeId,
      metadata.episode_id,
      input.fallbackId,
    );
    if (dispatchGroup || /missing|未形成|未确认/i.test(readString(metadata.summary, metadata.message))) {
      return `delegation:${runId}:${dispatchGroup || topic}:${topic}:${status}`;
    }
  }
  return "";
}

export function normalizeAuthoritativeRuntimeTimeline(input: unknown[]): AuthoritativeRuntimeTimelineEntry[] {
  const entries: AuthoritativeRuntimeTimelineEntry[] = [];
  const seen = new Set<string>();

  for (const raw of input) {
    if (!raw || typeof raw !== "object") {
      continue;
    }
    const record = raw as Record<string, unknown>;
    const runtimeId = normalizeRuntimeId(typeof record.runtimeId === "string" ? record.runtimeId : null);
    const topic = typeof record.topic === "string" ? record.topic : "";
    const summary = typeof record.summary === "string" ? record.summary.trim() : "";
    if (!runtimeId || !isRealtimeSurfaceRuntimeId(runtimeId) || !topic || !summary) {
      continue;
    }

    const id = typeof record.id === "string" && record.id.trim()
      ? record.id
      : `timeline-${runtimeId}-${record.seq || summary}`;
    const seq = Number(record.seq || 0) || 0;
    const key = `${id}:${seq}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);

    const kind = record.kind === "tool"
      || record.kind === "governance"
      || record.kind === "artifact"
      || record.kind === "handoff"
      ? record.kind
      : "progress";

    const metadata =
      record.metadata && typeof record.metadata === "object"
        ? record.metadata as Record<string, unknown>
        : record.data && typeof record.data === "object"
          ? record.data as Record<string, unknown>
          : undefined;
    const status = typeof record.status === "string" ? record.status : undefined;
    const runId = readString(record.runId, record.run_id, metadata?.runId, metadata?.run_id);
    const dedupeKey = typeof record.dedupeKey === "string" && record.dedupeKey.trim()
      ? record.dedupeKey.trim()
      : buildTimelineDedupeKey({ runtimeId, topic, runId, status, metadata, fallbackId: id });

    entries.push({
      id,
      seq,
      runId: runId || undefined,
      runtimeId,
      topic,
      kind,
      summary,
      actorLabel: typeof record.actorLabel === "string" ? record.actorLabel : undefined,
      timestamp: parseTimelineTimestamp(record.timestamp),
      status,
      dedupeKey: dedupeKey || undefined,
      replacesEventId: typeof record.replacesEventId === "string" ? record.replacesEventId : undefined,
      metadata,
    });
  }

  const merged = new Map<string, AuthoritativeRuntimeTimelineEntry>();
  for (const entry of entries) {
    const key = entry.dedupeKey || `${entry.id}:${entry.seq}`;
    const existing = merged.get(key);
    if (!existing || entry.seq >= existing.seq || entry.timestamp >= existing.timestamp) {
      merged.set(key, entry);
    }
  }

  return Array.from(merged.values()).sort((left, right) => {
    if (left.seq > 0 && right.seq > 0 && left.seq !== right.seq) {
      return right.seq - left.seq;
    }
    if (left.timestamp !== right.timestamp) {
      return right.timestamp - left.timestamp;
    }
    return right.id.localeCompare(left.id);
  });
}

export function buildAuthoritativeRuntimeTimelineEntryFromEvent(
  raw: unknown,
  options: NormalizeRuntimeEventOptions = {},
): AuthoritativeRuntimeTimelineEntry | null {
  const normalized = normalizeSessionRuntimeEvent(raw, options);
  if (!normalized || (normalized.visibility !== "visible" && normalized.visibility !== "hidden")) {
    return null;
  }

  const topic = String(normalized.topic || normalized.data?.topic || normalized.name || "").trim();
  if (!topic) {
    return null;
  }
  if (normalized.type === "reasoning_chunk" && (normalized.runtimeId || normalizeRuntimeId(topic) || "chat") === "chat") {
    return null;
  }
  if ((normalized.name === "context_governance_changed" || topic === "context.prepared")
    && !isEffectiveContextGovernancePayload(normalized.data || normalized)) {
    return null;
  }

  const runtimeId = normalized.runtimeId || normalizeRuntimeId(topic) || "chat";
  if (!isRealtimeSurfaceRuntimeId(runtimeId)) {
    return null;
  }
  const summary = String(
    normalized.type === "reasoning_chunk"
      ? (normalized.data?.snapshot || normalized.data?.summary || "正在思考...")
      : (
          normalized.data?.label
          || normalized.data?.summary
          || normalized.data?.message
          || normalized.data?.question
          || normalized.content
          || topic
        ),
  ).trim();
  if (!summary) {
    return null;
  }

  const kind: AuthoritativeRuntimeTimelineEntry["kind"] =
    topic.startsWith("handoff.ref.")
      ? "handoff"
      : normalized.name === "artifact_recorded"
      ? "artifact"
      : normalized.name === "ask_user"
        || normalized.name === "approval_requested"
        || normalized.name === "run_controlled"
        || normalized.name === "approval_resolved"
        || normalized.name === "safety_blocked"
        || normalized.name === "context_governance_changed"
        || normalized.name === "lane_updated"
        ? "governance"
        : normalized.type === "agent_start"
          ? "handoff"
          : normalized.type === "tool_start" || normalized.type === "tool_result"
            ? "tool"
            : "progress";

  const metadata = normalized.data;
  const handoff = asRecord(normalized.data?.handoff || normalized.data?.handoffRef);
  const episode = asRecord(normalized.data?.episode);
  const status = normalized.status
    || (typeof normalized.data?.status === "string" ? normalized.data.status : undefined)
    || readString(episode.state, handoff.status)
    || undefined;
  const eventId = normalized.event_id || `timeline-${topic}-${normalized.seq || summary}`;
  const dedupeKey = readString(normalized.data?.dedupeKey, normalized.data?.dedupe_key)
    || buildTimelineDedupeKey({ runtimeId, topic, runId: normalized.run_id, status, metadata, fallbackId: eventId });

  return {
    id: eventId,
    seq: normalized.seq || 0,
    runId: normalized.run_id,
    runtimeId,
    topic,
    kind,
    summary,
    actorLabel: normalized.actorLabel || getRuntimeRegistryEntry(runtimeId, options.locale).label,
    timestamp: parseTimelineTimestamp(normalized.ts || normalized.data?.timestamp),
    status,
    dedupeKey: dedupeKey || undefined,
    replacesEventId: typeof normalized.data?.replacesEventId === "string" ? normalized.data.replacesEventId : undefined,
    metadata,
  };
}
