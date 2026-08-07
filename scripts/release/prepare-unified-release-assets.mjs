#!/usr/bin/env node
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { loadReleaseManifest, resolveReleaseTag } from "./release-manifest.mjs";

const DESKTOP_ASSETS = Object.freeze({
  "windows-x64": ["win-x64-setup.exe"],
  "windows-arm64": ["win-arm64-setup.exe"],
  "macos-x64": ["macos-x64.dmg"],
  "macos-arm64": ["macos-arm64.dmg"],
  "linux-x64": ["linux-x64.AppImage", "linux-x64.deb"],
  "linux-arm64": ["linux-arm64.AppImage", "linux-arm64.deb"],
});

function parseArgs(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (!token.startsWith("--")) continue;
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) {
      result[token.slice(2)] = true;
      continue;
    }
    result[token.slice(2)] = value;
    index += 1;
  }
  return result;
}

function sha256(filePath) {
  return createHash("sha256").update(fs.readFileSync(filePath)).digest("hex");
}

function requireFile(source, { required, label }) {
  if (fs.existsSync(source) && fs.statSync(source).isFile()) return true;
  if (required) throw new Error(`Required ${label} is missing: ${source}`);
  console.warn(`Optional ${label} is missing: ${source}`);
  return false;
}

function copyAsset(source, outputDir, targetName, requirement) {
  if (!requireFile(source, requirement)) return null;
  const target = path.join(outputDir, targetName);
  fs.copyFileSync(source, target);
  return target;
}

function desktopVersion(version, channel) {
  return channel === "stable" ? version : `preview-${version}`;
}

function selectedProducts(tagIdentity) {
  return tagIdentity.tagKind === "legacy-product"
    ? new Set([tagIdentity.product])
    : new Set(["desktop", "phone"]);
}

function prepareOutputDirectory(inputDir, outputDir) {
  const root = path.parse(outputDir).root;
  const inputFromOutput = path.relative(outputDir, inputDir);
  const outputContainsInput = inputFromOutput === "" || (
    inputFromOutput !== ".." &&
    !inputFromOutput.startsWith(`..${path.sep}`) &&
    !path.isAbsolute(inputFromOutput)
  );
  if (outputDir === root || outputContainsInput) {
    throw new Error(`Refusing unsafe release output directory: ${outputDir}`);
  }
  if (fs.existsSync(outputDir)) {
    if (!fs.statSync(outputDir).isDirectory()) {
      throw new Error(`Release output path is not a directory: ${outputDir}`);
    }
    if (fs.readdirSync(outputDir).length > 0) {
      throw new Error(`Release output directory must be empty: ${outputDir}`);
    }
  } else {
    fs.mkdirSync(outputDir, { recursive: true });
  }
}

export function prepareUnifiedReleaseAssets({ manifestPath, tag, inputDir, outputDir }) {
  const { manifest } = loadReleaseManifest(manifestPath);
  const tagIdentity = resolveReleaseTag({ manifest, tag });
  const products = selectedProducts(tagIdentity);
  const resolvedInput = path.resolve(inputDir);
  const resolvedOutput = path.resolve(outputDir);
  const releaseFiles = [];

  prepareOutputDirectory(resolvedInput, resolvedOutput);

  if (products.has("desktop") && manifest.products.desktop.enabled) {
    const version = desktopVersion(manifest.release.version, manifest.release.channel);
    for (const [targetName, suffixes] of Object.entries(DESKTOP_ASSETS)) {
      const target = manifest.products.desktop.targets[targetName];
      if (!target.enabled) continue;
      for (const suffix of suffixes) {
        const fileName = `V8-Agent-OS-${version}-${suffix}`;
        const copied = copyAsset(
          path.join(resolvedInput, "desktop", fileName),
          resolvedOutput,
          fileName,
          { required: target.required, label: `Desktop ${targetName} asset` },
        );
        if (copied) releaseFiles.push(copied);
      }
    }
  }

  if (products.has("phone") && manifest.products.phone.enabled) {
    const android = manifest.products.phone.targets.android;
    if (android.enabled) {
      const extension = manifest.release.channel === "stable" ? "aab" : "apk";
      const targetName = manifest.release.channel === "stable"
        ? `V8OS-Phone-${manifest.release.version}-android.aab`
        : `V8OS-Phone-${manifest.release.version}-android-preview.apk`;
      const copied = copyAsset(
        path.join(resolvedInput, "phone", "android", `app-release.${extension}`),
        resolvedOutput,
        targetName,
        { required: android.required, label: "Phone Android asset" },
      );
      if (copied) releaseFiles.push(copied);
    }

    const ios = manifest.products.phone.targets.ios;
    if (ios.enabled) {
      const targetName = `V8OS-Phone-${manifest.release.version}-ios.ipa`;
      const copied = copyAsset(
        path.join(resolvedInput, "phone", "ios", "app-release.ipa"),
        resolvedOutput,
        targetName,
        { required: ios.required, label: "Phone iOS asset" },
      );
      if (copied) releaseFiles.push(copied);
    }
  }

  if (releaseFiles.length === 0) {
    throw new Error(`Release plan for ${tagIdentity.tag} produced no public assets.`);
  }

  const checksums = releaseFiles
    .sort((left, right) => path.basename(left).localeCompare(path.basename(right)))
    .map((filePath) => `${sha256(filePath)}  ${path.basename(filePath)}`);
  fs.writeFileSync(path.join(resolvedOutput, "SHA256SUMS.txt"), `${checksums.join("\n")}\n`, "utf8");
  return {
    tag: tagIdentity.tag,
    assets: releaseFiles.map((filePath) => path.basename(filePath)),
    checksumFile: "SHA256SUMS.txt",
  };
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  for (const required of ["manifest", "input-dir", "output-dir"]) {
    if (!args[required]) throw new Error(`Missing --${required}`);
  }
  const result = prepareUnifiedReleaseAssets({
    manifestPath: args.manifest,
    tag: args.tag,
    inputDir: args["input-dir"],
    outputDir: args["output-dir"],
  });
  console.log(`Prepared ${result.assets.length} public asset(s) for ${result.tag}.`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    main();
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exit(1);
  }
}
