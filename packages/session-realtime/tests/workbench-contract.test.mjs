import assert from "node:assert/strict";
import test from "node:test";

import {
  findRuntimeEventTaxonomyEntryByTopic,
  normalizeSessionRuntimeEvent,
} from "../dist/index.js";

test("workbench document events stay session-scoped and off the message timeline", () => {
  const taxonomy = findRuntimeEventTaxonomyEntryByTopic("workbench.document.opened");
  assert.equal(taxonomy?.scope, "session");
  assert.equal(taxonomy?.visibility, "hidden");
  assert.deepEqual(taxonomy?.targets, ["workbench"]);

  const event = normalizeSessionRuntimeEvent({
    kind: "event",
    topic: "workbench.document.opened",
    session_id: "session-1",
    seq: 8,
    payload: {
      document: {
        kind: "workspace_file",
        documentId: "file:README.md",
        title: "README.md",
        renderer: "markdown",
        lifecycle: "session",
        status: "available",
        capabilities: ["read", "search", "copy"],
        subjectRef: { sessionId: "session-1", workspacePath: "README.md" },
      },
    },
  });

  assert.equal(event?.name, "workbench_document_opened");
  assert.equal(event?.scope, "session");
  assert.equal(event?.visibility, "hidden");
  assert.deepEqual(event?.targets, ["workbench"]);
  assert.equal(event?.data?.document?.documentId, "file:README.md");
});
