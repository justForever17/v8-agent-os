#!/usr/bin/env node
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { loadReleaseManifest, resolveReleaseTag, toSemver } from "./release-manifest.mjs";

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
    : new Set(["desktop", "phone", "server", "tui"]);
}

function archiveMember(filename, member) {
  try {
    // Read to stdout only: no archive-controlled path is extracted to disk.
    const value = execFileSync("tar", ["-xzOf", filename, member], { encoding: "utf8", maxBuffer: 16 * 1024 * 1024, stdio: ["ignore", "pipe", "pipe"] });
    if (!value) throw new Error("empty member");
    return value;
  } catch { throw new Error(`Invalid release archive or missing member ${member}: ${path.basename(filename)}`); }
}

export function verifyArchiveIdentity(filename, { product, version, target, sourceCommit } = {}) {
  if (product === "server") {
    const root = `v8os-server-${version}-${target}`;
    const identity = JSON.parse(archiveMember(filename, `${root}/server-manifest.json`));
    const arch = target.replace(/^linux-/, "");
    if (identity.schema !== 1 || identity.profile !== "server" || identity.version !== version
        || identity.platform !== "linux" || identity.arch !== arch || identity.sourceDirty !== false
        || !/^[a-f0-9]{40}$/.test(identity.sourceCommit || "")
        || (sourceCommit && identity.sourceCommit !== sourceCommit)) throw new Error("Server archive identity does not match release version, target or source commit");
    if (archiveMember(filename, `${root}/VERSION`).trim() !== toSemver(version)) throw new Error("Server archive VERSION projection mismatch");
    archiveEntry(filename, `${root}/apps/v8-agent-os-engine/main.py`);
    archiveEntry(filename, `${root}/apps/v8-agent-os-cli/bin/v8os.mjs`);
    archiveEntry(filename, `${root}/SHA256SUMS`);
    return identity;
  }
  if (product !== "tui") throw new Error(`Unsupported archive product: ${product}`);
  const pkg = JSON.parse(archiveMember(filename, "package/package.json"));
  if (pkg.name !== "@v8-agent-os/v8-agent-os" || pkg.version !== toSemver(version)
      || pkg.bin?.v8os !== "bin/v8os.mjs" || pkg.bin?.["v8os-tui"] !== "bin/v8os-tui.mjs" || pkg.engines?.node !== ">=22"
      || pkg.v8Release?.version !== version || !/^[a-f0-9]{40}$/.test(pkg.v8Release?.sourceCommit || "")
      || (sourceCommit && pkg.v8Release.sourceCommit !== sourceCommit)) throw new Error("TUI archive identity does not match the release package, version, bin, runtime or source commit");
  for (const value of Object.values({ ...pkg.dependencies, ...pkg.optionalDependencies })) {
    if (/^(?:file:|link:|workspace:)/.test(value)) throw new Error("TUI published package contains a repository-local dependency");
  }
  archiveMember(filename, "package/bin/v8os-tui.mjs");
  archiveMember(filename, "package/bin/v8os.mjs");
  archiveMember(filename, "package/bin/engine-bootstrap.mjs");
  archiveMember(filename, "package/dist/main.js");
  archiveMember(filename, "package/dist/core-control.mjs");
  archiveMember(filename, "package/dist/cli.mjs");
  archiveMember(filename, "package/LICENSE");
  return pkg;
}

export function verifyEngineArchiveIdentity(filename, { version, target = "linux-x64", sourceCommit } = {}) {
  const root = `v8os-engine-${version}-${target}`;
  const expectedPython = target.startsWith("windows-")
    ? "apps/v8-agent-os-engine/.python/python.exe"
    : "apps/v8-agent-os-engine/.python/bin/python3";
  const identity = JSON.parse(archiveMember(filename, `${root}/engine-manifest.json`));
  if (identity.schema !== 1 || identity.profile !== "engine" || identity.version !== version
      || identity.target !== target || identity.sourceDirty !== false
      || identity.runtimeProfile !== "server" || identity.startupProfile !== "server"
      || !/^[a-f0-9]{40}$/.test(identity.sourceCommit || "")
      || identity.engineDir !== "apps/v8-agent-os-engine"
      || identity.python !== expectedPython
      || identity.cli !== "apps/v8-agent-os-cli/bin/v8os.mjs"
      || (sourceCommit && identity.sourceCommit !== sourceCommit)) {
    throw new Error("Engine archive identity does not match release version, target or source commit");
  }
  archiveEntry(filename, `${root}/apps/v8-agent-os-engine/main.py`);
  const pythonMember = `${root}/${identity.python}`;
  const pythonEntry = archiveEntry(filename, pythonMember);
  if (pythonEntry.kind === "directory") throw new Error("Engine archive Python entry is a directory");
  if (pythonEntry.kind === "symlink") {
    if (!pythonEntry.target || path.posix.isAbsolute(pythonEntry.target) || pythonEntry.target.split("/").includes("..")) {
      throw new Error("Engine archive Python symlink is unsafe");
    }
    const targetMember = path.posix.normalize(path.posix.join(path.posix.dirname(pythonMember), pythonEntry.target));
    const targetEntry = archiveEntry(filename, targetMember);
    if (targetEntry.kind === "directory") throw new Error("Engine archive Python symlink targets a directory");
  }
  archiveEntry(filename, `${root}/${identity.cli}`);
  verifyEngineChecksums(filename, root, identity);
  return identity;
}

function verifyEngineChecksums(filename, root, identity) {
  const raw = archiveMember(filename, `${root}/SHA256SUMS`);
  const entries = new Map();
  for (const line of raw.split(/\r?\n/).map(value => value.trim()).filter(Boolean)) {
    const match = /^([a-f0-9]{64})  ([^\\\x00-\x1f]+)$/.exec(line);
    if (!match || path.posix.isAbsolute(match[2]) || match[2].split('/').includes('..') || entries.has(match[2])) {
      throw new Error(`Invalid Engine checksum entry in ${path.basename(filename)}`);
    }
    entries.set(match[2], match[1]);
  }
  const pythonReceipt = `${identity.engineDir}/.python/v8os-runtime.json`;
  const required = ["engine-manifest.json", "README.md", "apps/v8-agent-os-engine/main.py", identity.cli, pythonReceipt];
  for (const member of required) {
    if (!entries.has(member)) throw new Error(`Engine checksum manifest omits ${member}`);
    const content = Buffer.from(archiveMember(filename, `${root}/${member}`));
    if (sha256Buffer(content) !== entries.get(member)) throw new Error(`Engine checksum mismatch for ${member}`);
  }
  const receipt = JSON.parse(archiveMember(filename, `${root}/${pythonReceipt}`));
  if (receipt.schema !== 1 || receipt.profile !== "server" || receipt.target !== identity.target || typeof receipt.browserIncluded !== "boolean") {
    throw new Error("Engine runtime receipt does not match the server portable profile");
  }
}

export function verifyEngineReleaseAssets({ archive, manifest, version, target = "linux-x64", sourceCommit } = {}) {
  const publicManifest = JSON.parse(fs.readFileSync(manifest, "utf8"));
  const expectedRoot = `v8os-engine-${version}-${target}`;
  const expectedAsset = path.basename(archive);
  const digest = sha256(archive);
  if (publicManifest.schema !== 1 || publicManifest.profile !== "engine"
      || publicManifest.version !== version || publicManifest.target !== target
      || publicManifest.runtimeProfile !== "server" || publicManifest.startupProfile !== "server"
      || (publicManifest.runtimeProfile !== undefined && publicManifest.runtimeProfile !== "server")
      || publicManifest.root !== expectedRoot || publicManifest.asset !== expectedAsset
      || !/^[a-f0-9]{64}$/.test(publicManifest.sha256 || "") || publicManifest.sha256 !== digest
      || !/^[a-f0-9]{40}$/.test(publicManifest.sourceCommit || "")
      || (sourceCommit && publicManifest.sourceCommit !== sourceCommit)) {
    throw new Error("Engine public manifest does not match release version, target, archive SHA-256 or source commit");
  }
  const internal = verifyEngineArchiveIdentity(archive, { version, target, sourceCommit });
  if (publicManifest.sourceCommit !== internal.sourceCommit) {
    throw new Error("Engine public manifest source commit differs from internal engine manifest");
  }
  return { publicManifest, internal };
}

function sha256Buffer(content) {
  return createHash("sha256").update(content).digest("hex");
}

function archiveEntry(filename, member) {
  try {
    const names = execFileSync("tar", ["-tzf", filename, member], { encoding: "utf8", maxBuffer: 16 * 1024 * 1024, stdio: ["ignore", "pipe", "pipe"] })
      .split(/\r?\n/).map(value => value.trim()).filter(Boolean);
    if (!names.includes(member)) throw new Error("missing member");
    const detail = execFileSync("tar", ["-tvzf", filename, member], { encoding: "utf8", maxBuffer: 16 * 1024 * 1024, stdio: ["ignore", "pipe", "pipe"] })
      .split(/\r?\n/).map(value => value.trim()).find(Boolean);
    if (!detail) throw new Error("missing listing");
    const kind = detail[0] === "l" ? "symlink" : detail[0] === "d" ? "directory" : "file";
    const arrow = detail.indexOf(" -> ");
    return { kind, target: arrow >= 0 ? detail.slice(arrow + 4).trim() : "" };
  } catch {
    throw new Error(`Invalid release archive or missing member ${member}: ${path.basename(filename)}`);
  }
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

export function prepareUnifiedReleaseAssets({ manifestPath, tag, inputDir, outputDir, sourceCommit }) {
  const { manifest } = loadReleaseManifest(manifestPath);
  const tagIdentity = resolveReleaseTag({ manifest, tag });
  const products = selectedProducts(tagIdentity);
  const resolvedInput = path.resolve(inputDir);
  const resolvedOutput = path.resolve(outputDir);
  const releaseFiles = [];

  prepareOutputDirectory(resolvedInput, resolvedOutput);

  if (products.has("server") && manifest.products.server?.enabled) {
    for (const [targetName, target] of Object.entries(manifest.products.server.targets)) {
      if (!target.enabled) continue;
      const fileName = `V8OS-Server-${manifest.release.version}-${targetName}.tar.gz`;
      const source = path.join(resolvedInput, "server", fileName);
      if (fs.existsSync(source)) verifyArchiveIdentity(source, { product: "server", version: manifest.release.version, target: targetName, sourceCommit });
      const copied = copyAsset(path.join(resolvedInput, "server", fileName), resolvedOutput, fileName,
        { required: target.required, label: `Server ${targetName} asset` });
      if (copied) releaseFiles.push(copied);
      const standalone = target.standalone;
      if (standalone?.enabled) {
        const engineName = `V8OS-Engine-${manifest.release.version}-${targetName}.tar.gz`;
        const engineSource = path.join(resolvedInput, "server", engineName);
        const manifestName = `V8OS-Engine-${manifest.release.version}-${targetName}.json`;
        const manifestSource = path.join(resolvedInput, "server", manifestName);
        const hasEngine = fs.existsSync(engineSource) && fs.statSync(engineSource).isFile();
        const hasManifest = fs.existsSync(manifestSource) && fs.statSync(manifestSource).isFile();
        if (hasEngine !== hasManifest) {
          throw new Error(`Engine ${targetName} asset and manifest must be published as a pair`);
        }
        if (!hasEngine) {
          if (standalone.required) throw new Error(`Required Engine ${targetName} asset is missing: ${engineSource}`);
          console.warn(`Optional Engine ${targetName} asset is missing: ${engineSource}`);
        } else {
          verifyEngineReleaseAssets({ archive: engineSource, manifest: manifestSource, version: manifest.release.version, target: targetName, sourceCommit });
          const engine = copyAsset(engineSource, resolvedOutput, engineName,
            { required: true, label: `Engine ${targetName} asset` });
          const manifestAsset = copyAsset(manifestSource, resolvedOutput, manifestName,
            { required: true, label: `Engine ${targetName} manifest` });
          if (engine) releaseFiles.push(engine);
          if (manifestAsset) releaseFiles.push(manifestAsset);
        }
      }
    }
    for (const [targetName, standalone] of Object.entries(manifest.products.server.standaloneTargets || {})) {
      if (!standalone?.enabled) continue;
      const engineName = `V8OS-Engine-${manifest.release.version}-${targetName}.tar.gz`;
      const engineSource = path.join(resolvedInput, "server", engineName);
      const manifestName = `V8OS-Engine-${manifest.release.version}-${targetName}.json`;
      const manifestSource = path.join(resolvedInput, "server", manifestName);
      const hasEngine = fs.existsSync(engineSource) && fs.statSync(engineSource).isFile();
      const hasManifest = fs.existsSync(manifestSource) && fs.statSync(manifestSource).isFile();
      if (hasEngine !== hasManifest) throw new Error(`Engine ${targetName} asset and manifest must be published as a pair`);
      if (!hasEngine) {
        if (standalone.required) throw new Error(`Required Engine ${targetName} asset is missing: ${engineSource}`);
        console.warn(`Optional Engine ${targetName} asset is missing: ${engineSource}`);
        continue;
      }
      verifyEngineReleaseAssets({ archive: engineSource, manifest: manifestSource, version: manifest.release.version, target: targetName, sourceCommit });
      const engine = copyAsset(engineSource, resolvedOutput, engineName, { required: true, label: `Engine ${targetName} asset` });
      const manifestAsset = copyAsset(manifestSource, resolvedOutput, manifestName, { required: true, label: `Engine ${targetName} manifest` });
      if (engine) releaseFiles.push(engine);
      if (manifestAsset) releaseFiles.push(manifestAsset);
    }
  }

  if (products.has("tui") && manifest.products.tui?.enabled && manifest.products.tui.targets.npm.enabled) {
    const fileName = `V8OS-TUI-${manifest.release.version}.tgz`;
    const source = path.join(resolvedInput, "tui", fileName);
    if (fs.existsSync(source)) verifyArchiveIdentity(source, { product: "tui", version: manifest.release.version, sourceCommit });
    const copied = copyAsset(source, resolvedOutput, fileName,
      { required: manifest.products.tui.targets.npm.required, label: "TUI npm asset" });
    if (copied) releaseFiles.push(copied);
  }

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
    sourceCommit: args["source-commit"],
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
