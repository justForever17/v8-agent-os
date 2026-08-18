const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

const webRoot = path.resolve(__dirname, "..");

function read(relativePath) {
  return fs.readFileSync(path.join(webRoot, relativePath), "utf8");
}

function loadTypeScriptModule(relativePath) {
  const filename = path.join(webRoot, relativePath);
  const output = ts.transpileModule(fs.readFileSync(filename, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    fileName: filename,
  }).outputText;
  const module = { exports: {} };
  new Function("module", "exports", "require", output)(module, module.exports, require);
  return module.exports;
}

test("Session request coordinator keeps the newest owner across stale finally blocks", () => {
  const { createCanvasSessionRequestCoordinator } = loadTypeScriptModule(
    "src/components/workbench/creative-canvas/request-owner.ts",
  );
  const coordinator = createCanvasSessionRequestCoordinator("session-a");
  const ownerA = { sessionId: "session-a", token: "run-a", kind: "run" };
  const ownerB = { sessionId: "session-b", token: "retry-b", kind: "retry" };

  assert.equal(coordinator.acquire(ownerA), ownerA);
  coordinator.activateSession("session-b");
  assert.equal(coordinator.isActive(ownerA), false);
  assert.equal(coordinator.acquire(ownerB), ownerB);
  assert.equal(coordinator.release(ownerA), false, "A finally must not release B");
  assert.deepEqual(coordinator.current(), ownerB);
  assert.equal(coordinator.isActive(ownerB), true);
  assert.equal(coordinator.release(ownerB), true);
  assert.equal(coordinator.current(), null);

  coordinator.activateSession("session-a");
  const reloadA = { sessionId: "session-a", token: "reload-a" };
  assert.equal(coordinator.acquire(reloadA), reloadA);
  assert.equal(coordinator.isActive(reloadA), true);
  coordinator.activateSession("session-b");
  coordinator.activateSession("session-a");
  assert.equal(coordinator.isActive(reloadA), false, "a previous visit must not regain ownership after returning");
  const secondA = { sessionId: "session-a", token: "run-a-new" };
  assert.equal(coordinator.acquire(secondA), secondA);
  assert.equal(coordinator.isActive(reloadA), false);
  assert.equal(coordinator.isActive(secondA), true);
});

test("Inspector and A/B Review remain result-scoped and hide runtime identifiers", () => {
  const review = read("src/components/workbench/creative-canvas/inspector-review.tsx");
  const canvas = read("src/components/workbench/CreativeArtifactCanvas.tsx");

  assert.match(review, /candidate\.resultNodeId === resultNodeId/);
  assert.match(canvas, /resultNodeId: inspectNode\.nodeId/);
  assert.match(canvas, /identity: version\.outputVersionId/);
  assert.match(review, /imageMode === "side"/);
  assert.match(review, /clipPath: `inset\(0 \$\{100 - wipePosition\}% 0 0\)`/);
  assert.match(review, /controls\.mediaARef|mediaARef\.current/);
  assert.match(review, /Promise\.allSettled\(pair\.map\(\(\[, media\]\) => media\.play\(\)\)\)/);
  assert.match(review, /follower\.currentTime/);
  assert.match(review, /kind === "audio"/);
  assert.match(review, /selectedVersion/);
  assert.match(review, /outputVersionProof\(selectedVersion\?\.version\)/);
  assert.match(review, /versionBound/);
  assert.match(review, /data-canvas-media-state/);
  assert.match(review, /removeAttribute\("src"\)/);
  assert.doesNotMatch(review, />\s*\{?[^<]*(?:graphRunId|artifactId|workspacePath|providerHandle)[^<]*</);
  assert.match(canvas, /<CanvasInspectorReviewPanel/);
  assert.match(canvas, /setInspectMode\("review"\)/);
  assert.match(canvas, /outputVersionResource\(version, resourceMap, sessionId\)/);
  assert.match(canvas, /artifact_content/);
  assert.match(canvas, /sessionId=\$\{encodeURIComponent\(sessionId\)\}/);
  assert.match(canvas, /selectedVersion=\{inspectSelectedVersion\}/);
});

test("Output-version proof cannot silently fall back to the latest node runtime", () => {
  const review = read("src/components/workbench/creative-canvas/inspector-review.tsx");
  const canvas = read("src/components/workbench/CreativeArtifactCanvas.tsx");
  assert.match(review, /const versionBound = Boolean\(selectedVersion\)/);
  assert.match(review, /const provider = versionBound\s*\n\s*\? outputVersionValue/);
  assert.match(review, /versionProof\?\.providerLabel \|\| versionProof\?\.provider/);
  assert.match(canvas, /inspectNode\?\.kind === "result"\s*\n\s*\? inspectSelectedVersion\?\.resource \|\| null/);
  assert.match(canvas, /availability: url && refSessionMatches && refArtifactMatches \? "available" : "unavailable"/);
});

test("Video and audio review expose an audition selector and preserve one-sided failures", () => {
  const review = read("src/components/workbench/creative-canvas/inspector-review.tsx");
  assert.match(review, /onError=\{\(\) => controls\.setMediaState\(side, "error"\)\}/);
  assert.match(review, /controls\.mediaState\[side\]/);
  assert.match(review, /\(\["a", "b"\] as const\)\.map/);
  assert.doesNotMatch(review, /kind === "audio" \? \(/);
  assert.match(review, /pair\.forEach\(\(\[, media\]\) => media\.pause\(\)\)/);
});

test("Real media review harness is explicit about Range, FFmpeg fixtures, swaps, and cleanup", () => {
  const harness = read("tests/creative_canvas_media_review_real.mjs");
  assert.match(harness, /ffmpeg/);
  assert.match(harness, /bytes=\\d\*-/);
  assert.match(harness, /for \(let index = 0; index < 30; index \+= 1\)/);
  assert.match(harness, /failed=b/);
  assert.match(harness, /removeAttribute|mediaCount/);
});

test("Review and delivery controls preserve the output-version contract", () => {
  const review = read("src/components/workbench/creative-canvas/inspector-review.tsx");
  const canvas = read("src/components/workbench/CreativeArtifactCanvas.tsx");
  const matrix = read("tests/creative_canvas_review_delivery_matrix.md");
  assert.match(canvas, /canvas\/graph\/outputs\/\$\{encodeURIComponent\(version\.identity\)\}\/review/);
  assert.match(canvas, /selectedForDelivery/);
  assert.match(canvas, /expectedRevision: Number\(currentVersion\.review\?\.revision \|\| 0\)/);
  assert.match(canvas, /canvas\/graph\/outputs\/\$\{encodeURIComponent\(version\.identity\)\}\/delivery/);
  assert.match(canvas, /dryRun/);
  assert.match(canvas, /isCurrentMutationOwner\(owner\)/);
  assert.match(canvas, /isCurrentCanvasRuntimeEpoch\(epoch, runtimeMutationEpochRef\.current\)/);
  assert.match(canvas, /reloadOutputReviewProjection\(owner, epoch\)/);
  assert.match(canvas, /reconcileCanvasRuntimeProjection\(current, authoritativeRuntime\)/);
  assert.doesNotMatch(canvas, /mergeCanvasOutputVersion/, "review mutations must reload the authoritative result projection");
  assert.match(review, /data-canvas-review-action="approve"/);
  assert.match(review, /data-canvas-review-action="reject"/);
  assert.match(review, /data-canvas-review-selected/);
  assert.match(review, /data-canvas-delivery-action="dry-run"/);
  assert.match(review, /data-canvas-delivery-action="confirm"/);
  assert.match(review, /deliveryManifestArtifactId/);
  assert.doesNotMatch(review, /manifestArtifactId\}\s*<\//, "manifest identity must stay out of the human surface");
  assert.match(matrix, /Review revision did not advance|review revision/i);
  assert.match(matrix, /dryRun: true/);
  assert.match(matrix, /dryRun: false/);
  assert.match(matrix, /temporary directory/);
  assert.match(matrix, /V1\/V2 selection race/);
  const visualHarness = read("tests/creative_canvas_inspector_review_visual.mjs");
  assert.match(visualHarness, /__runCanvasSelectionRace/);
  assert.match(visualHarness, /selected\.length !== 1/);
});

test("Inspector and review copy is complete in both locales", () => {
  const en = JSON.parse(read("src/i18n/locales/en.json"));
  const zh = JSON.parse(read("src/i18n/locales/zh-CN.json"));
  const keys = [
    "web.workbench.canvas.inspector.provider",
    "web.workbench.canvas.inspector.model",
    "web.workbench.canvas.inspector.readiness",
    "web.workbench.canvas.inspector.recovery.remoteUncertain",
    "web.workbench.canvas.review.title",
    "web.workbench.canvas.review.sideBySide",
    "web.workbench.canvas.review.wipe",
    "web.workbench.canvas.review.timeline",
    "web.workbench.canvas.review.unsupported",
    "web.workbench.canvas.review.controls",
    "web.workbench.canvas.review.approve",
    "web.workbench.canvas.review.reject",
    "web.workbench.canvas.review.selectedForDelivery",
    "web.workbench.canvas.review.checkDelivery",
    "web.workbench.canvas.review.confirmDelivery",
    "web.workbench.canvas.review.deliveryCreated",
  ];
  for (const key of keys) {
    assert.equal(typeof en[key], "string", `missing English ${key}`);
    assert.equal(typeof zh[key], "string", `missing Chinese ${key}`);
  }
});
