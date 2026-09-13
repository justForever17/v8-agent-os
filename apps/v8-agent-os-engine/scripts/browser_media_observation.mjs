// Fixed observations on an existing, already-authorized Playwright page.
// The caller owns session/target/lease, Safety, cancellation and artifacts.
import { captureMediaAudio } from "./browser_media_audio.mjs";
const activePages = new WeakSet();
function fail(code) { throw new Error(code); }
function publicError(error) {
  const code = String(error?.message || "").match(/\b(video_(?:not_found|ambiguous|not_loaded|target_changed|sample_out_of_range_or_live|seek_failed|seek_timeout|observation_timeout|observation_cancelled|playback_changed_during_observation|changed_during_capture)|target_is_not_video|drm_video_observation_unsupported)\b/)?.[1];
  return new Error(code || (error?.name === "TimeoutError" ? "video_observation_timeout" : "video_observation_failed"));
}

async function seek(state, target, timeoutMs) {
  return state.evaluate(async (saved, spec) => {
    const video = saved.element;
    if (!video.isConnected || video.currentSrc !== saved.source || video.srcObject !== saved.sourceObject) throw new Error("video_target_changed");
    if (Math.abs(video.currentTime - spec.time) < 0.025 && !video.seeking && video.readyState >= 2) return;
    await new Promise((resolve, reject) => {
      const finish = (error) => {
        clearTimeout(timer);
        video.removeEventListener("seeked", onReady);
        video.removeEventListener("loadeddata", onReady);
        video.removeEventListener("error", onError);
        error ? reject(new Error(error)) : resolve();
      };
      const onReady = () => { if (!video.seeking && video.readyState >= 2) finish(); };
      const onError = () => finish("video_seek_failed");
      const timer = setTimeout(() => finish("video_seek_timeout"), spec.timeoutMs);
      video.addEventListener("seeked", onReady);
      video.addEventListener("loadeddata", onReady);
      video.addEventListener("error", onError);
      try { video.currentTime = spec.time; } catch { finish("video_seek_failed"); }
    });
  }, { time: target, timeoutMs });
}

async function restore(state, timeoutMs) {
  const original = await state.evaluate((saved) => ({ time: saved.time, paused: saved.paused }));
  await seek(state, original.time, timeoutMs);
  await state.evaluate(async (saved, waitMs) => {
    const video = saved.element;
    if (!video.isConnected || video.currentSrc !== saved.source || video.srcObject !== saved.sourceObject) throw new Error("video_target_changed");
    if (saved.paused) { video.pause(); return; }
    let timer;
    try {
      await Promise.race([
        video.play(),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("video_resume_timeout")), waitMs); }),
      ]);
    } catch {
      video.pause(); // Cancel a still-pending play promise rather than resume late.
      throw new Error("video_resume_failed");
    } finally { clearTimeout(timer); }
  }, timeoutMs);
  return { ok: true, restoredTime: original.time, restoredPaused: original.paused };
}

export async function observeAgentMedia(page, spec = {}) {
  const selector = String(spec.selector || "video").trim();
  const times = spec.sampleTimes;
  const audioRange = spec.audioRange;
  if (audioRange !== undefined && (!Array.isArray(audioRange) || audioRange.length !== 2
      || audioRange.some((value) => typeof value !== "number" || !Number.isFinite(value) || value < 0)
      || audioRange[1] <= audioRange[0] || audioRange[1] - audioRange[0] > 10)) fail("invalid_audio_range_max_10_seconds");
  if (spec.signal?.aborted) fail("video_observation_cancelled");
  if (!selector || selector.length > 1000) fail("invalid_video_selector");
  if (times !== undefined && (!Array.isArray(times) || !times.length || times.length > 8
      || times.some((time) => typeof time !== "number" || !Number.isFinite(time) || time < 0))) fail("invalid_video_sample_times");
  if (activePages.has(page)) fail("video_observation_in_progress");
  const timeoutMs = Math.max(100, Math.min(30000, Number(spec.timeoutMs) || 15000));
  const maxCues = Math.max(1, Math.min(200, Math.floor(Number(spec.maxCues) || 200)));
  const maxTextChars = Math.max(1, Math.min(12000, Math.floor(Number(spec.maxTextChars) || 12000)));
  const deadline = Date.now() + timeoutMs;
  const remaining = () => { const ms = deadline - Date.now(); if (ms <= 0) fail("video_observation_timeout"); return Math.min(ms, 4000); };
  activePages.add(page);
  let video, state, changed = false;
  let result, error;
  const abort = () => { if (state) void state.evaluate((saved) => { saved.cancelled = true; }).catch(() => {}); };
  spec.signal?.addEventListener("abort", abort, { once: true });
  try {
    const candidates = page.locator(selector).filter({ visible: true });
    const count = await candidates.count();
    if (count !== 1) fail(count ? "video_ambiguous" : "video_not_found");
    video = await candidates.elementHandle({ timeout: remaining() });
    if (!video) fail("video_not_found");
    state = await video.evaluateHandle((element) => {
      if (element.tagName !== "VIDEO") throw new Error("target_is_not_video");
      if (!element.isConnected) throw new Error("video_target_changed");
      if (element.mediaKeys) throw new Error("drm_video_observation_unsupported");
      if (element.error || element.readyState < 2 || !element.videoWidth || !element.videoHeight) throw new Error("video_not_loaded");
      return { element, source: element.currentSrc, sourceObject: element.srcObject, time: element.currentTime, paused: element.paused };
    });
    const metadata = await state.evaluate((saved, limits) => {
      const video = saved.element;
      const tracks = [];
      let cueCount = 0, totalCues = 0, chars = 0;
      for (const track of Array.from(video.textTracks).slice(0, 16)) {
        if (!["subtitles", "captions"].includes(track.kind)) continue;
        const cues = [], loaded = track.cues;
        totalCues += loaded?.length || 0;
        for (let index = 0; loaded && index < loaded.length && cueCount < limits.maxCues && chars < limits.maxTextChars; index++) {
          const cue = loaded[index];
          const text = String(cue.text || "").slice(0, limits.maxTextChars - chars);
          cues.push({ startTime: cue.startTime, endTime: cue.endTime, text });
          chars += text.length; cueCount++;
        }
        tracks.push({ kind: track.kind, language: String(track.language || "").slice(0, 80), label: String(track.label || "").slice(0, 200),
          mode: track.mode, available: loaded !== null, cues });
      }
      return { currentTime: saved.time, duration: Number.isFinite(video.duration) ? video.duration : null,
        live: !Number.isFinite(video.duration), paused: saved.paused, width: video.videoWidth, height: video.videoHeight,
        tracks, cueCount, totalCues, tracksTruncated: video.textTracks.length > 16,
        cuesTruncated: cueCount < totalCues || chars >= limits.maxTextChars, inputTrust: "untrusted_media_observation" };
    }, { maxCues, maxTextChars });
    if (times && (metadata.duration === null || times.some((time) => time >= metadata.duration))) fail("video_sample_out_of_range_or_live");
    if (audioRange && (metadata.duration === null || audioRange[1] > metadata.duration)) fail("video_sample_out_of_range_or_live");
    changed = true;
    await state.evaluate((saved) => saved.element.pause());
    const frames = [];
    for (const requestedTime of times || [metadata.currentTime]) {
      if (spec.signal?.aborted || await state.evaluate((saved) => !!saved.cancelled)) fail("video_observation_cancelled");
      await seek(state, requestedTime, remaining());
      const before = await state.evaluate((saved) => {
        if (!saved.element.isConnected || saved.element.currentSrc !== saved.source || saved.element.srcObject !== saved.sourceObject) throw new Error("video_target_changed");
        if (saved.element.mediaKeys) throw new Error("drm_video_observation_unsupported");
        return { time: saved.element.currentTime, paused: saved.element.paused };
      });
      if (!before.paused) fail("video_playback_changed_during_observation");
      if (Math.abs(before.time - requestedTime) > 0.1) fail("video_seek_failed");
      const frameTiming = await state.evaluate(async (saved) => {
        const video = saved.element;
        if (typeof video.requestVideoFrameCallback !== "function") return null;
        return await Promise.race([
          new Promise((resolve) => video.requestVideoFrameCallback((_now, metadata) => resolve({
            mediaTime: Number.isFinite(metadata.mediaTime) ? metadata.mediaTime : null,
            presentedFrames: metadata.presentedFrames ?? null,
          }))),
          new Promise((resolve) => setTimeout(() => resolve(null), 500)),
        ]);
      });
      const data = await video.screenshot({ type: "jpeg", quality: 65, timeout: remaining() });
      if (spec.signal?.aborted || await state.evaluate((saved) => !!saved.cancelled)) fail("video_observation_cancelled");
      const after = await state.evaluate((saved) => ({ current: saved.element.isConnected && saved.element.currentSrc === saved.source && saved.element.srcObject === saved.sourceObject,
        time: saved.element.currentTime, paused: saved.element.paused }));
      if (!after.current || !after.paused || Math.abs(after.time - before.time) > 0.025) fail("video_changed_during_capture");
      frames.push({ index: frames.length + 1, requestedTime, currentTime: after.time,
        mediaTime: frameTiming?.mediaTime ?? after.time, presentedFrames: frameTiming?.presentedFrames ?? null,
        timeAccuracy: frameTiming?.mediaTime === null || frameTiming === null ? "element_current_time_approximate" : "request_video_frame_callback",
        capturedAt: Date.now(),
        mimeType: "image/jpeg", data: data.toString("base64") });
    }
    let audio = { status: "not_requested" };
    if (audioRange) {
      if (spec.signal?.aborted) fail("video_observation_cancelled");
      await seek(state, audioRange[0], remaining());
      audio = await captureMediaAudio(state, audioRange, Math.max(1, deadline - Date.now()));
    }
    const entries = frames.map((frame) => ({ kind: "frame", startTime: frame.mediaTime, endTime: frame.mediaTime, frameIndex: frame.index,
      timeAccuracy: frame.timeAccuracy }));
    metadata.tracks.forEach((track, trackIndex) => track.cues.forEach((cue, cueIndex) => entries.push({ kind: "subtitle", startTime: cue.startTime, endTime: cue.endTime, trackIndex, cueIndex })));
    if (audio.status === "captured") entries.push({ kind: "audio", startTime: audio.startTime, endTime: audio.endTime });
    entries.sort((left, right) => left.startTime - right.startTime);
    result = { ok: true, ...metadata, frames, audio, timeline: { timebase: "media_seconds", entries,
      wholeVideoReviewed: false, gaps: audio.status === "unavailable" ? [{ kind: "audio", range: audioRange, reason: audio.reason }] : [] },
      observationsOnly: true, wholeVideoReviewed: false };
  } catch (caught) {
    error = publicError(caught);
  } finally {
    spec.signal?.removeEventListener("abort", abort);
    const userChanged = state && await state.evaluate((saved) => !!saved.userChanged).catch(() => false);
    if (userChanged && result) result.playbackRestoration = { ok: false, reason: "user_interrupted_not_overwritten" };
    if (state && changed && !userChanged) {
      try { const playbackRestoration = await restore(state, 2000); if (result) result.playbackRestoration = playbackRestoration; }
      catch { error = new Error("video_playback_restore_failed"); }
    }
    await state?.dispose().catch(() => {});
    await video?.dispose().catch(() => {});
    activePages.delete(page);
  }
  if (error) throw error;
  return result;
}
