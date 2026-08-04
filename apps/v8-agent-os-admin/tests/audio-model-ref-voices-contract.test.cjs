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
  assert.match(managedBlock, /isQualificationOnlyVoice/);
  assert.match(managedBlock, /isProviderSlotVoice/);
  assert.match(managedBlock, /modelRefTtsVoiceCapabilities\.list === true \? \(/);
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
  assert.match(hub, /payload\.assetPolicy/);
  assert.match(hub, /payload\.designConstraints/);
  assert.match(hub, /payload\.credentialStatus/);
  assert.match(hub, /modelRefTtsVoiceAssetPolicy\?\.assetScope === "qualification_only"/);
  assert.match(hub, /modelRefTtsVoiceAssetPolicy\?\.assetScope === "ephemeral_request"/);
  assert.match(hub, /modelRefTtsVoiceAssetPolicy\?\.assetScope === "provider_slot"/);
  assert.match(hub, /providerCode/);
  assert.match(hub, /traceId/);
});

test("Voice design follows Engine-declared direct, ephemeral, slot, and preview-commit semantics", () => {
  const hub = readText("src/app/admin/(dashboard)/model-hub/page.tsx");

  assert.match(hub, /action: "design"/);
  assert.match(hub, /action: "commit_design"/);
  assert.match(hub, /generatedVoiceId: candidate\.generatedVoiceId/);
  assert.match(hub, /modelRefTtsVoiceAssetPolicy\?\.designFlow === "preview_then_commit"/);
  assert.match(hub, /isTtsVoiceDesignIdRequired && !ttsDesignVoiceId\.trim\(\)/);
  assert.match(hub, /ttsVoiceDesignIdRole === "prefix"/);
  assert.match(hub, /minLength=\{ttsDesignPromptMinChars\}/);
  assert.match(hub, /maxLength=\{ttsDesignPreviewMaxChars\}/);
  assert.match(hub, /disabled=\{isTtsDesigning \|\| !isTtsDesignInputValid\}/);
  assert.match(hub, /payload\.ephemeral === true \|\| isEphemeralReferenceVoice/);

  const ephemeralBranchStart = hub.indexOf("if (payload.ephemeral === true || isEphemeralReferenceVoice)");
  const persistentSelection = hub.indexOf('setTtsModelRefValue("voice", designedVoiceId)', ephemeralBranchStart);
  assert.ok(ephemeralBranchStart >= 0);
  assert.ok(persistentSelection > ephemeralBranchStart, "ephemeral result must return before persistent voice selection");
  assert.match(hub.slice(ephemeralBranchStart, persistentSelection), /return;/);
});

test("Qualification-only providers expose official eligibility and consent paths without fake operations", () => {
  const hub = readText("src/app/admin/(dashboard)/model-hub/page.tsx");

  assert.match(hub, /modelRefTtsVoiceAssetPolicy\?\.eligibilityStatus === "eligible"/);
  assert.match(hub, /modelRefTtsVoiceAssetPolicy\?\.consentRequired/);
  assert.match(hub, /modelRefTtsVoiceAssetPolicy\?\.applicationUrl/);
  assert.match(hub, /modelRefTtsVoiceAssetPolicy\?\.docsUrl/);
  assert.match(hub, /voiceQualificationApply/);
  assert.match(hub, /voiceQualificationDocs/);
});

test("Missing credentials block managed voice side effects while leaving capability discovery visible", () => {
  const hub = readText("src/app/admin/(dashboard)/model-hub/page.tsx");

  assert.match(hub, /isModelRefVoiceCredentialMissing/);
  assert.match(hub, /disabled=\{isModelRefTtsVoiceLoading \|\| !selectedTtsModelRef \|\| isModelRefVoiceCredentialMissing\}/);
  assert.match(hub, /onDelete=\{modelRefTtsVoiceCapabilities\.delete === true && !isModelRefVoiceCredentialMissing/);
  assert.match(hub, /disabled=\{isModelRefVoiceCredentialMissing\}/);
});

test("Edge TTS remains the canonical no-key default and is not routed through voice customization", () => {
  const hub = readText("src/app/admin/(dashboard)/model-hub/page.tsx");

  assert.match(hub, /active_provider: "edge-tts"/);
  assert.match(hub, /edge_tts: \{ voice: "zh-CN-XiaoxiaoNeural", rate: "\+0%", volume: "\+0%" \}/);
  assert.match(hub, /audioConfig\.tts\.active_provider === "edge-tts"/);
  assert.match(hub, /setTtsValue\("edge_tts", "voice", value\)/);
});

test("The audio surface only exposes loaded or cached canonical audio config", () => {
  const hub = readText("src/app/admin/(dashboard)/model-hub/page.tsx");

  assert.match(hub, /const cachedBootstrap = peekAdminJsonCache<ModelHubBootstrapPayload>\(MODEL_HUB_BOOTSTRAP_URL\)/);
  assert.match(hub, /const \[hasLoadedAudioConfig, setHasLoadedAudioConfig\] = useState\(\(\) => Boolean\(cachedBootstrap\)\)/);
  assert.match(hub, /const \[audioConfig, setAudioConfig\] = useState<AudioRuntimeConfig>\(\(\) => mergeAudioConfig\(cachedBootstrap\?\.audioConfig \|\| null\)\)/);
  assert.match(hub, /setAudioConfig\(mergeAudioConfig\(payload\.audioConfig \|\| null\)\);\s*setHasLoadedAudioConfig\(true\)/);
  assert.match(hub, /hasLoadedAudioConfig \? systemAudioConfigCard/);
  assert.match(hub, /audio\.loadingConfig/);
});
