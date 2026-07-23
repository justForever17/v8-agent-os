#!/usr/bin/env node

import { createHash } from "node:crypto";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { gunzipSync } from "node:zlib";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const packageRoot = path.join(repoRoot, "packages", "product-ui");
const verifyBuild = process.argv.includes("--verify-build");

function invariant(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function readJson(filePath) {
  return JSON.parse(readFileSync(filePath, "utf8"));
}

function parseTarOctal(buffer, start, length) {
  const value = buffer.subarray(start, start + length).toString("utf8").replace(/\0.*$/, "").trim();
  return value ? Number.parseInt(value, 8) : 0;
}

function readPackageArchive(archivePath) {
  const tar = gunzipSync(readFileSync(archivePath));
  const entries = new Map();

  for (let offset = 0; offset + 512 <= tar.length; ) {
    const header = tar.subarray(offset, offset + 512);
    if (header.every((value) => value === 0)) break;

    const name = header.subarray(0, 100).toString("utf8").replace(/\0.*$/, "");
    const prefix = header.subarray(345, 500).toString("utf8").replace(/\0.*$/, "");
    const entryName = prefix ? `${prefix}/${name}` : name;
    const size = parseTarOctal(header, 124, 12);
    const type = String.fromCharCode(header[156] || 48);
    const contentStart = offset + 512;

    if ((type === "0" || type === "\0") && entryName.startsWith("package/")) {
      entries.set(entryName.slice("package/".length), tar.subarray(contentStart, contentStart + size));
    }

    offset = contentStart + Math.ceil(size / 512) * 512;
  }

  return entries;
}

function collectFiles(root, relative = "") {
  const target = path.join(root, relative);
  if (!existsSync(target)) return [];
  if (statSync(target).isFile()) return [relative.replaceAll("\\", "/")];
  return readdirSync(target, { withFileTypes: true }).flatMap((entry) =>
    collectFiles(root, path.join(relative, entry.name)),
  );
}

function verifyConsumer(relativeRoot, expectedDependency, version, integrity) {
  const consumerRoot = path.join(repoRoot, relativeRoot);
  const packageJson = readJson(path.join(consumerRoot, "package.json"));
  const lock = readJson(path.join(consumerRoot, "package-lock.json"));
  const lockedRoot = lock.packages?.[""];
  const lockedPackage = lock.packages?.["node_modules/@v8/product-ui"];

  invariant(
    packageJson.dependencies?.["@v8/product-ui"] === expectedDependency,
    `${relativeRoot}/package.json must reference ${expectedDependency}`,
  );
  invariant(
    lockedRoot?.dependencies?.["@v8/product-ui"] === expectedDependency,
    `${relativeRoot}/package-lock.json root dependency is stale`,
  );
  invariant(lockedPackage?.version === version, `${relativeRoot} locks Product UI ${lockedPackage?.version || "unknown"}`);
  invariant(lockedPackage?.resolved === expectedDependency, `${relativeRoot} resolves an unexpected Product UI archive`);
  invariant(lockedPackage?.integrity === integrity, `${relativeRoot} Product UI integrity does not match the archive`);
}

function verifyBuiltContents(entries) {
  const packagedFiles = [...entries.keys()].filter((entry) => entry !== "package.json").sort();
  const builtFiles = ["styles.css", ...collectFiles(packageRoot, "dist")].sort();

  invariant(existsSync(path.join(packageRoot, "styles.css")), "Product UI styles.css is missing; build the package first");
  invariant(existsSync(path.join(packageRoot, "dist", "index.js")), "Product UI dist is missing; build the package first");
  invariant(
    JSON.stringify(packagedFiles) === JSON.stringify(builtFiles),
    "Product UI archive file list differs from the current build output",
  );

  for (const relativePath of builtFiles) {
    const packaged = entries.get(relativePath);
    const built = readFileSync(path.join(packageRoot, relativePath));
    const packagedText = packaged?.toString("utf8").replaceAll("\r\n", "\n");
    const builtText = built.toString("utf8").replaceAll("\r\n", "\n");
    invariant(packagedText === builtText, `Product UI archive contains stale build output: ${relativePath}`);
  }
}

try {
  const sourcePackage = readJson(path.join(packageRoot, "package.json"));
  const version = String(sourcePackage.version || "").trim();
  invariant(sourcePackage.name === "@v8/product-ui", "Unexpected Product UI package name");
  invariant(/^\d+\.\d+\.\d+$/.test(version), `Invalid Product UI version: ${version || "missing"}`);

  const archiveName = `v8-product-ui-${version}.tgz`;
  const archivePath = path.join(packageRoot, archiveName);
  const archives = readdirSync(packageRoot)
    .filter((name) => /^v8-product-ui-\d+\.\d+\.\d+\.tgz$/.test(name))
    .sort();
  invariant(
    archives.length === 1 && archives[0] === archiveName,
    `Keep exactly one Product UI archive (${archiveName}); found: ${archives.join(", ") || "none"}`,
  );

  const archiveBuffer = readFileSync(archivePath);
  const integrity = `sha512-${createHash("sha512").update(archiveBuffer).digest("base64")}`;
  const entries = readPackageArchive(archivePath);
  const archivedPackage = JSON.parse(entries.get("package.json")?.toString("utf8") || "null");
  invariant(archivedPackage?.name === sourcePackage.name, "Product UI archive package name is stale");
  invariant(archivedPackage?.version === version, "Product UI archive version is stale");
  for (const requiredFile of ["dist/index.js", "dist/index.d.ts", "dist/product-theme-bootstrap.js", "styles.css"]) {
    invariant(entries.has(requiredFile), `Product UI archive is missing ${requiredFile}`);
  }

  const dependency = `file:../../packages/product-ui/${archiveName}`;
  verifyConsumer("apps/v8-agent-os-admin", dependency, version, integrity);
  verifyConsumer("apps/v8-agent-os-web", dependency, version, integrity);
  if (verifyBuild) verifyBuiltContents(entries);

  console.log(`Product UI ${version} archive, consumers, lockfiles, and integrity are aligned${verifyBuild ? " with the current build" : ""}.`);
} catch (error) {
  console.error(`Product UI package verification failed: ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
}
