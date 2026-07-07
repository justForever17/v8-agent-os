import assert from "node:assert/strict";
import {
  isEventVoiceEnabled,
  normalizeAttachmentCapture,
  normalizeEventVoiceMode,
} from "../src/lib/desktopPetConfigContract";

assert.equal(normalizeEventVoiceMode("system_tts"), "system_tts");
assert.equal(normalizeEventVoiceMode("voice_tag"), "voice_tag");
assert.equal(normalizeEventVoiceMode("voice_tag_only"), "voice_tag");
assert.equal(normalizeEventVoiceMode("off"), "muted");
assert.equal(normalizeEventVoiceMode("muted"), "muted");

assert.equal(isEventVoiceEnabled({ enabled: true, mode: "voice_tag_only" }), true);
assert.equal(isEventVoiceEnabled({ enabled: true, mode: "off" }), false);
assert.equal(isEventVoiceEnabled({ enabled: false, mode: "system_tts" }), false);

assert.deepEqual(
  normalizeAttachmentCapture({
    cameraEnabled: true,
    includeDesktopScreenshot: true,
    layout: "desktop_pip_camera",
  }),
  {
    cameraEnabled: true,
    includeDesktopScreenshot: true,
    layout: "desktop_pip_camera",
  },
);

assert.deepEqual(
  normalizeAttachmentCapture({
    cameraEnabled: false,
    includeDesktopScreenshot: true,
  }),
  {
    cameraEnabled: false,
    includeDesktopScreenshot: false,
    layout: "desktop_pip_camera",
  },
);

console.log("desktop pet config contract ok");
