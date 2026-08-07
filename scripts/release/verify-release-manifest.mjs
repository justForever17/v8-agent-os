#!/usr/bin/env node
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import {
  loadReleaseManifest,
  resolveReleaseTag,
  validateReleaseProjections,
} from "./release-manifest.mjs";

function parseArgs(argv) {
  const args = { manifest: "release-manifest.json" };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--product") {
      args.product = argv[++index];
    } else if (arg === "--tag") {
      args.tag = argv[++index];
    } else if (arg === "--manifest") {
      args.manifest = argv[++index];
    } else if (arg === "--json") {
      args.json = true;
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
    "  node scripts/release/verify-release-manifest.mjs --tag v8-os-v2026.08.07.3",
    "  node scripts/release/verify-release-manifest.mjs --product desktop --tag v8-os-desktop-v2026.08.07.3",
    "",
    "Options:",
    "  --product phone|desktop  Optional for unified tags; required identity is derived for legacy tags",
    "  --tag <tag>              Defaults to release.tag from the manifest",
    "  --manifest <path>        Defaults to release-manifest.json",
    "  --json                   Print the resolved contract as JSON",
  ].join("\n");
}

function fail(message) {
  console.error(`[release-manifest] ${message}`);
  process.exitCode = 1;
}

export function verifyReleaseManifest({ product, tag, manifestPath = "release-manifest.json", verifyProjections = false }) {
  const loaded = loadReleaseManifest(manifestPath);
  if (verifyProjections) {
    validateReleaseProjections(loaded.manifest, path.dirname(loaded.manifestPath));
  }
  const resolved = resolveReleaseTag({ manifest: loaded.manifest, tag, product });
  return {
    schema: loaded.manifest.schema,
    ...resolved,
    manifestPath: loaded.manifestPath,
  };
}

function printDeprecationWarning(message) {
  console.error(`[release-manifest] deprecated: ${message}`);
  if (process.env.GITHUB_ACTIONS === "true") {
    console.error(`::warning title=Deprecated V8OS release tag::${message}`);
  }
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
      verifyProjections: true,
    });
    if (result.warning) printDeprecationWarning(result.warning);
    if (args.json) {
      console.log(JSON.stringify(result, null, 2));
      return;
    }
    const productLabel = result.product || "all enabled products";
    console.log(
      `[release-manifest] ok: ${productLabel} ${result.version} (${result.tag}, ${result.tagKind}) matches ${result.manifestPath}`,
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
