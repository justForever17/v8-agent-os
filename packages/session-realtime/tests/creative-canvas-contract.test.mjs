import assert from "node:assert/strict";
import test from "node:test";

import {
  CREATIVE_CANVAS_CONTRACT_START,
  classifyCreativeCanvasDispatch,
  findRuntimeEventTaxonomyEntryByTopic,
  isCreativeCanvasUserMessage,
  normalizeCreativeCanvasGraphRunStateEvent,
  parseCreativeCanvasExecutionContract,
  projectCreativeCanvasAuthorityScope,
  projectCreativeCanvasGraphRunHumanSurface,
  projectCreativeCanvasHumanSurfaceMessage,
} from "../dist/index.js";

const operationId = "canvas-operation-secret";
const contract = {
  schema: "v8.creative_canvas_task.v1",
  canvasOperationId: operationId,
  actionId: "creative_media.edit_image_region",
  resources: {
    sourceIds: ["source-private"],
    maskSourceId: "mask-private",
  },
  execution: {
    tool: "creative_media_jobs",
    arguments: {
      action: "create",
      request: {
        modality: "image",
        operationKind: "image.edit",
        canvasOperationId: operationId,
        sourceId: "source-private",
        maskSourceId: "mask-private",
      },
    },
  },
};

function envelope(value = contract) {
  return [
    "本消息来自画布",
    "[CANVAS EXECUTION CONTRACT v1]",
    JSON.stringify(value),
    "[/CANVAS EXECUTION CONTRACT]",
    "E:/private/workspace/input.png",
  ].join("\n");
}

function canvasAttachment(overrides = {}) {
  return {
    sourceId: "source-private",
    url: "/api/private/source-private",
    mimeType: "image/png",
    metadata: { canvasOperationId: operationId },
    ...overrides,
  };
}

test("canonical Canvas envelope parses only the typed schema with operation and action ids", () => {
  assert.deepEqual(parseCreativeCanvasExecutionContract(envelope()), contract);
  assert.equal(parseCreativeCanvasExecutionContract("[CANVAS EXECUTION CONTRACT v1]\n{}\n[/CANVAS EXECUTION CONTRACT]"), null);
  assert.equal(parseCreativeCanvasExecutionContract(envelope({ ...contract, schema: "spoofed" })), null);
  assert.equal(parseCreativeCanvasExecutionContract(`${envelope()}\n${envelope({ ...contract, canvasOperationId: "other" })}`), null);
  assert.equal(parseCreativeCanvasExecutionContract("ordinary chat"), null);
});

test("Canvas Human Surface is closed and never retains payloads, ids, paths, masks or attachments", () => {
  const message = {
    role: "user",
    content: envelope(),
    metadata: {
      contextMentions: [{ kind: "canvas_operation", id: operationId }],
      attachments: [canvasAttachment()],
      workspacePath: "E:/private/workspace",
    },
  };
  assert.equal(isCreativeCanvasUserMessage(message), true);
  const projection = projectCreativeCanvasHumanSurfaceMessage(message, "本消息来自画布");
  assert.deepEqual(projection, {
    kind: "canvas_message",
    text: "本消息来自画布",
    copyText: "本消息来自画布",
    hideAttachments: true,
    hideInternalMetadata: true,
  });
  const serialized = JSON.stringify(projection);
  for (const secret of [operationId, "source-private", "mask-private", "E:/private", "creative_media_jobs"]) {
    assert.equal(serialized.includes(secret), false);
  }
  assert.equal(projectCreativeCanvasHumanSurfaceMessage({ role: "assistant", content: envelope() }, "本消息来自画布"), null);
});

test("malformed Canvas markers stay masked without gaining direct-route privilege", () => {
  const content = `${CREATIVE_CANVAS_CONTRACT_START}\n{\"schema\":`;
  const projection = projectCreativeCanvasHumanSurfaceMessage({ role: "user", content }, "本消息来自画布");
  assert.equal(projection?.text, "本消息来自画布");
  assert.equal(classifyCreativeCanvasDispatch({ content, canvasSupervisorDirect: true }).kind, "invalid_canvas_direct");
});

test("Human Surface requires an explicit Canvas presentation signal and does not trust attachment lineage alone", () => {
  assert.equal(isCreativeCanvasUserMessage({
    role: "user",
    content: "请识别这张图片",
    metadata: { attachments: [canvasAttachment()] },
  }), false);
  assert.equal(isCreativeCanvasUserMessage({
    role: "user",
    content: "opaque historical message",
    metadata: {
      composerPresentation: {
        references: [{ kind: "canvas_resource", id: "hidden-resource", label: "resource" }],
      },
    },
  }), true);
});

test("typed Canvas dispatch reaches the direct route only when every operation and attachment lineage agrees", () => {
  const result = classifyCreativeCanvasDispatch({
    content: envelope(),
    data: {
      canvasSupervisorDirect: true,
      contextMentions: [{ kind: "canvas_operation", id: operationId }],
      attachments: [canvasAttachment()],
    },
  });
  assert.equal(result.kind, "canvas_supervisor_direct");
  assert.equal(result.privileged, true);
  assert.equal(result.routeKind, "creative_media");
  assert.equal(result.canvasOperationId, operationId);
});

test("ordinary chat attachments never opt into Canvas privilege", () => {
  const ordinary = classifyCreativeCanvasDispatch({
    content: "请识别这张图片",
    data: {
      supervisorRuntimeMode: "creative_media",
      canvasSupervisorDirect: "true",
      attachments: [{ sourceId: "normal-source", url: "/api/normal.png", mimeType: "image/png" }],
    },
  });
  assert.deepEqual(ordinary, { kind: "ordinary", privileged: false });
});

test("incomplete or mismatched Canvas privilege is invalid instead of silently becoming ordinary", () => {
  const cases = [
    {
      input: { content: "ordinary", data: { canvasSupervisorDirect: true } },
      reason: "invalid_execution_contract",
    },
    {
      input: {
        content: envelope({ ...contract, canvas_operation_id: "other" }),
        data: {
          canvasSupervisorDirect: true,
          contextMentions: [{ kind: "canvas_operation", id: operationId }],
          attachments: [canvasAttachment()],
        },
      },
      reason: "invalid_execution_contract",
    },
    {
      input: {
        content: envelope(),
        data: {
          canvasSupervisorDirect: true,
          contextMentions: [{ kind: "canvas_operation", id: operationId, canvasOperationId: "other" }],
          attachments: [canvasAttachment()],
        },
      },
      reason: "invalid_canvas_operation_mention",
    },
    {
      input: {
        content: envelope(),
        data: {
          canvasSupervisorDirect: true,
          contextMentions: [{ kind: "canvas_operation", id: operationId }],
          attachments: [canvasAttachment()],
        },
        metadata: {
          contextMentions: [{ kind: "canvas_operation", id: "other" }],
        },
      },
      reason: "invalid_canvas_operation_mention",
    },
    {
      input: {
        content: envelope(),
        data: {
          canvasSupervisorDirect: true,
          contextMentions: [{ kind: "canvas_operation", id: operationId }],
          attachments: [canvasAttachment({
            metadata: { canvasOperationId: operationId, canvas_operation_id: "other" },
          })],
        },
      },
      reason: "attachment_operation_id_mismatch",
    },
    {
      input: {
        content: envelope(),
        data: {
          canvasSupervisorDirect: true,
          contextMentions: [{ kind: "canvas_operation", id: operationId }],
          attachments: [canvasAttachment({ source_id: "other" })],
        },
      },
      reason: "attachment_source_id_conflict",
    },
    {
      input: {
        content: envelope(),
        data: {
          canvasSupervisorDirect: true,
          contextMentions: [{ kind: "canvas_operation", id: operationId }],
          attachments: [canvasAttachment(), canvasAttachment({ id: "extra", sourceId: "extra" })],
        },
      },
      reason: "unbound_source_id",
    },
    {
      input: {
        content: envelope({
          ...contract,
          resources: { sourceIds: ["source-private"], source_ids: ["other"] },
        }),
        data: {
          canvasSupervisorDirect: true,
          contextMentions: [{ kind: "canvas_operation", id: operationId }],
          attachments: [canvasAttachment()],
        },
      },
      reason: "invalid_source_id_contract",
    },
    {
      input: {
        content: envelope({
          ...contract,
          resources: { sourceIds: ["source-private", 42, { id: "hidden" }] },
        }),
        data: {
          canvasSupervisorDirect: true,
          contextMentions: [{ kind: "canvas_operation", id: operationId }],
          attachments: [canvasAttachment()],
        },
      },
      reason: "invalid_source_id_contract",
    },
    {
      input: {
        content: envelope(),
        data: {
          canvasSupervisorDirect: true,
          contextMentions: [{ kind: "canvas_operation", id: operationId }, "garbage"],
          attachments: [canvasAttachment()],
        },
      },
      reason: "invalid_canvas_operation_mention",
    },
    {
      input: {
        content: envelope(),
        data: {
          canvasSupervisorDirect: true,
          contextMentions: [{ kind: "canvas_operation", id: operationId }],
          attachments: [canvasAttachment(), "garbage"],
        },
      },
      reason: "invalid_canvas_attachment",
    },
    {
      input: {
        content: envelope({
          ...contract,
          execution: {
            ...contract.execution,
            arguments: {
              ...contract.execution.arguments,
              request: { ...contract.execution.arguments.request, sourceId: "other" },
            },
          },
        }),
        data: {
          canvasSupervisorDirect: true,
          contextMentions: [{ kind: "canvas_operation", id: operationId }],
          attachments: [canvasAttachment()],
        },
      },
      reason: "creative_media_source_binding_mismatch",
    },
    {
      input: {
        content: envelope(),
        data: { canvasSupervisorDirect: true, contextMentions: [{ kind: "canvas_operation", id: "other" }] },
      },
      reason: "operation_id_mismatch",
    },
    {
      input: {
        content: envelope(),
        data: {
          canvasSupervisorDirect: true,
          contextMentions: [{ kind: "canvas_operation", id: operationId }],
          attachments: [canvasAttachment({ metadata: { canvasOperationId: "other" } })],
        },
      },
      reason: "attachment_operation_id_mismatch",
    },
    {
      input: {
        content: envelope(),
        data: {
          canvasSupervisorDirect: true,
          contextMentions: [{ kind: "canvas_operation", id: operationId }],
          attachments: [],
        },
      },
      reason: "unbound_source_id",
    },
  ];
  for (const item of cases) {
    const result = classifyCreativeCanvasDispatch(item.input);
    assert.equal(result.kind, "invalid_canvas_direct");
    assert.equal(result.privileged, false);
    assert.equal(result.reason, item.reason);
  }
});

test("Canvas authority projection accepts only exact session and workspace lineage for every record kind", () => {
  const scoped = (value) => ({
    ...value,
    lineage: { sessionId: "session-a", workspaceId: "workspace-a" },
  });
  const artifact = scoped({
    artifactId: "artifact-a",
    externalUrl: "https://provider.example/result.png",
    canvasOperationId: operationId,
  });
  const projection = projectCreativeCanvasAuthorityScope({
    scope: { sessionId: "session-a", workspaceId: "workspace-a" },
    records: {
      source: [scoped({ sourceId: "source-a" }), { sourceId: "source-b", sessionId: "session-b", workspaceId: "workspace-a" }],
      artifact: [artifact, { artifactId: "artifact-missing-scope" }],
      graph: [scoped({ graphId: "graph-a" }), scoped({ graphId: "graph-b", workspaceId: "workspace-b" })],
      run: [scoped({ graphRunId: "graph-run-a", graphId: "graph-a", canvasOperationId: operationId })],
      operation: [scoped({ canvasOperationId: operationId, runId: "chat-run-a" })],
    },
  });

  assert.deepEqual(
    projection.accepted.map((item) => `${item.kind}:${item.id}`),
    [
      "source:source-a",
      "artifact:artifact-a",
      "graph:graph-a",
      "run:graph-run-a",
      `operation:${operationId}`,
    ],
  );
  assert.equal(projection.accepted.find((item) => item.id === "artifact-a")?.value.externalUrl, artifact.externalUrl);
  assert.deepEqual(
    projection.rejected.map((item) => `${item.kind}:${item.reason}`),
    ["source:session_mismatch", "artifact:missing_session_id", "graph:conflicting_authority"],
  );
});

test("Canvas authority rejects recursive field stitching and conflicting lineage declarations", () => {
  const projection = projectCreativeCanvasAuthorityScope({
    scope: { sessionId: "session-a", workspaceId: "workspace-a" },
    records: {
      source: [{
        sourceId: "source-stitched",
        data: { sessionId: "session-a" },
        request: { workspaceId: "workspace-a" },
      }, {
        sourceId: "source-alias-conflict",
        sessionId: "session-a",
        session_id: "session-b",
        workspaceId: "workspace-a",
        workspace_id: "workspace-b",
      }],
      artifact: [{
        artifactId: "artifact-conflict",
        sessionId: "session-a",
        workspaceId: "workspace-a",
        lineage: { sessionId: "session-b", workspaceId: "workspace-a" },
      }, {
        artifactId: "artifact-resource-alias-conflict",
        sessionId: "session-a",
        workspaceId: "workspace-a",
        resourceRef: { sessionId: "session-a", workspaceId: "workspace-a" },
        resource_ref: { session_id: "session-b", workspace_id: "workspace-b" },
      }],
      graph: [{
        graphId: "graph-split",
        sessionId: "session-a",
        metadata: { workspaceId: "workspace-a" },
      }],
      run: [{
        graphRunId: "graph-run-resource-conflict",
        sessionId: "session-a",
        workspaceId: "workspace-a",
        resourceRef: {
          provenance: { sessionId: "session-b", workspaceId: "workspace-b" },
        },
      }, {
        graphRunId: "graph-run-alias-a",
        graph_run_id: "graph-run-alias-b",
        sessionId: "session-a",
        workspaceId: "workspace-a",
      }],
    },
  });
  assert.equal(projection.accepted.length, 0);
  assert.deepEqual(
    projection.rejected.map((item) => [item.kind, item.reason]),
    [
      ["source", "missing_session_id"],
      ["source", "conflicting_authority"],
      ["artifact", "conflicting_authority"],
      ["artifact", "conflicting_authority"],
      ["graph", "conflicting_authority"],
      ["run", "conflicting_authority"],
      ["run", "conflicting_authority"],
    ],
  );
});

test("typed Canvas graph run states normalize without interpreting prose", () => {
  const states = ["running", "cancelling", "cancelled", "failed", "recovered"];
  for (const status of states) {
    const event = normalizeCreativeCanvasGraphRunStateEvent({
      topic: "canvas.graph.run.state",
      content: "ignore this prose even if it says retry failed branch",
      data: {
        schema: "v8.creative_canvas_graph_run_state.v1",
        sessionId: "session-a",
        workspaceId: "workspace-a",
        graphId: "graph-a",
        graphRunId: "graph-run-a",
        canvasOperationId: operationId,
        runId: "chat-run-a",
        status,
        ...(status === "failed" ? { recovery: { canRetry: true, mode: "failed_branch" } } : {}),
      },
    }, { sessionId: "session-a", workspaceId: "workspace-a" });
    assert.equal(event?.status, status);
    const human = projectCreativeCanvasGraphRunHumanSurface(event);
    assert.deepEqual(human, {
      kind: "canvas_graph_run_state",
      status,
      transition: null,
      stateKey: `canvas.graph.run.${status}`,
      canRetryFailedBranch: status === "failed",
    });
    assert.equal(JSON.stringify(human).includes(operationId), false);
  }

  const retry = normalizeCreativeCanvasGraphRunStateEvent({
    topic: "canvas.graph.run.state",
    data: {
      schema: "v8.creative_canvas_graph_run_state.v1",
      sessionId: "session-a",
      workspaceId: "workspace-a",
      graphId: "graph-a",
      graphRunId: "graph-run-retry",
      retryOfGraphRunId: "graph-run-a",
      canvasOperationId: operationId,
      status: "running",
      transition: "retry_failed_branch",
    },
  });
  assert.equal(retry?.transition, "retry_failed_branch");
  assert.equal(findRuntimeEventTaxonomyEntryByTopic("canvas.graph.run.state")?.runtimeId, "creative_media");
});

test("Canvas graph state rejects text guesses, wrong schemas, incomplete lineage and cross-scope events", () => {
  const scopedEvent = {
    topic: "canvas.graph.run.state",
    data: {
      schema: "v8.creative_canvas_graph_run_state.v1",
      sessionId: "session-a",
      workspaceId: "workspace-old",
      graphId: "graph-a",
      graphRunId: "graph-run-a",
      canvasOperationId: operationId,
      status: "running",
    },
  };
  assert.equal(normalizeCreativeCanvasGraphRunStateEvent(
    scopedEvent,
    { sessionId: "session-a", workspaceId: "" },
  ), null);
  assert.equal(normalizeCreativeCanvasGraphRunStateEvent(
    scopedEvent,
    { sessionId: "session-a", workspaceId: "workspace-current" },
  ), null);
  assert.equal(normalizeCreativeCanvasGraphRunStateEvent({
    topic: "creative_media.progress",
    content: "Canvas graph failed",
    data: { status: "failed" },
  }), null);
  assert.equal(normalizeCreativeCanvasGraphRunStateEvent({
    topic: "canvas.graph.run.state",
    data: {
      schema: "v8.creative_canvas_graph_run_state.v1",
      sessionId: "session-a",
      session_id: "session-b",
      workspaceId: "workspace-a",
      workspace_id: "workspace-b",
      graphId: "graph-a",
      graphRunId: "graph-run-a",
      canvasOperationId: operationId,
      status: "running",
    },
  }, { sessionId: "session-a", workspaceId: "workspace-a" }), null);
  assert.equal(normalizeCreativeCanvasGraphRunStateEvent({
    topic: "canvas.graph.run.state",
    data: {
      schema: "v8.creative_canvas_graph_run_state.v1",
      sessionId: "session-a",
      workspaceId: "workspace-a",
      graphId: "graph-a",
      graph_id: "graph-b",
      graphRunId: "graph-run-a",
      canvasOperationId: operationId,
      status: "running",
    },
  }), null);
  assert.equal(normalizeCreativeCanvasGraphRunStateEvent({
    topic: "canvas.graph.run.state",
    data: {
      schema: "v8.creative_canvas_graph_run_state.v1",
      sessionId: "session-a",
      workspaceId: "workspace-a",
      graphId: "graph-a",
      graphRunId: "graph-run-retry",
      retryOfGraphRunId: "graph-run-a",
      retry_of_graph_run_id: "graph-run-b",
      canvasOperationId: operationId,
      status: "running",
      transition: "retry_failed_branch",
    },
  }), null);
  assert.equal(normalizeCreativeCanvasGraphRunStateEvent({
    topic: "canvas.graph.run.state",
    data: {
      schema: "v8.creative_canvas_graph_run_state.v1",
      sessionId: "session-a",
      graphId: "graph-a",
      graphRunId: "graph-run-a",
      canvasOperationId: operationId,
      status: "failed",
    },
  }), null);
  assert.equal(normalizeCreativeCanvasGraphRunStateEvent({
    topic: "canvas.graph.run.state",
    data: {
      schema: "v8.creative_canvas_graph_run_state.v1",
      sessionId: "session-b",
      workspaceId: "workspace-a",
      graphId: "graph-a",
      graphRunId: "graph-run-a",
      canvasOperationId: operationId,
      status: "failed",
    },
  }, { sessionId: "session-a", workspaceId: "workspace-a" }), null);
  assert.equal(normalizeCreativeCanvasGraphRunStateEvent({
    topic: "canvas.graph.run.state",
    data: {
      schema: "v8.creative_canvas_graph_run_state.v1",
      sessionId: "session-a",
      workspaceId: "workspace-a",
      graphId: "graph-a",
      graphRunId: "graph-run-retry",
      canvasOperationId: operationId,
      status: "running",
      transition: "retry_failed_branch",
    },
  }), null);
});
