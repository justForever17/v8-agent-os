import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";

import { resolvePreparationIdentity, resolvePreparationRequest } from "./prepare-release.mjs";
import { loadReleaseManifest, toSemver } from "./release-manifest.mjs";

const manifest = loadReleaseManifest(path.resolve(import.meta.dirname, "../../release-manifest.json")).manifest;

test("prepare-release defaults to one unified tag and every enabled product", () => {
  const identity = resolvePreparationIdentity({
    manifest,
    version: "2026.08.08.1",
    channel: "preview",
  });

  assert.equal(identity.tag, "v8-os-v2026.08.08.1");
  assert.deepEqual(identity.products, ["desktop", "phone"]);
  assert.equal(identity.deprecatedProduct, null);
  assert.equal(toSemver(identity.version), "2026.8.8-1");
});

test("deprecated --product cannot create a product tag or narrow the release", () => {
  const identity = resolvePreparationIdentity({
    manifest,
    version: "2026.08.08.1",
    channel: "preview",
    product: "desktop",
  });

  assert.equal(identity.tag, "v8-os-v2026.08.08.1");
  assert.deepEqual(identity.products, ["desktop", "phone"]);
  assert.equal(identity.deprecatedProduct, "desktop");
  assert.notEqual(identity.tag, "v8-os-desktop-v2026.08.08.1");
});

test("prepare-release fails closed for the unimplemented stable channel", () => {
  assert.throws(
    () => resolvePreparationIdentity({
      manifest,
      version: "2026.08.08.1",
      channel: "stable",
    }),
    /Only the preview channel is currently publishable/,
  );
});

test("prepare-release rejects repeated and descending versions", () => {
  for (const version of [manifest.release.version, "2026.08.06.1"]) {
    assert.throws(
      () => resolvePreparationIdentity({ manifest, version, channel: "preview" }),
      /must be newer than current manifest version/,
    );
  }
});

test("--from-manifest is repeatable, read-only, and skips tag availability checks", () => {
  const request = resolvePreparationRequest({ "from-manifest": true }, manifest);

  assert.equal(request.version, manifest.release.version);
  assert.equal(request.channel, "preview");
  assert.equal(request.apply, false);
  assert.equal(request.checkTagAvailability, false);
});

test("--from-manifest rejects write mode and conflicting release identity", () => {
  assert.throws(
    () => resolvePreparationRequest({ "from-manifest": true, apply: true }, manifest),
    /read-only validation/,
  );
  assert.throws(
    () => resolvePreparationRequest({ "from-manifest": true, version: "2026.08.08.1" }, manifest),
    /do not also pass/,
  );
});
