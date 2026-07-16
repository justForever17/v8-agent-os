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
