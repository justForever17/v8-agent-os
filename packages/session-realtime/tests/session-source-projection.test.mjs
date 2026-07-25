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
