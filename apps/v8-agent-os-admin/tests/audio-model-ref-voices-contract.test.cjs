const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const adminRoot = path.resolve(__dirname, "..");

function readText(relativePath) {
  return fs.readFileSync(path.join(adminRoot, relativePath), "utf8");
}

test("voice customization stays behind the Engine credential boundary", () => {
  const route = readText("src/app/api/audio/model-ref-voices/route.ts");

  assert.match(route, /resolveInternalSecret/);
  assert.match(route, /\$\{ENGINE_URL\}\/audio\/model-ref-voices/);
  assert.match(route, /x-v8-agent-os-secret/);
  assert.doesNotMatch(route, /detectAdapter/);
  assert.doesNotMatch(route, /minimax_tts|aliyun_bailian_cosyvoice|volcengine_doubao_voice/);
  assert.doesNotMatch(route, /api\.minimaxi\.com|dashscope\.aliyuncs\.com|openspeech\.bytedance\.com/);
});

test("Managed TTS voices use a searchable confirmed selector and reveal upload controls only on demand", () => {
  const hub = readText("src/app/admin/(dashboard)/model-hub/page.tsx");
  const start = hub.indexOf("{isManagedModelRefTtsVoice ? (");
  const end = hub.indexOf(") : ttsVoicePresets.length > 0 ? (", start);
  const managedBlock = hub.slice(start, end);

  assert.match(managedBlock, /SearchableVoiceSelect/);
  assert.match(managedBlock, /onDelete=/);
  assert.match(managedBlock, /modelRefTtsVoiceCapabilities\.delete === true/);
  assert.match(managedBlock, /voiceSearchPlaceholder/);
  assert.match(managedBlock, /isConfiguredModelRefTtsVoiceUnavailable/);
  assert.doesNotMatch(hub, /group: "configured"/);
  assert.match(managedBlock, /setIsTtsClonePanelOpen/);
  assert.match(managedBlock, /audio\.uploadVoice/);
  assert.match(managedBlock, /isTtsClonePanelOpen/);
  assert.match(managedBlock, /modelRefTtsVoiceCapabilities\.clone === true/);
  assert.match(managedBlock, /modelRefTtsVoiceCapabilities\.preview === true/);
  assert.match(managedBlock, /voiceCloneSampleHint/);
  assert.match(managedBlock, /sampleLimits\?\.minDurationSeconds \?\? 10/);
  assert.doesNotMatch(managedBlock, /customVoicePlaceholder/);
  assert.match(managedBlock, /\.mp3,\.m4a,\.wav/);
});

test("Only deletable custom voice options expose an inline transparent delete control", () => {
  const selector = readText("src/components/models/SearchableVoiceSelect.tsx");

  assert.match(selector, /option\.deletable && onDelete/);
  assert.match(selector, /aria-label=\{`\$\{deleteLabel\}: \$\{option\.label\}`\}/);
  assert.match(selector, /bg-transparent/);
  assert.match(selector, /onPointerDown/);
});

test("Audio save consumes a canonical response and exposes a real unsaved-config preview", () => {
  const hub = readText("src/app/admin/(dashboard)/model-hub/page.tsx");
  const previewRoute = readText("src/app/api/audio/tts/preview/route.ts");

  assert.match(hub, /"stt" in savedConfig/);
  assert.match(hub, /"tts" in savedConfig/);
  assert.match(hub, /\/api\/audio\/tts\/preview/);
  assert.match(hub, /config: audioConfig/);
  assert.match(hub, /previewAction/);
  assert.match(previewRoute, /\/audio\/tts\/preview/);
  assert.doesNotMatch(hub, /mergeAudioConfig\(await response\.json/);
});

test("Model Hub discovers voice customization from Engine capabilities instead of provider names", () => {
  const hub = readText("src/app/admin/(dashboard)/model-hub/page.tsx");

  assert.match(hub, /action: "capabilities", modelRef: selectedTtsModelRef/);
  assert.doesNotMatch(hub, /function isManagedModelRefTtsVoiceModel/);
  assert.match(hub, /providerCode/);
  assert.match(hub, /traceId/);
});
