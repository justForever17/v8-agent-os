import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { verifyReleaseManifest } from "./verify-release-manifest.mjs";

function manifestFixture(overrides = {}) {
  return {
    schema: 2,
    release: {
      version: "2026.08.07.3",
      channel: "preview",
      tag: "v8-os-v2026.08.07.3",
      ...overrides.release,
    },
    products: {
      desktop: {
        enabled: true,
        required: true,
        targets: Object.fromEntries(
          ["windows-x64", "windows-arm64", "macos-x64", "macos-arm64", "linux-x64", "linux-arm64"]
            .map((target) => [target, { enabled: true, required: true }]),
        ),
      },
      phone: {
        enabled: true,
        required: true,
        targets: {
          android: { enabled: true, required: true },
          ios: {
            enabled: false,
            required: false,
            reason: "Non-interactive iOS signing credentials are not configured.",
          },
        },
      },
      ...overrides.products,
    },
    compatibility: {
      legacyProductTags: {
        status: "deprecated",
        supportedUnifiedReleaseCycles: 2,
        deriveVersionFrom: "release.version",
      },
      ...overrides.compatibility,
    },
  };
}

function writeManifest(overrides = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "v8-release-manifest-"));
  const manifestPath = path.join(dir, "release-manifest.json");
  fs.writeFileSync(manifestPath, `${JSON.stringify(manifestFixture(overrides), null, 2)}\n`);
  return manifestPath;
}

test("unified release tag resolves without a product", () => {
  const result = verifyReleaseManifest({
    tag: "v8-os-v2026.08.07.3",
    manifestPath: writeManifest(),
  });

  assert.equal(result.schema, 2);
  assert.equal(result.product, null);
  assert.equal(result.version, "2026.08.07.3");
  assert.equal(result.tagKind, "unified");
  assert.equal(result.deprecated, false);
});

test("legacy product tag is derived from top-level release version", () => {
  const result = verifyReleaseManifest({
    product: "desktop",
    tag: "v8-os-desktop-v2026.08.07.3",
    manifestPath: writeManifest(),
  });

  assert.equal(result.product, "desktop");
  assert.equal(result.tagKind, "legacy-product");
  assert.equal(result.deprecated, true);
  assert.match(result.warning, /deprecated compatibility trigger/);
  assert.match(result.warning, /two successfully published unified release cycles/);
});

test("legacy tag cannot carry a version different from release.version", () => {
  assert.throws(
    () => verifyReleaseManifest({
      product: "desktop",
      tag: "v8-os-desktop-v2026.08.07.2",
      manifestPath: writeManifest(),
    }),
    /does not match derived tag/,
  );
});

test("top-level tag cannot drift from release.version", () => {
  assert.throws(
    () => verifyReleaseManifest({
      tag: "v8-os-v2026.08.07.3",
      manifestPath: writeManifest({
        release: { tag: "v8-os-v2026.08.07.2" },
      }),
    }),
    /Invalid release manifest schema 2/,
  );
});

test("disabled iOS target must be optional and explain why it is disabled", () => {
  const manifest = manifestFixture();
  manifest.products.phone.targets.ios.reason = "";
  const manifestPath = writeManifest({ products: manifest.products });

  assert.throws(
    () => verifyReleaseManifest({ tag: "v8-os-v2026.08.07.3", manifestPath }),
    /reason is required/,
  );
});

test("all six desktop targets remain required", () => {
  const manifest = manifestFixture();
  manifest.products.desktop.targets["windows-arm64"].required = false;
  const manifestPath = writeManifest({ products: manifest.products });

  assert.throws(
    () => verifyReleaseManifest({ tag: "v8-os-v2026.08.07.3", manifestPath }),
    /windows-arm64 must be enabled and required/,
  );
});

test("product entries cannot duplicate the top-level release identity", () => {
  const manifest = manifestFixture();
  manifest.products.phone.version = manifest.release.version;
  const manifestPath = writeManifest({ products: manifest.products });

  assert.throws(
    () => verifyReleaseManifest({ tag: "v8-os-v2026.08.07.3", manifestPath }),
    /only release identity truth/,
  );
});

test("release version rejects impossible dates and values that break store version ordering", () => {
  for (const version of [
    "2026.02.29.1",
    "2026.13.01.1",
    "2026.08.08.01",
    "2026.08.08.0",
    "2026.08.08.100",
    "2100.01.01.1",
  ]) {
    const manifest = manifestFixture({ release: { version } });
    manifest.release.tag = `v8-os-v${version}`;
    const manifestPath = writeManifest({ release: manifest.release });
    assert.throws(
      () => verifyReleaseManifest({ tag: manifest.release.tag, manifestPath }),
      /release\.version must be a real UTC date/,
    );
  }
});
