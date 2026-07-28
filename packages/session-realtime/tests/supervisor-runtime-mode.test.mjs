import assert from "node:assert/strict";
import test from "node:test";

import {
  isSupervisorRuntimeMode,
  normalizeAuthoritativeSessionHistoryRecord,
  normalizeSupervisorRuntimeMode,
} from "../dist/index.js";

test("supervisor runtime mode accepts only the shared canonical values", () => {
  for (const mode of ["auto", "engineering", "research", "creative_media", "computer_use", "rpa"]) {
    assert.equal(isSupervisorRuntimeMode(mode), true);
    assert.equal(normalizeSupervisorRuntimeMode(mode), mode);
  }
  assert.equal(isSupervisorRuntimeMode("daily"), false);
  assert.equal(normalizeSupervisorRuntimeMode("daily"), "auto");
  assert.equal(normalizeSupervisorRuntimeMode(undefined), "auto");
});

test("session history restores runtime mode from top-level or metadata without cross-session drift", () => {
  const research = normalizeAuthoritativeSessionHistoryRecord({
    id: "session-research",
    title: "Research",
    supervisorRuntimeMode: "research",
  });
  const media = normalizeAuthoritativeSessionHistoryRecord({
    id: "session-media",
    title: "Media",
    metadata: JSON.stringify({ supervisor_runtime_mode: "creative_media" }),
  });
  const automatic = normalizeAuthoritativeSessionHistoryRecord({
    id: "session-auto",
    title: "Auto",
    metadata: { supervisorRuntimeMode: "unknown", supervisorWorkMode: "engineering" },
  });
  const legacyEngineering = normalizeAuthoritativeSessionHistoryRecord({
    id: "session-legacy-engineering",
    title: "Legacy Engineering",
    metadata: { supervisorWorkMode: "engineering" },
  });

  assert.equal(research.supervisorRuntimeMode, "research");
  assert.equal(media.supervisorRuntimeMode, "creative_media");
  assert.equal(automatic.supervisorRuntimeMode, "auto");
  assert.equal(automatic.supervisorWorkMode, "engineering");
  assert.equal(legacyEngineering.supervisorRuntimeMode, "engineering");
});
