/** Conversation mutations are commands against canonical versions, never local history writes. */
export type TranscriptIdentity = { transcriptRevision: number; contextEpoch: number };
export type ConversationMutationResult = TranscriptIdentity & {
  sessionId: string; newSessionId?: string; sourceSessionId?: string; messageId?: string;
};
export type MessageRevisionDraft = {
  messageId: string; content: string; expectedMessageVersion: number; expectedTranscriptRevision: number;
};
export type RecoveryMessage = {
  id: string; role: string; content: string; turnId?: string; turnPosition?: number; ordinal?: number;
  version?: number; status?: string; state?: string; editedBy?: string; metadata?: Record<string, unknown>;
};
function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? value as Record<string, unknown> : {};
}
export function readTranscriptIdentity(value: unknown): TranscriptIdentity {
  const root = record(value), snapshot = record(root.snapshot), projection = record(root.projection);
  return {
    transcriptRevision: Number(root.transcriptRevision ?? projection.transcriptRevision ?? snapshot.transcriptRevision ?? 0),
    contextEpoch: Number(root.contextEpoch ?? projection.contextEpoch ?? snapshot.contextEpoch ?? 0),
  };
}
export function isStaleTranscript(current: TranscriptIdentity, incoming: TranscriptIdentity): boolean {
  return incoming.contextEpoch < current.contextEpoch || incoming.transcriptRevision < current.transcriptRevision;
}
export function conversationEventDisposition(current: TranscriptIdentity, value: unknown): "apply" | "ignore" | "refresh" {
  const event = record(value), data = record(event.data ?? event.payload);
  const topic = String(event.topic ?? data.topic ?? event.name ?? "");
  const epoch = event.contextEpoch ?? event.context_epoch ?? data.contextEpoch ?? data.context_epoch;
  const revision = Number(event.transcriptRevision ?? data.transcriptRevision ?? 0);
  if (current.contextEpoch > 0 && epoch == null) return "ignore";
  if (epoch != null && Number(epoch) < current.contextEpoch) return "ignore";
  if (topic === "session.branch.created" || topic === "session_branch_created") return "refresh";
  if (topic === "message.revised" || topic === "message_revised") {
    return revision <= current.transcriptRevision ? "ignore" : "refresh";
  }
  if (epoch != null && Number(epoch) > current.contextEpoch) return "refresh";
  return "apply";
}
export function messageRevisionVersion(message: RecoveryMessage): number {
  return Number(message.version ?? message.metadata?.transcriptVersion ?? 0);
}
export function canReviseConversationMessage(message: RecoveryMessage, busy: boolean): boolean {
  const state = String(message.status ?? message.state ?? message.metadata?.status ?? message.metadata?.state ?? "");
  return !busy && (message.role === "user" || message.role === "assistant")
    && Boolean(message.turnId) && messageRevisionVersion(message) > 0
    && ["completed", "failed", "cancelled", "canceled", "interrupted"].includes(state);
}
export function createMessageRevisionDraft(message: RecoveryMessage, transcriptRevision: number): MessageRevisionDraft {
  return { messageId: message.id, content: message.content, expectedMessageVersion: messageRevisionVersion(message), expectedTranscriptRevision: transcriptRevision };
}
export function conversationMutationError(status: number): "conflict" | "offline" | "failed" {
  return status === 409 ? "conflict" : status === 0 || status >= 502 ? "offline" : "failed";
}
/** Read the full authoritative history, not a viewport/around window, for destructive copy. */
export function describeRecoveryDescendants(value: unknown, messageId: string): { hasDescendants: boolean; laterTurnCount: number } {
  const root = record(value);
  const messages = (Array.isArray(root.messages) ? root.messages : Array.isArray(root.timeline) ? root.timeline : []) as RecoveryMessage[];
  const index = messages.findIndex((message) => message.id === messageId);
  if (index < 0) throw new Error("conflict");
  const descendants = messages.slice(index + 1);
  return { hasDescendants: descendants.length > 0, laterTurnCount: new Set(descendants.map((message) => message.turnId || message.id)).size };
}
