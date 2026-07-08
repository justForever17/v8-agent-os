#!/usr/bin/env node
import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const VERSION_RE = /^\d{4}\.\d{2}\.\d{2}\.\d+$/;

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

function toTag(product, version, channel = "preview") {
  if (product === "desktop" && channel === "preview") {
    return `v8-os-desktop-preview-v${version}`;
  }
  return `v8-os-${product}-v${version}`;
}

function toSemver(version) {
  const [year, month, day, build] = version.split(".").map((value) => Number(value));
  return `${year}.${month}.${day}-${build}`;
}

function toAppVersion(version) {
  const [year, month, day] = version.split(".").map((value) => Number(value));
  return `${year}.${month}.${day}`;
}

function toAndroidVersionCode(version) {
  const [year, month, day, build] = version.split(".");
  return Number(`${year.slice(2)}${month}${day}${build.padStart(2, "0")}`);
}

function toAppleBuildNumber(version) {
  const [year, month, day, build] = version.split(".");
  return `${year}${month}${day}${build.padStart(2, "0")}`;
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

function updateManifest(product, version, channel) {
  const manifestPath = resolve(ROOT, "release-manifest.json");
  const manifest = existsSync(manifestPath)
    ? readJson(manifestPath)
    : { schema: 1, products: {} };
  manifest.products = manifest.products || {};
  manifest.products[product] = {
    version,
    channel,
    tag: toTag(product, version, channel),
    updatedAt: new Date().toISOString(),
  };
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

function printPlan({ product, version, channel, tag, apply, integrity }) {
  const semver = toSemver(version);
  console.log(`V8OS release ${apply ? "apply" : "dry-run"}`);
  console.log(`product: ${product}`);
  console.log(`version: ${version}`);
  console.log(`semver projection: ${semver}`);
  console.log(`channel: ${channel}`);
  console.log(`tag: ${tag}`);
  console.log(integrity.message);
  if (!apply) {
    console.log("");
    console.log("No files changed. Add --apply to update version files, create a release commit, and create the local annotated tag.");
  }
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const product = args.product;
  const version = args.version;
  const channel = args.channel || "preview";
  const apply = Boolean(args.apply);

  if (!["phone", "desktop"].includes(product)) {
    throw new Error("Missing or invalid --product. Use phone or desktop.");
  }
  if (!VERSION_RE.test(version || "")) {
    throw new Error("Missing or invalid --version. Expected YYYY.MM.DD.N.");
  }
  if (!["preview", "stable"].includes(channel)) {
    throw new Error("Invalid --channel. Use preview or stable.");
  }

  const tag = toTag(product, version, channel);
  ensureTagAvailable(tag);
  ensureCleanForApply(apply);

  const integrity = product === "phone"
    ? validatePhoneTgzIntegrity()
    : { ok: true, message: "Desktop release has no Phone local tarball integrity check." };
  if (!integrity.ok) {
    throw new Error(integrity.message);
  }

  printPlan({ product, version, channel, tag, apply, integrity });
  if (!apply) return;

  const changed = [];
  if (product === "phone") changed.push(...updatePhoneVersion(version));
  if (product === "desktop") changed.push(...updateDesktopVersion(version));
  changed.push(updateManifest(product, version, channel));
  const notesPath = writeNotes(product, version, channel, tag);

  run("git", ["add", ...changed.map((item) => relative(ROOT, item).replaceAll("\\", "/"))]);
  run("git", ["commit", "-m", `chore(release): prepare ${tag}`]);
  run("git", ["tag", "-a", tag, "-F", notesPath]);

  console.log("");
  console.log(`Created local release commit and annotated tag: ${tag}`);
  console.log("Review the result, then push explicitly when ready:");
  console.log(`git push origin HEAD && git push origin ${tag}`);
}

try {
  main();
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}
