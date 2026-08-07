#!/usr/bin/env node
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const repoRoot = path.resolve(path.dirname(__filename), "..", "..");
const shellRoot = path.join(repoRoot, "apps", "v8-agent-os-shell");
const releaseDir = path.join(shellRoot, "dist", "release");
const ASSET_KINDS = {
  "windows-x64": [{ extension: ".exe", suffix: "win-x64-setup.exe" }],
  "windows-arm64": [{ extension: ".exe", suffix: "win-arm64-setup.exe" }],
  "macos-x64": [{ extension: ".dmg", suffix: "macos-x64.dmg" }],
  "macos-arm64": [{ extension: ".dmg", suffix: "macos-arm64.dmg" }],
  "linux-x64": [
    { extension: ".AppImage", suffix: "linux-x64.AppImage" },
    { extension: ".deb", suffix: "linux-x64.deb" },
  ],
  "linux-arm64": [
    { extension: ".AppImage", suffix: "linux-arm64.AppImage" },
    { extension: ".deb", suffix: "linux-arm64.deb" },
  ],
};

function argValue(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? String(process.argv[index + 1] || "") : "";
}

function releaseVersion() {
  const tag = String(process.env.GITHUB_REF_NAME || "");
  const match = /^v8-os-desktop-v(.+)$/.exec(tag);
  return match ? `preview-${match[1]}` : `preview-${process.env.GITHUB_RUN_NUMBER || "local"}`;
}

function sha256(filePath) {
  return createHash("sha256").update(fs.readFileSync(filePath)).digest("hex");
}

function rootFiles(dir) {
  return fs.readdirSync(dir, { withFileTypes: true })
    .filter((entry) => entry.isFile())
    .map((entry) => path.join(dir, entry.name));
}

try {
  const platform = argValue("--platform");
  const kinds = ASSET_KINDS[platform];
  if (!kinds) throw new Error(`Unsupported --platform ${JSON.stringify(platform)}`);
  const outputDir = path.resolve(argValue("--output-dir") || path.join(shellRoot, "desktop-release-assets"));
  if (!fs.existsSync(releaseDir)) throw new Error(`Desktop release directory not found: ${releaseDir}`);
  const files = rootFiles(releaseDir);
  fs.rmSync(outputDir, { recursive: true, force: true });
  fs.mkdirSync(outputDir, { recursive: true });

  const releaseAssets = [];
  for (const kind of kinds) {
    const candidates = files.filter((filePath) => path.extname(filePath) === kind.extension);
    if (candidates.length !== 1) {
      throw new Error(`Expected one ${kind.extension} installer for ${platform} in ${releaseDir}; found ${candidates.length}.`);
    }
    const targetName = `V8-Agent-OS-${releaseVersion()}-${kind.suffix}`;
    const target = path.join(outputDir, targetName);
    fs.copyFileSync(candidates[0], target);
    releaseAssets.push(target);
  }

  for (const diagnosticName of ["RUNTIME_PROBE.json", `PACKAGE_LAYOUT-${platform}.json`]) {
    const source = path.join(releaseDir, diagnosticName);
    if (!fs.existsSync(source)) throw new Error(`Required desktop diagnostic is missing: ${source}`);
    const targetName = diagnosticName === "RUNTIME_PROBE.json" ? `RUNTIME_PROBE-${platform}.json` : diagnosticName;
    fs.copyFileSync(source, path.join(outputDir, targetName));
  }

  const checksums = releaseAssets.map((filePath) => `${sha256(filePath)}  ${path.basename(filePath)}`);
  const checksumName = `SHA256SUMS-${platform}.txt`;
  fs.writeFileSync(path.join(outputDir, checksumName), `${checksums.join("\n")}\n`, "utf8");
  console.log(checksums.join("\n"));
  console.log(`Prepared ${releaseAssets.length} release asset(s) in ${outputDir}`);
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}
