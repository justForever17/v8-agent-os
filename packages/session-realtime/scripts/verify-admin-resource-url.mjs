import assert from "node:assert/strict";
import {
  deriveAdminResourceRefFromArtifactLike,
  resolveAdminResourceUrl,
} from "../dist/resources.js";

const artifactRef = deriveAdminResourceRefFromArtifactLike({
  artifactId: "artifact_audio_001",
  sessionId: "session-a",
  title: "voice.mp3",
  mimeType: "audio/mpeg",
  previewUrl: "http://127.0.0.1:9530/private/artifacts/voice.mp3",
});

assert.equal(artifactRef?.kind, "artifact_content");
assert.equal(
  resolveAdminResourceUrl("web", undefined, artifactRef),
  "/api/artifacts/artifact_audio_001/content?sessionId=session-a",
);

const phoneUrl = resolveAdminResourceUrl("phone", "http://100.64.0.20:9528", artifactRef);
assert.equal(phoneUrl, "http://100.64.0.20:9528/api/client/artifacts/artifact_audio_001/content?sessionId=session-a");
assert(!phoneUrl.includes("127.0.0.1"));
assert(!/^[A-Za-z]:[\\/]/.test(phoneUrl));

const managedArtifactRef = deriveAdminResourceRefFromArtifactLike({
  artifactId: "artifact_managed_001",
  sessionId: "session-a",
  title: "result.json",
  mimeType: "application/json",
  sourcePath: "E:/managed/run/result.json",
  workspaceRoot: "E:/managed/run",
  workspaceRelativePath: "result.json",
  workspaceId: "project-workspace",
  projectId: "project-workspace",
  pathPlane: "workspace_artifact",
});

assert.equal(managedArtifactRef?.kind, "artifact_content");
assert.equal(
  resolveAdminResourceUrl("web", undefined, managedArtifactRef),
  "/api/artifacts/artifact_managed_001/content?sessionId=session-a",
);

assert.equal(deriveAdminResourceRefFromArtifactLike({
  artifactId: "artifact_unscoped_001",
  title: "unsafe.txt",
}), null);

const workspaceRef = deriveAdminResourceRefFromArtifactLike({
  resourceRef: {
    kind: "workspace_file",
    adminPath: "/api/client/workspace/files/v8/uploads/image.png",
    mimeType: "image/png",
  },
});

assert.equal(
  resolveAdminResourceUrl("web", undefined, workspaceRef),
  "/api/workspace/files/v8/uploads/image.png",
);
assert.equal(
  resolveAdminResourceUrl("phone", "http://100.64.0.20:9528", workspaceRef),
  "http://100.64.0.20:9528/api/client/workspace/files/v8/uploads/image.png",
);

console.log("admin resource URL verification passed");
