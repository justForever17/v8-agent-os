import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { prepareUnifiedReleaseAssets } from "./prepare-unified-release-assets.mjs";

const ROOT = path.resolve(import.meta.dirname, "..", "..");
const MANIFEST = path.join(ROOT, "release-manifest.json");
const VERSION = JSON.parse(fs.readFileSync(MANIFEST, "utf8")).release.version;
const DESKTOP_NAMES = [
  `V8-Agent-OS-preview-${VERSION}-win-x64-setup.exe`,
  `V8-Agent-OS-preview-${VERSION}-win-arm64-setup.exe`,
  `V8-Agent-OS-preview-${VERSION}-macos-x64.dmg`,
  `V8-Agent-OS-preview-${VERSION}-macos-arm64.dmg`,
  `V8-Agent-OS-preview-${VERSION}-linux-x64.AppImage`,
  `V8-Agent-OS-preview-${VERSION}-linux-x64.deb`,
  `V8-Agent-OS-preview-${VERSION}-linux-arm64.AppImage`,
  `V8-Agent-OS-preview-${VERSION}-linux-arm64.deb`,
];

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-unified-release-"));
  const inputDir = path.join(root, "input");
  const outputDir = path.join(root, "output");
  fs.mkdirSync(path.join(inputDir, "desktop"), { recursive: true });
  fs.mkdirSync(path.join(inputDir, "phone", "android"), { recursive: true });
  for (const name of DESKTOP_NAMES) {
    fs.writeFileSync(path.join(inputDir, "desktop", name), `desktop:${name}`);
  }
  fs.writeFileSync(path.join(inputDir, "desktop", "RUNTIME_PROBE-windows-x64.json"), "{}");
  fs.writeFileSync(path.join(inputDir, "phone", "android", "app-release.apk"), "android");
  return { root, inputDir, outputDir };
}

test("unified preview emits required Desktop and Android assets but no diagnostic JSON", () => {
  const { root, inputDir, outputDir } = fixture();
  try {
    const result = prepareUnifiedReleaseAssets({
      manifestPath: MANIFEST,
      tag: `v8-os-v${VERSION}`,
      inputDir,
      outputDir,
    });
    assert.equal(result.assets.length, 9);
    const files = fs.readdirSync(outputDir).sort();
    assert.deepEqual(files, [
      ...DESKTOP_NAMES,
      `V8OS-Phone-${VERSION}-android-preview.apk`,
      "SHA256SUMS.txt",
    ].sort());
    assert.equal(files.some((name) => name.endsWith(".json")), false);
    const checksums = fs.readFileSync(path.join(outputDir, "SHA256SUMS.txt"), "utf8").trim().split(/\r?\n/);
    assert.equal(checksums.length, 9);
    assert.equal(checksums.every((line) => /^[a-f0-9]{64}  \S+$/.test(line)), true);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("legacy Phone tag only emits the Phone compatibility surface", () => {
  const { root, inputDir, outputDir } = fixture();
  try {
    const result = prepareUnifiedReleaseAssets({
      manifestPath: MANIFEST,
      tag: `v8-os-phone-v${VERSION}`,
      inputDir,
      outputDir,
    });
    assert.deepEqual(result.assets, [`V8OS-Phone-${VERSION}-android-preview.apk`]);
    assert.deepEqual(fs.readdirSync(outputDir).sort(), [
      `V8OS-Phone-${VERSION}-android-preview.apk`,
      "SHA256SUMS.txt",
    ].sort());
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("a missing required target blocks fan-in publication", () => {
  const { root, inputDir, outputDir } = fixture();
  try {
    fs.rmSync(path.join(inputDir, "desktop", DESKTOP_NAMES[0]));
    assert.throws(
      () => prepareUnifiedReleaseAssets({
        manifestPath: MANIFEST,
        tag: `v8-os-v${VERSION}`,
        inputDir,
        outputDir,
      }),
      /Required Desktop windows-x64 asset is missing/,
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("fan-in refuses to replace its input tree or a non-empty output directory", () => {
  const { root, inputDir, outputDir } = fixture();
  try {
    assert.throws(
      () => prepareUnifiedReleaseAssets({
        manifestPath: MANIFEST,
        tag: `v8-os-v${VERSION}`,
        inputDir,
        outputDir: inputDir,
      }),
      /Refusing unsafe release output directory/,
    );
    assert.equal(fs.existsSync(path.join(inputDir, "phone", "android", "app-release.apk")), true);

    fs.mkdirSync(outputDir, { recursive: true });
    fs.writeFileSync(path.join(outputDir, "keep.txt"), "do not overwrite");
    assert.throws(
      () => prepareUnifiedReleaseAssets({
        manifestPath: MANIFEST,
        tag: `v8-os-v${VERSION}`,
        inputDir,
        outputDir,
      }),
      /Release output directory must be empty/,
    );
    assert.equal(fs.readFileSync(path.join(outputDir, "keep.txt"), "utf8"), "do not overwrite");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
