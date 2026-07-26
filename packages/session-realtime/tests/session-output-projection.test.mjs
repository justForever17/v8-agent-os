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
    { id: "current", sessionId: "session-a", sourcePath: "media/final.png", origin: "provider_result" },
    { id: "other", sessionId: "session-b", sourcePath: "other/leak.png", origin: "provider_result" },
    { id: "upload", sessionId: "session-a", sourcePath: "uploads/input.png", origin: "os_web_upload" },
    { id: "adopted", sessionId: "session-a", sourcePath: "manual/copied.md", origin: "workspace_adopted" },
    { id: "folder", sessionId: "session-a", sourcePath: "src/", kind: "directory" },
  ];

  const projection = buildSessionOutputProjection(messages, artifacts, {
    sessionId: "session-a",
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
