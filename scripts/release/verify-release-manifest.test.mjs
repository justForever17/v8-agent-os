import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { verifyReleaseManifest } from "./verify-release-manifest.mjs";

function writeManifest(desktop = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "v8-release-manifest-"));
  const manifestPath = path.join(dir, "release-manifest.json");
  fs.writeFileSync(
    manifestPath,
    JSON.stringify(
      {
        schema: 1,
        products: {
          desktop: {
            version: "2026.07.09.1",
            channel: "preview",
            tag: "v8-os-desktop-v2026.07.09.1",
            updatedAt: "2026-07-09T00:00:00.000Z",
            ...desktop,
          },
        },
      },
      null,
      2,
    ),
  );
  return manifestPath;
}

test("desktop release tag must match release manifest metadata", () => {
  const manifestPath = writeManifest();
  const result = verifyReleaseManifest({
    product: "desktop",
    tag: "v8-os-desktop-v2026.07.09.1",
    manifestPath,
  });

  assert.equal(result.product, "desktop");
  assert.equal(result.version, "2026.07.09.1");
});

test("desktop release tag fails when manifest version lags behind", () => {
  const manifestPath = writeManifest({
    version: "2026.07.08.6",
    tag: "v8-os-desktop-v2026.07.08.6",
  });

  assert.throws(
    () =>
      verifyReleaseManifest({
        product: "desktop",
        tag: "v8-os-desktop-v2026.07.09.1",
        manifestPath,
      }),
    /desktop release manifest mismatch/,
  );
});

test("desktop release tag format is strict", () => {
  const manifestPath = writeManifest();

  assert.throws(
    () =>
      verifyReleaseManifest({
        product: "desktop",
        tag: "desktop-v2026.07.09.1",
        manifestPath,
      }),
    /does not match/,
  );
});
