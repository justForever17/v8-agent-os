import assert from "node:assert/strict";
import {
  deriveAdminResourceRefFromArtifactLike,
  resolveAdminResourceUrl,
} from "../dist/resources.js";

const artifactRef = deriveAdminResourceRefFromArtifactLike({
  artifactId: "artifact_audio_001",
  title: "voice.mp3",
  mimeType: "audio/mpeg",
  previewUrl: "http://127.0.0.1:9530/private/artifacts/voice.mp3",
});

assert.equal(artifactRef?.kind, "artifact_content");
assert.equal(
  resolveAdminResourceUrl("web", undefined, artifactRef),
  "/api/artifacts/artifact_audio_001/content",
);

const phoneUrl = resolveAdminResourceUrl("phone", "http://100.64.0.20:9528", artifactRef);
assert.equal(phoneUrl, "http://100.64.0.20:9528/api/client/artifacts/artifact_audio_001/content");
assert(!phoneUrl.includes("127.0.0.1"));
assert(!/^[A-Za-z]:[\\/]/.test(phoneUrl));

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
