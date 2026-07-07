export type DesktopPetEventVoiceMode = "system_tts" | "voice_tag" | "muted";

export type DesktopPetAttachmentCapture = {
  cameraEnabled: boolean;
  includeDesktopScreenshot: boolean;
  layout: "desktop_pip_camera";
};

export function normalizeEventVoiceMode(value: unknown): DesktopPetEventVoiceMode {
  const mode = String(value || "").trim().toLowerCase();
  if (mode === "voice_tag" || mode === "voice_tag_only" || mode === "voice-tag-only") {
    return "voice_tag";
  }
  if (mode === "muted" || mode === "off" || mode === "disabled" || mode === "none") {
    return "muted";
  }
  return "system_tts";
}

export function isEventVoiceEnabled(eventVoice: { enabled?: unknown; mode?: unknown } | null | undefined) {
  return eventVoice?.enabled !== false && normalizeEventVoiceMode(eventVoice?.mode) !== "muted";
}

export function normalizeAttachmentCapture(value: unknown): DesktopPetAttachmentCapture {
  const record = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  const cameraEnabled = record.cameraEnabled === true;
  return {
    cameraEnabled,
    includeDesktopScreenshot: cameraEnabled && record.includeDesktopScreenshot === true,
    layout: "desktop_pip_camera",
  };
}
