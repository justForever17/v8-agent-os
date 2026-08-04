import assert from "node:assert/strict";
import test from "node:test";

import {
  deriveMediaOperationKinds,
  getMediaCapabilityOptions,
  resolveMediaCapabilityModes,
} from "../src/lib/models/media-capabilities.ts";

test("video capability modes preserve fine-grained user declarations", () => {
  const modes = [
    "video.image_reference",
    "video.multimodal_reference",
    "video.first_last_frame",
  ];
  assert.deepEqual(resolveMediaCapabilityModes("VIDEO", modes, []), modes);
  assert.deepEqual(
    deriveMediaOperationKinds("VIDEO", modes),
    ["video.first_last_frame", "video.reference_to_video"],
  );
});

test("image-to-image and image editing remain separate UI truths", () => {
  const modes = ["image.image_to_image", "image.edit"];
  assert.deepEqual(resolveMediaCapabilityModes("IMAGE", modes, []), modes);
  assert.deepEqual(deriveMediaOperationKinds("IMAGE", modes), ["image.edit"]);
});

test("legacy operation kinds receive a conservative primary projection", () => {
  assert.deepEqual(
    resolveMediaCapabilityModes("VIDEO", undefined, ["video.reference_to_video"]),
    ["video.image_reference"],
  );
  assert.deepEqual(
    resolveMediaCapabilityModes("MODEL3D", undefined, ["model3d.generate"]),
    ["model3d.text_to_3d"],
  );
});

test("explicitly clearing all capability modes remains empty", () => {
  assert.deepEqual(resolveMediaCapabilityModes("MUSIC", [], ["music.generate"]), []);
  assert.deepEqual(deriveMediaOperationKinds("MUSIC", []), []);
});

test("only currently supported media families expose checkbox options", () => {
  assert.equal(getMediaCapabilityOptions("VIDEO").length, 5);
  assert.deepEqual(getMediaCapabilityOptions("WORKFLOW"), [
    {
      id: "video.action_transfer",
      labelKey: "app.admin.dashboard.model.hub.capability.workflow.actionTransfer",
      operationKind: "video.action_transfer",
    },
  ]);
  assert.equal(getMediaCapabilityOptions("TEXT").length, 0);
});
