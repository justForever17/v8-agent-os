#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import {
  loadReleaseManifest,
  resolveReleasePlan,
  resolveReleaseTag,
  validateReleaseProjections,
} from "./release-manifest.mjs";

function parseArgs(argv) {
  const args = { manifest: "release-manifest.json", mode: "dry-run" };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--manifest") {
      args.manifest = argv[++index];
    } else if (arg === "--tag") {
      args.tag = argv[++index];
    } else if (arg === "--mode") {
      args.mode = argv[++index];
    } else if (arg === "--github-output") {
      const next = argv[index + 1];
      if (next && !next.startsWith("--")) {
        args.githubOutput = next;
        index += 1;
      } else {
        args.githubOutput = process.env.GITHUB_OUTPUT || "";
      }
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
    "  node scripts/release/resolve-release-plan.mjs [--tag v8-os-vYYYY.MM.DD.N]",
    "",
    "Options:",
    "  --manifest <path>       Defaults to release-manifest.json",
    "  --tag <tag>             A unified or transitional legacy tag; enables build and publish",
    "  --mode dry-run|build     Manual mode without a tag; defaults to dry-run",
    "  --github-output [path]  Append stable fan-out outputs to this path or $GITHUB_OUTPUT",
    "",
    "stdout is always a JSON release plan suitable for PR matrix dry-runs.",
  ].join("\n");
}

function githubOutputs(plan) {
  const android = plan.phone.targets.find((target) => target.name === "android");
  const ios = plan.phone.targets.find((target) => target.name === "ios");
  const desktopTargets = plan.desktop.enabled
    ? plan.desktop.targets.filter((target) => target.enabled).map((target) => target.name)
    : [];
  return {
    version: plan.version,
    channel: plan.channel,
    tag: plan.tag,
    canonical_tag: plan.canonical_tag,
    prerelease: plan.prerelease,
    run_builds: plan.run_builds,
    publish: plan.publish,
    legacy_product: plan.legacy_product,
    desktop_enabled: plan.desktop.enabled,
    desktop_required: plan.desktop.required,
    desktop_targets_json: JSON.stringify(desktopTargets),
    phone_enabled: plan.phone.enabled,
    phone_required: plan.phone.required,
    android_enabled: plan.phone.enabled && android.enabled,
    android_required: plan.phone.required && android.required,
    ios_enabled: plan.phone.enabled && ios.enabled,
    ios_required: plan.phone.required && ios.required,
    phone_platform: plan.phone.platform,
    phone_profile: plan.channel === "stable" ? "production" : "preview",
  };
}

export function writeGithubOutputs(outputPath, plan) {
  if (!outputPath) {
    throw new Error("--github-output requires a path or a non-empty GITHUB_OUTPUT variable");
  }
  const lines = Object.entries(githubOutputs(plan))
    .map(([key, value]) => `${key}=${String(value)}`)
    .join("\n");
  fs.appendFileSync(path.resolve(outputPath), `${lines}\n`, "utf8");
}

export function loadReleasePlan({ manifestPath = "release-manifest.json", tag, mode = "dry-run" } = {}) {
  if (!["dry-run", "build"].includes(mode)) {
    throw new Error("--mode must be dry-run or build");
  }
  const loaded = loadReleaseManifest(manifestPath);
  validateReleaseProjections(loaded.manifest, path.dirname(loaded.manifestPath));
  const hasExplicitTag = Boolean(tag);
  const identity = resolveReleaseTag({
    manifest: loaded.manifest,
    tag: tag || loaded.manifest.release.tag,
  });
  const plan = resolveReleasePlan(loaded.manifest);
  const legacyProduct = identity.deprecated ? identity.product : "";
  if (legacyProduct === "desktop") {
    plan.phone.enabled = false;
    plan.phone.required = false;
    plan.phone.platform = "none";
    plan.phone.targets = plan.phone.targets.map((target) => ({
      ...target,
      enabled: false,
      required: false,
    }));
  } else if (legacyProduct === "phone") {
    plan.desktop.enabled = false;
    plan.desktop.required = false;
    plan.desktop.targets = plan.desktop.targets.map((target) => ({
      ...target,
      enabled: false,
      required: false,
    }));
  }
  return {
    ...plan,
    tag: identity.tag,
    canonical_tag: loaded.manifest.release.tag,
    run_builds: hasExplicitTag || mode === "build",
    publish: hasExplicitTag,
    legacy_product: legacyProduct,
    tag_kind: hasExplicitTag ? identity.tagKind : "manual",
    warning: identity.warning || null,
  };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(usage());
    return;
  }
  const plan = loadReleasePlan({
    manifestPath: args.manifest,
    tag: args.tag,
    mode: args.mode,
  });
  if (plan.warning) console.error(`[release-plan] deprecated: ${plan.warning}`);
  if (args.githubOutput !== undefined) {
    writeGithubOutputs(args.githubOutput, plan);
  }
  console.log(JSON.stringify(plan, null, 2));
}

const isMain =
  process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isMain) {
  try {
    await main();
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}
