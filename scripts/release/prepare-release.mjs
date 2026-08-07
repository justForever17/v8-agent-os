#!/usr/bin/env node
import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  compareReleaseVersions,
  loadReleaseManifest,
  isValidReleaseVersion,
  resolveReleasePlan,
  toAndroidVersionCode,
  toAppVersion,
  toAppleBuildNumber,
  toSemver,
  toUnifiedTag,
  validateReleaseProjections,
  validateReleaseManifest,
} from "./release-manifest.mjs";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith("--")) continue;
    const key = token.slice(2);
    const next = argv[i + 1];
    if (!next || next.startsWith("--")) {
      args[key] = true;
    } else {
      args[key] = next;
      i += 1;
    }
  }
  return args;
}

function git(args, options = {}) {
  return execFileSync("git", args, {
    cwd: ROOT,
    encoding: "utf8",
    stdio: ["ignore", "pipe", options.quiet ? "ignore" : "pipe"],
  }).trim();
}

function run(command, args) {
  const result = spawnSync(command, args, {
    cwd: ROOT,
    encoding: "utf8",
    stdio: "pipe",
  });
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(" ")} failed:\n${result.stderr || result.stdout}`);
  }
  return result.stdout.trim();
}

function readJson(pathname) {
  return JSON.parse(readFileSync(pathname, "utf8"));
}

function writeJson(pathname, value) {
  writeFileSync(pathname, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

function sha512Integrity(pathname) {
  const digest = createHash("sha512").update(readFileSync(pathname)).digest("base64");
  return `sha512-${digest}`;
}

// Compatibility reference only. prepare-release never creates this deprecated tag.
function legacyTagForCompatibility(product, version) {
  return `v8-os-${product}-v${version}`;
}

function updateVersionProjection(version) {
  const versionPath = resolve(ROOT, "VERSION");
  writeFileSync(versionPath, `${toSemver(version)}\n`, "utf8");
  return versionPath;
}

function ensureCleanForApply(apply) {
  if (!apply) return;
  const status = git(["status", "--porcelain"]);
  if (status) {
    throw new Error("Working tree is dirty. Commit or stash unrelated changes before --apply.");
  }
}

function ensureTagAvailable(tag) {
  const existing = git(["tag", "--list", tag], { quiet: true });
  if (existing) {
    throw new Error(`Tag already exists: ${tag}`);
  }
}

function validatePhoneTgzIntegrity() {
  const phoneRoot = resolve(ROOT, "apps/v8-agent-os-phone");
  const lockPath = resolve(phoneRoot, "package-lock.json");
  const lock = readJson(lockPath);
  const entry = lock.packages?.["node_modules/@v8/session-realtime"];
  if (!entry?.resolved || !entry?.integrity) {
    return { ok: false, message: "Phone lockfile does not contain @v8/session-realtime integrity metadata." };
  }

  const tarballPath = resolve(phoneRoot, entry.resolved.replace(/^file:/, ""));
  if (!existsSync(tarballPath)) {
    return { ok: false, message: `Phone local tarball is missing: ${relative(ROOT, tarballPath)}` };
  }

  const actual = sha512Integrity(tarballPath);
  if (actual !== entry.integrity) {
    return {
      ok: false,
      message: [
        "Phone local tarball integrity mismatch.",
        `expected: ${entry.integrity}`,
        `actual:   ${actual}`,
        `tarball:  ${relative(ROOT, tarballPath)}`,
      ].join("\n"),
    };
  }
  return { ok: true, message: `Phone local tarball integrity OK: ${relative(ROOT, tarballPath)}` };
}

function validateDesktopTgzIntegrity() {
  const appRoots = [
    resolve(ROOT, "apps/v8-agent-os-admin"),
    resolve(ROOT, "apps/v8-agent-os-web"),
    resolve(ROOT, "apps/v8-agent-os-desktop-pet"),
  ];
  const verified = [];
  for (const appRoot of appRoots) {
    const lockPath = resolve(appRoot, "package-lock.json");
    const lock = readJson(lockPath);
    for (const [packagePath, entry] of Object.entries(lock.packages || {})) {
      const resolved = String(entry?.resolved || "");
      if (!resolved.startsWith("file:") || !resolved.endsWith(".tgz")) continue;
      if (!entry.integrity) {
        return { ok: false, message: `${relative(ROOT, lockPath)} has no integrity for ${packagePath}.` };
      }
      const tarballPath = resolve(appRoot, resolved.replace(/^file:/, ""));
      if (!existsSync(tarballPath)) {
        return { ok: false, message: `Desktop local tarball is missing: ${relative(ROOT, tarballPath)}` };
      }
      const actual = sha512Integrity(tarballPath);
      if (actual !== entry.integrity) {
        return {
          ok: false,
          message: [
            `Desktop local tarball integrity mismatch for ${packagePath}.`,
            `lockfile: ${relative(ROOT, lockPath)}`,
            `expected: ${entry.integrity}`,
            `actual:   ${actual}`,
            `tarball:  ${relative(ROOT, tarballPath)}`,
          ].join("\n"),
        };
      }
      verified.push(`${relative(ROOT, lockPath)}:${packagePath}`);
    }
  }
  return {
    ok: true,
    message: `Desktop local tarball integrity OK: ${verified.join(", ")}`,
  };
}

function updatePhoneVersion(version) {
  const semver = toSemver(version);
  const packagePath = resolve(ROOT, "apps/v8-agent-os-phone/package.json");
  const lockPath = resolve(ROOT, "apps/v8-agent-os-phone/package-lock.json");
  const appPath = resolve(ROOT, "apps/v8-agent-os-phone/app.json");

  const pkg = readJson(packagePath);
  pkg.version = semver;
  writeJson(packagePath, pkg);

  const lock = readJson(lockPath);
  lock.version = semver;
  if (lock.packages?.[""]) lock.packages[""].version = semver;
  writeJson(lockPath, lock);

  const app = readJson(appPath);
  app.expo = app.expo || {};
  app.expo.version = toAppVersion(version);
  app.expo.android = app.expo.android || {};
  app.expo.android.versionCode = toAndroidVersionCode(version);
  app.expo.ios = app.expo.ios || {};
  app.expo.ios.buildNumber = toAppleBuildNumber(version);
  writeJson(appPath, app);

  return [packagePath, lockPath, appPath];
}

function updateDesktopVersion(version) {
  const semver = toSemver(version);
  const packagePath = resolve(ROOT, "apps/v8-agent-os-shell/package.json");
  const lockPath = resolve(ROOT, "apps/v8-agent-os-shell/package-lock.json");
  const pkg = readJson(packagePath);
  pkg.version = semver;
  writeJson(packagePath, pkg);

  const lock = readJson(lockPath);
  lock.version = semver;
  if (lock.packages?.[""]) lock.packages[""].version = semver;
  writeJson(lockPath, lock);

  return [packagePath, lockPath];
}

function updateManifest(version, channel) {
  const manifestPath = resolve(ROOT, "release-manifest.json");
  const manifest = loadReleaseManifest(manifestPath).manifest;
  manifest.release = {
    version,
    channel,
    tag: toUnifiedTag(version),
    updatedAt: new Date().toISOString(),
  };
  validateReleaseManifest(manifest);
  writeJson(manifestPath, manifest);
  return manifestPath;
}

function writeNotes(product, version, channel, tag) {
  const outPath = resolve(ROOT, "dist/release-notes", `${tag}.md`);
  mkdirSync(dirname(outPath), { recursive: true });
  run("node", [
    "scripts/release/generate-release-notes.mjs",
    "--product",
    product,
    "--version",
    version,
    "--tag",
    tag,
    "--channel",
    channel,
    "--out",
    outPath,
  ]);
  return outPath;
}

function printPlan({ version, channel, tag, apply, integrity, products, deprecatedProduct, fromManifest }) {
  const semver = toSemver(version);
  console.log(`V8OS release ${apply ? "apply" : "dry-run"}`);
  console.log(`products: ${products.join(", ")}`);
  console.log(`version: ${version}`);
  console.log(`semver projection: ${semver}`);
  console.log(`channel: ${channel}`);
  console.log(`tag: ${tag}`);
  console.log(`source: ${fromManifest ? "schema 2 manifest (repeatable validation)" : "command line"}`);
  for (const result of integrity) console.log(result.message);
  if (deprecatedProduct) {
    const legacyTag = legacyTagForCompatibility(deprecatedProduct, version);
    console.warn(
      `Deprecated --product ${deprecatedProduct} was ignored. ${legacyTag} remains a supported compatibility trigger during the two-cycle transition, but prepare-release creates only ${tag} and updates every enabled product.`,
    );
  }
  if (!apply) {
    console.log("");
    console.log(fromManifest
      ? "Validation complete. No files changed."
      : "No files changed. Add --apply to update version files, create a release commit, and create the local annotated tag.");
  }
}

export function resolvePreparationIdentity({ manifest, version, channel = "preview", product, allowCurrent = false }) {
  if (product && !["phone", "desktop"].includes(product)) {
    throw new Error("Invalid --product. The deprecated compatibility values are phone or desktop.");
  }
  if (!isValidReleaseVersion(version)) {
    throw new Error("Missing or invalid --version. Expected a real UTC date in YYYY.MM.DD.N form with N >= 1.");
  }
  if (channel !== "preview") {
    throw new Error("Only the preview channel is currently publishable; stable signing and installation gates are not implemented.");
  }
  const comparison = compareReleaseVersions(version, manifest.release.version);
  if ((!allowCurrent && comparison <= 0) || (allowCurrent && comparison !== 0)) {
    throw new Error(
      allowCurrent
        ? `--from-manifest must validate the current release ${manifest.release.version}`
        : `Release version ${version} must be newer than current manifest version ${manifest.release.version}`,
    );
  }
  const plan = resolveReleasePlan(manifest);
  const products = [
    ...(plan.desktop.enabled ? ["desktop"] : []),
    ...(plan.phone.enabled ? ["phone"] : []),
  ];
  return {
    version,
    channel,
    tag: toUnifiedTag(version),
    products,
    deprecatedProduct: product || null,
  };
}

export function resolvePreparationRequest(args, currentManifest) {
  const fromManifest = Boolean(args["from-manifest"]);
  const apply = Boolean(args.apply);
  if (fromManifest && apply) {
    throw new Error("--from-manifest is a repeatable read-only validation and cannot be combined with --apply");
  }
  if (fromManifest && (args.version || args.channel)) {
    throw new Error("--from-manifest reads release.version and release.channel; do not also pass --version or --channel");
  }
  return {
    fromManifest,
    apply,
    checkTagAvailability: !fromManifest,
    version: fromManifest ? currentManifest.release.version : args.version,
    channel: fromManifest ? currentManifest.release.channel : (args.channel || "preview"),
  };
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const currentManifest = loadReleaseManifest(resolve(ROOT, "release-manifest.json")).manifest;
  const currentProjection = validateReleaseProjections(currentManifest, ROOT);
  const request = resolvePreparationRequest(args, currentManifest);
  const { fromManifest, apply, version, channel } = request;
  const identity = resolvePreparationIdentity({
    manifest: currentManifest,
    version,
    channel,
    product: args.product,
    allowCurrent: fromManifest,
  });
  const { tag, products, deprecatedProduct } = identity;
  if (request.checkTagAvailability) ensureTagAvailable(tag);
  ensureCleanForApply(apply);

  const integrity = products.map((product) => (
    product === "phone" ? validatePhoneTgzIntegrity() : validateDesktopTgzIntegrity()
  ));
  if (fromManifest) {
    integrity.unshift({
      ok: true,
      message: `Release version projections OK: ${currentProjection.semver}`,
    });
  }
  const failedIntegrity = integrity.find((result) => !result.ok);
  if (failedIntegrity) {
    throw new Error(failedIntegrity.message);
  }

  printPlan({
    version,
    channel,
    tag,
    apply,
    integrity,
    products,
    deprecatedProduct,
    fromManifest,
  });
  if (!apply) return;

  const changed = [];
  if (products.includes("phone")) changed.push(...updatePhoneVersion(version));
  if (products.includes("desktop")) changed.push(...updateDesktopVersion(version));
  changed.push(updateVersionProjection(version));
  changed.push(updateManifest(version, channel));
  validateReleaseProjections(loadReleaseManifest(resolve(ROOT, "release-manifest.json")).manifest, ROOT);
  const notesPath = writeNotes("all", version, channel, tag);

  run("git", ["add", ...changed.map((item) => relative(ROOT, item).replaceAll("\\", "/"))]);
  run("git", ["commit", "-m", `chore(release): prepare ${tag}`]);
  run("git", ["tag", "-a", tag, "-F", notesPath]);

  console.log("");
  console.log(`Created local release commit and annotated tag: ${tag}`);
  console.log("Review the result, then push explicitly when ready:");
  console.log(`git push origin HEAD && git push origin ${tag}`);
}

const isMain =
  process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isMain) {
  try {
    main();
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exit(1);
  }
}
