import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";

import { resolvePreparationIdentity, resolvePreparationRequest } from "./prepare-release.mjs";
import { loadReleaseManifest, toSemver } from "./release-manifest.mjs";

const manifest = loadReleaseManifest(path.resolve(import.meta.dirname, "../../release-manifest.json")).manifest;
const [currentYear, currentMonth, currentDay, currentBuild] = manifest.release.version.split(".").map(Number);
const nextVersion = currentBuild < 99
  ? `${currentYear}.${String(currentMonth).padStart(2, "0")}.${String(currentDay).padStart(2, "0")}.${currentBuild + 1}`
  : (() => {
      const nextDate = new Date(Date.UTC(currentYear, currentMonth - 1, currentDay + 1));
      assert.ok(nextDate.getUTCFullYear() <= 2099, "test fixture exhausted the supported release year range");
      return `${nextDate.getUTCFullYear()}.${String(nextDate.getUTCMonth() + 1).padStart(2, "0")}.${String(nextDate.getUTCDate()).padStart(2, "0")}.1`;
    })();
const nextTag = `v8-os-v${nextVersion}`;

test("prepare-release defaults to one unified tag and every enabled product", () => {
  const identity = resolvePreparationIdentity({
    manifest,
    version: nextVersion,
    channel: "preview",
  });

  assert.equal(identity.tag, nextTag);
  assert.deepEqual(identity.products, ["desktop", "phone"]);
  assert.equal(identity.deprecatedProduct, null);
  assert.equal(toSemver(identity.version), toSemver(nextVersion));
});

test("deprecated --product cannot create a product tag or narrow the release", () => {
  const identity = resolvePreparationIdentity({
    manifest,
    version: nextVersion,
    channel: "preview",
    product: "desktop",
  });

  assert.equal(identity.tag, nextTag);
  assert.deepEqual(identity.products, ["desktop", "phone"]);
  assert.equal(identity.deprecatedProduct, "desktop");
  assert.notEqual(identity.tag, `v8-os-desktop-v${nextVersion}`);
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
