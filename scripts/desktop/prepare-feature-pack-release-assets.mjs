#!/usr/bin/env node
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { Readable, Transform } from "node:stream";
import { pipeline } from "node:stream/promises";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "..", "..");
const featurePackRoot = path.join(
  repoRoot,
  "apps",
  "v8-agent-os-engine",
  "requirements",
  "feature-packs",
);
const outputRoot = path.join(featurePackRoot, "bundled-assets");
const supportedTargets = new Set(["windows-x64", "linux-x64"]);
const manifestNames = [
  "creative-media-image-analysis.manifest.json",
  "creative-media-motion-capture.manifest.json",
];

function argValue(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? String(process.argv[index + 1] || "") : "";
}

function sha256File(filePath) {
  const hash = createHash("sha256");
  const descriptor = fs.openSync(filePath, "r");
  const buffer = Buffer.allocUnsafe(1024 * 1024);
  try {
    let bytesRead = 0;
    do {
      bytesRead = fs.readSync(descriptor, buffer, 0, buffer.length, null);
      if (bytesRead > 0) hash.update(buffer.subarray(0, bytesRead));
    } while (bytesRead > 0);
  } finally {
    fs.closeSync(descriptor);
  }
  return hash.digest("hex");
}

function readAssets() {
  return manifestNames.flatMap((name) => {
    const manifest = JSON.parse(fs.readFileSync(path.join(featurePackRoot, name), "utf8"));
    return (manifest.assets || []).map((asset) => ({
      ...asset,
      packId: String(manifest.id || ""),
      sources: [asset.url, ...(asset.mirrors || [])]
        .map((value) => String(value || "").trim())
        .filter(Boolean),
    }));
  });
}

function assertAssetContract(asset) {
  if (!asset.packId || !asset.id || !/^[a-f0-9]{64}$/i.test(String(asset.sha256 || ""))) {
    throw new Error(`Invalid feature-pack asset contract: ${asset.packId || "unknown"}/${asset.id || "unknown"}`);
  }
  if (!Number.isSafeInteger(asset.size) || asset.size <= 0 || !asset.target || !asset.sources.length) {
    throw new Error(`Incomplete feature-pack asset contract: ${asset.packId}/${asset.id}`);
  }
  for (const source of asset.sources) {
    const parsed = new URL(source);
    if (parsed.protocol !== "https:") throw new Error(`Insecure feature-pack asset source: ${parsed.hostname}`);
  }
}

function assertVerifiedAsset(filePath, asset) {
  if (!fs.existsSync(filePath) || fs.statSync(filePath).size !== asset.size) return false;
  return sha256File(filePath).toLowerCase() === String(asset.sha256).toLowerCase();
}

async function downloadSource(source, temporaryPath, asset) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15 * 60_000);
  let received = 0;
  try {
    const response = await fetch(source, {
      redirect: "follow",
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
    const limiter = new Transform({
      transform(chunk, _encoding, callback) {
        received += Buffer.byteLength(chunk);
        if (received > asset.size) {
          callback(new Error("asset_size_exceeded"));
          return;
        }
        callback(null, chunk);
      },
    });
    await pipeline(
      Readable.fromWeb(response.body),
      limiter,
      fs.createWriteStream(temporaryPath, { flags: "wx" }),
      { signal: controller.signal },
    );
  } finally {
    clearTimeout(timeout);
  }
}

async function materializeAsset(asset, seedRoot) {
  assertAssetContract(asset);
  const assetRoot = path.join(outputRoot, String(asset.sha256).toLowerCase());
  const target = path.resolve(assetRoot, asset.target);
  if (target !== assetRoot && !target.startsWith(`${assetRoot}${path.sep}`)) {
    throw new Error(`Feature-pack asset target escapes output root: ${asset.packId}/${asset.id}`);
  }
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const seed = seedRoot ? path.join(seedRoot, asset.target) : "";
  if (seed && assertVerifiedAsset(seed, asset)) {
    fs.copyFileSync(seed, target);
  } else {
    let lastError = null;
    for (const source of asset.sources) {
      const temporary = `${target}.download`;
      fs.rmSync(temporary, { force: true });
      try {
        await downloadSource(source, temporary, asset);
        if (!assertVerifiedAsset(temporary, asset)) throw new Error("size_or_sha256_mismatch");
        fs.renameSync(temporary, target);
        lastError = null;
        break;
      } catch (error) {
        lastError = error;
        fs.rmSync(temporary, { force: true });
      }
    }
    if (lastError || !fs.existsSync(target)) {
      throw new Error(
        `Unable to prepare ${asset.packId}/${asset.id}: ${lastError instanceof Error ? lastError.message : String(lastError)}`,
      );
    }
  }
  if (!assertVerifiedAsset(target, asset)) {
    throw new Error(`Prepared feature-pack asset failed verification: ${asset.packId}/${asset.id}`);
  }
  return {
    packId: asset.packId,
    assetId: asset.id,
    target: asset.target,
    size: asset.size,
    sha256: String(asset.sha256).toLowerCase(),
    file: path.relative(outputRoot, target).replace(/\\/g, "/"),
  };
}

try {
  const target = argValue("--target");
  if (!supportedTargets.has(target)) {
    throw new Error(`Unsupported --target ${JSON.stringify(target)}; bundled feature-pack assets are x64 desktop resources`);
  }
  const seedRoot = argValue("--seed-root") ? path.resolve(argValue("--seed-root")) : "";
  fs.rmSync(outputRoot, { recursive: true, force: true });
  fs.mkdirSync(outputRoot, { recursive: true });
  const prepared = [];
  for (const asset of readAssets()) prepared.push(await materializeAsset(asset, seedRoot));
  fs.writeFileSync(
    path.join(outputRoot, "manifest.json"),
    `${JSON.stringify({ version: 1, target, assets: prepared }, null, 2)}\n`,
    "utf8",
  );
  console.log(`Prepared ${prepared.length} verified offline feature-pack asset(s) for ${target}.`);
} catch (error) {
  console.error(error instanceof Error ? error.stack || error.message : String(error));
  process.exit(1);
}
