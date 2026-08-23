import assert from "node:assert/strict";
import test from "node:test";

import {
  appendCanvasRecordingChunk,
  consumeCanvasRecordingChunks,
  createCanvasRecordingSession,
  createWorkbenchResizeSession,
  discardCanvasRecordingSession,
  isCanvasCaptureRequestCurrent,
  releaseCanvasCapture,
} from "../src/components/workbench/workbench-motion-behavior.ts";

function createFrameHarness() {
  let nextFrameId = 1;
  const frames = new Map();
  const cancelled = [];
  return {
    scheduler: {
      request(callback) {
        const frameId = nextFrameId++;
        frames.set(frameId, callback);
        return frameId;
      },
      cancel(frameId) {
        cancelled.push(frameId);
        frames.delete(frameId);
      },
    },
    flush() {
      const pending = [...frames.values()];
      frames.clear();
      pending.forEach((callback) => callback());
    },
    frames,
    cancelled,
  };
}

test("Workbench resize coalesces pointer moves and commits once on finish", () => {
  const frameHarness = createFrameHarness();
  const previews = [];
  const commits = [];
  const session = createWorkbenchResizeSession({
    pointerId: 7,
    parentRight: 1_000,
    initialWidth: 300,
    minimumWidth: 200,
    maximumWidth: 600,
    onPreview: (width) => previews.push(width),
    onCommit: (width) => commits.push(width),
    scheduler: frameHarness.scheduler,
  });

  session.move(7, 700);
  session.move(7, 600);
  assert.equal(frameHarness.frames.size, 1);
  assert.deepEqual(previews, []);
  assert.deepEqual(commits, []);

  frameHarness.flush();
  assert.deepEqual(previews, [400]);

  session.move(7, 550);
  session.finish(7);
  session.finish(7);
  assert.deepEqual(previews, [400, 450]);
  assert.deepEqual(commits, [450]);
  assert.equal(frameHarness.cancelled.length, 1);
});

test("Workbench resize disposal cancels pending work without persisting", () => {
  const frameHarness = createFrameHarness();
  const previews = [];
  const commits = [];
  const session = createWorkbenchResizeSession({
    pointerId: 3,
    parentRight: 900,
    initialWidth: 300,
    minimumWidth: 200,
    maximumWidth: 600,
    onPreview: (width) => previews.push(width),
    onCommit: (width) => commits.push(width),
    scheduler: frameHarness.scheduler,
  });

  session.move(3, 500);
  session.dispose();
  frameHarness.flush();
  session.finish(3);

  assert.deepEqual(previews, []);
  assert.deepEqual(commits, []);
  assert.equal(frameHarness.cancelled.length, 1);
});

test("Workbench resize ignores move and end events from another pointer", () => {
  const frameHarness = createFrameHarness();
  const commits = [];
  const session = createWorkbenchResizeSession({
    pointerId: 11,
    parentRight: 1_000,
    initialWidth: 300,
    minimumWidth: 200,
    maximumWidth: 600,
    onPreview: () => {},
    onCommit: (width) => commits.push(width),
    scheduler: frameHarness.scheduler,
  });

  assert.equal(session.move(12, 400), false);
  assert.equal(session.finish(12), false);
  assert.equal(frameHarness.frames.size, 0);
  assert.equal(session.move(11, 650), true);
  assert.equal(session.finish(11), true);
  assert.deepEqual(commits, [350]);
});

test("Canvas capture release discards chunks, stops the recorder, and releases every track", () => {
  const stoppedTracks = [];
  const chunks = [{ size: 10 }, { size: 20 }];
  let recorderStops = 0;
  const result = releaseCanvasCapture({
    recorder: {
      state: "recording",
      stop() {
        recorderStops += 1;
      },
    },
    stream: {
      getTracks() {
        return [
          { stop: () => stoppedTracks.push("video") },
          { stop: () => { stoppedTracks.push("faulty"); throw new Error("track stop failed"); } },
          { stop: () => stoppedTracks.push("audio") },
        ];
      },
    },
    chunks,
  });

  assert.equal(recorderStops, 1);
  assert.deepEqual(stoppedTracks, ["video", "faulty", "audio"]);
  assert.deepEqual(chunks, []);
  assert.deepEqual(result, { recorderStopped: true, tracksStopped: 2 });
});

test("Canvas recorder callbacks remain isolated when an older stop arrives late", () => {
  const oldSession = createCanvasRecordingSession({ state: "recording", stop() {} });
  const newSession = createCanvasRecordingSession({ state: "recording", stop() {} });

  appendCanvasRecordingChunk(oldSession, "old-initial");
  appendCanvasRecordingChunk(newSession, "new-initial");
  discardCanvasRecordingSession(oldSession);
  assert.equal(appendCanvasRecordingChunk(oldSession, "old-final"), false);
  appendCanvasRecordingChunk(newSession, "new-final");

  assert.deepEqual(consumeCanvasRecordingChunks(oldSession), []);
  assert.deepEqual(consumeCanvasRecordingChunks(newSession), ["new-initial", "new-final"]);
});

test("Canvas rejects a delayed camera grant after hide, session change, or newer request", () => {
  const base = {
    requestEpoch: 4,
    currentEpoch: 4,
    visible: true,
    requestSessionId: "session-a",
    currentSessionId: "session-a",
    sessionRunning: false,
  };

  assert.equal(isCanvasCaptureRequestCurrent(base), true);
  assert.equal(isCanvasCaptureRequestCurrent({ ...base, visible: false }), false);
  assert.equal(isCanvasCaptureRequestCurrent({ ...base, currentSessionId: "session-b" }), false);
  assert.equal(isCanvasCaptureRequestCurrent({ ...base, currentEpoch: 5 }), false);
  assert.equal(isCanvasCaptureRequestCurrent({ ...base, sessionRunning: true }), false);
});
