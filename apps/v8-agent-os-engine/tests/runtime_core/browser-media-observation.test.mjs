import assert from "node:assert/strict";
import test from "node:test";
import { observeAgentMedia } from "../../scripts/browser_media_observation.mjs";

function fixture() {
  const events = [], listeners = new Map();
  let position = 3, count = 1;
  const video = { tagName: "VIDEO", isConnected: true, currentSrc: "https://fixture.test/video?token=DO-NOT-EXPORT",
    paused: false, duration: 20, readyState: 4, videoWidth: 640, videoHeight: 360,
    seeking: false, mediaKeys: null, textTracks: [
      { kind: "subtitles", label: "Test", language: "zh", mode: "hidden", cues: [
        { startTime: 0, endTime: 4, text: "字幕第一行\n第二行" }, { startTime: 4, endTime: 6, text: "下一段" },
      ] },
    ],
    pause() { events.push("pause"); this.paused = true; },
    async play() { events.push("play"); this.paused = false; },
    addEventListener(name, fn) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(fn); },
    removeEventListener(name, fn) { listeners.get(name)?.delete(fn); },
  };
  Object.defineProperty(video, "currentTime", { get: () => position, set: (time) => {
    events.push(["seek", time]); position = time;
    queueMicrotask(() => { for (const fn of listeners.get("seeked") || []) fn(); });
  } });
  const handles = [];
  const elementHandle = {
    async evaluateHandle(fn) {
      const saved = fn(video);
      const state = { disposed: false, evaluate: async (fn, arg) => fn(saved, arg),
        dispose: async () => { state.disposed = true; } };
      handles.push(state); return state;
    },
    async screenshot() { events.push(["capture", position]); return Buffer.from(`frame:${position}`); },
    async dispose() { events.push("dispose"); },
  };
  const locator = { filter(options) { assert.deepEqual(options, { visible: true }); return this; },
    count: async () => count, elementHandle: async () => elementHandle };
  const page = { locator: () => locator };
  return { page, video, events, elementHandle, handles, listeners, setCount(n) { count = n; } };
}

test("ordered samples capture real current times, subtitle lines and restore playing state", async () => {
  const f = fixture();
  const result = await observeAgentMedia(f.page, { sampleTimes: [1, 10, 1] });
  assert.deepEqual(result.frames.map((frame) => [frame.index, frame.requestedTime, frame.currentTime]), [[1, 1, 1], [2, 10, 10], [3, 1, 1]]);
  assert.deepEqual(result.frames.map((frame) => Buffer.from(frame.data, "base64").toString()), ["frame:1", "frame:10", "frame:1"]);
  assert.equal(result.tracks[0].cues[0].text, "字幕第一行\n第二行");
  assert.equal(result.wholeVideoReviewed, false);
  assert.equal(result.currentTime, 3);
  assert.equal(f.video.currentTime, 3);
  assert.equal(f.video.paused, false);
  assert.equal(result.playbackRestoration.ok, true);
  assert.equal(f.handles[0].disposed, true);
  assert.doesNotMatch(JSON.stringify(result), /DO-NOT-EXPORT|fixture\.test/);
});

test("default captures one current frame and keeps originally paused video paused", async () => {
  const f = fixture(); f.video.paused = true;
  const result = await observeAgentMedia(f.page);
  assert.equal(result.frames.length, 1);
  assert.equal(result.frames[0].currentTime, 3);
  assert.equal(f.video.paused, true);
  assert.equal(f.events.some((event) => Array.isArray(event) && event[0] === "seek"), false);
  assert.equal(f.events.includes("play"), false);
});

test("unavailable, ambiguous, unloaded, non-video and DRM targets produce no fake frame", async () => {
  for (const [fault, code] of [["none", "video_not_found"], ["many", "video_ambiguous"],
    ["unloaded", "video_not_loaded"], ["other", "target_is_not_video"], ["drm", "drm_video_observation_unsupported"]]) {
    const f = fixture();
    if (fault === "none") f.setCount(0);
    if (fault === "many") f.setCount(2);
    if (fault === "unloaded") f.video.readyState = 1;
    if (fault === "other") f.video.tagName = "DIV";
    if (fault === "drm") f.video.mediaKeys = {};
    await assert.rejects(observeAgentMedia(f.page), new RegExp(code));
    assert.equal(f.events.includes("pause"), false);
    assert.equal(f.events.some((event) => Array.isArray(event) && event[0] === "capture"), false);
  }
});

test("sample limits reject before playback changes, including seeking live streams", async () => {
  for (const times of [[], [-1], [NaN], [Infinity], ["3"], Array(9).fill(1), [20]]) {
    const f = fixture();
    await assert.rejects(observeAgentMedia(f.page, { sampleTimes: times }), /invalid_video_sample_times|video_sample_out_of_range_or_live/);
    assert.equal(f.events.includes("pause"), false);
  }
  const f = fixture(); f.video.duration = Infinity;
  await assert.rejects(observeAgentMedia(f.page, { sampleTimes: [1] }), /video_sample_out_of_range_or_live/);
});

test("subtitle limits are explicit and disabled tracks do not trigger network loads", async () => {
  const f = fixture();
  f.video.textTracks.push({ kind: "captions", mode: "disabled", cues: null });
  const result = await observeAgentMedia(f.page, { maxCues: 1, maxTextChars: 4 });
  assert.equal(result.cueCount, 1);
  assert.equal(result.totalCues, 2);
  assert.equal(result.tracks[0].cues[0].text.length, 4);
  assert.equal(result.cuesTruncated, true);
  assert.equal(result.tracks[1].available, false);
  assert.equal(f.video.textTracks[1].mode, "disabled");
});

test("capture failure restores original position and does not leave a stale operation lock", async () => {
  const f = fixture();
  f.elementHandle.screenshot = async () => { throw new Error("capture unavailable https://fixture.test/video?token=PRIVATE"); };
  await assert.rejects(observeAgentMedia(f.page, { sampleTimes: [7] }), (error) => {
    assert.equal(error.message, "video_observation_failed");
    return true;
  });
  assert.equal(f.video.currentTime, 3);
  assert.equal(f.video.paused, false);
  f.elementHandle.screenshot = async () => Buffer.from("recovered");
  assert.equal((await observeAgentMedia(f.page)).ok, true);
});

test("source replacement cannot restore or seek a different media resource", async () => {
  const f = fixture();
  f.elementHandle.screenshot = async () => { f.video.currentSrc = "https://fixture.test/replacement"; return Buffer.from("changed"); };
  await assert.rejects(observeAgentMedia(f.page, { sampleTimes: [1] }), /video_playback_restore_failed/);
  assert.deepEqual(f.events.filter((event) => Array.isArray(event) && event[0] === "seek"), [["seek", 1]]);
});

test("replacing srcObject also invalidates a stream with the same empty currentSrc", async () => {
  const f = fixture(); f.video.currentSrc = ""; f.video.srcObject = {};
  f.elementHandle.screenshot = async () => { f.video.srcObject = {}; return Buffer.from("changed stream"); };
  await assert.rejects(observeAgentMedia(f.page), /video_playback_restore_failed/);
  assert.equal(f.events.includes("play"), false);
});

test("resume denial exposes restoration failure instead of completed observation", async () => {
  const f = fixture();
  f.video.play = async () => { throw new Error("autoplay rejected"); };
  await assert.rejects(observeAgentMedia(f.page, { sampleTimes: [1] }), /video_playback_restore_failed/);
  assert.equal(f.video.currentTime, 3);
  assert.equal(f.video.paused, true);
});

test("concurrent same-page sampling refuses, independent page still succeeds", async () => {
  const f = fixture();
  let release, began;
  const started = new Promise((resolve) => { began = resolve; });
  f.elementHandle.screenshot = async () => { began(); await new Promise((resolve) => { release = resolve; }); return Buffer.from("held"); };
  const first = observeAgentMedia(f.page);
  await started;
  await assert.rejects(observeAgentMedia(f.page), /video_observation_in_progress/);
  assert.equal((await observeAgentMedia(fixture().page)).ok, true);
  release();
  assert.equal((await first).ok, true);
});

test("a stalled seek times out and restores state with no listener leak", async () => {
  const f = fixture(); f.video.seeking = true;
  // Only the first requested position remains stalled. Restoration can settle.
  const add = f.video.addEventListener;
  f.video.addEventListener = function (name, fn) {
    if (f.events.some((event) => Array.isArray(event) && event[0] === "seek")) f.video.seeking = false;
    add.call(this, name, fn);
  };
  await assert.rejects(observeAgentMedia(f.page, { sampleTimes: [4], timeoutMs: 100 }), /video_seek_timeout/);
  assert.equal(f.video.currentTime, 3);
  assert.equal(f.video.paused, false);
  for (const listeners of f.listeners.values()) assert.equal(listeners.size, 0);
});
