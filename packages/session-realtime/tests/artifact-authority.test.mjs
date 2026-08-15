import assert from "node:assert/strict";
import test from "node:test";

import {
  buildAdminArtifactContentRef,
  coerceAdminResourceRef,
  deriveAdminResourceRefFromArtifactLike,
  resolveAdminResourceUrl,
} from "../dist/resources.js";

test("artifact content refs require and preserve session authority", () => {
  assert.equal(buildAdminArtifactContentRef("artifact-a", { sessionId: "" }), null);
  assert.equal(deriveAdminResourceRefFromArtifactLike({ artifactId: "artifact-a" }), null);

  const ref = buildAdminArtifactContentRef("artifact-a", { sessionId: "session-a" });
  assert.deepEqual(ref, {
    kind: "artifact_content",
    artifactId: "artifact-a",
    sessionId: "session-a",
    adminPath: "/api/client/artifacts/artifact-a/content?sessionId=session-a",
  });
  assert.equal(
    resolveAdminResourceUrl("web", undefined, ref),
    "/api/artifacts/artifact-a/content?sessionId=session-a",
  );
});

test("artifact refs fail closed on missing or conflicting session lineage", () => {
  assert.equal(coerceAdminResourceRef({
    kind: "artifact_content",
    artifactId: "artifact-a",
    adminPath: "/api/client/artifacts/artifact-a/content",
  }), null);

  assert.equal(deriveAdminResourceRefFromArtifactLike({
    artifactId: "artifact-a",
    sessionId: "session-a",
    resourceRef: {
      kind: "artifact_content",
      artifactId: "artifact-a",
      sessionId: "session-b",
    },
  }), null);

  assert.equal(deriveAdminResourceRefFromArtifactLike({
    artifactId: "artifact-a",
    sessionId: "session-a",
    resourceRef: {
      kind: "artifact_content",
      artifactId: "artifact-b",
      sessionId: "session-a",
    },
  }), null);

  const parsed = coerceAdminResourceRef(
    "/api/client/artifacts/artifact-a/content?sessionId=session-a&v8exp=1&v8sig=ignored",
  );
  assert.equal(parsed?.sessionId, "session-a");
  assert.equal(parsed?.adminPath, "/api/client/artifacts/artifact-a/content?sessionId=session-a");
});
