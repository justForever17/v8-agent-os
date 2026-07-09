#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

function parseArgs(argv) {
  const args = {
    manifest: "release-manifest.json",
  };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--product") {
      args.product = argv[++index];
    } else if (arg === "--tag") {
      args.tag = argv[++index];
    } else if (arg === "--manifest") {
      args.manifest = argv[++index];
    } else if (arg === "--help" || arg === "-h") {
      args.help = true;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }
  return args;
}

function usage() {
  return [
    "Usage:",
    "  node scripts/release/verify-release-manifest.mjs --product desktop --tag v8-os-desktop-v2026.07.09.1",
    "",
    "Options:",
    "  --product phone|desktop",
    "  --tag v8-os-<product>-vYYYY.MM.DD.N",
    "  --manifest release-manifest.json",
  ].join("\n");
}

function fail(message) {
  console.error(`[release-manifest] ${message}`);
  process.exitCode = 1;
}

export function verifyReleaseManifest({ product, tag, manifestPath = "release-manifest.json" }) {
  if (!product || !["phone", "desktop"].includes(product)) {
    throw new Error("--product must be phone or desktop");
  }
  if (!tag) {
    throw new Error("--tag is required");
  }

  const pattern = new RegExp(`^v8-os-${product}-v(\\d{4}\\.\\d{2}\\.\\d{2}\\.\\d+)$`);
  const match = pattern.exec(tag);
  if (!match) {
    throw new Error(`Tag ${tag} does not match v8-os-${product}-vYYYY.MM.DD.N`);
  }
  const version = match[1];
  const resolvedManifest = path.resolve(manifestPath);
  const manifest = JSON.parse(fs.readFileSync(resolvedManifest, "utf8"));
  const entry = manifest?.products?.[product];
  if (!entry) {
    throw new Error(`release-manifest.json has no products.${product} entry`);
  }

  const problems = [];
  if (entry.version !== version) {
    problems.push(`version is ${entry.version}, expected ${version}`);
  }
  if (entry.tag !== tag) {
    problems.push(`tag is ${entry.tag}, expected ${tag}`);
  }
  if (problems.length > 0) {
    throw new Error(`${product} release manifest mismatch: ${problems.join("; ")}`);
  }

  return {
    product,
    tag,
    version,
    manifestPath: resolvedManifest,
  };
}

async function main() {
  let args;
  try {
    args = parseArgs(process.argv.slice(2));
  } catch (error) {
    fail(error.message);
    console.error(usage());
    return;
  }

  if (args.help) {
    console.log(usage());
    return;
  }

  try {
    const result = verifyReleaseManifest({
      product: args.product,
      tag: args.tag,
      manifestPath: args.manifest,
    });
    console.log(
      `[release-manifest] ok: ${result.product} ${result.version} (${result.tag}) matches ${result.manifestPath}`,
    );
  } catch (error) {
    fail(error.message);
  }
}

const isMain =
  process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isMain) {
  await main();
}
