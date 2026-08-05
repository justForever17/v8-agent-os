import assert from "node:assert/strict";
import test from "node:test";

import { buildSessionSourceProjection, coerceAuthoritativeSessionSnapshot } from "../dist/index.js";

test("session sources combine the durable ledger with legacy user attachments without treating assistant artifacts as sources", () => {
  const projection = buildSessionSourceProjection([
    {
      id: "user-1",
      role: "user",
      metadata: {
        attachments: [{
          id: "legacy-audio",
          url: "/api/file?path=voice%2Dinput%2Emp3",
          name: "voice-input.mp3",
          source: "os_web_voice_upload",
        }],
      },
    },
    {
      id: "assistant-1",
      role: "assistant",
      metadata: { attachments: [{ id: "must-not-leak", url: "/generated/output.png" }] },
    },
  ], [{
    sourceId: "src-image",
    sessionId: "session-a",
    messageId: "user-2",
    sourceKind: "phone_upload",
    title: "reference.png",
    mimeType: "image/png",
    externalUrl: "/api/source/reference.png",
  }]);

  assert.deepEqual(projection.map((item) => item.id), ["src-image", "legacy-audio"]);
  assert.equal(projection[0].mediaKind, "image");
  assert.equal(projection[1].mediaKind, "audio");
  assert.equal(projection[1].messageId, "user-1");
});

test("authoritative snapshots preserve durable session sources", () => {
  const snapshot = coerceAuthoritativeSessionSnapshot({
    sessionId: "session-a",
    sources: [{ sourceId: "src-1", title: "voice.mp3", mimeType: "audio/mpeg" }],
  });
  assert.equal(snapshot?.sources?.[0]?.sourceId, "src-1");
});

test("durable sources and message attachments for the same workspace resource collapse without using filename guesses", () => {
  const projection = buildSessionSourceProjection([{
    id: "user-1",
    role: "user",
    metadata: {
      attachments: [{
        id: "client-upload-1",
        name: "reference.png",
        workspacePath: "E:\\workspace\\uploads\\reference.png",
        workspaceRelativePath: "uploads/reference.png",
        url: "/api/client/workspace/resource?workspace_id=w1&workspace_relative_path=uploads%2Freference.png",
      }],
    },
  }], [{
    sourceId: "src-ledger-1",
    title: "reference.png",
    mimeType: "image/png",
    workspacePath: "uploads/reference.png",
    resourceRef: { workspaceRelativePath: "uploads/reference.png" },
  }]);

  assert.equal(projection.length, 1);
  assert.equal(projection[0].id, "src-ledger-1");
  assert.equal(projection[0].messageId, "user-1");
  assert.equal(projection[0].workspaceRelativePath, "uploads/reference.png");
});

test("same-name sources at different paths remain distinct", () => {
  const projection = buildSessionSourceProjection([], [
    { sourceId: "src-a", title: "reference.png", workspacePath: "a/reference.png" },
    { sourceId: "src-b", title: "reference.png", workspacePath: "b/reference.png" },
  ]);
  assert.equal(projection.length, 2);
});

test("case-distinct relative paths and external origins are not collapsed", () => {
  const projection = buildSessionSourceProjection([], [
    { sourceId: "src-a", title: "Demo.ts", workspacePath: "src/Demo.ts" },
    { sourceId: "src-b", title: "demo.ts", workspacePath: "src/demo.ts" },
    { sourceId: "src-c", title: "asset.png", externalUrl: "https://a.example/assets/asset.png" },
    { sourceId: "src-d", title: "asset.png", externalUrl: "https://b.example/assets/asset.png" },
  ]);
  assert.equal(projection.length, 4);
});

test("durable source projection fails closed on missing, cross-session, and cross-workspace authority", () => {
  const projection = buildSessionSourceProjection([{
    id: "user-current",
    role: "user",
    metadata: {
      attachments: [{
        id: "current-message-source",
        name: "current.png",
        workspaceRelativePath: "uploads/current.png",
      }],
    },
  }], [
    { sourceId: "source-current", sessionId: "session-a", workspaceId: "workspace-a", title: "current.wav" },
    { sourceId: "source-other-session", sessionId: "session-b", workspaceId: "workspace-a", title: "other.wav" },
    { sourceId: "source-other-workspace", sessionId: "session-a", workspaceId: "workspace-b", title: "other.png" },
    { sourceId: "source-missing-authority", title: "unknown.json" },
  ], {
    sessionId: "session-a",
    workspaceId: "workspace-a",
  });

  assert.deepEqual(
    projection.map((item) => item.id),
    ["source-current", "current-message-source"],
  );
});

test("source projection rejects conflicting lineage and explicitly cross-scoped message attachments", () => {
  const projection = buildSessionSourceProjection([{
    id: "user-current",
    role: "user",
    metadata: {
      attachments: [
        { id: "inherited-current", name: "current.png" },
        { id: "other-session", sessionId: "session-b", workspaceId: "workspace-a", name: "other.png" },
        {
          id: "conflicting-attachment",
          sessionId: "session-a",
          workspaceId: "workspace-a",
          lineage: { sessionId: "session-b", workspaceId: "workspace-a" },
          name: "conflict.png",
        },
        {
          id: "conflicting-provenance",
          sessionId: "session-a",
          workspaceId: "workspace-a",
          provenance: { sessionId: "session-b", workspaceId: "workspace-b" },
          name: "provenance-conflict.png",
        },
        {
          id: "conflicting-aliases",
          sessionId: "session-a",
          session_id: "session-b",
          workspaceId: "workspace-a",
          workspace_id: "workspace-b",
          name: "alias-conflict.png",
        },
      ],
    },
  }, {
    id: "other-message",
    role: "user",
    sessionId: "session-b",
    workspaceId: "workspace-a",
    metadata: { attachments: [{ id: "hidden-with-parent", name: "hidden.png" }] },
  }], [{
    sourceId: "canonical-current",
    source_id: "canonical-current",
    id: "local-row-current",
    sessionId: "session-a",
    workspaceId: "workspace-a",
    title: "canonical-current.wav",
  }, {
    sourceId: "conflicting-durable",
    sessionId: "session-a",
    workspaceId: "workspace-a",
    lineage: { sessionId: "session-b", workspaceId: "workspace-a" },
    title: "conflict.wav",
  }, {
    sourceId: "conflicting-durable-provenance",
    sessionId: "session-a",
    workspaceId: "workspace-a",
    metadata: { provenance: { sessionId: "session-b", workspaceId: "workspace-b" } },
    title: "provenance-conflict.wav",
  }, {
    sourceId: "conflicting-resource-alias",
    sessionId: "session-a",
    workspaceId: "workspace-a",
    resourceRef: { sessionId: "session-a", workspaceId: "workspace-a" },
    resource_ref: { session_id: "session-b", workspace_id: "workspace-b" },
    title: "resource-alias-conflict.wav",
  }, {
    sourceId: "conflicting-source-id-a",
    source_id: "conflicting-source-id-b",
    sessionId: "session-a",
    workspaceId: "workspace-a",
    title: "source-id-conflict.wav",
  }, {
    sourceId: "conflicting-nested-source-id",
    sessionId: "session-a",
    workspaceId: "workspace-a",
    metadata: { source_id: "different-nested-source-id" },
    title: "nested-source-id-conflict.wav",
  }], {
    sessionId: "session-a",
    workspaceId: "workspace-a",
  });

  assert.deepEqual(projection.map((item) => item.id), ["canonical-current", "inherited-current"]);
});
