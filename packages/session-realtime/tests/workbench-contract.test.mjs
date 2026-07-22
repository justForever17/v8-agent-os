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

test("first-party UI actions remain typed and never become arbitrary MCP HTML", () => {
  const event = normalizeSessionRuntimeEvent({
    kind: "event",
    topic: "tool.finished",
    session_id: "session-1",
    seq: 9,
    payload: {
      tool: {
        toolCallId: "call-1",
        toolName: "config_broker",
        mcpApp: {
          appInstanceId: "ui_action_1",
          serverName: "v8os-action",
          resourceUri: "ui://v8os/actions/ui_action_1",
          renderer: "v8_action",
          actionRequest: {
            actionRequestId: "ui_action_1",
            sessionId: "session-1",
            kind: "secret_input",
            state: "pending",
            title: "Connect provider",
            targetLabel: "https://api.example.test/v1",
            fields: [{ id: "apiKey", kind: "secret", label: "API Key", required: true }],
            expiresAt: "2026-07-22T12:00:00Z",
          },
        },
      },
    },
  });

  assert.equal(event?.tool?.mcpApp?.renderer, "v8_action");
  assert.equal(event?.tool?.mcpApp?.actionRequest?.kind, "secret_input");
  assert.equal(event?.tool?.mcpApp?.actionRequest?.fields?.[0]?.id, "apiKey");
  assert.equal(event?.tool?.mcpApp?.actionRequest?.fields?.[0]?.kind, "secret");
});

test("unknown UI action kinds and field kinds are not projected", () => {
  const event = normalizeSessionRuntimeEvent({
    kind: "event",
    topic: "tool.finished",
    session_id: "session-1",
    seq: 10,
    payload: {
      tool: {
        toolCallId: "call-2",
        toolName: "config_broker",
        mcpApp: {
          appInstanceId: "ui_action_2",
          resourceUri: "ui://v8os/actions/ui_action_2",
          renderer: "v8_action",
          actionRequest: {
            actionRequestId: "ui_action_2",
            sessionId: "session-1",
            kind: "arbitrary_html",
            state: "pending",
            title: "Untrusted action",
            fields: [{ id: "payload", kind: "html", label: "Payload", required: true }],
            expiresAt: "2026-07-22T12:00:00Z",
          },
        },
      },
    },
  });

  assert.equal(event?.tool?.mcpApp?.actionRequest, undefined);
});
