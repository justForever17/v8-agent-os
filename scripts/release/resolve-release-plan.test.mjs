import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { loadReleasePlan, writeGithubOutputs } from "./resolve-release-plan.mjs";

const ROOT = path.resolve(import.meta.dirname, "../..");
const MANIFEST = path.join(ROOT, "release-manifest.json");
const CURRENT_MANIFEST = JSON.parse(fs.readFileSync(MANIFEST, "utf8"));
const CURRENT_VERSION = CURRENT_MANIFEST.release.version;
const CURRENT_TAG = CURRENT_MANIFEST.release.tag;

function projectionFixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "v8-release-projections-"));
  const files = [
    "VERSION",
    "release-manifest.json",
    path.join("apps", "v8-agent-os-shell", "package.json"),
    path.join("apps", "v8-agent-os-shell", "package-lock.json"),
    path.join("apps", "v8-agent-os-phone", "package.json"),
    path.join("apps", "v8-agent-os-phone", "package-lock.json"),
    path.join("apps", "v8-agent-os-phone", "app.json"),
  ];
  for (const relativePath of files) {
    const target = path.join(root, relativePath);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.copyFileSync(path.join(ROOT, relativePath), target);
  }
  return root;
}

function mutateJson(filePath, mutate) {
  const value = JSON.parse(fs.readFileSync(filePath, "utf8"));
  mutate(value);
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`);
}

test("schema 2 resolves one release identity and the required product matrix", () => {
  const plan = loadReleasePlan({ manifestPath: MANIFEST });

  assert.equal(plan.schema, 2);
  assert.equal(plan.version, CURRENT_VERSION);
  assert.equal(plan.channel, "preview");
  assert.equal(plan.tag, CURRENT_TAG);
  assert.equal(plan.prerelease, true);
  assert.equal(plan.run_builds, false);
  assert.equal(plan.publish, false);
  assert.equal(plan.legacy_product, "");
  assert.equal(plan.desktop.enabled, true);
  assert.equal(plan.desktop.required, true);
  assert.equal(plan.desktop.targets.length, 6);
  assert.equal(plan.desktop.targets.every((target) => target.enabled && target.required), true);
  assert.equal(plan.phone.enabled, true);
  assert.equal(plan.phone.required, true);
  assert.equal(plan.phone.platform, "android");
  assert.deepEqual(
    plan.phone.targets.map(({ name, enabled, required }) => ({ name, enabled, required })),
    [
      { name: "android", enabled: true, required: true },
      { name: "ios", enabled: false, required: false },
    ],
  );
});

test("GitHub output exposes stable fan-out keys", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "v8-release-plan-"));
  const outputPath = path.join(dir, "github-output.txt");
  const plan = loadReleasePlan({ manifestPath: MANIFEST });
  writeGithubOutputs(outputPath, plan);
  const outputs = fs.readFileSync(outputPath, "utf8");

  assert.ok(outputs.includes(`version=${CURRENT_VERSION}\n`));
  assert.match(outputs, /^channel=preview$/m);
  assert.ok(outputs.includes(`tag=${CURRENT_TAG}\n`));
  assert.match(outputs, /^prerelease=true$/m);
  assert.match(outputs, /^run_builds=false$/m);
  assert.match(outputs, /^publish=false$/m);
  assert.match(outputs, /^legacy_product=$/m);
  assert.match(outputs, /^desktop_enabled=true$/m);
  assert.match(outputs, /^desktop_required=true$/m);
  assert.match(outputs, /^desktop_targets_json=\["windows-x64","windows-arm64","macos-x64","macos-arm64","linux-x64","linux-arm64"\]$/m);
  assert.match(outputs, /^phone_enabled=true$/m);
  assert.match(outputs, /^phone_required=true$/m);
  assert.match(outputs, /^android_enabled=true$/m);
  assert.match(outputs, /^android_required=true$/m);
  assert.match(outputs, /^ios_enabled=false$/m);
  assert.match(outputs, /^ios_required=false$/m);
  assert.match(outputs, /^phone_platform=android$/m);
  assert.match(outputs, /^phone_profile=preview$/m);
});

test("manual build runs both products without publishing", () => {
  const plan = loadReleasePlan({ manifestPath: MANIFEST, mode: "build" });
  assert.equal(plan.run_builds, true);
  assert.equal(plan.publish, false);
  assert.equal(plan.desktop.enabled, true);
  assert.equal(plan.phone.enabled, true);
});

test("unified tag builds and publishes every enabled product", () => {
  const plan = loadReleasePlan({
    manifestPath: MANIFEST,
    tag: CURRENT_TAG,
  });
  assert.equal(plan.run_builds, true);
  assert.equal(plan.publish, true);
  assert.equal(plan.legacy_product, "");
  assert.equal(plan.desktop.enabled, true);
  assert.equal(plan.phone.enabled, true);
});

test("legacy product tag builds and publishes only that compatibility product", () => {
  const plan = loadReleasePlan({
    manifestPath: MANIFEST,
    tag: `v8-os-phone-v${CURRENT_VERSION}`,
  });
  assert.equal(plan.run_builds, true);
  assert.equal(plan.publish, true);
  assert.equal(plan.legacy_product, "phone");
  assert.equal(plan.tag_kind, "legacy-product");
  assert.equal(plan.desktop.enabled, false);
  assert.equal(plan.desktop.required, false);
  assert.equal(plan.desktop.targets.every((target) => !target.enabled && !target.required), true);
  assert.equal(plan.phone.enabled, true);
  assert.equal(plan.phone.platform, "android");

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "v8-legacy-release-plan-"));
  const outputPath = path.join(dir, "github-output.txt");
  writeGithubOutputs(outputPath, plan);
  const outputs = fs.readFileSync(outputPath, "utf8");
  assert.match(outputs, /^legacy_product=phone$/m);
  assert.match(outputs, /^desktop_enabled=false$/m);
  assert.match(outputs, /^desktop_targets_json=\[\]$/m);
  assert.match(outputs, /^android_enabled=true$/m);
});

test("legacy Desktop plan deactivates every Phone target", () => {
  const plan = loadReleasePlan({
    manifestPath: MANIFEST,
    tag: `v8-os-desktop-v${CURRENT_VERSION}`,
  });

  assert.equal(plan.desktop.enabled, true);
  assert.equal(plan.phone.enabled, false);
  assert.equal(plan.phone.required, false);
  assert.equal(plan.phone.platform, "none");
  assert.equal(plan.phone.targets.every((target) => !target.enabled && !target.required), true);
});

test("stable channel is rejected until stable signing and installation gates exist", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "v8-stable-release-plan-"));
  const manifestPath = path.join(dir, "release-manifest.json");
  const stableManifest = JSON.parse(fs.readFileSync(MANIFEST, "utf8"));
  stableManifest.release.channel = "stable";
  fs.writeFileSync(manifestPath, `${JSON.stringify(stableManifest, null, 2)}\n`);

  assert.throws(
    () => loadReleasePlan({ manifestPath }),
    /release\.channel must remain preview until the stable signing and installation gates are implemented/,
  );
});

test("unknown manual mode is rejected", () => {
  assert.throws(
    () => loadReleasePlan({ manifestPath: MANIFEST, mode: "publish" }),
    /--mode must be dry-run or build/,
  );
});

test("plan rejects every drifted version projection before fan-out", () => {
  const cases = [
    {
      file: "VERSION",
      mutate: (filePath) => fs.writeFileSync(filePath, "0.0.0\n"),
      message: /VERSION is 0\.0\.0/,
    },
    {
      file: "VERSION",
      mutate: (filePath) => fs.writeFileSync(filePath, ""),
      message: /VERSION is , expected/,
    },
    {
      file: path.join("apps", "v8-agent-os-shell", "package.json"),
      mutate: (filePath) => mutateJson(filePath, (value) => { value.version = "0.0.0"; }),
      message: /Desktop package\.json version is 0\.0\.0/,
    },
    {
      file: path.join("apps", "v8-agent-os-shell", "package-lock.json"),
      mutate: (filePath) => mutateJson(filePath, (value) => { value.version = "0.0.0"; }),
      message: /Desktop package-lock\.json version is 0\.0\.0/,
    },
    {
      file: path.join("apps", "v8-agent-os-shell", "package-lock.json"),
      mutate: (filePath) => mutateJson(filePath, (value) => { value.packages[""].version = "0.0.0"; }),
      message: /Desktop package-lock root version is 0\.0\.0/,
    },
    {
      file: path.join("apps", "v8-agent-os-phone", "package.json"),
      mutate: (filePath) => mutateJson(filePath, (value) => { value.version = "0.0.0"; }),
      message: /Phone package\.json version is 0\.0\.0/,
    },
    {
      file: path.join("apps", "v8-agent-os-phone", "package-lock.json"),
      mutate: (filePath) => mutateJson(filePath, (value) => { value.version = "0.0.0"; }),
      message: /Phone package-lock\.json version is 0\.0\.0/,
    },
    {
      file: path.join("apps", "v8-agent-os-phone", "package-lock.json"),
      mutate: (filePath) => mutateJson(filePath, (value) => { value.packages[""].version = "0.0.0"; }),
      message: /Phone package-lock root version is 0\.0\.0/,
    },
    {
      file: path.join("apps", "v8-agent-os-phone", "app.json"),
      mutate: (filePath) => mutateJson(filePath, (value) => { value.expo.version = "0.0.0"; }),
      message: /Phone Expo version is 0\.0\.0/,
    },
    {
      file: path.join("apps", "v8-agent-os-phone", "app.json"),
      mutate: (filePath) => mutateJson(filePath, (value) => { value.expo.android.versionCode = 1; }),
      message: /Phone Android versionCode is 1/,
    },
    {
      file: path.join("apps", "v8-agent-os-phone", "app.json"),
      mutate: (filePath) => mutateJson(filePath, (value) => { value.expo.ios.buildNumber = "1"; }),
      message: /Phone iOS buildNumber is 1/,
    },
  ];

  for (const scenario of cases) {
    const root = projectionFixture();
    try {
      scenario.mutate(path.join(root, scenario.file));
      assert.throws(
        () => loadReleasePlan({ manifestPath: path.join(root, "release-manifest.json") }),
        scenario.message,
      );
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  }
});
