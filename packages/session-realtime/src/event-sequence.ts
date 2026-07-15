import type { NormalizedSessionRuntimeEvent } from "./contract.js";

export type SessionRuntimeEventSequenceInput = Pick<
  NormalizedSessionRuntimeEvent,
  "event_id" | "seq" | "type" | "name" | "topic" | "run_id" | "message_id" | "node_id" | "content" | "data"
>;

export type SessionRuntimeEventAcceptance = {
  accept: boolean;
  identity: string;
  reason?: "duplicate" | "covered_by_snapshot";
};

function compactText(value: unknown, limit = 160) {
  return String(value || "").trim().replace(/\s+/g, " ").slice(0, limit);
}

function recordOf(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

/**
 * Builds the same stable event identity for Web and Phone. Runtime event ids are
 * preferred; the deterministic fallback is only for old envelopes that predate
 * event_id. Wall-clock time and random values are intentionally excluded.
 */
export function buildSessionRuntimeEventIdentity(event: SessionRuntimeEventSequenceInput) {
  const eventId = compactText(event.event_id, 240);
  if (eventId) return `event:${eventId}`;

  const data = recordOf(event.data);
  const tool = recordOf(data.tool);
  const fingerprint = compactText(
    event.content
      || data.label
      || data.summary
      || data.topic
      || tool.toolInvocationId
      || tool.toolCallId
      || data.toolInvocationId
      || data.toolCallId
      || data.tool_call_id,
  );
  return [
    "seq",
    Number(event.seq || 0) || 0,
    compactText(event.type),
    compactText(event.name),
    compactText(event.topic),
    compactText(event.run_id),
    compactText(event.message_id),
    compactText(event.node_id),
    fingerprint,
  ].join("¦");
}

/**
 * A snapshot sequence is a coverage watermark, not the live high-water mark.
 * Events above the snapshot watermark may arrive out of order and must be
 * accepted once. Events at or below it are already represented by the
 * authoritative snapshot and are ignored after reconnect.
 */
export function evaluateSessionRuntimeEvent(
  event: SessionRuntimeEventSequenceInput,
  options: { snapshotCoveredSeq?: number; seenEventIdentities?: ReadonlySet<string> } = {},
): SessionRuntimeEventAcceptance {
  const identity = buildSessionRuntimeEventIdentity(event);
  if (options.seenEventIdentities?.has(identity)) {
    return { accept: false, identity, reason: "duplicate" };
  }
  const seq = Number(event.seq || 0) || 0;
  const snapshotCoveredSeq = Number(options.snapshotCoveredSeq || 0) || 0;
  if (seq > 0 && snapshotCoveredSeq > 0 && seq <= snapshotCoveredSeq) {
    return { accept: false, identity, reason: "covered_by_snapshot" };
  }
  return { accept: true, identity };
}
