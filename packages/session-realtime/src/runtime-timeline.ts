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
  return Date.now();
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

    entries.push({
      id,
      seq,
      runId: typeof record.runId === "string" ? record.runId : undefined,
      runtimeId,
      topic,
      kind,
      summary,
      actorLabel: typeof record.actorLabel === "string" ? record.actorLabel : undefined,
      timestamp: parseTimelineTimestamp(record.timestamp),
      status: typeof record.status === "string" ? record.status : undefined,
      metadata: record.metadata && typeof record.metadata === "object"
        ? record.metadata as Record<string, unknown>
        : undefined,
    });
  }

  entries.sort((left, right) => right.timestamp - left.timestamp);
  return entries;
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
    normalized.name === "artifact_recorded"
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

  return {
    id: normalized.event_id || `timeline-${topic}-${normalized.seq || summary}`,
    seq: normalized.seq || 0,
    runId: normalized.run_id,
    runtimeId,
    topic,
    kind,
    summary,
    actorLabel: normalized.actorLabel || getRuntimeRegistryEntry(runtimeId, options.locale).label,
    timestamp: parseTimelineTimestamp(normalized.ts || normalized.data?.timestamp),
    status: normalized.status || (typeof normalized.data?.status === "string" ? normalized.data.status : undefined),
    metadata: normalized.data,
  };
}
