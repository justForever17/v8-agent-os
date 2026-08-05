import assert from "node:assert/strict";
import test from "node:test";

import { buildSessionOutputProjection } from "../dist/index.js";

test("session outputs stay session-bound and exclude uploads, adopted files, directories, and speculative deliverables", () => {
  const messages = [{
    id: "assistant-1",
    role: "assistant",
    nodes: [
      {
        id: "spec-result",
        kind: "execution",
        executionType: "tool_result",
        toolName: "spec_broker",
        result: {
          ok: true,
          specBrief: {
            specId: "spec-1",
            linkedSections: [
              { stage: "requirements", relativePath: ".v8/specs/demo/requirements.md" },
              { stage: "design", relativePath: ".v8/specs/demo/design.md" },
            ],
            targetOutputDirectories: ["out/not-an-artifact"],
            explicitDeliverableFiles: ["future.html"],
            pipelineControl: { approvedStages: ["requirements"], blockedByApproval: "design" },
          },
        },
      },
      {
        id: "write-call",
        kind: "execution",
        executionType: "tool_call",
        toolName: "write_native_file",
        toolCallId: "call-1",
        args: { path: "src/result.ts" },
      },
      {
        id: "write-result",
        kind: "execution",
        executionType: "tool_result",
        toolName: "write_native_file",
        toolCallId: "call-1",
        result: "Successfully Created/Overwritten file: src/result.ts",
      },
    ],
  }];
  const artifacts = [
    { id: "current", sessionId: "session-a", workspaceId: "workspace-a", sourcePath: "media/final.png", origin: "provider_result" },
    { id: "other-session", sessionId: "session-b", workspaceId: "workspace-a", sourcePath: "other/leak.png", origin: "provider_result" },
    { id: "other-workspace", sessionId: "session-a", workspaceId: "workspace-b", sourcePath: "other/workspace-leak.png", origin: "provider_result" },
    { id: "missing-session", sourcePath: "other/unbound.png", origin: "provider_result" },
    { id: "missing-workspace", sessionId: "session-a", sourcePath: "other/unbound-workspace.png", origin: "provider_result" },
    { id: "upload", sessionId: "session-a", sourcePath: "uploads/input.png", origin: "os_web_upload" },
    { id: "adopted", sessionId: "session-a", sourcePath: "manual/copied.md", origin: "workspace_adopted" },
    { id: "folder", sessionId: "session-a", sourcePath: "src/", kind: "directory" },
  ];

  const projection = buildSessionOutputProjection(messages, artifacts, {
    sessionId: "session-a",
    workspaceId: "workspace-a",
    evidence: [{
      request: {
        specBrief: {
          specId: "spec-1",
          linkedSections: [{ stage: "tasks", relativePath: ".v8/specs/demo/tasks.md" }],
          pipelineControl: { blockedByApproval: "tasks" },
        },
      },
    }],
  });
  assert.deepEqual(
    projection.map((item) => item.path).sort(),
    [".v8/specs/demo/design.md", ".v8/specs/demo/requirements.md", ".v8/specs/demo/tasks.md", "media/final.png", "src/result.ts"].sort(),
  );
  assert.equal(projection.some((item) => item.path === "out/not-an-artifact/future.html"), false);
  assert.equal(projection.find((item) => item.path?.endsWith("requirements.md"))?.statusLabel, "已同意");
  assert.equal(projection.find((item) => item.path?.endsWith("design.md"))?.statusLabel, "待确认");
  assert.equal(projection.find((item) => item.path?.endsWith("tasks.md"))?.statusLabel, "待确认");
});

test("user message attachments are never duplicated into the overview", () => {
  const projection = buildSessionOutputProjection([{
    role: "user",
    artifacts: [{ id: "input", sourcePath: "uploads/input.png" }],
    metadata: {
      taskShapeHint: {
        specBrief: {
          specId: "spec-user-context",
          linkedSections: [
            { stage: "requirements", relativePath: ".v8/specs/current/requirements.md" },
          ],
          targetOutputDirectories: ["out"],
          explicitDeliverableFiles: ["future.html"],
        },
      },
    },
  }]);
  assert.deepEqual(projection.map((item) => item.path), [".v8/specs/current/requirements.md"]);
  assert.equal(projection.some((item) => item.path === "uploads/input.png"), false);
  assert.equal(projection.some((item) => item.path === "out/future.html"), false);
});

test("failed file tools never become overview outputs after the result is compacted for agents", () => {
  const projection = buildSessionOutputProjection([{
    id: "assistant-failed-write",
    role: "assistant",
    nodes: [
      {
        id: "write-call",
        kind: "execution",
        executionType: "tool_call",
        toolName: "write_native_file",
        toolCallId: "call-failed-write",
        args: { path: "src/never-created.ts" },
      },
      {
        id: "write-result",
        kind: "execution",
        executionType: "tool_result",
        toolName: "write_native_file",
        toolCallId: "call-failed-write",
        result: [
          "write native file result",
          "Status: failed",
          "Summary:",
          "The workspace is not trusted, so the write was blocked.",
        ].join("\n"),
      },
    ],
  }]);

  assert.deepEqual(projection, []);
});

test("runtime artifact overview keeps local paths on the Runtime Surface", () => {
  const projection = buildSessionOutputProjection([], [
    {
      id: "art-runtime-image",
      sessionId: "session-a",
      title: "edited-image.png",
      mimeType: "image/png",
      sourcePath: "E:/workspace/creative_media/cm_private/edited-image.png",
      workspacePath: "creative_media/cm_private/edited-image.png",
      metadata: {
        storageClass: "runtime_artifact",
        pathPlane: "runtime",
      },
    },
    {
      id: "art-workspace-doc",
      sessionId: "session-a",
      title: "README.md",
      mimeType: "text/markdown",
      workspaceRelativePath: "docs/README.md",
      metadata: {
        storageClass: "workspace",
        pathPlane: "workspace_artifact",
      },
    },
  ], { sessionId: "session-a" });

  const runtimeImage = projection.find((item) => item.artifactId === "art-runtime-image");
  const workspaceDoc = projection.find((item) => item.artifactId === "art-workspace-doc");
  assert.equal(runtimeImage?.path, null);
  assert.equal(runtimeImage?.name, "edited-image.png");
  assert.equal(runtimeImage?.mimeType, "image/png");
  assert.equal(workspaceDoc?.path, "docs/README.md");
});

test("artifact projection rejects conflicting root, lineage, metadata, and resource authority", () => {
  const projection = buildSessionOutputProjection([], [
    {
      id: "current",
      sessionId: "session-a",
      workspaceId: "workspace-a",
      sourcePath: "media/current.png",
      origin: "provider_result",
    },
    {
      id: "local-row-current",
      artifactId: "canonical-current",
      artifact_id: "canonical-current",
      sessionId: "session-a",
      workspaceId: "workspace-a",
      sourcePath: "media/canonical-current.png",
      origin: "provider_result",
    },
    {
      id: "conflicting-session",
      sessionId: "session-a",
      workspaceId: "workspace-a",
      lineage: { sessionId: "session-b", workspaceId: "workspace-a" },
      sourcePath: "media/session-leak.png",
      origin: "provider_result",
    },
    {
      id: "conflicting-workspace",
      sessionId: "session-a",
      workspaceId: "workspace-a",
      resourceRef: { workspaceId: "workspace-b" },
      sourcePath: "media/workspace-leak.png",
      origin: "provider_result",
    },
    {
      id: "conflicting-provenance",
      sessionId: "session-a",
      workspaceId: "workspace-a",
      metadata: { provenance: { sessionId: "session-b", workspaceId: "workspace-b" } },
      sourcePath: "media/provenance-leak.png",
      origin: "provider_result",
    },
    {
      id: "conflicting-aliases",
      sessionId: "session-a",
      session_id: "session-b",
      workspaceId: "workspace-a",
      workspace_id: "workspace-b",
      sourcePath: "media/alias-leak.png",
      origin: "provider_result",
    },
    {
      id: "conflicting-resource-alias",
      sessionId: "session-a",
      workspaceId: "workspace-a",
      resourceRef: { sessionId: "session-a", workspaceId: "workspace-a" },
      resource_ref: { session_id: "session-b", workspace_id: "workspace-b" },
      sourcePath: "media/resource-alias-leak.png",
      origin: "provider_result",
    },
    {
      artifactId: "conflicting-artifact-id-a",
      artifact_id: "conflicting-artifact-id-b",
      sessionId: "session-a",
      workspaceId: "workspace-a",
      sourcePath: "media/artifact-id-alias-leak.png",
      origin: "provider_result",
    },
    {
      artifactId: "conflicting-nested-artifact-id",
      sessionId: "session-a",
      workspaceId: "workspace-a",
      metadata: { artifact_id: "different-nested-artifact-id" },
      sourcePath: "media/artifact-id-nested-leak.png",
      origin: "provider_result",
    },
  ], {
    sessionId: "session-a",
    workspaceId: "workspace-a",
  });

  assert.deepEqual(projection.map((item) => item.artifactId), ["canonical-current", "current"]);
});
