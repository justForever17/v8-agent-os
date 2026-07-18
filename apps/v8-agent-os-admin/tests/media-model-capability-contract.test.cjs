const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const root = path.resolve(__dirname, "..");

function read(relativePath) {
  return fs.readFileSync(path.join(root, relativePath), "utf8");
}

test("model edit uses compact capability checkboxes instead of a free-form operation field", () => {
  const page = read("src/app/admin/(dashboard)/model-hub/page.tsx");
  assert.match(page, /getMediaCapabilityOptions\(modelType\)\.map/);
  assert.match(page, /className="h-3\.5 w-3\.5 rounded-\[3px\]"/);
  assert.match(page, /payload\.capabilityModes = mediaCapabilityModes/);
  assert.doesNotMatch(page, /id="model-operation-kind"/);
});

test("manual capability modes persist both human and runtime projections", () => {
  const admin = read("src/lib/models/model-admin.ts");
  assert.match(admin, /payload\.operationKinds = derivedOperationKinds/);
  assert.match(admin, /capabilityModes,\s*operationKinds: derivedOperationKinds/);
  assert.match(admin, /provenance: \{ source: "manual", confidence: "authoritative" \}/);
});

test("the UI supports the requested image video voice music and 3D families", () => {
  const capabilities = read("src/lib/models/media-capabilities.ts");
  for (const expected of [
    "image.text_to_image",
    "image.image_to_image",
    "image.edit",
    "video.text_to_video",
    "video.image_to_video",
    "video.first_last_frame",
    "video.image_reference",
    "video.multimodal_reference",
    "voice.tts",
    "voice.design",
    "music.generate",
    "music.cover",
    "model3d.text_to_3d",
    "model3d.image_to_3d",
  ]) {
    assert.ok(capabilities.includes(`id: "${expected}"`), `missing ${expected}`);
  }
});
