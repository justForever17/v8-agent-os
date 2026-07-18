/* eslint-disable @typescript-eslint/no-require-imports */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const adminRoot = path.resolve(__dirname, "..");

function readText(relativePath) {
  return fs.readFileSync(path.join(adminRoot, relativePath), "utf8");
}

test("MiniMax voice management stays behind the Engine credential boundary", () => {
  const route = readText("src/app/api/audio/model-ref-voices/route.ts");

  assert.match(route, /resolveInternalSecret/);
  assert.match(route, /\$\{ENGINE_URL\}\/audio\/model-ref-voices/);
  assert.match(route, /x-v8-agent-os-secret/);
  assert.doesNotMatch(route, /miniMaxEndpoint/);
  assert.doesNotMatch(route, /api\.minimaxi\.com\/v1\/get_voice/);
});

test("Managed TTS voices use a selector and reveal upload controls only on demand", () => {
  const hub = readText("src/app/admin/(dashboard)/model-hub/page.tsx");
  const start = hub.indexOf("{isManagedModelRefTtsVoice ? (");
  const end = hub.indexOf(") : ttsVoicePresets.length > 0 ? (", start);
  const managedBlock = hub.slice(start, end);

  assert.match(managedBlock, /selectableModelRefTtsVoices\.map/);
  assert.match(managedBlock, /setIsTtsClonePanelOpen/);
  assert.match(managedBlock, /audio\.uploadVoice/);
  assert.match(managedBlock, /isTtsClonePanelOpen/);
  assert.doesNotMatch(managedBlock, /customVoicePlaceholder/);
  assert.match(managedBlock, /\.mp3,\.m4a,\.wav/);
});
