import test from "node:test";
import assert from "node:assert/strict";
import {
  readTranscriptIdentity, isStaleTranscript, conversationEventDisposition,
  canReviseConversationMessage, createMessageRevisionDraft, normalizeSessionRuntimeEvent,
  applySnapshotToSessionRealtimeState, applyRuntimeEventToSessionRealtimeState, createInitialSessionRealtimeState,
  buildAuthoritativeSnapshotFingerprint,
  describeRecoveryDescendants,
} from "../dist/index.js";

const identity = { transcriptRevision: 9, contextEpoch: 2 };
test("destructive confirmation counts complete history, including unrendered descendants", () => {
  const messages = [{ id: "anchor", turnId: "t1" }, { id: "answer", turnId: "t1" }, { id: "later-u", turnId: "t2" }, { id: "later-a", turnId: "t2" }];
  assert.deepEqual(describeRecoveryDescendants({ messages }, "anchor"), { hasDescendants: true, laterTurnCount: 2 });
  assert.deepEqual(describeRecoveryDescendants({ messages }, "later-a"), { hasDescendants: false, laterTurnCount: 0 });
  assert.throws(() => describeRecoveryDescendants({ messages }, "deleted"), /conflict/);
});
test("epoch fence rejects delayed tools and events without an epoch after revision", () => {
  for (const topic of ["run.text.delta", "tool.finished", "approval.requested", "run.completed"]) {
    assert.equal(conversationEventDisposition(identity, { topic, payload: { contextEpoch: 1 } }), "ignore");
    assert.equal(conversationEventDisposition(identity, { topic, payload: {} }), "ignore");
    assert.equal(conversationEventDisposition(identity, { topic, payload: { contextEpoch: 2 } }), "apply");
    assert.equal(conversationEventDisposition(identity, { topic, payload: { contextEpoch: 3 } }), "refresh");
  }
});
test("revision duplicate is idempotent and a revision gap forces authoritative refresh", () => {
  const event = { topic: "message.revised", payload: { contextEpoch: 2, transcriptRevision: 9 } };
  assert.equal(conversationEventDisposition(identity, event), "ignore");
  event.payload.transcriptRevision = 12;
  assert.equal(conversationEventDisposition(identity, event), "refresh");
});
test("normalizer preserves revision identity and names both durable topics", () => {
  for (const [topic, name] of [["message.revised", "message_revised"], ["session.branch.created", "session_branch_created"]]) {
    const normalized = normalizeSessionRuntimeEvent({ topic, session_id: "source", seq: 13, payload: { ...identity, messageId: "m1" } });
    assert.ok(normalized);
    assert.equal(normalized.name, name);
    assert.equal(normalized.contextEpoch, 2);
    assert.equal(normalized.transcriptRevision, 9);
  }
});
test("stale snapshot cannot resurrect truncated tail even with a higher stream sequence", () => {
  const current = { sessionId: "s", ...identity, latestSeq: 20, messages: [{ id: "m", content: "new", version: 2 }] };
  const state = applySnapshotToSessionRealtimeState(createInitialSessionRealtimeState(), current);
  const rejected = applySnapshotToSessionRealtimeState(state, { ...current, transcriptRevision: 8, contextEpoch: 1, latestSeq: 100, messages: [{ id: "tail", content: "old" }] });
  assert.equal(rejected, state);
  const event = normalizeSessionRuntimeEvent({ topic: "tool.finished", session_id: "s", seq: 101, payload: { contextEpoch: 1 } });
  assert.equal(applyRuntimeEventToSessionRealtimeState(state, event), state);
});
test("revision identity refreshes snapshot even if MAX(message.version) does not change", () => {
  const before = { ...identity, messages: [{ id: "newest", content: "unchanged", version: 100 }] };
  assert.notEqual(buildAuthoritativeSnapshotFingerprint(before), buildAuthoritativeSnapshotFingerprint({ ...before, transcriptRevision: 10, contextEpoch: 3 }));
});
test("only terminal visible text is editable and drafts preserve exact Markdown and old CAS", () => {
  const message = { id: "m", role: "assistant", turnId: "t", version: 2, status: "failed", content: "1. 第一行\n\n   第二行 👩🏽‍💻\n" };
  assert.equal(canReviseConversationMessage(message, false), true);
  for (const status of ["running", "waiting_approval", "pending"]) assert.equal(canReviseConversationMessage({ ...message, status }, false), false);
  for (const role of ["system", "tool"]) assert.equal(canReviseConversationMessage({ ...message, role }, false), false);
  assert.equal(canReviseConversationMessage(message, true), false);
  const draft = createMessageRevisionDraft(message, 9);
  message.content = "concurrent edit"; message.version = 3;
  assert.equal(draft.content, "1. 第一行\n\n   第二行 👩🏽‍💻\n");
  assert.deepEqual([draft.expectedMessageVersion, draft.expectedTranscriptRevision], [2, 9]);
  assert.deepEqual(readTranscriptIdentity({ projection: identity }), identity);
  assert.equal(isStaleTranscript(identity, { transcriptRevision: 8, contextEpoch: 2 }), true);
});
