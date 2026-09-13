// Selected element only: no microphone, loopback, URL extraction or cookie export.
// A recorder clock is not a media clock; keep anchors and reject discontinuities.
export async function captureMediaAudio(state, range, timeoutMs) {
  return state.evaluate(async (saved, spec) => {
    const video = saved.element;
    const unavailable = (reason) => ({ status: "unavailable", reason, requestedRange: spec.range });
    if (!video.captureStream || typeof MediaRecorder === "undefined") return unavailable("audio_capture_unsupported");
    if (video.playbackRate !== 1) return unavailable("audio_requires_normal_playback_rate");
    let stream;
    try { stream = video.captureStream(); }
    catch (error) { return unavailable(error.name === "SecurityError" ? "audio_cross_origin_restricted" : "audio_capture_unsupported"); }
    const tracks = stream.getAudioTracks();
    // Stop only cloned capture tracks, never srcObject's original tracks.
    const stopTracks = () => stream.getTracks().forEach((track) => track.stop());
    if (!tracks.length) { stopTracks(); return unavailable("audio_track_unavailable"); }
    const mimeType = ["audio/webm;codecs=opus", "audio/webm"].find((mime) => MediaRecorder.isTypeSupported(mime));
    if (!mimeType) { stopTracks(); return unavailable("audio_recorder_format_unsupported"); }
    let recorder;
    try { recorder = new MediaRecorder(new MediaStream(tracks), { mimeType, audioBitsPerSecond: 64000 }); }
    catch { stopTracks(); return unavailable("audio_recorder_unavailable"); }
    const chunks = [], anchors = [], listeners = [];
    let bytes = 0, timer, poll, startedAt, startTime, endTime, reason, finishing = false;
    const listen = (target, type, fn) => { target.addEventListener(type, fn); listeners.push(() => target.removeEventListener(type, fn)); };
    const result = await new Promise((resolve) => {
      const finish = (why) => {
        if (finishing) return;
        finishing = true; reason = why; endTime = video.currentTime;
        clearInterval(poll); clearTimeout(timer);
        if (recorder.state !== "inactive") recorder.stop(); else resolve();
      };
      recorder.ondataavailable = (event) => {
        bytes += event.data.size;
        if (bytes > 2 * 1024 * 1024) finish("audio_capture_size_limit");
        else if (event.data.size) chunks.push(event.data);
      };
      recorder.onstop = () => resolve();
      recorder.onerror = () => finish("audio_recorder_failed");
      const current = () => video.isConnected && video.currentSrc === saved.source && video.srcObject === saved.sourceObject;
      listen(video, "seeking", () => finish("audio_timeline_discontinuity"));
      listen(video, "ratechange", () => finish("audio_timeline_discontinuity"));
      listen(video, "waiting", () => finish("audio_playback_stalled"));
      listen(video, "ended", () => finish(video.currentTime >= spec.range[1] - 0.05 ? null : "audio_ended_early"));
      for (const track of tracks) {
        listen(track, "ended", () => finish("audio_track_changed"));
        listen(track, "mute", () => finish("audio_track_muted_or_restricted"));
      }
      // captureStream queues initial addtrack events, including its unused video
      // track. Only a different audio track changes this recorder's input.
      listen(stream, "addtrack", (event) => { if (event.track.kind === "audio" && !tracks.includes(event.track)) finish("audio_track_changed"); });
      listen(stream, "removetrack", (event) => { if (tracks.includes(event.track)) finish("audio_track_changed"); });
      // A user click/keypress interrupts sampling; restoration must not overwrite it.
      const takeover = (event) => { if (event.isTrusted) { saved.userChanged = true; finish("audio_user_interrupted"); } };
      listen(document, "pointerdown", takeover); listen(document, "keydown", takeover);
      timer = setTimeout(() => finish("audio_capture_timeout"), spec.timeoutMs);
      recorder.onstart = () => {
        startedAt = performance.now(); startTime = video.currentTime;
        anchors.push({ recordingTime: 0, mediaTime: startTime });
        poll = setInterval(() => {
          if (saved.cancelled) return finish("audio_capture_cancelled");
          if (!current() || video.mediaKeys) return finish("audio_target_changed");
          if (tracks.some((track) => track.muted || track.readyState !== "live")) return finish("audio_track_muted_or_restricted");
          const recordingTime = (performance.now() - startedAt) / 1000;
          const mediaTime = video.currentTime;
          anchors.push({ recordingTime, mediaTime });
          if (Math.abs((mediaTime - startTime) - recordingTime) > 0.3) return finish("audio_timeline_discontinuity");
          if (mediaTime >= spec.range[1]) finish(null);
        }, 50);
      };
      // Chromium may delay the recorder's start event until media arrives.
      // Waiting for that event before play would deadlock a paused element.
      try { recorder.start(250); video.play().catch(() => finish("audio_playback_denied")); }
      catch { finish("audio_recorder_failed"); }
    }).then(async () => {
      if (reason) return unavailable(reason);
      const blob = new Blob(chunks, { type: mimeType });
      if (!blob.size) return unavailable("audio_capture_empty");
      const data = await new Promise((resolve, reject) => {
        const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(",")[1]);
        reader.onerror = reject; reader.readAsDataURL(blob);
      });
      return { status: "captured", requestedRange: spec.range, startTime, endTime, mimeType, data,
        anchors, alignmentToleranceMs: 300, method: "element_capture_stream", speechVerified: false };
    }).finally(() => {
      clearInterval(poll); clearTimeout(timer); listeners.forEach((remove) => remove());
      recorder.ondataavailable = recorder.onstop = recorder.onerror = recorder.onstart = null;
      if (recorder.state !== "inactive") recorder.stop();
      stopTracks();
      if (video.isConnected && video.currentSrc === saved.source && video.srcObject === saved.sourceObject && !saved.userChanged) video.pause();
    });
    return result;
  }, { range, timeoutMs });
}
