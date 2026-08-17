import type { NormalizedSessionRuntimeEvent } from "./contract.js";

export type SessionRuntimeEventSequenceInput = Pick<
  NormalizedSessionRuntimeEvent,
  "event_id" | "seq" | "type" | "name" | "topic" | "run_id" | "message_id" | "node_id" | "content" | "data"
>;

export type SessionRuntimeEventAcceptance = {
  accept: boolean;
  identity: string;
  reason?: "duplicate" | "covered_by_snapshot";
  gap?: SessionRuntimeEventGap;
};

export type SessionRuntimeEventGap = {
  expectedSeq: number;
  observedSeq: number;
  missingFromSeq: number;
  missingToSeq: number;
};

export type SessionRuntimeEventContinuity = {
  seq: number;
  contiguousSeq: number;
  highestObservedSeq: number;
  gap?: SessionRuntimeEventGap;
};

export type SessionRuntimeEventObservationReason =
  | "pending_duplicate"
  | "snapshot_covered"
  | "contiguous_duplicate";

export type SessionRuntimeEventObservation = SessionRuntimeEventContinuity & {
  acceptEvent: boolean;
  observationReason?: SessionRuntimeEventObservationReason;
};

function compactText(value: unknown, limit = 160) {
  return String(value || "").trim().replace(/\s+/g, " ").slice(0, limit);
}

function recordOf(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function positiveSequence(value: unknown) {
  const seq = Number(value || 0) || 0;
  return Number.isSafeInteger(seq) && seq > 0 ? seq : 0;
}

export function detectSessionRuntimeEventGap(
  observedSeq: unknown,
  contiguousSeq: unknown,
): SessionRuntimeEventGap | undefined {
  const observed = positiveSequence(observedSeq);
  const contiguous = Math.max(0, Number(contiguousSeq || 0) || 0);
  if (!observed || observed <= contiguous + 1) return undefined;
  return {
    expectedSeq: contiguous + 1,
    observedSeq: observed,
    missingFromSeq: contiguous + 1,
    missingToSeq: observed - 1,
  };
}

/**
 * Tracks the largest fully covered durable prefix while retaining later
 * observations. Callers may render out-of-order events immediately; polling
 * must resume after contiguousSeq so a fast fanout event cannot hide a gap.
 */
export class SessionRuntimeEventContiguousCursor {
  private contiguous: number;
  private highest: number;
  private snapshotCovered: number;
  private readonly pending = new Set<number>();

  constructor(initialCoveredSeq = 0) {
    this.contiguous = Math.max(0, positiveSequence(initialCoveredSeq));
    this.highest = this.contiguous;
    this.snapshotCovered = this.contiguous;
  }

  get contiguousSeq() {
    return this.contiguous;
  }

  get highestObservedSeq() {
    return this.highest;
  }

  coverThrough(coveredSeq: unknown): SessionRuntimeEventContinuity {
    const seq = positiveSequence(coveredSeq);
    this.snapshotCovered = Math.max(this.snapshotCovered, seq);
    if (seq > this.contiguous) {
      this.contiguous = seq;
      for (const pendingSeq of this.pending) {
        if (pendingSeq <= seq) this.pending.delete(pendingSeq);
      }
    }
    this.highest = Math.max(this.highest, seq);
    this.advanceContiguousPrefix();
    return this.snapshot(seq);
  }

  observe(observedSeq: unknown): SessionRuntimeEventObservation {
    const seq = positiveSequence(observedSeq);
    let acceptEvent = true;
    let observationReason: SessionRuntimeEventObservationReason | undefined;
    if (seq > 0 && seq <= this.snapshotCovered) {
      acceptEvent = false;
      observationReason = "snapshot_covered";
    } else if (seq > 0 && this.pending.has(seq)) {
      acceptEvent = false;
      observationReason = "pending_duplicate";
    } else if (seq > 0 && seq <= this.contiguous) {
      acceptEvent = false;
      observationReason = "contiguous_duplicate";
    } else if (seq > this.contiguous) {
      this.pending.add(seq);
    }
    this.highest = Math.max(this.highest, seq);
    this.advanceContiguousPrefix();
    return {
      ...this.snapshot(seq),
      acceptEvent,
      ...(observationReason ? { observationReason } : {}),
    };
  }

  private advanceContiguousPrefix() {
    while (this.pending.delete(this.contiguous + 1)) {
      this.contiguous += 1;
    }
  }

  private snapshot(seq: number): SessionRuntimeEventContinuity {
    const gap = this.highest > this.contiguous
      ? detectSessionRuntimeEventGap(this.highest, this.contiguous)
      : undefined;
    return {
      seq,
      contiguousSeq: this.contiguous,
      highestObservedSeq: this.highest,
      ...(gap ? { gap } : {}),
    };
  }
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
  options: {
    snapshotCoveredSeq?: number;
    contiguousSeq?: number;
    seenEventIdentities?: ReadonlySet<string>;
  } = {},
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
  const gap = options.contiguousSeq === undefined
    ? undefined
    : detectSessionRuntimeEventGap(seq, options.contiguousSeq);
  return { accept: true, identity, ...(gap ? { gap } : {}) };
}
